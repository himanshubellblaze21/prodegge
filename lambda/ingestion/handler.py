import json
import boto3
from botocore.config import Config
import os
import uuid
from datetime import datetime

# Initialize AWS clients
# MUST use regional endpoint for presigned URLs — global endpoint causes 307
# redirects that browsers block due to missing CORS headers on the redirect.
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
RECORDINGS_BUCKET = os.environ['RECORDINGS_BUCKET']
DYNAMODB_TABLE = os.environ['DYNAMODB_TABLE']

def generate_presigned_url(event, context):
    """
    Generate pre-signed S3 URL for direct upload from frontend
    
    API Gateway event:
    {
        "body": {
            "filename": "recording.mp3",
            "application_id": "APP123",
            "customer_name": "John Doe",
            "call_type": "RCM_TELE_PD"
        }
    }
    """
    try:
        # Parse request body
        if isinstance(event.get('body'), str):
            body = json.loads(event['body'])
        else:
            body = event.get('body', {})
        
        filename = body.get('filename')
        application_id = body.get('application_id')
        customer_name = body.get('customer_name')
        call_type = body.get('call_type', 'AUTO_DETECT')
        
        if not filename or not application_id:
            return {
                'statusCode': 400,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({
                    'error': 'filename and application_id are required'
                })
            }
        
        # Generate unique evaluation ID
        evaluation_id = f"eval-{uuid.uuid4().hex[:12]}"
        created_at = datetime.utcnow().isoformat()
        
        # Construct S3 key
        file_extension = filename.split('.')[-1].lower()
        s3_key = f"recordings/{application_id}/{evaluation_id}.{file_extension}"
        
        # Generate pre-signed URL (valid for 15 minutes)
        # Note: Not specifying ContentType to allow any audio format
        presigned_url = s3_client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': RECORDINGS_BUCKET,
                'Key': s3_key
            },
            ExpiresIn=900  # 15 minutes
        )
        
        # Create initial DynamoDB record
        table = dynamodb.Table(DYNAMODB_TABLE)
        table.put_item(
            Item={
                'evaluation_id': evaluation_id,
                'created_at': created_at,
                'application_id': application_id,
                'customer_name': customer_name,
                'call_type': call_type,
                'recording_s3_key': s3_key,
                'status': 'UPLOAD_PENDING',
                'uploaded_by': event.get('requestContext', {}).get('authorizer', {}).get('claims', {}).get('email', 'unknown')
            }
        )
        
        print(f"Generated pre-signed URL for {evaluation_id}: {s3_key}")
        
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'evaluation_id': evaluation_id,
                'upload_url': presigned_url,
                's3_key': s3_key,
                'expires_in': 900
            })
        }
        
    except Exception as e:
        print(f"Error generating pre-signed URL: {str(e)}")
        import traceback
        traceback.print_exc()
        
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'error': str(e)
            })
        }


def s3_event_handler(event, context):
    """
    Handle S3 upload event - trigger preprocessing Lambda
    This function is invoked by S3 event notification when file is uploaded
    """
    try:
        # Process each S3 record
        for record in event.get('Records', []):
            bucket_name = record['s3']['bucket']['name']
            object_key = record['s3']['object']['key']
            
            print(f"Processing S3 event for: s3://{bucket_name}/{object_key}")
            
            # Extract evaluation_id from key: recordings/APP123/eval-xxx.mp3
            parts = object_key.split('/')
            if len(parts) < 3:
                print(f"Invalid S3 key format: {object_key}")
                continue
            
            filename = parts[-1]
            evaluation_id = filename.split('.')[0]
            
            # Update DynamoDB status
            table = dynamodb.Table(DYNAMODB_TABLE)
            
            # Find the record by evaluation_id
            response = table.scan(
                FilterExpression='evaluation_id = :eval_id',
                ExpressionAttributeValues={
                    ':eval_id': evaluation_id
                },
                Limit=1
            )
            
            if response.get('Items'):
                item = response['Items'][0]
                
                # Update status to UPLOADED
                table.update_item(
                    Key={
                        'evaluation_id': item['evaluation_id'],
                        'created_at': item['created_at']
                    },
                    UpdateExpression='SET #status = :status, uploaded_at = :timestamp',
                    ExpressionAttributeNames={
                        '#status': 'status'
                    },
                    ExpressionAttributeValues={
                        ':status': 'UPLOADED',
                        ':timestamp': datetime.utcnow().isoformat()
                    }
                )
                
                print(f"Updated {evaluation_id} status to UPLOADED")
                print(f"Preprocessing will be triggered automatically by S3 event")
            
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Processing started',
                'records_processed': len(event.get('Records', []))
            })
        }
        
    except Exception as e:
        print(f"Error processing S3 event: {str(e)}")
        import traceback
        traceback.print_exc()
        
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e)
            })
        }


def get_evaluation_status(event, context):
    """
    Get evaluation status and results
    
    API Gateway event:
    {
        "pathParameters": {
            "evaluation_id": "eval-xxx"
        }
    }
    """
    try:
        evaluation_id = event.get('pathParameters', {}).get('evaluation_id')
        
        if not evaluation_id:
            return {
                'statusCode': 400,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({
                    'error': 'evaluation_id is required'
                })
            }
        
        # Query DynamoDB
        table = dynamodb.Table(DYNAMODB_TABLE)
        response = table.scan(
            FilterExpression='evaluation_id = :eval_id',
            ExpressionAttributeValues={
                ':eval_id': evaluation_id
            },
            Limit=1
        )
        
        if not response.get('Items'):
            return {
                'statusCode': 404,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({
                    'error': 'Evaluation not found'
                })
            }
        
        item = response['Items'][0]
        
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'evaluation_id': item['evaluation_id'],
                'application_id': item.get('application_id'),
                'customer_name': item.get('customer_name'),
                'call_type': item.get('call_type'),
                'status': item.get('status'),
                'total_score': item.get('total_score'),
                'grade_band': item.get('grade_band'),
                'verdict': item.get('verdict'),
                'created_at': item.get('created_at'),
                'completed_at': item.get('evaluated_at'),
                'excel_s3_key': item.get('excel_s3_key')
            })
        }
        
    except Exception as e:
        print(f"Error getting evaluation status: {str(e)}")
        import traceback
        traceback.print_exc()
        
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'error': str(e)
            })
        }


def list_evaluations(event, context):
    """
    List all evaluations with optional filtering
    
    Query parameters:
    - status: filter by status
    - call_type: filter by call type
    - limit: number of results (default 50)
    """
    try:
        query_params = event.get('queryStringParameters') or {}
        status_filter = query_params.get('status')
        call_type_filter = query_params.get('call_type')
        limit = int(query_params.get('limit', 50))
        
        # Query DynamoDB
        table = dynamodb.Table(DYNAMODB_TABLE)
        
        # Build filter expression
        filter_expressions = []
        expression_values = {}
        
        if status_filter:
            filter_expressions.append('#status = :status')
            expression_values[':status'] = status_filter
        
        if call_type_filter:
            filter_expressions.append('call_type = :call_type')
            expression_values[':call_type'] = call_type_filter
        
        # Scan with filter
        scan_params = {'Limit': limit}
        
        if filter_expressions:
            scan_params['FilterExpression'] = ' AND '.join(filter_expressions)
            scan_params['ExpressionAttributeValues'] = expression_values
            if '#status' in ' AND '.join(filter_expressions):
                scan_params['ExpressionAttributeNames'] = {'#status': 'status'}
        
        response = table.scan(**scan_params)
        items = response.get('Items', [])
        
        # Sort by created_at descending
        items.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'evaluations': items,
                'count': len(items)
            })
        }
        
    except Exception as e:
        print(f"Error listing evaluations: {str(e)}")
        import traceback
        traceback.print_exc()
        
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'error': str(e)
            })
        }

