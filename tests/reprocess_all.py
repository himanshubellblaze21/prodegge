"""
Re-score every recording in the table with the current pipeline.

Existing scores are a mix of outputs from every version the evaluator has ever
run, including the versions that returned "No" for all 35 criteria. Anything
scored before the current logic is not comparable with anything scored after,
so this re-runs the whole corpus and writes a before/after report.

    python tests/reprocess_all.py --dry-run     # inventory only, no changes
    python tests/reprocess_all.py               # re-score everything
    python tests/reprocess_all.py --limit 10    # first 10, for a smoke test

Snapshots the previous scores to reprocess_snapshot.json first, so a bad batch
can be compared against (and, if needed, reasoned about) afterwards.
"""
import argparse
import io
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime

import boto3
from botocore.config import Config

REGION = os.environ.get('AWS_REGION', 'ap-south-1')
TABLE = os.environ.get('DYNAMODB_TABLE', 'audio-pd-evaluations-dev')
FN = 'audio-pd-evaluation-dev'
# This account's Lambda concurrency limit is 10, not the AWS default of 1000.
# Running 4 evaluations at once (each of which also triggers the Excel Lambda)
# saturated it: Lambda throttled 100+ invocations per 5 minutes, API Gateway
# returned 503 to the frontend, and the history and downloads went dark for as
# long as the batch ran. Async invocations that were throttled away also left
# records stranded in EVALUATING for ever.
#
# So this runs ONE evaluation at a time unless told otherwise. Raise it only
# after the account's concurrency limit has been increased.
BATCH = int(os.environ.get('REPROCESS_BATCH', '1'))
SNAPSHOT = 'reprocess_snapshot.json'


def scan_all(table):
    items, lek = [], None
    while True:
        kw = {'Limit': 200}
        if lek:
            kw['ExclusiveStartKey'] = lek
        r = table.scan(**kw)
        items += r['Items']
        lek = r.get('LastEvaluatedKey')
        if not lek:
            return items


def duration_of(s3, key):
    """Recording length from the transcript's own segments."""
    try:
        t = json.loads(s3.get_object(Bucket='audio-pd-transcripts-dev', Key=key)['Body'].read())
        segs = t.get('segments') or []
        return int(max((s.get('end', 0) for s in segs), default=0)) or None
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--limit', type=int)
    args = ap.parse_args()
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

    s3 = boto3.client('s3', region_name=REGION)
    lc = boto3.client('lambda', region_name=REGION,
                      config=Config(read_timeout=900, retries={'max_attempts': 0}))
    table = boto3.resource('dynamodb', region_name=REGION).Table(TABLE)

    records = [i for i in scan_all(table) if i.get('transcript_s3_key')]
    records.sort(key=lambda x: x.get('created_at', ''))
    if args.limit:
        records = records[:args.limit]

    before = {i['evaluation_id']: {
        'created_at': i['created_at'],
        'call_type': i.get('call_type'),
        'score': float(i['total_score']) if i.get('total_score') is not None else None,
        'grade': i.get('grade_band'),
    } for i in records}
    io.open(SNAPSHOT, 'w', encoding='utf-8').write(json.dumps(before, indent=1, default=str))
    print(f'{len(records)} recordings with transcripts | snapshot -> {SNAPSHOT}')
    print(f'call types: {dict(Counter(v["call_type"] for v in before.values()))}')
    if args.dry_run:
        return 0

    started = datetime.utcnow().isoformat()
    sent = 0
    for n in range(0, len(records), BATCH):
        for rec in records[n:n + BATCH]:
            dur = duration_of(s3, rec['transcript_s3_key'])
            payload = {
                'evaluation_id': rec['evaluation_id'],
                'application_id': rec.get('application_id', 'UNKNOWN'),
                # Historic uploads predate the call-type picker; let the
                # classifier decide those rather than guessing.
                'call_type': rec.get('call_type') or 'AUTO_DETECT',
                'transcript_s3_key': rec['transcript_s3_key'],
                'created_at': rec['created_at'],
                'audio_duration_seconds': dur,
            }
            # Synchronous: an async invoke that gets throttled is silently
            # dropped and the record sits in EVALUATING for ever.
            lc.invoke(FunctionName=FN, InvocationType='RequestResponse',
                      Payload=json.dumps(payload).encode())
            sent += 1
        print(f'  dispatched {sent}/{len(records)}')
        time.sleep(8)           # let the Excel Lambda finish too

    # Wait for the table to catch up.
    print('waiting for results...')
    for _ in range(40):
        time.sleep(30)
        done = 0
        for rec in records:
            it = table.get_item(Key={'evaluation_id': rec['evaluation_id'],
                                     'created_at': rec['created_at']}).get('Item', {})
            if str(it.get('evaluated_at', '')) > started:
                done += 1
        print(f'  {done}/{len(records)} re-scored')
        if done >= len(records):
            break

    # Report
    rows = []
    for rec in records:
        it = table.get_item(Key={'evaluation_id': rec['evaluation_id'],
                                 'created_at': rec['created_at']}).get('Item', {})
        eid = rec['evaluation_id']
        rows.append((eid, before[eid]['score'],
                     float(it['total_score']) if it.get('total_score') is not None else None,
                     it.get('call_type'), it.get('grade_band'),
                     bool(it.get('not_evaluable')), bool(it.get('needs_review'))))

    print()
    print(f"{'evaluation':<24}{'before':>8}{'after':>8}  {'type':<17}{'grade':<16}flags")
    for eid, b, a, ct, g, ne, nr in rows:
        flags = ('NOT-EVALUABLE ' if ne else '') + ('needs-review' if nr else '')
        print(f"{eid:<24}{'-' if b is None else b:>8}{'-' if a is None else a:>8}  "
              f"{str(ct):<17}{str(g)[:15]:<16}{flags}")

    after = [a for _, _, a, _, _, ne, _ in rows if a is not None and not ne]
    if after:
        import statistics
        print()
        print(f'after: n={len(after)} mean={statistics.mean(after):.1f} '
              f'median={statistics.median(after)} min={min(after)} max={max(after)}')
        dist = Counter('0-19' if s < 20 else '20-39' if s < 40 else '40-59' if s < 60
                       else '60-74' if s < 75 else '75+' for s in after)
        print('  ' + '  '.join(f'{k}:{dist.get(k, 0)}' for k in
                               ['0-19', '20-39', '40-59', '60-74', '75+']))
        print(f'  not evaluable: {sum(1 for r in rows if r[5])} | '
              f'needs review: {sum(1 for r in rows if r[6])}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
