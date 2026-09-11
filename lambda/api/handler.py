import json
import boto3
from botocore.config import Config
import os
from decimal import Decimal

# Custom JSON encoder for Decimal types
class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super(DecimalEncoder, self).default(obj)

# Initialize AWS clients
# MUST use regional endpoint + s3v4 for presigned URLs — global endpoint
# causes 307 redirects that browsers block due to missing CORS headers.
AWS_REGION = os.environ.get('AWS_REGION', 'ap-south-1')
s3_client = boto3.client(
    's3',
    region_name=AWS_REGION,
    endpoint_url=f'https://s3.{AWS_REGION}.amazonaws.com',
    config=Config(signature_version='s3v4')
)
dynamodb = boto3.resource('dynamodb')
lambda_client = boto3.client('lambda')

# Environment variables
TRANSCRIPTS_BUCKET = os.environ['TRANSCRIPTS_BUCKET']
REPORTS_BUCKET = os.environ['REPORTS_BUCKET']
RECORDINGS_BUCKET = os.environ.get('RECORDINGS_BUCKET', 'audio-pd-recordings-dev')
DYNAMODB_TABLE = os.environ['DYNAMODB_TABLE']
TRANSCRIPTION_LAMBDA_ARN = os.environ.get('TRANSCRIPTION_LAMBDA_ARN', '')
EVALUATION_LAMBDA_NAME = os.environ.get('EVALUATION_LAMBDA_NAME', '')


def cors_headers():
    """Return CORS headers"""
    return {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
        'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS'
    }

def response(status_code, body):
    """Return API Gateway response"""
    return {
        'statusCode': status_code,
        'headers': cors_headers(),
        'body': json.dumps(body, cls=DecimalEncoder)
    }

def generate_presigned_upload(event, context):
    """
    POST /upload/presigned
    Generate pre-signed URL for file upload and create evaluation record
    """
    try:
        body = json.loads(event.get('body', '{}'))
        
        filename = body.get('filename')
        application_id = body.get('application_id')
        customer_name = body.get('customer_name', 'N/A')
        call_type = body.get('call_type', 'RCM_AUDIO_PD')
        language_code = body.get('language_code', 'hi-IN')
        
        if not filename or not application_id:
            return response(400, {'error': 'Missing required fields: filename, application_id'})
        
        # Check if this exact file was already uploaded and processed successfully
        # Look for existing evaluations with same application_id and filename
        table = dynamodb.Table(DYNAMODB_TABLE)
        existing_result = table.query(
            IndexName='application_id-index',  # Assumes you have a GSI on application_id
            KeyConditionExpression='application_id = :app_id',
            FilterExpression='filename = :fname AND #status = :completed',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':app_id': application_id,
                ':fname': filename,
                ':completed': 'COMPLETED'
            },
            Limit=1,
            ScanIndexForward=False  # Get most recent first
        )
        
        if existing_result.get('Items'):
            # Found a completed evaluation for this exact file
            existing_eval = existing_result['Items'][0]
            print(f"Cache hit! Found existing evaluation: {existing_eval['evaluation_id']}")
            
            return response(200, {
                'evaluation_id': existing_eval['evaluation_id'],
                'cached': True,
                'message': 'This file was already processed. Using cached results.',
                'total_score': existing_eval.get('total_score'),
                'grade_band': existing_eval.get('grade_band'),
                'verdict': existing_eval.get('verdict'),
                'completed_at': existing_eval.get('completed_at'),
                'excel_s3_key': existing_eval.get('excel_s3_key')
            })
        
        # No cache hit, proceed with normal upload flow
        # Generate evaluation ID
        import uuid
        from datetime import datetime
        evaluation_id = f"eval-{uuid.uuid4().hex[:12]}"
        created_at = datetime.utcnow().isoformat()
        
        # Determine S3 key for recording
        file_extension = filename.split('.')[-1] if '.' in filename else 'mp3'
        recording_s3_key = f"recordings/{application_id}/{evaluation_id}.{file_extension}"
        
        # Generate pre-signed URL for upload (valid for 1 hour)
        # Don't specify ContentType to allow any audio format
        upload_url = s3_client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': RECORDINGS_BUCKET,  # Use RECORDINGS_BUCKET not REPORTS_BUCKET
                'Key': recording_s3_key
            },
            ExpiresIn=3600
        )
        
        # Create initial DynamoDB record
        table.put_item(
            Item={
                'evaluation_id': evaluation_id,
                'created_at': created_at,
                'application_id': application_id,
                'customer_name': customer_name,
                'call_type': call_type,
                'language_code': language_code,
                'status': 'PENDING_UPLOAD',
                'recording_s3_key': recording_s3_key,
                'filename': filename
            }
        )
        
        return response(200, {
            'evaluation_id': evaluation_id,
            'upload_url': upload_url,
            'recording_s3_key': recording_s3_key,
            'cached': False,
            'message': 'Upload the file to upload_url, then call POST /upload/complete with evaluation_id'
        })
        
    except Exception as e:
        # If GSI doesn't exist, fall back to normal flow without caching
        if 'IndexName' in str(e) or 'ValidationException' in str(e):
            print(f"Warning: GSI not available for caching, proceeding without cache check: {str(e)}")
            
            # Generate evaluation ID
            import uuid
            from datetime import datetime
            evaluation_id = f"eval-{uuid.uuid4().hex[:12]}"
            created_at = datetime.utcnow().isoformat()
            
            filename = body.get('filename')
            application_id = body.get('application_id')
            
            file_extension = filename.split('.')[-1] if '.' in filename else 'mp3'
            recording_s3_key = f"recordings/{application_id}/{evaluation_id}.{file_extension}"
            
            upload_url = s3_client.generate_presigned_url(
                'put_object',
                Params={
                    'Bucket': RECORDINGS_BUCKET,
                    'Key': recording_s3_key
                },
                ExpiresIn=3600
            )
            
            table = dynamodb.Table(DYNAMODB_TABLE)
            table.put_item(
                Item={
                    'evaluation_id': evaluation_id,
                    'created_at': created_at,
                    'application_id': application_id,
                    'customer_name': body.get('customer_name', 'N/A'),
                    'call_type': body.get('call_type', 'RCM_AUDIO_PD'),
                    'language_code': body.get('language_code', 'hi-IN'),
                    'status': 'PENDING_UPLOAD',
                    'recording_s3_key': recording_s3_key,
                    'filename': filename
                }
            )
            
            return response(200, {
                'evaluation_id': evaluation_id,
                'upload_url': upload_url,
                'recording_s3_key': recording_s3_key,
                'cached': False,
                'message': 'Upload the file to upload_url, then call POST /upload/complete'
            })
        
        print(f"Error generating presigned upload: {str(e)}")
        import traceback
        traceback.print_exc()
        return response(500, {'error': str(e)})

def trigger_transcription(event, context):
    """
    POST /upload/complete
    Trigger transcription after file upload
    """
    try:
        body = json.loads(event.get('body', '{}'))
        evaluation_id = body.get('evaluation_id')
        force_retranscribe = body.get('force_retranscribe', False)
        
        if not evaluation_id:
            return response(400, {'error': 'Missing evaluation_id'})
        
        # Get evaluation record from DynamoDB
        table = dynamodb.Table(DYNAMODB_TABLE)
        result = table.query(
            KeyConditionExpression='evaluation_id = :eid',
            ExpressionAttributeValues={':eid': evaluation_id},
            ScanIndexForward=False,
            Limit=1
        )
        
        if not result.get('Items'):
            return response(404, {'error': 'Evaluation not found'})
        
        item = result['Items'][0]
        
        # Check if transcript already exists (for retry scenarios)
        transcript_s3_key = item.get('transcript_s3_key')
        transcript_exists = False
        
        if transcript_s3_key and not force_retranscribe:
            try:
                s3_client.head_object(Bucket=TRANSCRIPTS_BUCKET, Key=transcript_s3_key)
                transcript_exists = True
                print(f"Transcript already exists at {transcript_s3_key}, skipping transcription")
            except:
                pass
        
        if transcript_exists:
            # Skip transcription, trigger evaluation directly
            from boto3 import client as boto3_client
            lambda_client_temp = boto3_client('lambda')
            
            evaluation_payload = {
                'evaluation_id': evaluation_id,
                'transcript_s3_key': transcript_s3_key,
                'recording_s3_key': item['recording_s3_key'],
                'created_at': item['created_at'],
                'application_id': item.get('application_id'),
                'call_type': item.get('call_type', 'RCM_AUDIO_PD'),
                'language_code': item.get('language_code', 'hi-IN')
            }
            
            print(f"Skipping transcription, invoking evaluation Lambda directly")
            
            lambda_client_temp.invoke(
                FunctionName=EVALUATION_LAMBDA_NAME,
                InvocationType='Event',
                Payload=json.dumps(evaluation_payload)
            )
            
            table.update_item(
                Key={
                    'evaluation_id': evaluation_id,
                    'created_at': item['created_at']
                },
                UpdateExpression='SET #status = :status',
                ExpressionAttributeNames={'#status': 'status'},
                ExpressionAttributeValues={':status': 'EVALUATING'}
            )
            
            return response(200, {
                'evaluation_id': evaluation_id,
                'status': 'EVALUATING',
                'message': 'Using cached transcript, started evaluation',
                'transcript_cached': True
            })
        else:
            # Invoke transcription Lambda
            transcription_payload = {
                'evaluation_id': evaluation_id,
                'recording_s3_key': item['recording_s3_key'],
                'created_at': item['created_at'],
                'application_id': item.get('application_id'),
                'call_type': item.get('call_type', 'RCM_AUDIO_PD'),
                'language_code': item.get('language_code', 'hi-IN')
            }
            
            print(f"Invoking transcription Lambda with payload: {json.dumps(transcription_payload)}")
            
            lambda_client.invoke(
                FunctionName=TRANSCRIPTION_LAMBDA_ARN,
                InvocationType='Event',  # Async invocation
                Payload=json.dumps(transcription_payload)
            )
            
            # Update status to TRANSCRIBING
            table.update_item(
                Key={
                    'evaluation_id': evaluation_id,
                    'created_at': item['created_at']
                },
                UpdateExpression='SET #status = :status',
                ExpressionAttributeNames={'#status': 'status'},
                ExpressionAttributeValues={':status': 'TRANSCRIBING'}
            )
            
            return response(200, {
                'evaluation_id': evaluation_id,
                'status': 'TRANSCRIBING',
                'message': 'Transcription started',
                'transcript_cached': False
            })
        
    except Exception as e:
        print(f"Error triggering transcription: {str(e)}")
        import traceback
        traceback.print_exc()
        return response(500, {'error': str(e)})


def get_evaluation_status(event, context):
    """
    GET /evaluations/{evaluation_id}/status
    Returns current processing status
    """
    try:
        evaluation_id = event['pathParameters']['evaluation_id']
        
        # Query DynamoDB
        table = dynamodb.Table(DYNAMODB_TABLE)
        result = table.query(
            KeyConditionExpression='evaluation_id = :eid',
            ExpressionAttributeValues={':eid': evaluation_id},
            ScanIndexForward=False,
            Limit=1
        )
        
        if not result.get('Items'):
            return response(404, {'error': 'Evaluation not found'})
        
        item = result['Items'][0]
        
        return response(200, {
            'evaluation_id': item['evaluation_id'],
            'status': item.get('status', 'UNKNOWN'),
            'created_at': item.get('created_at'),
            'total_score': item.get('total_score'),
            'grade_band': item.get('grade_band'),
            'verdict': item.get('verdict'),
            'error_message': item.get('error_message'),
            'transcript_s3_key': item.get('transcript_s3_key'),
            'excel_s3_key': item.get('excel_s3_key'),
            'evaluated_at': item.get('evaluated_at')
        })
        
    except Exception as e:
        print(f"Error getting status: {str(e)}")
        return response(500, {'error': str(e)})

def get_transcript(event, context):
    """
    GET /evaluations/{evaluation_id}/transcript
    Returns transcript content
    """
    try:
        evaluation_id = event['pathParameters']['evaluation_id']
        
        # Get evaluation record
        table = dynamodb.Table(DYNAMODB_TABLE)
        result = table.query(
            KeyConditionExpression='evaluation_id = :eid',
            ExpressionAttributeValues={':eid': evaluation_id},
            ScanIndexForward=False,
            Limit=1
        )
        
        if not result.get('Items'):
            return response(404, {'error': 'Evaluation not found'})
        
        item = result['Items'][0]
        transcript_key = item.get('transcript_s3_key')
        
        if not transcript_key:
            return response(404, {'error': 'Transcript not available yet'})
        
        # Fetch transcript from S3
        try:
            transcript_response = s3_client.get_object(
                Bucket=TRANSCRIPTS_BUCKET,
                Key=transcript_key
            )
            transcript_data = json.loads(transcript_response['Body'].read())
            
            return response(200, transcript_data)
            
        except s3_client.exceptions.NoSuchKey:
            return response(404, {'error': 'Transcript file not found'})
        
    except Exception as e:
        print(f"Error getting transcript: {str(e)}")
        return response(500, {'error': str(e)})

def get_excel_download_url(event, context):
    """
    GET /evaluations/{evaluation_id}/excel
    Returns presigned URL for Excel download
    """
    try:
        evaluation_id = event['pathParameters']['evaluation_id']
        
        # Get evaluation record
        table = dynamodb.Table(DYNAMODB_TABLE)
        result = table.query(
            KeyConditionExpression='evaluation_id = :eid',
            ExpressionAttributeValues={':eid': evaluation_id},
            ScanIndexForward=False,
            Limit=1
        )
        
        if not result.get('Items'):
            return response(404, {'error': 'Evaluation not found'})
        
        item = result['Items'][0]
        excel_key = item.get('excel_s3_key')
        
        if not excel_key:
            return response(404, {'error': 'Excel scorecard not available yet'})
        
        # Generate presigned URL (valid for 1 hour)
        download_url = s3_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': REPORTS_BUCKET,
                'Key': excel_key
            },
            ExpiresIn=3600
        )
        
        # Extract filename
        application_id = item.get('application_id', evaluation_id)
        filename = f"Audio_PD_Scorecard_{application_id}.xlsx"
        
        return response(200, {
            'download_url': download_url,
            'filename': filename,
            'excel_s3_key': excel_key
        })
        
    except Exception as e:
        print(f"Error getting Excel URL: {str(e)}")
        return response(500, {'error': str(e)})

def get_evaluation_detail(event, context):
    """
    GET /evaluations/{evaluation_id}
    Returns full evaluation details
    """
    try:
        evaluation_id = event['pathParameters']['evaluation_id']
        
        # Query DynamoDB
        table = dynamodb.Table(DYNAMODB_TABLE)
        result = table.query(
            KeyConditionExpression='evaluation_id = :eid',
            ExpressionAttributeValues={':eid': evaluation_id},
            ScanIndexForward=False,
            Limit=1
        )
        
        if not result.get('Items'):
            return response(404, {'error': 'Evaluation not found'})
        
        item = result['Items'][0]
        
        # Load evaluation result from S3 if available
        result_data = None
        if item.get('result_s3_key'):
            try:
                result_response = s3_client.get_object(
                    Bucket=REPORTS_BUCKET,
                    Key=item['result_s3_key']
                )
                result_data = json.loads(result_response['Body'].read())
            except:
                pass
        
        return response(200, {
            'evaluation_id': item['evaluation_id'],
            'application_id': item.get('application_id'),
            'call_type': item.get('call_type'),
            'status': item.get('status'),
            'created_at': item.get('created_at'),
            'total_score': item.get('total_score'),
            'grade_band': item.get('grade_band'),
            'verdict': item.get('verdict'),
            'transcript_s3_key': item.get('transcript_s3_key'),
            'excel_s3_key': item.get('excel_s3_key'),
            'result_s3_key': item.get('result_s3_key'),
            'evaluated_at': item.get('evaluated_at'),
            'error_message': item.get('error_message'),
            'result_data': result_data
        })
        
    except Exception as e:
        print(f"Error getting evaluation detail: {str(e)}")
        return response(500, {'error': str(e)})

def list_evaluations(event, context):
    """
    GET /evaluations
    Returns list of all evaluations with optional filters
    """
    try:
        # Get query parameters
        query_params = event.get('queryStringParameters') or {}
        status_filter = query_params.get('status')
        call_type_filter = query_params.get('call_type')
        
        # Scan DynamoDB (could be optimized with GSI)
        table = dynamodb.Table(DYNAMODB_TABLE)
        
        scan_kwargs = {}
        filter_expressions = []
        expression_values = {}
        
        if status_filter:
            filter_expressions.append('#status = :status')
            expression_values[':status'] = status_filter
            scan_kwargs['ExpressionAttributeNames'] = {'#status': 'status'}
        
        if call_type_filter:
            filter_expressions.append('call_type = :call_type')
            expression_values[':call_type'] = call_type_filter
        
        if filter_expressions:
            scan_kwargs['FilterExpression'] = ' AND '.join(filter_expressions)
            scan_kwargs['ExpressionAttributeValues'] = expression_values
        
        result = table.scan(**scan_kwargs)
        items = result.get('Items', [])
        
        # Handle DynamoDB pagination — scan may not return all items in one call
        while 'LastEvaluatedKey' in result:
            scan_kwargs['ExclusiveStartKey'] = result['LastEvaluatedKey']
            result = table.scan(**scan_kwargs)
            items.extend(result.get('Items', []))
        
        # Sort by created_at descending
        items.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        
        # Limit to 100 most recent
        items = items[:100]
        
        return response(200, {
            'evaluations': items,
            'count': len(items)
        })
        
    except Exception as e:
        print(f"Error listing evaluations: {str(e)}")
        return response(500, {'error': str(e)})

def lambda_handler(event, context):
    """
    Main router for API endpoints
    """
    print(f"Event: {json.dumps(event)}")
    
    # Handle OPTIONS for CORS
    if event.get('httpMethod') == 'OPTIONS':
        return {
            'statusCode': 200,
            'headers': cors_headers(),
            'body': ''
        }
    
    # Route based on path and method
    path = event.get('path', '')
    method = event.get('httpMethod', '')
    
    try:
        if path == '/upload/presigned' and method == 'POST':
            return generate_presigned_upload(event, context)
        elif path == '/upload/complete' and method == 'POST':
            return trigger_transcription(event, context)
        elif path.endswith('/status') and method == 'GET':
            return get_evaluation_status(event, context)
        elif path.endswith('/transcript') and method == 'GET':
            return get_transcript(event, context)
        elif path.endswith('/excel') and method == 'GET':
            return get_excel_download_url(event, context)
        elif '/evaluations/' in path and not path.endswith(('/status', '/transcript', '/excel')) and method == 'GET':
            return get_evaluation_detail(event, context)
        elif path == '/evaluations' and method == 'GET':
            return list_evaluations(event, context)
        else:
            return response(404, {'error': 'Endpoint not found'})
            
    except Exception as e:
        print(f"Error in lambda_handler: {str(e)}")
        import traceback
        traceback.print_exc()
        return response(500, {'error': str(e)})
