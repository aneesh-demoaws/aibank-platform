"""DynamoDB Stream Trigger Lambda.

Two responsibilities:
  1. Start the Five Cs Step Functions workflow when a loan's status flips
     to 'processing' (existing behavior).
  2. Keep Aurora `loan_applications` in sync with DDB — the single source
     of truth is DDB; this Lambda propagates status changes (and deletes)
     to Aurora via the RDS Data API so every customer-facing view (session
     API, dashboard widgets, customer loans page) gets a consistent picture
     regardless of which Lambda wrote to DDB.

The Aurora sync is best-effort — a failure never prevents the SFN trigger
from firing and never raises back to the stream consumer (which would
block further records).
"""
import json
import boto3
import logging
import os
from typing import Dict, Any
from datetime import datetime, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)

stepfunctions_client = boto3.client('stepfunctions')
dynamodb = boto3.resource('dynamodb')
rds = boto3.client('rds-data', region_name='eu-west-1')

STATE_MACHINE_ARN = os.environ.get(
    'STATE_MACHINE_ARN',
    'arn:aws:states:eu-west-1:519124228967:stateMachine:aibank-five-cs-loan-processing-workflow',
)
DYNAMODB_TABLE = os.environ.get('DYNAMODB_TABLE', 'aibank-personal-loan')
CLUSTER_ARN = os.environ.get(
    'AURORA_CLUSTER_ARN',
    'arn:aws:rds:eu-west-1:519124228967:cluster:aibank-core-banking-dr',
)
AURORA_SECRET_ARN = os.environ.get(
    'AURORA_SECRET_ARN',
    'arn:aws:secretsmanager:eu-west-1:519124228967:secret:aibank-core-banking-CQeAg6',
)
AURORA_DB = os.environ.get('AURORA_DB', 'corebanking')

# Map DDB internal statuses to canonical Aurora status values.
# Aurora's `loan_applications.status` is an ENUM with these valid values:
#   draft, submitted, processing, underwriting, manual_review, approved,
#   rejected, disbursed, cancelled
_DDB_TO_AURORA = {
    'SUBMITTED': 'submitted',
    'PROCESSING': 'processing',
    'PENDING_REVIEW': 'manual_review',
    'APPROVED': 'approved',
    'APPROVED_AND_NOTIFIED': 'approved',
    'AUTO_APPROVED': 'approved',
    'REJECTED': 'rejected',
    'REJECTED_AND_NOTIFIED': 'rejected',
    'AUTO_REJECTED': 'rejected',
    'DRAFT': 'draft',
    'UNDERWRITING': 'underwriting',
    'DISBURSED': 'disbursed',
    'CANCELLED': 'cancelled',
}
_AURORA_VALID_STATUSES = {
    'draft', 'submitted', 'processing', 'underwriting', 'manual_review',
    'approved', 'rejected', 'disbursed', 'cancelled',
}


def lambda_handler(event, context):
    logger.info(f"Received DynamoDB stream event with {len(event.get('Records', []))} record(s)")
    processed = 0
    triggered_workflows = 0
    synced_aurora = 0

    for record in event.get('Records', []):
        processed += 1
        event_name = record.get('eventName', '')

        try:
            # SFN trigger: status change to 'processing'
            if event_name == 'MODIFY' and _is_status_change_to_processing(record):
                app_data = _extract_application_data(record)
                if app_data and _trigger_five_cs_workflow(app_data, record):
                    triggered_workflows += 1

            # Aurora sync: any MODIFY with status change, and any REMOVE
            if event_name == 'MODIFY':
                if _sync_modify_to_aurora(record):
                    synced_aurora += 1
            elif event_name == 'REMOVE':
                if _sync_remove_to_aurora(record):
                    synced_aurora += 1
            # INSERT: Alma's submit_loan_application already writes to Aurora
            # directly with INSERT...ON DUPLICATE KEY UPDATE, so nothing extra
            # to do here. (If an INSERT arrives for a record that isn't yet
            # in Aurora, a later MODIFY will pick it up and upsert.)

        except Exception as e:
            logger.error(f"record processing error: {e}", exc_info=True)
            # do not re-raise — never block downstream records

    return {
        'statusCode': 200,
        'body': json.dumps({
            'processed_records': processed,
            'triggered_workflows': triggered_workflows,
            'synced_aurora': synced_aurora,
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }),
    }


# ── SFN trigger helpers (existing behavior preserved) ───────────────────────

def _is_status_change_to_processing(record: Dict[str, Any]) -> bool:
    try:
        old_status = record.get('dynamodb', {}).get('OldImage', {}).get('status', {}).get('S', '')
        new_status = record.get('dynamodb', {}).get('NewImage', {}).get('status', {}).get('S', '')
        return new_status == 'processing' and old_status != 'processing'
    except Exception:
        return False


def _extract_application_data(record: Dict[str, Any]) -> Dict[str, Any]:
    try:
        new_image = record.get('dynamodb', {}).get('NewImage', {})
        customer_id = new_image.get('customer_id', {}).get('S', '')
        application_id = new_image.get('application_id', {}).get('S', '')
        if not customer_id or not application_id:
            logger.error("Missing composite key in stream record")
            return {}
        data = {
            'customer_id': customer_id,
            'application_id': application_id,
            'amount': float(new_image.get('amount', {}).get('N', '0')),
            'duration': int(new_image.get('duration', {}).get('N', '0')),
            'loan_type': new_image.get('loan_type', {}).get('S', 'personal'),
            'basic_salary': float(new_image.get('basic_salary', {}).get('N', '0')),
            'employer_name': new_image.get('employer_name', {}).get('S', ''),
            'bank_name': new_image.get('bank_name', {}).get('S', ''),
            'status': new_image.get('status', {}).get('S', ''),
            'created_at': new_image.get('created_at', {}).get('S', ''),
            'updated_at': new_image.get('updated_at', {}).get('S', ''),
            'loan_salary_document_received': new_image.get('loan_salary_document_received', {}).get('BOOL', False),
            'loan_statement_document_received': new_image.get('loan_statement_document_received', {}).get('BOOL', False),
        }
        for f in ('nationality', 'salary_transfer', 'average_balance', 'ending_balance'):
            if f in new_image:
                if 'S' in new_image[f]:
                    data[f] = new_image[f]['S']
                elif 'N' in new_image[f]:
                    data[f] = float(new_image[f]['N'])
        return data
    except Exception as e:
        logger.error(f"data extraction error: {e}")
        return {}


def _trigger_five_cs_workflow(app_data: Dict[str, Any], stream_record: Dict[str, Any]) -> bool:
    try:
        workflow_input = {
            'applicationData': app_data,
            'triggerContext': {
                'streamEventId': stream_record.get('eventID', ''),
                'eventTimestamp': stream_record.get('dynamodb', {}).get('ApproximateCreationDateTime', ''),
                'eventSource': 'DynamoDB',
                'triggerTimestamp': datetime.now(timezone.utc).isoformat(),
            },
        }
        execution_name = f"five-cs-{app_data['application_id']}-{int(datetime.now().timestamp())}"
        response = stepfunctions_client.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            name=execution_name,
            input=json.dumps(workflow_input, default=str),
        )
        exec_arn = response['executionArn']
        logger.info(f"✅ Five Cs workflow started for {app_data['application_id']}: {exec_arn}")
        _save_exec_arn(app_data['customer_id'], app_data['application_id'], exec_arn)
        return True
    except Exception as e:
        logger.error(f"SFN start error: {e}")
        return False


def _save_exec_arn(customer_id: str, application_id: str, exec_arn: str):
    try:
        dynamodb.Table(DYNAMODB_TABLE).update_item(
            Key={'customer_id': customer_id, 'application_id': application_id},
            UpdateExpression='SET five_cs_execution_arn = :arn, five_cs_triggered_at = :t',
            ExpressionAttributeValues={
                ':arn': exec_arn,
                ':t': datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as e:
        logger.warning(f"exec arn persist failed: {e}")


# ── Aurora sync helpers (new) ───────────────────────────────────────────────

def _map_status(ddb_status: str) -> str:
    """Map DDB status to a valid Aurora ENUM value, or '' if not mappable.

    Returns an empty string for unknown statuses so we skip the Aurora
    sync rather than crash on a DB constraint error.
    """
    if not ddb_status:
        return ''
    mapped = _DDB_TO_AURORA.get(str(ddb_status).upper())
    if mapped:
        return mapped
    lower = str(ddb_status).lower()
    return lower if lower in _AURORA_VALID_STATUSES else ''


def _sync_modify_to_aurora(record: Dict[str, Any]) -> bool:
    """Propagate status (and known fields) changes to Aurora.

    Only issues an UPDATE when the status actually changed — avoids churn.
    Falls back to an upsert-style write only if the status newly becomes
    non-empty and Aurora might not yet have the row.
    """
    try:
        new_image = record.get('dynamodb', {}).get('NewImage', {})
        old_image = record.get('dynamodb', {}).get('OldImage', {})
        application_id = new_image.get('application_id', {}).get('S', '')
        customer_id = new_image.get('customer_id', {}).get('S', '')
        if not application_id:
            return False

        old_status = old_image.get('status', {}).get('S', '')
        new_status = new_image.get('status', {}).get('S', '')
        if old_status == new_status:
            return False  # nothing to sync

        mapped = _map_status(new_status)
        if not mapped:
            return False

        # Extract a few additional fields for the rare case where Aurora
        # is behind (e.g., row didn't get INSERTed via Alma but was added
        # directly to DDB — we include enough fields for a sensible upsert).
        amount = None
        if 'amount' in new_image and 'N' in new_image['amount']:
            try:
                amount = float(new_image['amount']['N'])
            except Exception:
                amount = None
        loan_type = new_image.get('loan_type', {}).get('S', 'personal')
        duration = None
        if 'duration' in new_image and 'N' in new_image['duration']:
            try:
                duration = int(new_image['duration']['N'])
            except Exception:
                duration = None
        monthly_payment = None
        if 'monthly_payment' in new_image and 'N' in new_image['monthly_payment']:
            try:
                monthly_payment = float(new_image['monthly_payment']['N'])
            except Exception:
                monthly_payment = None

        # Primary path: UPDATE status (+ monthly_payment if present)
        params = [
            {"name": "s", "value": {"stringValue": mapped}},
            {"name": "aid", "value": {"stringValue": application_id}},
        ]
        set_clause = "status = :s, updated_at = NOW()"
        if monthly_payment is not None:
            set_clause += ", monthly_payment = :mp"
            params.append({"name": "mp", "value": {"doubleValue": monthly_payment}})

        res = rds.execute_statement(
            resourceArn=CLUSTER_ARN, secretArn=AURORA_SECRET_ARN, database=AURORA_DB,
            sql=f"UPDATE loan_applications SET {set_clause} WHERE application_id = :aid",
            parameters=params,
        )
        affected = res.get('numberOfRecordsUpdated', 0)
        if affected > 0:
            logger.info(f"Aurora sync: {application_id} status={old_status!r}->{new_status!r} (→{mapped})")
            return True

        # Upsert path: row didn't exist in Aurora yet — INSERT
        if customer_id and amount is not None and duration is not None:
            logger.info(f"Aurora sync: row not found, inserting {application_id}")
            rds.execute_statement(
                resourceArn=CLUSTER_ARN, secretArn=AURORA_SECRET_ARN, database=AURORA_DB,
                sql=("INSERT INTO loan_applications "
                     "(application_id, customer_id, loan_type, amount, duration, status, monthly_payment) "
                     "VALUES (:aid, :cid, :lt, :amt, :dur, :s, :mp) "
                     "ON DUPLICATE KEY UPDATE status = :s, updated_at = NOW()"),
                parameters=[
                    {"name": "aid", "value": {"stringValue": application_id}},
                    {"name": "cid", "value": {"stringValue": customer_id}},
                    {"name": "lt", "value": {"stringValue": loan_type}},
                    {"name": "amt", "value": {"doubleValue": float(amount)}},
                    {"name": "dur", "value": {"longValue": int(duration)}},
                    {"name": "s", "value": {"stringValue": mapped}},
                    {"name": "mp", "value": ({"doubleValue": float(monthly_payment)}
                                              if monthly_payment is not None else {"isNull": True})},
                ],
            )
            return True
        logger.warning(f"Aurora sync: row not found for {application_id} and insufficient data to insert")
        return False
    except Exception as e:
        logger.error(f"Aurora MODIFY sync failed: {e}")
        return False


def _sync_remove_to_aurora(record: Dict[str, Any]) -> bool:
    """Delete from Aurora when a record is removed from DDB."""
    try:
        old_image = record.get('dynamodb', {}).get('OldImage', {})
        application_id = old_image.get('application_id', {}).get('S', '')
        if not application_id:
            return False
        res = rds.execute_statement(
            resourceArn=CLUSTER_ARN, secretArn=AURORA_SECRET_ARN, database=AURORA_DB,
            sql="DELETE FROM loan_applications WHERE application_id = :aid",
            parameters=[{"name": "aid", "value": {"stringValue": application_id}}],
        )
        affected = res.get('numberOfRecordsUpdated', 0)
        logger.info(f"Aurora sync: REMOVE {application_id} -> {affected} row(s) deleted")
        return affected > 0
    except Exception as e:
        logger.error(f"Aurora REMOVE sync failed: {e}")
        return False
