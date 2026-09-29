"""
Regression check for the evaluation pipeline.

Run this after ANY change to lambda/evaluation/handler.py (prompts, model id,
scoring logic) and before deploying. It runs the real classifier and, with
--full, the real per-section scoring against pinned production transcripts and
checks them against expected results.

Why this exists: two separate production bugs shipped silently because nothing
checked the pipeline end to end —
  1. Every criterion came back "No", producing 0/100 scorecards from good
     recordings (the single 35-criteria mega-prompt made the model give up).
  2. Genuine BCM field visits were classified as BM, applying the wrong rubric
     to the whole scorecard.
Both would have been caught instantly by this file.

Usage:
    python tests/regression_check.py            # call-type classification only (~10 Bedrock calls)
    python tests/regression_check.py --full     # also re-scores every section (~60 calls, slower/costlier)

Exits non-zero if any case fails, so it can gate a deploy.
"""
import argparse
import io
import json
import os
import sys

import boto3

REGION             = os.environ.get('AWS_REGION', 'ap-south-1')
TRANSCRIPTS_BUCKET = os.environ.get('TRANSCRIPTS_BUCKET', 'audio-pd-transcripts-dev')
DDB_TABLE          = os.environ.get('DYNAMODB_TABLE', 'audio-pd-evaluations-dev')

# ── Pinned cases ──────────────────────────────────────────────────────────────
# expected_call_type was established by reading each transcript in full, not by
# trusting whatever the pipeline previously stored (the stored values were
# themselves produced by the buggy heuristic). The `why` field records the
# evidence, so a future failure can be judged rather than blindly "fixed".
CASES = [
    {
        'evaluation_id': 'eval-2df6a9d38942',
        'expected_call_type': 'BCM_PHYSICAL_PD',
        'why': 'User-confirmed BCM recording. In-person: livestock counted, land/crops, '
               'property papers and family income all covered face to face.',
    },
    {
        'evaluation_id': 'eval-27577366e811',
        'expected_call_type': 'BCM_PHYSICAL_PD',
        'why': 'In-person shop visit — employee names/wages discussed on site, stock valued '
               'by eye, officer arranges to go to the home next for photos.',
    },
    {
        'evaluation_id': 'eval-5c0336205d2b',
        'expected_call_type': 'BCM_PHYSICAL_PD',
        'why': 'Opens with the officer arriving in person ("main Prodigee Finance aaya hoon").',
    },
    {
        'evaluation_id': 'eval-9efa60356e17',
        'expected_call_type': 'RCM_AUDIO_PD',
        'why': 'Phone call ("hello ... kaise ho"), granular loan-by-loan verification against a '
               'bureau report already pulled up, decision to be made same day.',
    },
    {
        'evaluation_id': 'eval-1ae423f9a9fe',
        'expected_call_type': 'BM_AUDIO_FI',
        'why': 'Phone call, 2.3 min, light identity/business check, closes with '
               '"main aapki file aage badhata hoon" (forward decision).',
    },
    {
        'evaluation_id': 'eval-d4ddb3c1fe55',
        'expected_call_type': 'BM_AUDIO_FI',
        'why': 'Phone call, pre-visit — officer says "ek aadh din mein visit pe aa jaayenge", '
               'which structurally rules out RCM.',
    },
]

# A healthy scorecard must not collapse to all-zero, and must not be suspiciously
# perfect. These are deliberately wide — this guards against evaluator failure,
# not against a particular score.
MIN_PLAUSIBLE_SCORE = 1.0
MIN_SCORED_ITEMS    = 1

# Share of credited criteria whose evidence quote must resolve to a real
# timestamp in the recording. Set below 1.0 because the evaluator occasionally
# paraphrases rather than copying; a sustained drop below this means scores are
# drifting away from being provable.
MIN_EVIDENCE_RATE = 0.7


def load_handler():
    os.environ.setdefault('TRANSCRIPTS_BUCKET', TRANSCRIPTS_BUCKET)
    os.environ.setdefault('REPORTS_BUCKET', 'audio-pd-reports-dev')
    os.environ.setdefault('DYNAMODB_TABLE', DDB_TABLE)
    os.environ.setdefault('AWS_REGION', REGION)
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lambda', 'evaluation'))
    if 'handler' in sys.modules:
        del sys.modules['handler']
    import handler
    return handler


def fetch_transcript(s3, ddb_table, evaluation_id):
    resp = ddb_table.scan(
        FilterExpression='evaluation_id = :e',
        ExpressionAttributeValues={':e': evaluation_id},
    )
    if not resp.get('Items'):
        raise LookupError(f"{evaluation_id} not found in {DDB_TABLE}")
    item = resp['Items'][0]
    key = item.get('transcript_s3_key')
    if not key:
        raise LookupError(f"{evaluation_id} has no transcript_s3_key")
    raw = s3.get_object(Bucket=TRANSCRIPTS_BUCKET, Key=key)
    tdata = json.loads(raw['Body'].read())
    segments = tdata.get('segments', [])
    return {
        'transcript': tdata.get('transcript', ''),
        'segments': segments,
        'speaker_count': len({s.get('speaker') for s in segments if s.get('speaker')}),
        'duration': int(max((s.get('end', 0) for s in segments), default=0)),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--full', action='store_true',
                        help='also re-score every section (slower, more Bedrock calls)')
    args = parser.parse_args()

    # Devanagari in reasons would crash the default Windows console codec.
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

    handler = load_handler()
    s3 = boto3.client('s3', region_name=REGION)
    ddb_table = boto3.resource('dynamodb', region_name=REGION).Table(DDB_TABLE)

    print(f"Model: {handler.BEDROCK_MODEL_ID} | scorecard v{handler.SCORECARD_VERSION}")
    print(f"Mode : {'classification + scoring' if args.full else 'classification only'}")
    print("=" * 100)

    failures = []
    for case in CASES:
        eid      = case['evaluation_id']
        expected = case['expected_call_type']
        try:
            t = fetch_transcript(s3, ddb_table, eid)
        except Exception as e:
            print(f"FAIL  {eid}: could not load transcript — {e}")
            failures.append((eid, f"transcript load failed: {e}"))
            continue

        # ── Call type ────────────────────────────────────────────────────────
        classification = handler.classify_call_type(
            t['transcript'], t['speaker_count'], 'AUTO_DETECT', t['duration'])
        actual = classification['call_type']
        ok = actual == expected
        status = 'PASS ' if ok else 'FAIL '
        print(f"{status} {eid}  call_type: expected {expected}, got {actual} "
              f"(confidence={classification['confidence']})")
        if not ok:
            print(f"       expected because: {case['why']}")
            print(f"       model's reason  : {classification['reason']}")
            failures.append((eid, f"call type {actual} != {expected}"))

        # ── Scoring sanity ───────────────────────────────────────────────────
        if args.full:
            criteria, _, section_names = handler.get_config(expected)
            try:
                profile = handler.call_bedrock_json(
                    handler.build_profile_prompt(t['transcript'], expected), 'profile')
            except Exception as e:
                print(f"       profile failed: {e}")
                profile = {}
            all_items = []
            for sec_id in handler.ordered_sections(criteria):
                sec_criteria = [c for c in criteria if c['sec'] == sec_id]
                prompt = handler.build_section_prompt(
                    t['transcript'], expected, sec_id, sec_criteria,
                    section_names[sec_id], t['duration'], profile)
                try:
                    sdata = handler.call_bedrock_json(prompt, f"{eid[:12]}-{sec_id}")
                    sec_ids = {c['id'] for c in sec_criteria}
                    for i in sdata.get('items', []):
                        if not isinstance(i, dict) or i.get('id') not in sec_ids:
                            continue
                        item = {**i, 'score': handler.normalise_score(i.get('score'))}
                        quote = (item.get('evidence') or '').strip()
                        ev = handler.verify_evidence(quote, t['segments']) if quote else \
                            {'timestamp': '', 'verified': False}
                        item['evidence_timestamp'] = ev['timestamp']
                        item['evidence_verified'] = ev['verified']
                        all_items.append(item)
                except Exception as e:
                    print(f"       section {sec_id} failed: {e}")
                    failures.append((eid, f"section {sec_id} failed: {e}"))

            # Same post-passes the Lambda runs, so the harness reflects production.
            absence_rule = handler.get_rubric(expected).get('absence_rule')
            handler.recheck_no_items(t['transcript'], t['segments'], all_items, criteria,
                                     absence_rule=absence_rule)
            handler.audit_evidence_relevance(t['transcript'], t['segments'], all_items, criteria,
                                             absence_rule=absence_rule)

            scoring = handler.calculate_scores(
                {'items': all_items, '_duration_secs': t['duration']}, expected,
                other_income=handler.has_other_income(profile))
            score        = float(scoring['total_score'])
            scored_items = [i for i in all_items if i['score'] in ('Yes', 'Half')]

            if len(scored_items) < MIN_SCORED_ITEMS or score < MIN_PLAUSIBLE_SCORE:
                print(f"FAIL  {eid}  scoring collapsed: score={score}, "
                      f"{len(scored_items)} items credited (evaluator-failure signature)")
                failures.append((eid, f"scoring collapsed to {score}"))
            else:
                dist = {}
                for i in all_items:
                    dist[i['score']] = dist.get(i['score'], 0) + 1
                print(f"       scoring OK: {score}/100, distribution {dist}")

                # Every criterion awarded points must be traceable to real words
                # in the recording — that trail is what proves the score wasn't
                # hallucinated, so guard its coverage like any other behaviour.
                traced = [i for i in scored_items if i.get('evidence_verified')]
                rate = len(traced) / len(scored_items) if scored_items else 0
                print(f"       evidence: {len(traced)}/{len(scored_items)} credited items "
                      f"traced to a verified timestamp ({rate:.0%})")
                if rate < MIN_EVIDENCE_RATE:
                    print(f"FAIL  {eid}  evidence coverage {rate:.0%} below "
                          f"{MIN_EVIDENCE_RATE:.0%} — scores are not provably grounded")
                    failures.append((eid, f"evidence coverage {rate:.0%}"))

    print("=" * 100)
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for eid, reason in failures:
            print(f"  - {eid}: {reason}")
        return 1
    print(f"All {len(CASES)} cases passed.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
