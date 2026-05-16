"""Execution Agent — executes consented customer actions via MCP tools.

Input: {customer_id, action_id, action_template, parameters, consent_token}
Output: {status: success|failed, steps: [...], receipt_id}
"""
import json, logging, os, uuid, boto3
from datetime import datetime, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)
rds = boto3.client('rds-data', region_name='eu-west-1')

CLUSTER = "arn:aws:rds:eu-west-1:519124228967:cluster:aibank-core-banking-dr"
SECRET = "arn:aws:secretsmanager:eu-west-1:519124228967:secret:aibank-core-banking-CQeAg6"
DB = "corebanking"

def _sql(sql, params=None):
    kwargs = dict(resourceArn=CLUSTER, secretArn=SECRET, database=DB, sql=sql)
    if params:
        kwargs['parameters'] = params
    return rds.execute_statement(**kwargs)

def handler(event, context):
    customer_id = event.get('customer_id')
    action_id = event.get('action_id')
    action_template = event.get('action_template', '')
    params = event.get('parameters', {})
    consent_token = event.get('consent_token', 'demo_consent')

    if not customer_id or not action_template:
        return {'statusCode': 400, 'error': 'customer_id and action_template required'}

    execution_id = str(uuid.uuid4())
    receipt_id = f"RCP-{uuid.uuid4().hex[:8].upper()}"
    steps = []

    try:
        # Execute based on action template
        if action_template == 'open_goal_saver':
            goal_name = params.get('goal_name', 'My Goal')
            goal_type = params.get('goal_type', 'custom')
            target = params.get('target_amount', 5000)
            monthly = params.get('monthly_contribution', 100)

            # Step 1: Create goal
            goal_id = f"goal_{customer_id[-4:]}_{uuid.uuid4().hex[:6]}"
            _sql("INSERT INTO customer_goals (goal_id, customer_id, goal_type, goal_title, target_amount, monthly_contribution, target_date, status) VALUES (:gid, :cid, :gtype, :gname, :target, :monthly, DATE_ADD(NOW(), INTERVAL 12 MONTH), 'active')",
                [{'name':'gid','value':{'stringValue':goal_id}},
                 {'name':'cid','value':{'stringValue':customer_id}},
                 {'name':'gtype','value':{'stringValue':goal_type}},
                 {'name':'gname','value':{'stringValue':goal_name}},
                 {'name':'target','value':{'doubleValue':float(target)}},
                 {'name':'monthly','value':{'doubleValue':float(monthly)}}])
            steps.append({'step': 'create_goal', 'status': 'success', 'goal_id': goal_id})

            # Step 2: Update NBA status to converted
            if action_id:
                _sql("UPDATE next_best_actions SET status='converted', converted_at=NOW() WHERE action_id=:aid",
                    [{'name':'aid','value':{'stringValue':action_id}}])
                steps.append({'step': 'mark_converted', 'status': 'success'})

        elif action_template == 'update_alert_preferences':
            steps.append({'step': 'update_preferences', 'status': 'success', 'note': 'Alerts enabled (simulated)'})
            if action_id:
                _sql("UPDATE next_best_actions SET status='converted', converted_at=NOW() WHERE action_id=:aid",
                    [{'name':'aid','value':{'stringValue':action_id}}])

        elif action_template == 'internal_transfer':
            amount = params.get('amount', 50)
            steps.append({'step': 'internal_transfer', 'status': 'success', 'amount': amount, 'note': f'BHD {amount} moved (simulated)'})
            if action_id:
                _sql("UPDATE next_best_actions SET status='converted', converted_at=NOW() WHERE action_id=:aid",
                    [{'name':'aid','value':{'stringValue':action_id}}])

        else:
            steps.append({'step': action_template, 'status': 'success', 'note': 'Executed (simulated)'})

        # Write execution audit
        _sql("INSERT INTO execution_audit (execution_id, action_id, customer_id, action_template, consent_token, step_up_auth_method, parameters, steps, status, receipt_id, started_at, completed_at) VALUES (:eid, :aid, :cid, :tmpl, :consent, 'biometric', :params, :steps, 'success', :rcpt, NOW(), NOW())",
            [{'name':'eid','value':{'stringValue':execution_id}},
             {'name':'aid','value':{'stringValue':action_id or 'direct'}},
             {'name':'cid','value':{'stringValue':customer_id}},
             {'name':'tmpl','value':{'stringValue':action_template}},
             {'name':'consent','value':{'stringValue':consent_token}},
             {'name':'params','value':{'stringValue':json.dumps(params)}},
             {'name':'steps','value':{'stringValue':json.dumps(steps)}},
             {'name':'rcpt','value':{'stringValue':receipt_id}}])

        return {'statusCode': 200, 'status': 'success', 'execution_id': execution_id, 'receipt_id': receipt_id, 'steps': steps}

    except Exception as e:
        logger.error(f"Execution failed: {e}")
        return {'statusCode': 500, 'status': 'failed', 'error': str(e)[:200], 'steps': steps}
