"""
Transcription Lambda — Prodigee Finance V2.5
Uses IdentifyLanguage=True for ALL audio files — no MediaFormat, no extension
remapping. Transcribe auto-detects codec and language from file bytes.
Speaker diarization → call type detection → triggers evaluation Lambda.
"""
import json, boto3, os, time
from datetime import datetime

transcribe_client = boto3.client('transcribe', region_name=os.environ.get('AWS_REGION', 'ap-south-1'))
s3_client  = boto3.client('s3')
dynamodb   = boto3.resource('dynamodb')

RECORDINGS_BUCKET  = os.environ['RECORDINGS_BUCKET']
TRANSCRIPTS_BUCKET = os.environ['TRANSCRIPTS_BUCKET']
DYNAMODB_TABLE     = os.environ['DYNAMODB_TABLE']


def lambda_handler(event, context):
    print(f"Transcription V2.5: {json.dumps(event)}")
    table = dynamodb.Table(DYNAMODB_TABLE)

    try:
        evaluation_id  = event['evaluation_id']
        recording_key  = event['recording_s3_key']
        created_at     = event['created_at']
        language_code  = event.get('language_code', 'hi-IN')
        application_id = event.get('application_id', '')
        call_type      = event.get('call_type', 'AUTO_DETECT')

        if not application_id or application_id in ('UNKNOWN', ''):
            parts = recording_key.split('/')
            application_id = parts[1] if len(parts) >= 3 else 'UNKNOWN'

        table.update_item(
            Key={'evaluation_id': evaluation_id, 'created_at': created_at},
            UpdateExpression='SET #s=:s, transcription_started_at=:t',
            ExpressionAttributeNames={'#s': 'status'},
            ExpressionAttributeValues={':s': 'TRANSCRIBING', ':t': datetime.utcnow().isoformat()}
        )

        media_uri = f"s3://{RECORDINGS_BUCKET}/{recording_key}"
        job_name  = f"t-{evaluation_id}-{int(time.time())}"
        print(f"Job: {job_name} | {media_uri}")

        # IdentifyLanguage=True → Transcribe detects both codec AND language
        # from file bytes. No MediaFormat needed. Handles mp3/wav/m4a/mp4/ogg/
        # flac/webm/amr AND raw AAC-ADTS (.aac), opus, wma etc.
        transcribe_client.start_transcription_job(
            TranscriptionJobName=job_name,
            Media={'MediaFileUri': media_uri},
            IdentifyLanguage=True,
            LanguageOptions=['hi-IN', 'en-IN', 'en-US'],
            Settings={
                'ShowSpeakerLabels': True,
                'MaxSpeakerLabels': 3,
                'ChannelIdentification': False,
            },
            OutputBucketName=TRANSCRIPTS_BUCKET,
            OutputKey=f"transcribe-jobs/{evaluation_id}/",
        )
        print("Transcribe job started with IdentifyLanguage=True")

        # Poll until done
        start = time.time()
        while time.time() - start < 840:
            time.sleep(15)
            job    = transcribe_client.get_transcription_job(TranscriptionJobName=job_name)['TranscriptionJob']
            status = job['TranscriptionJobStatus']
            print(f"[{int(time.time()-start)}s] {status}")

            if status == 'COMPLETED':
                # Download transcript
                ts_key = f"transcribe-jobs/{evaluation_id}/{job_name}.json"
                try:
                    raw  = s3_client.get_object(Bucket=TRANSCRIPTS_BUCKET, Key=ts_key)
                    data = json.loads(raw['Body'].read())
                except Exception as dl_err:
                    print(f"S3 dl error ({dl_err}), using HTTP")
                    import urllib.request
                    with urllib.request.urlopen(job['Transcript']['TranscriptFileUri']) as r:
                        data = json.loads(r.read())

                formatted = _format_transcript(data, language_code)
                out_key   = f"transcripts/{evaluation_id}/transcript.json"
                s3_client.put_object(
                    Bucket=TRANSCRIPTS_BUCKET, Key=out_key,
                    Body=json.dumps(formatted, ensure_ascii=False, indent=2),
                    ContentType='application/json'
                )
                print(f"Saved transcript: {out_key} | {len(formatted['transcript'])} chars")

                # Duration from last pronunciation item
                dur = None
                try:
                    pron = [i for i in data['results'].get('items', []) if i.get('type') == 'pronunciation']
                    if pron:
                        dur = int(float(pron[-1].get('end_time', 0))) + 1
                        print(f"Duration: {dur}s")
                except Exception: pass

                # Update DynamoDB
                table.update_item(
                    Key={'evaluation_id': evaluation_id, 'created_at': created_at},
                    UpdateExpression='SET #s=:s, transcript_s3_key=:k, transcription_completed_at=:t',
                    ExpressionAttributeNames={'#s': 'status'},
                    ExpressionAttributeValues={':s': 'TRANSCRIBED', ':k': out_key, ':t': datetime.utcnow().isoformat()}
                )

                # Trigger evaluation Lambda
                lc = boto3.client('lambda')
                payload = {
                    'evaluation_id':          evaluation_id,
                    'transcript_s3_key':      out_key,
                    'recording_s3_key':       recording_key,
                    'created_at':             created_at,
                    'application_id':         application_id,
                    'call_type':              call_type,
                    'language_code':          language_code,
                    'audio_duration_seconds': dur,
                }
                r = lc.invoke(
                    FunctionName=os.environ.get('EVALUATION_LAMBDA_NAME', 'audio-pd-evaluation-dev'),
                    InvocationType='Event',
                    Payload=json.dumps(payload).encode()
                )
                print(f"Evaluation Lambda triggered: {r['StatusCode']}")

                try: transcribe_client.delete_transcription_job(TranscriptionJobName=job_name)
                except Exception: pass

                return {'statusCode': 200, 'body': json.dumps({'evaluation_id': evaluation_id, 'status': 'COMPLETED'})}

            elif status == 'FAILED':
                reason = job.get('FailureReason', 'Unknown')
                print(f"Transcribe FAILED: {reason}")
                table.update_item(
                    Key={'evaluation_id': evaluation_id, 'created_at': created_at},
                    UpdateExpression='SET #s=:s, error_message=:e',
                    ExpressionAttributeNames={'#s': 'status'},
                    ExpressionAttributeValues={':s': 'TRANSCRIPTION_FAILED', ':e': reason}
                )
                raise Exception(f"Transcription failed: {reason}")

        return {'statusCode': 202, 'body': json.dumps({'status': 'IN_PROGRESS'})}

    except Exception as e:
        print(f"Error: {e}")
        import traceback; traceback.print_exc()
        try:
            table.update_item(
                Key={'evaluation_id': event['evaluation_id'], 'created_at': event.get('created_at')},
                UpdateExpression='SET #s=:s, error_message=:e',
                ExpressionAttributeNames={'#s': 'status'},
                ExpressionAttributeValues={':s': 'TRANSCRIPTION_FAILED', ':e': str(e)}
            )
        except Exception: pass
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}


def _format_transcript(data: dict, language_code: str) -> dict:
    results   = data['results']
    full_text = results['transcripts'][0]['transcript']
    segments  = []

    if 'speaker_labels' in results:
        items = results['items']
        for seg in results['speaker_labels']['segments']:
            spk  = seg['speaker_label']
            s, e = float(seg['start_time']), float(seg['end_time'])
            words = [
                i['alternatives'][0]['content']
                for i in items
                if i['type'] == 'pronunciation'
                and float(i['start_time']) >= s
                and float(i['end_time'])   <= e
            ]
            if words:
                segments.append({'start': s, 'end': e, 'speaker': spk, 'text': ' '.join(words)})
    else:
        segments.append({'start': 0.0, 'end': 0.0, 'speaker': 'spk_0', 'text': full_text})

    unique = list(dict.fromkeys(s['speaker'] for s in segments))
    if len(unique) == 1:
        speaker_map = {unique[0]: 'BCM'}
    elif len(unique) == 2:
        speaker_map = {unique[0]: 'RCM', unique[1]: 'Customer'}
    else:
        speaker_map = {sp: f'Speaker_{i+1}' for i, sp in enumerate(unique)}

    return {
        'transcript': full_text, 'segments': segments,
        'language': language_code, 'speakers': speaker_map,
        'transcription_service': 'Amazon Transcribe',
        'generated_at': datetime.utcnow().isoformat(),
    }
