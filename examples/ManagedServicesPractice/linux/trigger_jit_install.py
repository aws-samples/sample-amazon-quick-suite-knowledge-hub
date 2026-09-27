
import json
import boto3

ses = boto3.client('ses', region_name='us-east-1')

def lambda_handler(event, context):
    record = event['Records'][0]
    sns_message = record['Sns']['Message']
    message = json.loads(sns_message)

    instance_id = message['instance_id']
    command = message['command']
    user = message['user']

    ses.send_email(
        Source='jvaidya@amazon.com',
        Destination={
            'ToAddresses': ['this_is_my_flow@us-east-1.mail.quick.aws.com']
        },
        Message={
            'Subject': {'Data': f'JIT-REQUEST: {command}'},
            'Body': {'Text': {'Data': json.dumps({
                'instance_id': instance_id,
                'command': command,
                'user': user
            })}}
        }
    )

    return {'statusCode': 200}

