"""Life-Event Detection Agent — classifies inbound messages for life events.

Input: {message_text, customer_id, channel}
Output: {event_detected, event_type, confidence, attributes}

If confidence >= 0.75, emits life_event.detected to EventBridge.
"""
import json, logging, os, uuid, boto3
from datetime import datetime, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)

bedrock = boto3.client('bedrock-runtime', region_name='eu-west-1')
rds = boto3.client('rds-data', region_name='eu-west-1')
events = boto3.client('events', region_name='eu-west-1')

CLUSTER = "arn:aws:rds:eu-west-1:519124228967:cluster:aibank-core-banking-dr"
SECRET = "arn:aws:secretsmanager:eu-west-1:519124228967:secret:aibank-core-banking-CQeAg6"
DB = "corebanking"
NOVA_MODEL = "eu.amazon.nova-2-lite-v1:0"

EVENT_TYPES = ["travel", "new_baby", "job_change", "income_change", "marriage", "relocation", "large_purchase_planned", "none"]

def handler(event, context):
    message = event.get('message_text', '')
    customer_id = event.get('customer_id', '')
    channel = event.get('channel', 'app_chat')

    if not message or not customer_id:
        return {'statusCode': 400, 'error': 'message_text and customer_id required'}

    system = (
        "You detect life events in banking customer messages. "
        "Output ONLY valid JSON: {event_detected: bool, event_type: string, confidence: float 0-1, attributes: {}}. "
        f"Valid event_types: {EVENT_TYPES}. "
        "If confidence < 0.5, set event_detected=false and event_type='none'. "
        "Extract relevant attributes (dates, destinations, amounts) when detected."
    )

    resp = bedrock.converse(
        modelId=NOVA_MODEL,
        system=[{'text': system}],
        messages=[{'role': 'user', 'content': [{'text': f"Customer message: \"{message}\""}]}],
        inferenceConfig={'maxTokens': 200, 'temperature': 0.1}
    )
    raw = resp['output']['message']['content'][0]['text'].strip()
    if raw.startswith('```'):
        raw = raw.split('\n', 1)[1].rsplit('```', 1)[0]

    try:
        result = json.loads(raw)
    except:
        return {'statusCode': 200, 'event_detected': False, 'event_type': 'none', 'confidence': 0}

    # If high confidence, persist + emit event
    if result.get('event_detected') and result.get('confidence', 0) >= 0.75:
        event_id = f"evt_{uuid.uuid4().hex[:12]}"
        
        # Write to customer_life_events
        rds.execute_statement(
            resourceArn=CLUSTER, secretArn=SECRET, database=DB,
            sql=f"INSERT INTO customer_life_events (event_id, customer_id, event_type, detection_source, confidence, attributes, status) VALUES (:eid, :cid, :etype, :src, :conf, :attrs, 'active')",
            parameters=[
                {'name': 'eid', 'value': {'stringValue': event_id}},
                {'name': 'cid', 'value': {'stringValue': customer_id}},
                {'name': 'etype', 'value': {'stringValue': result['event_type']}},
                {'name': 'src', 'value': {'stringValue': channel}},
                {'name': 'conf', 'value': {'doubleValue': result['confidence']}},
                {'name': 'attrs', 'value': {'stringValue': json.dumps(result.get('attributes', {}))}},
            ]
        )

        # Emit to EventBridge
        events.put_events(Entries=[{
            'Source': 'aibank.nba',
            'DetailType': 'life_event.detected',
            'Detail': json.dumps({
                'event_id': event_id,
                'customer_id': customer_id,
                'event_type': result['event_type'],
                'confidence': result['confidence'],
                'attributes': result.get('attributes', {}),
                'source_channel': channel,
            })
        }])
        result['event_id'] = event_id
        result['persisted'] = True

    return {'statusCode': 200, **result}
