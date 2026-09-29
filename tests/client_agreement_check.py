"""
Agreement check against the client's own marked-up evaluations.

On 2026-09-17 the client reviewed three scorecards we produced from the
transcripts in Prodigee_new_template/ and returned item-by-item corrections
(BM FI Notes.docx, RCM Audio PD Notes.docx). Those corrections are the closest
thing we have to ground truth, so they are pinned here: every item below is one
the client said we marked wrongly, together with the mark they say is right.

Run after any change to the scoring prompts or post-passes:

    python tests/client_agreement_check.py            # both call types
    python tests/client_agreement_check.py BM         # just one

It prints agreement per call type and exits non-zero if agreement drops below
the threshold, so a "fix" that quietly undoes the client's corrections fails
loudly instead of shipping.

The corrections were made against the client's V2.0 scorecards, so this check
always scores on the legacy scorecard (version 0) — several of those criteria
were reworded or removed in V2.1. It still guards every prompt and post-pass
change, because legacy scoring shares all of that code.
"""
import io
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('TRANSCRIPTS_BUCKET', 'audio-pd-transcripts-dev')
os.environ.setdefault('REPORTS_BUCKET', 'audio-pd-reports-dev')
os.environ.setdefault('DYNAMODB_TABLE', 'audio-pd-evaluations-dev')
os.environ.setdefault('BEDROCK_MODEL_ID', 'apac.amazon.nova-pro-v1:0')
os.environ.setdefault('AWS_REGION', 'ap-south-1')
sys.path.insert(0, os.path.join(REPO, 'lambda', 'evaluation'))
import handler  # noqa: E402

# The client's corrections were made on the V2.0 (legacy) scorecards.
VERSION = '0'

# The client's "Should be" column. Only items they disagreed with are listed —
# every other item we already agreed on.
CASES = {
    'BM': {
        'call_type': 'BM_AUDIO_FI',
        'duration': 400,
        'expected_score_range': (25, 40),   # client: "corrected score is roughly 29-31"
        'marks': {
            'C2': 'Half',   # loan amount stated by customer himself at 03:13
            'D2': 'No',     # cited evidence was the same business, not another
            'D3': 'Half',   # brother's earnings were discussed
            'D4': 'Half',   # only acreage asked; no crops/income/KCC
            'E3': 'No',     # never asked - MUST item, leniency is dangerous
            'F1': 'Yes',
            'F2': 'Half',   # "originals with customer" never asked
            'F3': 'Half',   # we missed it: Five Star registry on the property
        },
    },
    'RCM': {
        'call_type': 'RCM_AUDIO_PD',
        'duration': 1080,
        'expected_score_range': (55, 72),   # client: "corrected score is approximately 62"
        'marks': {
            'A1': 'Half',   # recording notice never said - was given full marks
            'A4': 'Half',
            'B2': 'Half',   # BCM's name never asked
            'D4': 'Half',   # KCC never asked
            'E1': 'Yes',    # bureau WAS put to the customer - we were too harsh
            'E3': 'Half',
            'E4': 'Half',
            'G1': 'Half',   # which original papers never asked
            'H3': 'Half',   # "committed to NACH" was fabricated - NACH never mentioned
            'I1': 'Half',   # no indicative EMI was ever given
            'I2': 'No',     # not re-confirmed at close
        },
    },
}

# Below this, the client's corrections are no longer being honoured.
MIN_AGREEMENT = 0.70


def synth_segments(transcript, duration):
    """The client sent plain transcripts, so approximate segments for evidence."""
    parts = [p.strip() for p in transcript.split('।') if p.strip()]
    step = duration / max(len(parts), 1)
    return [{'start': i * step, 'end': (i + 1) * step, 'speaker': 'spk', 'text': p}
            for i, p in enumerate(parts)]


def evaluate(name, cfg):
    transcript = io.open(os.path.join(REPO, 'Prodigee_new_template', f'{name}.txt'),
                         encoding='utf-8').read()
    call_type, dur = cfg['call_type'], cfg['duration']
    segs = synth_segments(transcript, dur)

    profile = {}
    try:
        profile = handler.call_bedrock_json(
            handler.build_profile_prompt(transcript, call_type), 'profile')
    except Exception as e:
        print(f"  (profile failed: {e})")

    criteria, _, names = handler.get_config(call_type, VERSION)
    items = []
    for sec in handler.ordered_sections(criteria):
        sc = [c for c in criteria if c['sec'] == sec]
        try:
            d = handler.call_bedrock_json(
                handler.build_section_prompt(transcript, call_type, sec, sc,
                                             names[sec], dur, profile, VERSION), f'{name}-{sec}')
        except Exception as e:
            print(f"  section {sec} failed: {e}")
            continue
        ids = {c['id'] for c in sc}
        for i in d.get('items', []):
            if isinstance(i, dict) and i.get('id') in ids:
                i['score'] = handler.normalise_score(i.get('score'))
                q = (i.get('evidence') or '').strip()
                ev = handler.verify_evidence(q, segs) if q else {
                    'timestamp': '', 'verified': False, 'match': 'none', 'speaker': ''}
                i['evidence_timestamp'] = ev['timestamp']
                i['evidence_verified'] = ev['verified']
                items.append(i)

    handler.recheck_no_items(transcript, segs, items, criteria)
    handler.audit_evidence_relevance(transcript, segs, items, criteria)
    handler.enforce_required_utterances(transcript, items, call_type, VERSION)
    scoring = handler.calculate_scores(
        {'items': items, '_duration_secs': dur}, call_type,
        other_income=handler.has_other_income(profile), version=VERSION)
    flags = handler.extract_red_flags(transcript, segs, call_type, VERSION)
    return items, scoring, flags


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    wanted = sys.argv[1:] or list(CASES)
    failures = []

    for name in wanted:
        cfg = CASES[name]
        print('=' * 78)
        print(f"{name} ({cfg['call_type']}) — against the client's corrections")
        items, scoring, flags = evaluate(name, cfg)
        marks = {i['id']: i['score'] for i in items}

        agreed = 0
        for cid, want in cfg['marks'].items():
            got = marks.get(cid, '(missing)')
            hit = got == want
            agreed += hit
            print(f"  {cid:<5} client={want:<6} ours={got:<10} {'OK' if hit else 'differs'}")
        rate = agreed / len(cfg['marks'])
        score = float(scoring['total_score'])
        lo, hi = cfg['expected_score_range']
        print(f"  agreement {agreed}/{len(cfg['marks'])} ({rate:.0%}) | "
              f"score {score} (client range {lo}-{hi}) | red flags {[f['code'] for f in flags]}")

        if rate < MIN_AGREEMENT:
            failures.append(f"{name}: agreement {rate:.0%} below {MIN_AGREEMENT:.0%}")
        if not (lo <= score <= hi):
            failures.append(f"{name}: score {score} outside the client's corrected range {lo}-{hi}")

    print('=' * 78)
    if failures:
        for f in failures:
            print('FAIL', f)
        return 1
    print('Client agreement holds.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
