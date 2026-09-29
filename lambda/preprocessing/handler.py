"""
Preprocessing Lambda — Prodigee Finance V2.1
Triggered by S3 ObjectCreated event when audio is uploaded.

Key behaviour:
- Accepts all common audio formats including raw AAC (.aac)
- For formats Transcribe cannot process (aac, opus, wma, 3gp):
  uses ffmpeg (/opt/bin/ffmpeg from Lambda layer) to convert to MP3
  and re-uploads the MP3 to S3, then triggers transcription on the MP3
- Passes call_type, application_id, language_code to transcription Lambda
"""
import json, boto3, os, subprocess, tempfile
from datetime import datetime
from decimal import Decimal

s3_client    = boto3.client('s3')
dynamodb     = boto3.resource('dynamodb')
lambda_client = boto3.client('lambda')

RECORDINGS_BUCKET = os.environ['RECORDINGS_BUCKET']
DYNAMODB_TABLE    = os.environ['DYNAMODB_TABLE']
FFMPEG_PATH       = '/opt/bin/ffmpeg'

# Formats Transcribe accepts natively
NATIVE_FORMATS = {'mp3', 'wav', 'm4a', 'mp4', 'ogg', 'flac', 'webm', 'amr'}
# Formats we accept from users but need conversion
NEEDS_CONVERSION = {'aac', 'opus', 'wma', '3gp', 'caf'}
# All accepted
ALL_FORMATS = NATIVE_FORMATS | NEEDS_CONVERSION


def convert_to_mp3(bucket: str, source_key: str) -> str:
    """
    Download source_key from S3, convert to MP3 via ffmpeg,
    upload as <base>_converted.mp3, return new S3 key.
    """
    ext = source_key.rsplit('.', 1)[-1].lower()
    with tempfile.TemporaryDirectory() as tmp:
        input_path  = os.path.join(tmp, f'input.{ext}')
        output_path = os.path.join(tmp, 'output.mp3')

        print(f"Downloading {source_key} for conversion...")
        s3_client.download_file(bucket, source_key, input_path)
        print(f"Downloaded {os.path.getsize(input_path)/1024/1024:.1f} MB")

        cmd = [FFMPEG_PATH, '-y', '-i', input_path,
               '-codec:a', 'libmp3lame', '-qscale:a', '4', output_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            raise Exception(f"ffmpeg failed: {result.stderr[-500:]}")
        print(f"Converted to MP3: {os.path.getsize(output_path)/1024/1024:.1f} MB")

        base    = source_key.rsplit('.', 1)[0]
        new_key = f"{base}_converted.mp3"
        s3_client.upload_file(output_path, bucket, new_key)
        print(f"Uploaded MP3: {new_key}")
        return new_key


def lambda_handler(event, context):
    print(f"Preprocessing event: {json.dumps(event)}")
    try:
        if 'Records' not in event:
            return {'statusCode': 200, 'body': 'No S3 records'}

        for record in event['Records']:
            bucket       = record['s3']['bucket']['name']
            recording_key = record['s3']['object']['key']
            print(f"Processing: s3://{bucket}/{recording_key}")

            parts = recording_key.split('/')
            if len(parts) < 3:
                print(f"Invalid key format: {recording_key}")
                continue

            evaluation_id  = parts[-1].split('.')[0]
            application_id = parts[1]

            # Find DynamoDB record (retry for race condition)
            table = dynamodb.Table(DYNAMODB_TABLE)
            item  = None
            import time
            for attempt in range(6):
                resp = table.scan(
                    FilterExpression='evaluation_id = :eid',
                    ExpressionAttributeValues={':eid': evaluation_id},
                    Limit=5
                )
                if resp.get('Items'):
                    item = resp['Items'][0]
                    break
                print(f"DynamoDB retry {attempt+1}...")
                time.sleep(2)

            if not item:
                print(f"DynamoDB record not found for {evaluation_id} — skipping")
                continue

            created_at = item['created_at']
            call_type  = item.get('call_type', 'AUTO_DETECT')
            lang       = item.get('language_code', 'hi-IN')
            app_id     = item.get('application_id', application_id)

            # Mark PREPROCESSING
            table.update_item(
                Key={'evaluation_id': evaluation_id, 'created_at': created_at},
                UpdateExpression='SET #s=:s, preprocessing_started_at=:t',
                ExpressionAttributeNames={'#s': 'status'},
                ExpressionAttributeValues={':s': 'PREPROCESSING', ':t': datetime.utcnow().isoformat()}
            )

            # Validate
            meta     = s3_client.head_object(Bucket=bucket, Key=recording_key)
            size_mb  = meta['ContentLength'] / (1024 * 1024)
            ext      = recording_key.rsplit('.', 1)[-1].lower() if '.' in recording_key else ''

            if size_mb > 500:
                raise ValueError(f"File {size_mb:.0f} MB exceeds 500 MB limit")
            if ext not in ALL_FORMATS:
                raise ValueError(f"Format .{ext} not supported. Accepted: {sorted(ALL_FORMATS)}")

            print(f"File: {size_mb:.1f} MB, format: .{ext}")

            # Convert if needed
            actual_key = recording_key
            if ext in NEEDS_CONVERSION:
                print(f".{ext} needs conversion → MP3")
                actual_key = convert_to_mp3(bucket, recording_key)

            # Mark VALIDATED
            table.update_item(
                Key={'evaluation_id': evaluation_id, 'created_at': created_at},
                UpdateExpression='SET #s=:s, preprocessing_completed_at=:t, recording_s3_key_processed=:k',
                ExpressionAttributeNames={'#s': 'status'},
                ExpressionAttributeValues={
                    ':s': 'VALIDATED', ':t': datetime.utcnow().isoformat(),
                    ':k': actual_key
                }
            )

            # Trigger transcription
            tx_payload = {
                'evaluation_id':    evaluation_id,
                'recording_s3_key': actual_key,
                'created_at':       created_at,
                'application_id':   app_id,
                'call_type':        call_type,
                'language_code':    lang,
            }
            resp = lambda_client.invoke(
                FunctionName=os.environ.get('TRANSCRIPTION_LAMBDA_NAME', 'audio-pd-transcription-dev'),
                InvocationType='Event',
                Payload=json.dumps(tx_payload).encode()
            )
            print(f"Transcription triggered: {resp['StatusCode']}")

        return {'statusCode': 200, 'body': 'OK'}

    except Exception as e:
        print(f"Preprocessing error: {e}")
        import traceback; traceback.print_exc()
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}
