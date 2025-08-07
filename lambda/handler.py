import os
import boto3
import json
import logging
from urllib.parse import unquote_plus
from pathlib import Path

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
ecs = boto3.client('ecs')
s3 = boto3.client('s3')

# Supported video file extensions
SUPPORTED_VIDEO_EXTENSIONS = {
    '.mp4', '.mov', '.avi', '.mkv', '.wmv', '.flv', '.webm', 
    '.m4v', '.3gp', '.ogv', '.ts', '.mts', '.m2ts'
}

# Minimum file size (in bytes) to consider as a valid video
MIN_VIDEO_SIZE_BYTES = 1024 * 1024  # 1 MB

def is_video_file(filename):
    """
    Check if the file has a supported video extension
    """
    file_path = Path(filename.lower())
    return file_path.suffix in SUPPORTED_VIDEO_EXTENSIONS

def validate_video_file(bucket, key):
    """
    Validate that the S3 object is a legitimate video file
    Returns: (is_valid, reason, file_info)
    """
    try:
        # Get object metadata
        response = s3.head_object(Bucket=bucket, Key=key)
        
        # Check file size
        file_size = response.get('ContentLength', 0)
        if file_size < MIN_VIDEO_SIZE_BYTES:
            return False, f"File too small ({file_size} bytes). Minimum size: {MIN_VIDEO_SIZE_BYTES} bytes", None
        
        # Check file extension
        if not is_video_file(key):
            return False, f"Unsupported file extension. Supported: {', '.join(sorted(SUPPORTED_VIDEO_EXTENSIONS))}", None
        
        # Check content type if available
        content_type = response.get('ContentType', '').lower()
        if content_type and not (content_type.startswith('video/') or content_type == 'application/octet-stream'):
            logger.warning(f"Unexpected content type: {content_type}. Proceeding anyway based on file extension.")
        
        file_info = {
            'size': file_size,
            'content_type': content_type,
            'last_modified': response.get('LastModified'),
            'metadata': response.get('Metadata', {})
        }
        
        return True, "Valid video file", file_info
        
    except Exception as e:
        return False, f"Error validating file: {str(e)}", None

def lambda_handler(event, context):
    """
    Lambda function triggered by S3 uploads to start ECS video processing task
    Only processes valid video files.
    """

    # Log the entire event for debugging purposes to confirm invocation
    logger.info(f"Lambda triggered. Event: {json.dumps(event)}")

    processed_files = []
    skipped_files = []

    try:
        # Parse S3 event
        for record in event['Records']:
            bucket = record['s3']['bucket']['name']
            key = unquote_plus(record['s3']['object']['key'])
            
            logger.info(f"Processing file: s3://{bucket}/{key}")
            
            # Validate that this is a video file
            is_valid, reason, file_info = validate_video_file(bucket, key)
            
            if not is_valid:
                logger.warning(f"Skipping file {key}: {reason}")
                skipped_files.append({
                    'key': key,
                    'reason': reason
                })
                continue
            
            logger.info(f"Valid video file detected: {key} ({file_info['size']} bytes, {file_info['content_type']})")
            
            # Extract custom prompt from metadata
            metadata = file_info['metadata']
            custom_prompt = metadata.get('prompt')
            
            if custom_prompt:
                logger.info(f"Using custom prompt from metadata: {custom_prompt}")
            else:
                # Use default prompt if no metadata provided
                custom_prompt = os.environ.get('EVENT_PROMPT', '<image> Is there a person in the air jumping into the water?')
                logger.info(f"Using default prompt: {custom_prompt}")
            
            # Get environment variables
            cluster = os.environ['CLUSTER_NAME']
            task_definition = os.environ['TASK_DEFINITION']
            subnet_ids = os.environ['SUBNET_IDS'].split(',')
            security_group = os.environ['SECURITY_GROUP']
            assign_public_ip = os.environ['ASSIGN_PUBLIC_IP']
            capacity_provider_name = os.environ['CAPACITY_PROVIDER_NAME']

            # Start ECS task
            response = ecs.run_task(
                cluster=cluster,
                capacityProviderStrategy=[
                    {
                        'capacityProvider': capacity_provider_name,
                        'weight': 1,
                    },
                ],
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
                                },
                                {
                                    'name': 'EVENT_PROMPT',
                                    'value': custom_prompt
                                }
                            ]
                        }
                    ]
                }
            )
            
            # Check for failures from the API call and log them clearly
            if response.get('failures'):
                failure = response['failures'][0]
                error_message = f"ECS task failed to start for {key}. Reason: {failure.get('reason')}. Detail: {failure.get('detail')}"
                logger.error(error_message)
                # Continue processing other files instead of failing completely
                skipped_files.append({
                    'key': key,
                    'reason': f"ECS task failed: {failure.get('reason')}"
                })
                continue

            if not response.get('tasks'):
                # This case should be rare if failures are handled, but it's good practice
                error_message = f"ECS run_task did not return any tasks or failures for {key}"
                logger.error(error_message)
                skipped_files.append({
                    'key': key,
                    'reason': "ECS task creation returned no tasks"
                })
                continue

            task_arn = response['tasks'][0]['taskArn']
            logger.info(f"Started ECS task for {key}: {task_arn}")
            
            processed_files.append({
                'key': key,
                'task_arn': task_arn,
                'file_size': file_info['size'],
                'prompt': custom_prompt
            })
        
        # Prepare response
        response_body = {
            'message': f'Processed {len(processed_files)} video files, skipped {len(skipped_files)} files',
            'processed_files': processed_files,
            'skipped_files': skipped_files
        }
        
        # Return success if at least one file was processed, or if no valid video files were found
        status_code = 200 if len(processed_files) > 0 or len(skipped_files) > 0 else 400
        
        logger.info(f"Lambda execution completed: {response_body['message']}")
        
        return {
            'statusCode': status_code,
            'body': json.dumps(response_body)
        }
        
    except Exception as e:
        logger.error(f"Unexpected error in Lambda handler: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': f'Unexpected error: {str(e)}',
                'processed_files': processed_files,
                'skipped_files': skipped_files
            })
        }
