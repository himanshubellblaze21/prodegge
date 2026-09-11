import json
import boto3
import os
import time
from datetime import datetime
from urllib.parse import urlparse

# Initialize AWS clients
transcribe_client = boto3.client('transcribe', region_name=os.environ.get('AWS_REGION', 'ap-south-1'))
s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')

# Environment variables
RECORDINGS_BUCKET = os.environ['RECORDINGS_BUCKET']
TRANSCRIPTS_BUCKET = os.environ['TRANSCRIPTS_BUCKET']
DYNAMODB_TABLE = os.environ['DYNAMODB_TABLE']

def lambda_handler(event, context):
    """
    Lambda handler for audio transcription using Amazon Transcribe
    
    Event structure:
    {
        "evaluation_id": "eval-xxx",
        "recording_s3_key": "recordings/xxx.mp3",
        "created_at": "2026-08-19T10:00:00Z",
        "language_code": "hi-IN"  # Hindi
    }
    """
    try:
        print(f"Processing transcription request: {json.dumps(event)}")
        
        evaluation_id = event['evaluation_id']
        recording_key = event['recording_s3_key']
        created_at = event['created_at']
        language_code = event.get('language_code', 'hi-IN')  # Default to Hindi
        
        # Update DynamoDB status
        table = dynamodb.Table(DYNAMODB_TABLE)
        table.update_item(
            Key={
                'evaluation_id': evaluation_id,
                'created_at': created_at
            },
            UpdateExpression='SET #status = :status, transcription_started_at = :timestamp',
            ExpressionAttributeNames={
                '#status': 'status'
            },
            ExpressionAttributeValues={
                ':status': 'TRANSCRIBING',
                ':timestamp': datetime.utcnow().isoformat()
            }
        )
        
        # Start Amazon Transcribe job
        job_name = f"transcribe-{evaluation_id}-{int(time.time())}"
        media_uri = f"s3://{RECORDINGS_BUCKET}/{recording_key}"
        
        print(f"Starting Transcribe job: {job_name} for {media_uri}")
        
        transcribe_response = transcribe_client.start_transcription_job(
            TranscriptionJobName=job_name,
            Media={'MediaFileUri': media_uri},
            MediaFormat=recording_key.split('.')[-1].lower(),  # mp3, wav, m4a
            LanguageCode=language_code,
            Settings={
                'ShowSpeakerLabels': True,  # Speaker diarization
                'MaxSpeakerLabels': 2,      # RCM and Customer (or BCM solo)
                'ChannelIdentification': False
            },
            OutputBucketName=TRANSCRIPTS_BUCKET,
            OutputKey=f"transcribe-jobs/{evaluation_id}/"
            # Note: ContentRedaction only supported for English, not Hindi
            # PII will be handled in post-processing if needed
        )
        
        job_status = transcribe_response['TranscriptionJob']['TranscriptionJobStatus']
        print(f"Transcribe job started: {job_name}, Status: {job_status}")
        
        # Poll for completion (Lambda can run up to 15 min)
        max_wait_time = 600  # 10 minutes
        start_time = time.time()
        
        while time.time() - start_time < max_wait_time:
            time.sleep(10)  # Check every 10 seconds
            
            job_status_response = transcribe_client.get_transcription_job(
                TranscriptionJobName=job_name
            )
            
            job = job_status_response['TranscriptionJob']
            current_status = job['TranscriptionJobStatus']
            
            print(f"Transcribe job {job_name} status: {current_status}")
            
            if current_status == 'COMPLETED':
                print(f"Transcription completed successfully")
                
                # Get transcript from S3 directly (not from HTTP URL)
                # Transcribe saves output to: OutputBucketName/OutputKey/job-name.json
                transcript_s3_key = f"transcribe-jobs/{evaluation_id}/{job_name}.json"
                
                print(f"Downloading transcript from s3://{TRANSCRIPTS_BUCKET}/{transcript_s3_key}")
                
                try:
                    transcript_response = s3_client.get_object(
                        Bucket=TRANSCRIPTS_BUCKET,
                        Key=transcript_s3_key
                    )
                    transcript_data = json.loads(transcript_response['Body'].read())
                except Exception as s3_error:
                    print(f"Error downloading from S3: {s3_error}")
                    # Fallback: try the HTTP URL from Transcribe response
                    transcript_uri = job['Transcript']['TranscriptFileUri']
                    print(f"Trying HTTP URL: {transcript_uri}")
                    transcript_data = download_transcript(transcript_uri)
                
                # Process and format transcript
                formatted_transcript = format_transcript(transcript_data, language_code)
                
                # Save formatted transcript to S3
                transcript_key = f"transcripts/{evaluation_id}/transcript.json"
                s3_client.put_object(
                    Bucket=TRANSCRIPTS_BUCKET,
                    Key=transcript_key,
                    Body=json.dumps(formatted_transcript, ensure_ascii=False, indent=2),
                    ContentType='application/json'
                )
                
                print(f"Formatted transcript saved to s3://{TRANSCRIPTS_BUCKET}/{transcript_key}")
                
                # Update DynamoDB with completion
                table.update_item(
                    Key={
                        'evaluation_id': evaluation_id,
                        'created_at': created_at
                    },
                    UpdateExpression='SET #status = :status, transcript_s3_key = :key, transcription_completed_at = :timestamp',
                    ExpressionAttributeNames={
                        '#status': 'status'
                    },
                    ExpressionAttributeValues={
                        ':status': 'TRANSCRIBED',
                        ':key': transcript_key,
                        ':timestamp': datetime.utcnow().isoformat()
                    }
                )
                
                # Get audio duration from Transcribe job metadata
                lambda_client = boto3.client('lambda')
                
                # Extract application_id from recording_key
                # Format: recordings/APP-xxx/eval-xxx.mp3
                application_id = event.get('application_id', 'UNKNOWN')
                if not application_id or application_id == 'UNKNOWN':
                    # Try to extract from recording_key
                    try:
                        parts = recording_key.split('/')
                        if len(parts) >= 2:
                            application_id = parts[1]
                    except:
                        application_id = 'UNKNOWN'
                
                # Get call_type from event or default
                call_type = event.get('call_type', 'RCM_AUDIO_PD')
                
                # Get audio duration from Transcribe job metadata
                audio_duration_seconds = None
                try:
                    media_duration = job.get('MediaSampleRateHertz')  # not duration
                    # Transcribe stores duration in the transcript output
                    if 'results' in transcript_data:
                        items = transcript_data['results'].get('items', [])
                        pronunciation_items = [i for i in items if i.get('type') == 'pronunciation']
                        if pronunciation_items:
                            last_item = pronunciation_items[-1]
                            audio_duration_seconds = int(float(last_item.get('end_time', 0))) + 1
                            print(f"Audio duration from transcript: {audio_duration_seconds}s")
                except Exception as dur_err:
                    print(f"Could not compute duration: {dur_err}")

                evaluation_payload = {
                    'evaluation_id': evaluation_id,
                    'transcript_s3_key': transcript_key,
                    'recording_s3_key': recording_key,
                    'created_at': created_at,
                    'application_id': application_id,
                    'call_type': call_type,
                    'language_code': language_code,
                    'audio_duration_seconds': audio_duration_seconds,
                }
                
                print(f"Invoking evaluation Lambda with payload: {json.dumps(evaluation_payload)}")
                
                eval_response = lambda_client.invoke(
                    FunctionName=os.environ.get('EVALUATION_LAMBDA_NAME', 'audio-pd-evaluation-dev'),
                    InvocationType='Event',  # Async invocation
                    Payload=json.dumps(evaluation_payload)
                )
                
                print(f"Evaluation Lambda invoked: StatusCode={eval_response['StatusCode']}")
                
                # Clean up Transcribe job (optional)
                try:
                    transcribe_client.delete_transcription_job(
                        TranscriptionJobName=job_name
                    )
                except:
                    pass
                
                return {
                    'statusCode': 200,
                    'body': json.dumps({
                        'evaluation_id': evaluation_id,
                        'status': 'COMPLETED',
                        'transcript_s3_key': transcript_key,
                        'duration': time.time() - start_time
                    })
                }
                
            elif current_status == 'FAILED':
                error_message = job.get('FailureReason', 'Unknown error')
                print(f"Transcription failed: {error_message}")
                
                # Update DynamoDB with failure
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
                        ':status': 'TRANSCRIPTION_FAILED',
                        ':error': error_message
                    }
                )
                
                raise Exception(f"Transcription job failed: {error_message}")
        
        # Timeout - job still in progress
        print(f"Transcription job still in progress after {max_wait_time}s")
        
        # For very long recordings, you might want to use Step Functions with callback pattern
        # For now, we'll mark as TRANSCRIBING and let a separate poller handle it
        return {
            'statusCode': 202,  # Accepted but not complete
            'body': json.dumps({
                'evaluation_id': evaluation_id,
                'status': 'IN_PROGRESS',
                'job_name': job_name,
                'message': 'Transcription still in progress'
            })
        }
        
    except Exception as e:
        print(f"Error in transcription: {str(e)}")
        
        # Update DynamoDB with error
        try:
            table = dynamodb.Table(DYNAMODB_TABLE)
            table.update_item(
                Key={
                    'evaluation_id': event['evaluation_id'],
                    'created_at': event.get('created_at')
                },
                UpdateExpression='SET #status = :status, error_message = :error',
                ExpressionAttributeNames={
                    '#status': 'status'
                },
                ExpressionAttributeValues={
                    ':status': 'TRANSCRIPTION_FAILED',
                    ':error': str(e)
                }
            )
        except:
            pass
        
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e),
                'evaluation_id': event.get('evaluation_id')
            })
        }


def download_transcript(transcript_uri):
    """Download transcript JSON from S3 URI"""
    import urllib.request
    
    # Transcribe provides HTTPS URL, download it using built-in urllib
    with urllib.request.urlopen(transcript_uri) as response:
        data = response.read()
    
    return json.loads(data)


def format_transcript(transcript_data, language_code):
    """
    Format Transcribe output into our standard format
    
    Returns:
    {
        "transcript": "full text",
        "segments": [
            {
                "start": 0.5,
                "end": 3.2,
                "speaker": "SPEAKER_0",
                "text": "..."
            }
        ],
        "language": "hi-IN",
        "speakers": {
            "SPEAKER_0": "RCM",
            "SPEAKER_1": "Customer"
        }
    }
    """
    results = transcript_data['results']
    
    # Extract full transcript
    full_transcript = results['transcripts'][0]['transcript']
    
    # Extract segments with speaker labels
    segments = []
    
    if 'speaker_labels' in results:
        speaker_segments = results['speaker_labels']['segments']
        items = results['items']
        
        for segment in speaker_segments:
            speaker = segment['speaker_label']
            start_time = float(segment['start_time'])
            end_time = float(segment['end_time'])
            
            # Collect words for this segment
            segment_words = []
            for item in items:
                if item['type'] == 'pronunciation':
                    item_start = float(item['start_time'])
                    item_end = float(item['end_time'])
                    
                    # Check if this item falls within the segment
                    if item_start >= start_time and item_end <= end_time:
                        segment_words.append(item['alternatives'][0]['content'])
            
            if segment_words:
                segments.append({
                    'start': start_time,
                    'end': end_time,
                    'speaker': speaker,
                    'text': ' '.join(segment_words)
                })
    else:
        # No speaker labels, create single segment
        segments.append({
            'start': 0.0,
            'end': 0.0,
            'speaker': 'SPEAKER_0',
            'text': full_transcript
        })
    
    # Map speakers to roles (simple heuristic: first speaker is RCM)
    speaker_map = {}
    unique_speakers = list(set(seg['speaker'] for seg in segments))
    
    if len(unique_speakers) == 1:
        # Single speaker (BCM narration)
        speaker_map[unique_speakers[0]] = 'BCM'
    elif len(unique_speakers) == 2:
        # Two speakers (RCM and Customer)
        speaker_map[unique_speakers[0]] = 'RCM'
        speaker_map[unique_speakers[1]] = 'Customer'
    else:
        # Multiple speakers (unexpected, use generic labels)
        for i, speaker in enumerate(unique_speakers):
            speaker_map[speaker] = f'Speaker_{i+1}'
    
    return {
        'transcript': full_transcript,
        'segments': segments,
        'language': language_code,
        'speakers': speaker_map,
        'transcription_service': 'Amazon Transcribe',
        'generated_at': datetime.utcnow().isoformat()
    }
