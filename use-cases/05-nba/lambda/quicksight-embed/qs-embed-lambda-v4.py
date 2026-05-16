"""QuickSight Embed — Registered user with anonymous fallback.

Tries registered user embedding first (Identity Center user).
Falls back to anonymous embedding if user not yet provisioned in QuickSight.
"""
import json, boto3, os

REGION = 'eu-west-1'
ACCOUNT_ID = '519124228967'
DASHBOARD_ID = os.environ.get('DASHBOARD_ID', '08e4366c-8586-4758-ba28-fd63be63d0cd')
ALLOWED_DOMAINS = [os.environ.get('ALLOWED_DOMAIN', 'https://d1pfo41ge1bxh5.cloudfront.net'), 'https://aibank.demoaws.com']
NAMESPACE = 'default'

qs = boto3.client('quicksight', region_name=REGION)


def handler(event, context):
    body = json.loads(event.get('body') or '{}')
    email = body.get('email', '')
    dashboard_id = body.get('dashboard_id', DASHBOARD_ID)

    # Try registered user embedding first
    if email:
        for username in [email, email.lower(), email.upper()]:
            try:
                user = qs.describe_user(AwsAccountId=ACCOUNT_ID, Namespace=NAMESPACE, UserName=username)
                user_arn = user['User']['Arn']

                resp = qs.generate_embed_url_for_registered_user(
                    AwsAccountId=ACCOUNT_ID,
                    SessionLifetimeInMinutes=600,
                    UserArn=user_arn,
                    ExperienceConfiguration={
                        'Dashboard': {
                            'InitialDashboardId': dashboard_id,
                            'FeatureConfigurations': {
                                'Bookmarks': {'Enabled': True},
                                'StatePersistence': {'Enabled': True},
                            }
                        }
                    },
                    AllowedDomains=ALLOWED_DOMAINS,
                )
                return _resp(200, {'embedUrl': resp['EmbedUrl'], 'method': 'registered_user', 'user': email})
            except qs.exceptions.ResourceNotFoundException:
                continue
            except Exception as e:
                if 'ResourceNotFoundException' in str(e):
                    continue
                return _resp(500, {'error': str(e)[:200]})

    # Fallback: anonymous embedding (works for all employees)
    try:
        resp = qs.generate_embed_url_for_anonymous_user(
            AwsAccountId=ACCOUNT_ID,
            Namespace=NAMESPACE,
            SessionLifetimeInMinutes=600,
            AuthorizedResourceArns=[
                f"arn:aws:quicksight:{REGION}:{ACCOUNT_ID}:dashboard/{dashboard_id}"
            ],
            ExperienceConfiguration={
                'Dashboard': {'InitialDashboardId': dashboard_id}
            },
            AllowedDomains=ALLOWED_DOMAINS,
            SessionTags=[{'Key': 'user', 'Value': email or 'anonymous'}]
        )
        return _resp(200, {'embedUrl': resp['EmbedUrl'], 'method': 'anonymous', 'user': email})
    except Exception as e:
        return _resp(500, {'error': str(e)[:200]})


def _resp(status, body):
    return {
        'statusCode': status,
        'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
        'body': json.dumps(body),
    }
