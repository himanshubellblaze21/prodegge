import json
import boto3
import os
from datetime import datetime
from decimal import Decimal

# Initialize AWS clients
s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')
lambda_client = boto3.client('lambda')

# Environment variables
RECORDINGS_BUCKET = os.environ['RECORDINGS_BUCKET']
DYNAMODB_TABLE = os.environ['DYNAMODB_TABLE']
TRANSCRIPTION_LAMBDA = os.environ.get('TRANSCRIPTION_LAMBDA_NAME', 'audio-pd-transcription-dev')

# Supported audio formats and constraints
SUPPORTED_FORMATS = ['mp3', 'wav', 'm4a', 'ogg', 'flac']
MIN_DURATION_SECONDS = 60      # 1 minute
MAX_DURATION_SECONDS = 3600    # 60 minutes
MAX_FILE_SIZE_MB = 500

def lambda_handler(event, context):
    """
    Preprocessing Lambda for audio validation
    Triggered by S3 event when audio file is uploaded
    """
    try:
        print(f"Received event: {json.dumps(event)}")
        
        # Handle S3 event format
        if 'Records' in event:
            # S3 Event format
            for record in event['Records']:
                bucket_name = record['s3']['bucket']['name']
                recording_key = record['s3']['object']['key']
                
                print(f"Processing S3 upload: s3://{bucket_name}/{recording_key}")
                
                # Extract evaluation_id from key: recordings/APP123/eval-xxx.mp3
                parts = recording_key.split('/')
                if len(parts) < 3:
                    print(f"Invalid S3 key format: {recording_key}")
                    continue
                
                filename = parts[-1]
                evaluation_id = filename.split('.')[0]
                application_id = parts[1]
                
                print(f"Extracted: evaluation_id={evaluation_id}, application_id={application_id}")
                
                # Query DynamoDB - use evaluation_id as partition key
                # We need to scan since we don't know created_at yet
                table = dynamodb.Table(DYNAMODB_TABLE)
                
                # Simple approach: scan for this evaluation_id
                # This is acceptable because we just created it seconds ago
                response = table.scan(
                    FilterExpression='evaluation_id = :eval_id',
                    ExpressionAttributeValues={
                        ':eval_id': evaluation_id
                    },
                    Limit=10
                )
                
                print(f"DynamoDB scan returned {len(response.get('Items', []))} items")
                
                if not response.get('Items'):
                    # Race condition: S3 event fired before ingestion wrote the DynamoDB record.
                    # Retry up to 5 times with 2-second intervals before giving up.
                    print(f"DynamoDB record not found for {evaluation_id} — retrying...")
                    import time
                    found = False
                    for attempt in range(1, 6):
                        time.sleep(2)
                        retry = table.scan(
                            FilterExpression='evaluation_id = :eval_id',
                            ExpressionAttributeValues={':eval_id': evaluation_id},
                            Limit=10
                        )
                        if retry.get('Items'):
                            item = retry['Items'][0]
                            created_at = item['created_at']
                            print(f"Found DynamoDB record on retry {attempt}: created_at={created_at}")
                            found = True
                            break
                        print(f"Retry {attempt}: still not found")
                    
                    if not found:
                        print(f"FATAL: DynamoDB record for {evaluation_id} not found after 5 retries. Skipping.")
                        continue  # Skip this record — do not invent a created_at
                else:
                    item = response['Items'][0]
                    created_at = item['created_at']
                    print(f"Found DynamoDB record with created_at: {created_at}")
                
                # Process this file
                result = process_audio_file(
                    evaluation_id=evaluation_id,
                    created_at=created_at,
                    recording_key=recording_key,
                    bucket_name=bucket_name,
                    application_id=application_id
                )
                
                print(f"Preprocessing result: {result}")
        
        return {
            'statusCode': 200,
            'body': json.dumps({'message': 'Processing complete'})
        }
        
    except Exception as e:
        print(f"Error in preprocessing: {str(e)}")
        import traceback
        traceback.print_exc()
        
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }


def process_audio_file(evaluation_id, created_at, recording_key, bucket_name, application_id):
    """Process and validate a single audio file"""
    try:
        table = dynamodb.Table(DYNAMODB_TABLE)
        
        # Update DynamoDB status
        table.update_item(
            Key={
                'evaluation_id': evaluation_id,
                'created_at': created_at
            },
            UpdateExpression='SET #status = :status, preprocessing_started_at = :timestamp',
            ExpressionAttributeNames={
                '#status': 'status'
            },
            ExpressionAttributeValues={
                ':status': 'PREPROCESSING',
                ':timestamp': datetime.utcnow().isoformat()
            }
        )
        
        # Get audio file metadata from S3
        s3_response = s3_client.head_object(
            Bucket=bucket_name,
            Key=recording_key
        )
        
        file_size_bytes = s3_response['ContentLength']
        file_size_mb = file_size_bytes / (1024 * 1024)
        content_type = s3_response.get('ContentType', '')
        
        print(f"File size: {file_size_mb:.2f} MB, Content-Type: {content_type}")
        
        # Validate file size
        if file_size_mb > MAX_FILE_SIZE_MB:
            raise ValueError(f"File size {file_size_mb:.2f} MB exceeds maximum {MAX_FILE_SIZE_MB} MB")
        
        # Validate file format
        file_extension = recording_key.split('.')[-1].lower()
        if file_extension not in SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported audio format: {file_extension}. Supported: {', '.join(SUPPORTED_FORMATS)}")
        
        # Estimate duration (rough estimate: ~1MB per minute for MP3)
        estimated_duration_minutes = file_size_mb
        print(f"Estimated duration: ~{estimated_duration_minutes:.1f} minutes")
        
        # Basic validation passed
        validation_result = {
            'file_size_mb': Decimal(str(round(file_size_mb, 2))),
            'format': file_extension,
            'estimated_duration_minutes': Decimal(str(round(estimated_duration_minutes, 1))),
            'content_type': content_type
        }
        
        # Update DynamoDB with preprocessing complete
        table.update_item(
            Key={
                'evaluation_id': evaluation_id,
                'created_at': created_at
            },
            UpdateExpression='SET #status = :status, preprocessing_completed_at = :timestamp, validation_result = :validation',
            ExpressionAttributeNames={
                '#status': 'status'
            },
            ExpressionAttributeValues={
                ':status': 'VALIDATED',
                ':timestamp': datetime.utcnow().isoformat(),
                ':validation': validation_result
            }
        )
        
        print(f"Preprocessing completed successfully for {evaluation_id}")
        
        # Invoke transcription Lambda
        lambda_client = boto3.client('lambda')
        transcription_payload = {
            'evaluation_id': evaluation_id,
            'recording_s3_key': recording_key,
            'created_at': created_at,
            'bucket_name': bucket_name
        }
        
        print(f"Invoking transcription Lambda with payload: {transcription_payload}")
        
        response = lambda_client.invoke(
            FunctionName=os.environ.get('TRANSCRIPTION_LAMBDA_NAME', 'audio-pd-transcription-dev'),
            InvocationType='Event',  # Async invocation
            Payload=json.dumps(transcription_payload)
        )
        
        print(f"Transcription Lambda invoked: StatusCode={response['StatusCode']}")
        
        return {
            'status': 'SUCCESS',
            'evaluation_id': evaluation_id,
            'validation_result': validation_result
        }
        
    except Exception as e:
        print(f"Error processing {evaluation_id}: {str(e)}")
        import traceback
        traceback.print_exc()
        
        # Update DynamoDB with error
        try:
            table.update_item(
                Key={
                    'evaluation_id': evaluation_id,
                    'created_at': created_at
                },
                UpdateExpression='SET #status = :status, error_message = :error',
                ExpressionAttributeNames={
                    '#status': 'status'
                },
                ExpressionAttributeValues={
                    ':status': 'PREPROCESSING_FAILED',
                    ':error': str(e)
                }
            )
        except:
            pass
        
        raise
