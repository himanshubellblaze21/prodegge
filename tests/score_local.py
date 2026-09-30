"""
Score a stored recording locally against any scorecard version — real Bedrock
calls, nothing written to DynamoDB or S3.

Runs the same steps as the evaluation Lambda (header, profile, per-section
scoring, re-check, evidence audit, required-phrase caps, red flags, scoring),
then fills the version's Excel template with the result so the sheet can be
opened and compared with the client's expectations.

    python tests/score_local.py eval-6ad7a505f0f4                 # current version
    python tests/score_local.py eval-6ad7a505f0f4 --version 0     # legacy
    python tests/score_local.py eval-... --out some/dir

Writes <out>/<evaluation_id>_v<N>.json and .xlsx (default out: tests/output/).
"""
import argparse
import io
import json
import os
import sys
from decimal import Decimal

import boto3

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGION = os.environ.get('AWS_REGION', 'ap-south-1')
for k, v in {'TRANSCRIPTS_BUCKET': 'audio-pd-transcripts-dev', 'REPORTS_BUCKET': 'audio-pd-reports-dev',
             'DYNAMODB_TABLE': 'audio-pd-evaluations-dev', 'AWS_REGION': REGION}.items():
    os.environ.setdefault(k, v)
sys.path.insert(0, os.path.join(REPO, 'lambda', 'evaluation'))
sys.path.insert(0, REPO)
import handler  # noqa: E402
import scorecards  # noqa: E402


def load_excel_handler():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'excel_handler', os.path.join(REPO, 'lambda', 'excel-generator', 'handler.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fetch(evaluation_id):
    table = boto3.resource('dynamodb', region_name=REGION).Table(os.environ['DYNAMODB_TABLE'])
    items = table.query(KeyConditionExpression='evaluation_id = :e',
                        ExpressionAttributeValues={':e': evaluation_id},
                        ScanIndexForward=False, Limit=1)['Items']
    if not items:
        raise LookupError(f'{evaluation_id} not found')
    item = items[0]
    raw = boto3.client('s3', region_name=REGION).get_object(
        Bucket=os.environ['TRANSCRIPTS_BUCKET'], Key=item['transcript_s3_key'])
    return item, json.loads(raw['Body'].read())


def score(evaluation_id, version):
    item, tdata = fetch(evaluation_id)
    call_type = item['call_type']
    transcript, segments = tdata.get('transcript', ''), tdata.get('segments', [])
    speaker_count = len({s.get('speaker') for s in segments if s.get('speaker')})
    dur = int(max((s.get('end', 0) for s in segments), default=0)) or None
    rubric = handler.get_rubric(call_type, version)
    criteria, _, _ = handler.get_config(call_type, version)
    print(f'{evaluation_id}: {call_type}, {dur}s, scorecard v{version} ({scorecards.label(version)})')

    header = handler.call_bedrock_json(handler.build_header_prompt(transcript, call_type, dur, version), 'HEADER')
    profile = handler.call_bedrock_json(handler.build_profile_prompt(transcript, call_type), 'PROFILE')
    # Same scoring path as the Lambda: timestamped transcript, element by element.
    scoring_transcript = handler.build_timestamped_transcript(segments, transcript)
    items, failed, defaulted = handler.score_all_sections(
        scoring_transcript, segments, call_type, dur, profile, version)
    if failed or defaulted:
        print(f'WARNING: failed sections {failed}, defaulted {defaulted}')

    absence_rule = rubric.get('absence_rule')
    handler.recheck_no_items(scoring_transcript, segments, items, criteria, absence_rule=absence_rule)
    audit = handler.audit_evidence_relevance(scoring_transcript, segments, items, criteria,
                                             absence_rule=absence_rule)
    handler.enforce_required_utterances(transcript, items, call_type, version)
    red_flags = handler.extract_red_flags(transcript, segments, call_type, version)
    other_income = handler.has_other_income(profile)
    scoring = handler.calculate_scores({'items': items, '_duration_secs': dur}, call_type,
                                       other_income=other_income, version=version)
    order = {c['id']: n for n, c in enumerate(criteria)}
    items.sort(key=lambda i: order.get(i['id'], 999))
    return {
        'evaluation_id': evaluation_id, 'application_id': item.get('application_id'),
        'call_type': call_type, 'scorecard_version': version,
        'scorecard_label': scorecards.label(version), 'duration_seconds': dur,
        'speaker_count': speaker_count, 'transcript_chars': len(transcript),
        'evidence_audit': audit, 'red_flags': red_flags, 'other_income': other_income,
        'header': header, 'items': items, 'summary': {}, 'scoring': scoring,
        'evaluator_model': handler.BEDROCK_MODEL_ID,
        'evaluated_at': handler.datetime.utcnow().isoformat(),
    }


def write_excel(result, path):
    from io import BytesIO
    import openpyxl
    eh = load_excel_handler()
    version, call_type = result['scorecard_version'], result['call_type']
    rubric = scorecards.config(call_type, version)
    criteria, sp, sn = eh.get_config(call_type, version)
    derived = eh.compute_scores(result['items'], criteria, sp, sn)
    derived['total_score'] = float(result['scoring']['total_score'])
    derived['verdict'] = result['scoring']['verdict']
    derived['grade'] = result['scoring']['grade']
    wb = openpyxl.load_workbook(os.path.join(REPO, rubric['template_source']))
    eh.fill_scorecard(wb['Scorecard'], result, derived, criteria, call_type, rubric.get('e6_input'))
    eh.link_my_result_evidence(wb['My Result'], 'Scorecard')
    cached = eh.build_cached_values(wb['Scorecard'], result, derived, criteria)
    out = BytesIO()
    wb.save(out)
    data = eh.cache_formula_values(out.getvalue(), 'Scorecard', cached)
    with open(path, 'wb') as f:
        f.write(data)
    return derived.get('template_warnings', [])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('evaluation_ids', nargs='+')
    ap.add_argument('--version', default=None)
    ap.add_argument('--out', default=os.path.join(REPO, 'tests', 'output'))
    args = ap.parse_args()
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    version = args.version or scorecards.current_version()
    os.makedirs(args.out, exist_ok=True)
    for eid in args.evaluation_ids:
        result = score(eid, version)
        base = os.path.join(args.out, f'{eid}_v{version}')
        with open(base + '.json', 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False,
                      default=lambda o: float(o) if isinstance(o, Decimal) else str(o))
        warnings = write_excel(result, base + '.xlsx')
        s = result['scoring']
        print(f"==> {eid} v{version}: score {s['total_score']} | {s['verdict']}")
        for i in result['items']:
            print(f"    {i['id']:4} {i['score']:5} {(i.get('note') or '')[:110]}")
        if warnings:
            print('    TEMPLATE WARNINGS:', warnings)
        print(f'    wrote {base}.json / .xlsx')


if __name__ == '__main__':
    main()
