"""Document Understanding Agent — Nova 2 Lite multimodal for document parsing.

Input: {image_s3_uri OR image_base64, customer_id, source_channel}
Output: {document_type, confidence, extracted_fields, suggested_action}
"""
import json, logging, os, boto3, base64
from datetime import datetime, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)
bedrock = boto3.client('bedrock-runtime', region_name='eu-west-1')
events = boto3.client('events', region_name='eu-west-1')
NOVA_MODEL = "eu.amazon.nova-2-lite-v1:0"

DOC_TYPES = ["airline_ticket", "utility_bill", "receipt", "delivery_note", "salary_slip", "bank_transfer_receipt", "unknown"]

def handler(event, context):
    customer_id = event.get('customer_id', '')
    channel = event.get('source_channel', 'whatsapp')
    image_b64 = event.get('image_base64', '')
    
    if not customer_id:
        return {'statusCode': 400, 'error': 'customer_id required'}

    # If no image provided, use text-only classification (for testing)
    text_description = event.get('text_description', '')
    
    system = (
        "You are a document understanding specialist for AI Bank. "
        "Classify the document and extract structured fields. "
        f"Valid document_types: {DOC_TYPES}. "
        "Output ONLY valid JSON: {document_type, confidence (0-1), extracted_fields: {...}, suggested_action: string}. "
        "For airline_ticket: extract airline, flight_number, departure_date, departure_airport, arrival_airport, passenger_name. "
        "For utility_bill: extract vendor, amount, due_date, account_number. "
        "For receipt: extract merchant, amount, date, category."
    )

    messages = [{'role': 'user', 'content': []}]
    
    if image_b64:
        messages[0]['content'].append({
            'image': {'format': 'png', 'source': {'bytes': base64.b64decode(image_b64)}}
        })
        messages[0]['content'].append({'text': 'Classify and extract fields from this document.'})
    elif text_description:
        messages[0]['content'].append({'text': f'A customer forwarded a document described as: "{text_description}". Classify it and extract likely fields.'})
    else:
        return {'statusCode': 400, 'error': 'image_base64 or text_description required'}

    try:
        resp = bedrock.converse(
            modelId=NOVA_MODEL,
            system=[{'text': system}],
            messages=messages,
            inferenceConfig={'maxTokens': 300, 'temperature': 0.1}
        )
        raw = resp['output']['message']['content'][0]['text'].strip()
        if raw.startswith('```'):
            raw = raw.split('\n', 1)[1].rsplit('```', 1)[0]
        result = json.loads(raw)
    except Exception as e:
        logger.error(f"Doc understanding failed: {e}")
        return {'statusCode': 500, 'error': str(e)[:200]}

    # If high confidence and actionable, emit life event
    if result.get('confidence', 0) >= 0.75 and result.get('document_type') == 'airline_ticket':
        events.put_events(Entries=[{
            'Source': 'aibank.nba',
            'DetailType': 'life_event.detected',
            'Detail': json.dumps({
                'customer_id': customer_id,
                'event_type': 'travel',
                'confidence': result['confidence'],
                'attributes': result.get('extracted_fields', {}),
                'source_channel': channel,
            })
        }])
        result['event_emitted'] = 'life_event.detected (travel)'

    return {'statusCode': 200, **result}
