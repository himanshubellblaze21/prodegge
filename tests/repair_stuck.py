"""
Re-run evaluations that are stuck or have no score, ONE AT A TIME.

This account's Lambda concurrency limit is 10 (the AWS default is 1000). A
bulk re-run at 4 concurrent evaluations — each of which also triggers the
Excel Lambda — saturated it, Lambda throttled 100+ invocations per 5 minutes,
and API Gateway returned 503 to the frontend: no history, no downloads. Async
invocations that were throttled away also left records stranded in EVALUATING
for ever.

So this repairs serially and verifies each one before moving on. One
evaluation plus its Excel run is 2 concurrent executions, leaving 8 for the
user-facing API.

    python tests/repair_stuck.py --dry-run
    python tests/repair_stuck.py
"""
import argparse
import io
import json
import os
import sys
import time

import boto3
from botocore.config import Config

REGION = os.environ.get('AWS_REGION', 'ap-south-1')
TABLE = os.environ.get('DYNAMODB_TABLE', 'audio-pd-evaluations-dev')
# Statuses that mean "the user sees nothing useful for this recording".
BROKEN = ('EVALUATING', 'FAILED', 'EXCEL_GENERATING', 'TRANSCRIBED')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

    s3 = boto3.client('s3', region_name=REGION)
    lc = boto3.client('lambda', region_name=REGION,
                      config=Config(read_timeout=900, retries={'max_attempts': 0}))
    table = boto3.resource('dynamodb', region_name=REGION).Table(TABLE)

    items, lek = [], None
    while True:
        kw = {'Limit': 200}
        if lek:
            kw['ExclusiveStartKey'] = lek
        r = table.scan(**kw)
        items += r['Items']
        lek = r.get('LastEvaluatedKey')
        if not lek:
            break

    broken = [i for i in items
              if i.get('transcript_s3_key')
              and (i.get('status') in BROKEN or i.get('total_score') is None)
              and not i.get('not_evaluable')]
    print(f'{len(broken)} recording(s) to repair')
    for i in broken:
        print(f"  {i['evaluation_id']}  status={i.get('status')} score={i.get('total_score')}")
    if args.dry_run or not broken:
        return 0

    ok = fail = 0
    for n, rec in enumerate(broken, 1):
        eid = rec['evaluation_id']
        try:
            t = json.loads(s3.get_object(Bucket='audio-pd-transcripts-dev',
                                         Key=rec['transcript_s3_key'])['Body'].read())
            dur = int(max((s.get('end', 0) for s in t.get('segments') or []), default=0)) or None
            r = lc.invoke(
                FunctionName='audio-pd-evaluation-dev', InvocationType='RequestResponse',
                Payload=json.dumps({
                    'evaluation_id': eid,
                    'application_id': rec.get('application_id', 'UNKNOWN'),
                    'call_type': rec.get('call_type') or 'AUTO_DETECT',
                    'transcript_s3_key': rec['transcript_s3_key'],
                    'created_at': rec['created_at'],
                    'audio_duration_seconds': dur,
                }).encode())
            body = json.loads(json.loads(r['Payload'].read())['body'])
            if body.get('score') is not None:
                ok += 1
                print(f"  [{n}/{len(broken)}] {eid}: score={body['score']} {body.get('grade')}")
            elif body.get('not_evaluable'):
                ok += 1
                print(f"  [{n}/{len(broken)}] {eid}: NOT EVALUABLE ({body.get('recording_kind')})")
            else:
                fail += 1
                print(f"  [{n}/{len(broken)}] {eid}: {str(body.get('error'))[:90]}")
        except Exception as e:
            fail += 1
            print(f"  [{n}/{len(broken)}] {eid}: EXCEPTION {str(e)[:90]}")
        time.sleep(8)      # let the Excel Lambda finish before the next one

    print(f'\nrepaired {ok}, still failing {fail}')
    return 0 if fail == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
