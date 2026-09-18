import json
import boto3
from botocore.config import Config
import datetime
import jwt
import os
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

sts_client = boto3.client('sts')
oidc_client = boto3.client('sso-oidc', region_name='us-east-1')

IDC_APP_CLIENT_ID = os.environ.get('IDC_APP_CLIENT_ID')
ROLE_ARN = os.environ.get('QUICKSUITE_ROLE_ARN')
ALLOWED_DOMAINS = os.environ.get('ALLOWED_DOMAINS', 'http://localhost:3000').split(',')
AWS_ACCOUNT_ID = os.environ.get('AWS_ACCOUNT_ID')
DASHBOARD_ID = os.environ.get('DASHBOARD_ID', '')
QUICKSIGHT_USER_ARN = os.environ.get('QUICKSIGHT_USER_ARN', '')

CORS_HEADERS = {
  "Access-Control-Allow-Headers":"Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
  "Access-Control-Allow-Origin" : "*",
  "Access-Control-Allow-Methods":"POST,OPTIONS,GET"
}

def lambda_handler(event, context):
  logger.info("IDC_APP_CLIENT_ID: %s" % IDC_APP_CLIENT_ID)
  logger.info("ROLE_ARN: %s" % ROLE_ARN)

  if (event.get("version") == "1.0"):
    httpMethod = event['httpMethod']
  elif (event.get("version") == "2.0"):
    httpMethod = event["requestContext"]["http"]["method"]
  else:
    httpMethod = event.get("requestContext", {}).get("http", {}).get("method", "POST")

  if httpMethod == 'OPTIONS':
    return {"statusCode": 200, "headers": CORS_HEADERS, "body": json.dumps({'message': 'Hello'})}

  elif httpMethod == 'POST':
    post_string = event.get('body', '{}')
    if len(post_string) > 0:
      post_json = json.loads(post_string)
      embed_type = post_json.get('embedType', 'quickchat')

      if ('idToken' in post_json):
        id_token = post_json['idToken']
        logger.info("ID Token received (first 50 chars): %s" % id_token[:50])
        logger.info("IDC_APP_CLIENT_ID being used: %s" % IDC_APP_CLIENT_ID)

        try:
          cognito_claims = jwt.decode(id_token, options={"verify_signature": False})
          logger.info("Cognito token claims: iss=%s, aud=%s, email=%s, token_use=%s" % (
            cognito_claims.get("iss"), cognito_claims.get("aud"),
            cognito_claims.get("email"), cognito_claims.get("token_use")
          ))
        except Exception as decode_error:
          logger.error("Failed to decode Cognito token: %s" % str(decode_error))

        try:
          logger.info("=== CALLING OIDC CREATE TOKEN ===")
          oidc_token_response = oidc_client.create_token_with_iam(
            clientId = IDC_APP_CLIENT_ID,
            grantType = 'urn:ietf:params:oauth:grant-type:jwt-bearer',
            assertion = id_token
          )
          logger.info("OIDC token response received successfully")
        except Exception as e:
          import traceback
          logger.error("OIDC token error: %s" % str(e))
          logger.error("Traceback: %s" % traceback.format_exc())
          error_code = getattr(e, 'response', {}).get('Error', {}).get('Code', 'Unknown')
          error_message = getattr(e, 'response', {}).get('Error', {}).get('Message', str(e))
          result = {
            'embedUrl': '',
            'status': "Exception: %s: %s (Code: %s)" % (type(e).__name__, error_message, error_code)
          }
        else:
          logger.info("=== DECODING OIDC TOKEN ===")
          claims = jwt.decode(oidc_token_response["idToken"], options={"verify_signature": False})
          logger.info("=== ASSUMING ROLE ===")
          x = datetime.datetime.now()
          try:
            user_creds = sts_client.assume_role(
              RoleArn=ROLE_ARN,
              RoleSessionName="quicksuite_session_"+str(x.strftime("%f")),
              DurationSeconds=3600,
              ProvidedContexts=[
                {
                  'ProviderArn': "arn:aws:iam::aws:contextProvider/IdentityCenter",
                  'ContextAssertion': claims["sts:identity_context"]
                }
              ]
            )
            logger.info("Role assumed successfully")
          except Exception as e:
            logger.error("Role assumption error: %s" % str(e))
            result = {'embedUrl': '', 'status': "Role Error: " + str(e)}
          else:
            quicksuite = boto3.client(
              'quicksight',
              aws_access_key_id=user_creds['Credentials']['AccessKeyId'],
              aws_secret_access_key=user_creds['Credentials']['SecretAccessKey'],
              aws_session_token=user_creds['Credentials']['SessionToken'],
            )

            logger.info("boto3 version: %s" % boto3.__version__)

            try:
              logger.info("=== GENERATING EMBED URL (type=%s) ===" % embed_type)

              if embed_type == 'both' and DASHBOARD_ID:
                dash_response = quicksuite.generate_embed_url_for_registered_user(
                  AwsAccountId=AWS_ACCOUNT_ID,
                  UserArn=QUICKSIGHT_USER_ARN,
                  ExperienceConfiguration={
                    'Dashboard': {
                      'InitialDashboardId': DASHBOARD_ID,
                      'FeatureConfigurations': {
                        'AmazonQInQuickSight': {
                          'ExecutiveSummary': {'Enabled': True}
                        },
                        'StatePersistence': {'Enabled': True}
                      }
                    }
                  },
                  AllowedDomains=ALLOWED_DOMAINS,
                  SessionLifetimeInMinutes=600
                )
                chat_response = quicksuite.generate_embed_url_for_registered_user(
                  AwsAccountId=AWS_ACCOUNT_ID,
                  UserArn=QUICKSIGHT_USER_ARN,
                  ExperienceConfiguration={
                    'QuickChat': {}
                  },
                  AllowedDomains=ALLOWED_DOMAINS,
                  SessionLifetimeInMinutes=600
                )
                logger.info("Both embed URLs generated successfully")
                result = {
                  'embedUrl': dash_response['EmbedUrl'],
                  'chatEmbedUrl': chat_response['EmbedUrl'],
                  'status': 'SUCCESS'
                }
              elif embed_type == 'dashboard' and DASHBOARD_ID:
                response = quicksuite.generate_embed_url_for_registered_user(
                  AwsAccountId=AWS_ACCOUNT_ID,
                  UserArn=QUICKSIGHT_USER_ARN,
                  ExperienceConfiguration={
                    'Dashboard': {
                      'InitialDashboardId': DASHBOARD_ID,
                      'FeatureConfigurations': {
                        'AmazonQInQuickSight': {
                          'ExecutiveSummary': {'Enabled': True}
                        },
                        'StatePersistence': {'Enabled': True}
                      }
                    }
                  },
                  AllowedDomains=ALLOWED_DOMAINS,
                  SessionLifetimeInMinutes=600
                )
                logger.info("Dashboard embed URL generated successfully")
                result = {'embedUrl': response['EmbedUrl'], 'status': 'SUCCESS'}
              else:
                response = quicksuite.generate_embed_url_for_registered_user(
                  AwsAccountId=AWS_ACCOUNT_ID,
                  UserArn=QUICKSIGHT_USER_ARN,
                  ExperienceConfiguration={
                    'QuickChat': {}
                  },
                  AllowedDomains=ALLOWED_DOMAINS,
                  SessionLifetimeInMinutes=600
                )
                logger.info("QuickChat embed URL generated successfully")
                result = {'embedUrl': response['EmbedUrl'], 'status': 'SUCCESS'}
            except Exception as e:
              logger.error("QuickSuite embed error: %s" % str(e))
              result = {'embedUrl': '', 'status': "QuickSuite Error: " + str(e)}
      else:
        result = {'embedUrl': '', 'status': "ERROR: idToken not provided"}
    else:
      result = {'embedUrl': '', 'status': "ERROR: JSON document body not provided"}

    return {'statusCode': 200, 'headers': CORS_HEADERS, "body": json.dumps(result)}
  else:
    return {'statusCode': 405, 'headers': CORS_HEADERS, "body": "Method not allowed"}
