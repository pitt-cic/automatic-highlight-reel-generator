import os
import boto3
import json
import logging
from urllib.parse import unquote_plus

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
ecs = boto3.client('ecs')

def lambda_handler(event, context):
    """
    Lambda function triggered by S3 uploads to start ECS video processing task
    """

    # Log the entire event for debugging purposes to confirm invocation
    logger.info(f"Lambda triggered. Event: {json.dumps(event)}")

    try:
        # Parse S3 event
        for record in event['Records']:
            bucket = record['s3']['bucket']['name']
            key = unquote_plus(record['s3']['object']['key'])
            
            logger.info(f"Processing file: s3://{bucket}/{key}")
            
            # Get environment variables
            cluster = os.environ['CLUSTER_NAME']
            task_definition = os.environ['TASK_DEFINITION']
            subnet_ids = os.environ['SUBNET_IDS'].split(',')
            security_group = os.environ['SECURITY_GROUP']
            assign_public_ip = os.environ['ASSIGN_PUBLIC_IP']

            
            # Start ECS task
            response = ecs.run_task(
                cluster=cluster,
                launchType='EC2',
                taskDefinition=task_definition,
                count=1,
                networkConfiguration={
                    'awsvpcConfiguration': {
                        'subnets': subnet_ids,
                        'assignPublicIp': assign_public_ip,
                        'securityGroups': [security_group]
                    }
                },
                overrides={
                    'containerOverrides': [
                        {
                            'name': 'video-processor',
                            'environment': [
                                {
                                    'name': 'S3_BUCKET',
                                    'value': bucket
                                },
                                {
                                    'name': 'S3_KEY',
                                    'value': key
                                }
                            ]
                        }
                    ]
                }
            )
            
            # Check for failures from the API call and log them clearly
            if response.get('failures'):
                failure = response['failures'][0]
                error_message = f"ECS task failed to start. Reason: {failure.get('reason')}. Detail: {failure.get('detail')}"
                logger.error(error_message)
                # Raise an exception to ensure the Lambda fails and returns a 500
                raise Exception(error_message)

            if not response.get('tasks'):
                # This case should be rare if failures are handled, but it's good practice
                raise Exception("ECS run_task did not return any tasks or failures.")

            task_arn = response['tasks'][0]['taskArn']
            logger.info(f"Started ECS task: {task_arn}")
            
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Video processing task started successfully',
                'taskArn': task_arn
            })
        }
        
    except Exception as e:
        logger.error(f"Error starting ECS task: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e)
            })
        }