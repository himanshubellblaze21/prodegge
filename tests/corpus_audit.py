"""
Corpus-wide audit of scoring behaviour, per criterion, across every recording.

Validating on a handful of recordings proves those recordings work. This looks
for the systematic faults that a small sample hides:

  * a criterion NEVER credited anywhere — either nobody ever does it, or it is
    impossible to satisfy as written and the points are dead
  * a criterion ALWAYS credited — likely being waved through
  * criteria whose evidence rarely resolves to real audio — marks awarded on
    quotes that cannot be located
  * recordings that still collapse (nothing credited at all)

Run after tests/reprocess_all.py so every result comes from the same logic:

    python tests/corpus_audit.py
"""
import io
import json
import os
import sys
from collections import Counter, defaultdict

import boto3

REGION = os.environ.get('AWS_REGION', 'ap-south-1')
REPORTS = os.environ.get('REPORTS_BUCKET', 'audio-pd-reports-dev')
TABLE = os.environ.get('DYNAMODB_TABLE', 'audio-pd-evaluations-dev')

# A criterion credited on fewer than this share of recordings of its type is
# worth a human look; one credited on more than the upper bound likewise.
RARELY = 0.05
ALWAYS = 0.95
MIN_SAMPLE = 5


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    s3 = boto3.client('s3', region_name=REGION)
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

    marks = defaultdict(lambda: defaultdict(Counter))     # type -> id -> Counter(score)
    evidence = defaultdict(lambda: defaultdict(lambda: [0, 0]))  # type -> id -> [verified, credited]
    per_type = Counter()
    collapsed, no_result, not_evaluable = [], [], []

    for it in items:
        key = it.get('result_s3_key')
        if not key:
            continue
        if it.get('not_evaluable'):
            not_evaluable.append(it['evaluation_id'])
            continue
        try:
            res = json.loads(s3.get_object(Bucket=REPORTS, Key=key)['Body'].read())
        except Exception:
            no_result.append(it['evaluation_id'])
            continue
        ct = res.get('call_type')
        rows = res.get('items') or []
        if not ct or not rows:
            no_result.append(it['evaluation_id'])
            continue
        per_type[ct] += 1
        credited_any = False
        for i in rows:
            cid, sc = i.get('id'), i.get('score')
            if not cid:
                continue
            marks[ct][cid][sc] += 1
            if sc in ('Yes', 'Half'):
                credited_any = True
                ev = evidence[ct][cid]
                ev[1] += 1
                if i.get('evidence_verified'):
                    ev[0] += 1
        if not credited_any:
            collapsed.append(it['evaluation_id'])

    print(f'recordings analysed by type: {dict(per_type)}')
    print(f'not evaluable (BCM interviews): {len(not_evaluable)}')
    if no_result:
        print(f'no usable result json: {len(no_result)} -> {no_result[:6]}')
    if collapsed:
        print(f'*** COLLAPSED (nothing credited at all): {collapsed}')
    else:
        print('no collapsed evaluations — every recording credited something')

    for ct in sorted(marks):
        n = per_type[ct]
        if n < MIN_SAMPLE:
            print(f'\n{ct}: only {n} recording(s) — too few to judge')
            continue
        print(f'\n{"="*72}\n{ct}  ({n} recordings)')
        never, always, weak_ev = [], [], []
        for cid in sorted(marks[ct], key=lambda c: (c[0], int(c[1:]))):
            c = marks[ct][cid]
            total = sum(c.values())
            credited = c['Yes'] + c['Half']
            rate = credited / total if total else 0
            ver, cred = evidence[ct][cid]
            if rate <= RARELY:
                never.append((cid, f'{rate:.0%}'))
            if rate >= ALWAYS:
                always.append((cid, f'{rate:.0%}'))
            if cred >= MIN_SAMPLE and ver / cred < 0.6:
                weak_ev.append((cid, f'{ver}/{cred} verified'))
        print(f'  never/rarely credited ({len(never)}): {never}')
        print(f'  always credited       ({len(always)}): {always}')
        print(f'  weak evidence         ({len(weak_ev)}): {weak_ev}')
        dist = Counter()
        for cid in marks[ct]:
            dist.update(marks[ct][cid])
        tot = sum(dist.values())
        print(f'  overall marks: ' + '  '.join(
            f'{k}={dist.get(k,0)} ({dist.get(k,0)/tot:.0%})' for k in ('Yes', 'Half', 'No')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
