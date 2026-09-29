# Prodigee Audio PD Evaluation System

Automated evaluation of BCM Physical PD self-audio recordings against the Prodigee LAP Credit Policy rubric. Produces a filled Excel scorecard, call summary, and gap coaching report for every recording.

## Architecture

```
Audio Upload (Frontend)
    ↓
Ingestion Lambda        — receives upload, stores to S3, creates DynamoDB record
    ↓
Transcription Lambda    — AWS Transcribe → transcript JSON stored to S3
    ↓
Evaluation Lambda       — 2x Bedrock (Claude 3.5 Sonnet) calls:
                            Call 1: 30-criteria scoring + header extraction
                            Call 2: Call summary (income/obligations/family/facts)
    ↓
Excel Generator Lambda  — fills Prodigee_Template.xlsx with computed values
                          (no formula reliance — all values pre-computed in Python)
    ↓
API Lambda              — REST endpoints for frontend (status, download, history)
```

## Repository Structure

```
.
├── lambda/
│   ├── evaluation/         # AI evaluation against 30-criteria rubric
│   │   ├── handler.py      # Two-call Bedrock approach (criteria + call summary)
│   │   ├── requirements.txt
│   │   ├── deployment-package.zip
│   │   └── package/        # boto3 dependencies
│   │
│   ├── excel-generator/    # Fills Excel template with evaluation results
│   │   ├── handler.py      # Computes all values in Python, no formula reliance
│   │   ├── requirements.txt
│   │   ├── deployment-package.zip
│   │   └── package/        # openpyxl + boto3 dependencies
│   │
│   ├── ingestion/          # Audio upload handler
│   │   ├── handler.py
│   │   └── requirements.txt
│   │
│   ├── preprocessing/      # Audio preprocessing (format normalisation)
│   │   ├── handler.py
│   │   └── requirements.txt
│   │
│   ├── transcription/      # AWS Transcribe integration
│   │   ├── handler.py
│   │   └── requirements.txt
│   │
│   └── api/                # REST API handler
│       ├── handler.py
│       └── requirements.txt
│
├── frontend/               # React + Vite frontend
│   ├── src/
│   │   ├── App.tsx
│   │   ├── pages/
│   │   ├── components/
│   │   ├── services/
│   │   └── config/
│   ├── package.json
│   └── vite.config.ts
│
├── terraform/              # AWS infrastructure as code
│   ├── main.tf
│   ├── variables.tf
│   ├── outputs.tf
│   └── modules/
│
├── config/
│   └── rcm-audio-pd-rubric.json   # Rubric config (used by Terraform for S3 upload)
│
├── evaluation-agent/
│   └── system-prompt.txt          # AI agent system prompt (reference)
│
├── sample-evaluations/            # Reference evaluation examples
│
├── .vscode/template/
│   └── Prodigee_Template.xlsx     # Excel template (source of truth)
│
└── create_excel_package.py        # Build script for Excel generator Lambda zip
```

## Key Design Decisions

**Two-call Bedrock approach** — Claude 3.5 Sonnet has an 8,192 token output limit. The evaluation is split into:
- Call 1: All 30 criteria scoring + header info (~4,000–5,000 tokens)
- Call 2: Full call summary with income/obligations/family tables (~3,000–4,000 tokens)

**No formula reliance in Excel** — openpyxl saves formulas as strings but doesn't evaluate them. All values (points, section subtotals, total score, grade band, verdict, repayment snapshot, gap report) are computed in Python and written as plain values.

**Versioned scorecards** — criteria, points, MUST flags and scoring rules are not hardcoded: they come from `scorecards/` (see [scorecards/README.md](scorecards/README.md)). The current version is **v1** (client V2.1 Simple, `templates_version_1.0/`). Every evaluation stores its `scorecard_version`, the UI shows it, and the Excel/PDF is written into that version's template — older evaluations stay on the Legacy (V2.0) scorecard.

## Testing — run these before every deploy

```bash
# Fast, offline, free — no AWS calls. Run on every change.
python tests/test_safety_nets.py

# Agreement against the client's own marked-up corrections (the closest thing
# to ground truth we have). Fails if their corrections stop being honoured.
python tests/client_agreement_check.py

# Real classification against pinned production transcripts (~10 Bedrock calls)
python tests/regression_check.py

# Also re-scores every section end to end (~60 Bedrock calls, slower/costlier)
python tests/regression_check.py --full

# Score a stored recording on any scorecard version, locally (writes nothing to
# AWS) — produces tests/output/<id>_v<N>.json and a filled .xlsx
python tests/score_local.py eval-6ad7a505f0f4 --version 1
```

`client_agreement_check.py` pins the client's 2026-09-17 corrections, which
were made on the V2.0 scorecards, so it always scores on the Legacy version.

## What the client's 2026-09-17 review changed

They marked up three of our scorecards item by item (`Prodigee_new_template/`).
Their verdicts and arithmetic were right; the item-level marking was not. Every
point below is a change made in response, and `tests/client_agreement_check.py`
pins their corrections so none of it can silently regress.

**BCM submissions must be self-audio, and are now refused if they are not.**
> "The Transcript is not a Monologue. It is rather an interview with the
> customer and hence the BCM Evaluation must not be done as it was not a
> Self-Audio Recording."

The BCM checklist is written as *what to say in the recording* — what you SAW,
your view, the photos you took — and none of it can be marked against a
recording of the officer interviewing the customer. `check_bcm_self_audio()`
now runs before scoring a BCM submission; an interview is returned as
**NOT EVALUABLE** with an explanation and no scorecard, instead of a number
that looks authoritative and means nothing. Verified both ways: it passes a
genuine first-person narration and refuses all three client transcripts.

**Evidence must be about the thing it is evidencing.** Their central finding was
that evidence was "matched by keyword proximity, not by meaning — the same
[00:00] business-intro segment is cited as evidence for C2, D1, D3 and G1".
`audit_evidence_relevance()` now re-checks every credited item's quote against
its own criterion, replaces the quote when a better one exists, and withdraws
the marks when the recording contains nothing that evidences it.

**Facts that were never said can no longer be awarded.** They caught a full
mark for a recording-notice that was never spoken and a note reading
"committed to NACH auto-debit" on a call where NACH is never mentioned. Where a
criterion hinges on a specific utterance, `REQUIRED_UTTERANCES` settles it in
code — the phrase is in the transcript or it is not.

Coverage is **27 rules across all three rubrics** (BCM 9, BM 8, RCM 10) — every
criterion that turns on a specific named thing: the recording notice, login fee,
commission/agent, mortgage, KCC, AA check, neighbours, photos, stock count,
document types, NACH, Clean Track Reward, dispute/charge, next steps, and the
forward/approve/reject decision. Two design rules make this safe:

- **Caps only ever lower a mark**, to `Half` where the utterance is one element
  among several (so the officer keeps credit for what he did cover) and to `No`
  only where the criterion is entirely about the missing thing.
- **Phrases are the shortest distinctive stem, not the full word.** Transcribe
  mangles Hindi — the RCM call renders "गिरवी" (mortgaged) as "गिरी", and
  matching the full word would have invented a failure on a criterion the client
  had accepted. Over-matching merely leaves the decision to the model;
  under-matching fabricates a failure, so every list errs loose. The test suite
  asserts both directions: a transcript containing every required utterance must
  cap nothing, and an empty one must fire every rule.

**No claiming checks we cannot perform.** The evaluator was asserting things
were "within 10% of CAM" and "consistent with the PD Report" while holding only
a transcript. It is now told it has no CAM, bureau, AA or PRAGATI, and must
write "CAM/bureau not available — not verified" instead of inventing the
comparison.

**Red flags have a cell now.** > "the evaluator only fills what has a cell, and
right now red flags have no cell." `extract_red_flags()` runs as its own pass
against the client's RF taxonomy and writes a **RED FLAGS OBSERVED** block into
the Scorecard with the RF code, what was observed, the timestamp and the exact
words. Every flag needs a quote that resolves to real audio, so a flag without
evidence is dropped rather than alleged.

**The scorecard is a live spreadsheet again.** We had been writing computed
values over all 76 Scorecard formulas and 215 My Result formulas, and writing
header values on top of the C-column labels (`D4` resolves to the merged anchor
`C4`). `safe_set()` now refuses to overwrite any formula, values go to the
merged `E:G` block where they belong, and only the cells a human evaluator
would fill are touched: `B4:B7`, `E4`, `E6`, `E7`, and `E`/`G`/`I` per item.
`E5` (uploaded to PRAGATI the same day) is deliberately left blank — it cannot
be known from the audio, and asserting it was one of the flagged mistakes.

**Scoring matches the template exactly**, because the client recomputes it live
when they open the sheet: applicable is 100 (BCM: `100-IF(E6="No",10,0)`),
SCORE is `ROUND(scored/applicable*100, 0)`, and a MUST item counts as missed
unless it is exactly "Yes". Their sheet has only Yes / Half / No — an "N/A"
there would score zero *and* count against the total — so N/A was removed from
the scoring vocabulary entirely.

**The score is readable without recalculating.** A formula cell written by
openpyxl carries no cached result: desktop Excel recalculates on open, but
every preview pane, browser viewer and programmatic reader shows a blank score.
`cache_formula_values()` injects the values we already computed as cached
results while leaving the formulas in place, so the sheet reads correctly
everywhere *and* still recalculates the moment anyone edits a mark.

**A failed evidence audit steps a mark down one level, not to zero.** Their
review kept marks whose evidence was mis-cited but whose substance was real
("Mark OK, but evidence and note are wrong"), so wiping a mark over a citation
fault overshoots — and a full Yes→No swing on a single model opinion was the
largest source of run-to-run movement (the same BM recording scored 36 and 22
on consecutive runs). After the change, three consecutive runs of each client
recording: RCM 59/62/62 against their corrected **62**, BM 33/30/28 against
their corrected **29-31**.

Note the totals are considerably steadier than any individual item: item-level
agreement with their corrections moves between roughly 55% and 82% run to run
while the total stays within a few points, because individual flips offset each
other. Treat a single item's mark as indicative and the total as reliable.

Agreement on the items they corrected: **RCM 9/11 with a total of 62.0 against
their corrected 62**, **BM 6/8 within their 29-31 range**. The handful that
still differ (BM D2/F1, RCM E1/I2) are genuine judgement calls, not the
fabrication and mis-evidencing they flagged.

`tests/regression_check.py` pins real recordings with their verified call types
and fails if any is misclassified or if scoring collapses to all-zero. Both of
the bugs that reached production — every criterion coming back "No", and BCM
field visits being classified as BM — would have been caught by it. Add a case
whenever a new recording is misjudged.

## Call type is chosen by the user, not detected

The uploader picks BCM / BM / RCM **before** selecting the recording, and the
evaluation is scored against exactly that rubric. `classify_call_type()`
short-circuits on a caller-supplied type and makes no model call at all —
the stored `call_type_confidence` reads `user-selected`.

The LLM classifier and the older keyword heuristic are both still in the code
and still covered by tests, used only when a caller passes `AUTO_DETECT`
(e.g. a direct API call). They are no longer on the UI path.

## How scoring avoids being unfairly harsh

Scores were badly deflated by the evaluator treating every partially-covered
criterion as a zero. Each criterion lists 4-6 elements, and covering two of
them is "Half", not "No". Three things now guard against that:

1. **Explicit Yes / Half / No guidance with worked examples** in the section
   prompt, balanced in both directions — Half is for genuinely partial
   coverage, and a criterion whose substance was covered with real detail gets
   Yes even if a minor sub-element is missing.
2. **A recall pass over everything marked "No"** (`recheck_no_items`). It asks
   the much narrower question "did this subject come up at all?" and upgrades
   to Half — but *only* when it returns a quote that verifies against the
   diarised segments, so nothing can be talked up without real words behind it.
   Upgrades are capped at Half and marked `upgraded_on_recheck`.
3. **Applicability-based N/A** (`apply_applicability`) for criteria that cannot
   apply — scoped deliberately to BCM section C, the only section the rubric
   itself marks "(N/A if none)". Section B (Business) is *not* included: this
   is a Business LAP product, so the officer is expected to document the
   business situation either way, and auto-excusing it would inflate scores and
   hide real coverage gaps.

On the six pinned recordings this moved scores from 8.5-38 to 24-39.5 without
loosening the rubric. Remaining low scores are genuine: these recordings really
do skip photos, neighbour checks, stock counts and time tracking.

## Reliability behaviour
- **Scoring is one Bedrock call per rubric section.** Do not "optimise" this
  back into a single all-criteria prompt — that is what made the model give up
  and return "No" for everything.
- **Every model call is parsed via `call_bedrock_json`**, which retries before
  giving up, so a transient failure no longer silently scores a section zero.
- **Runs that can't be trusted are flagged**, not hidden: `needs_review` plus
  `review_reasons` are written to DynamoDB and the result JSON, shown in the UI,
  and appended to the RESULT cell of the Excel scorecard. Triggers include a
  failed/defaulted section, low call-type confidence, and nothing scoring above
  zero.
- **Silent/unintelligible audio fails fast** (`MIN_TRANSCRIPT_CHARS`) instead of
  producing a meaningless 0/100 that looks like a real verdict.
- **Raw model outputs** are saved to
  `s3://<reports>/evaluations/<id>/raw-model-outputs.json` so a disputed
  scorecard can be debugged without re-running the pipeline.

## Evidence trail — why the scores are auditable

Every criterion scored Yes or Half carries the timestamp and the exact words
from the recording that earned those marks, written to column **I** of the
Scorecard sheet and column **G** of My Result. A reviewer can jump to that point
in the audio and hear it.

**The timestamp is computed, not generated.** The model is asked only to copy
the exact words it scored on, and is explicitly told not to output a timestamp.
`verify_evidence()` then locates those words in the diarised Transcribe
segments and reads off the real start time. A model cannot fabricate a
timestamp it is never asked for, and a quote that cannot be found in the
recording is written to the sheet as `[time not located] ... verify manually`
rather than being dressed up with a plausible number.

This matters because the reverse approach was already failing silently: the
call-summary prompt asked for `[MM:SS]` against every figure while being handed
a transcript containing no timing at all, so those stamps were invented. It is
now given a genuinely timestamped transcript.

Two consequences worth knowing:

- **Scores skew slightly lower.** The evaluator is told that if it cannot quote
  real words for a criterion, it must score it No. Points that were previously
  awarded on a vague impression are no longer awarded — which is the point.
- **Verification proves the words were said at that time, not that they satisfy
  the criterion.** Judging whether the quote actually justifies the mark is the
  reviewer's job; the trail exists to make that judgement possible.

`evidence_stats` on each result records how many credited criteria were traced,
and an evaluation where more than 40% could not be traced is flagged
`needs_review`. `tests/regression_check.py --full` fails if coverage across the
pinned recordings drops below 70%.

## Deployment

```powershell
# Both packages bundle the scorecard registry (scorecards/)
python create_excel_package.py
aws lambda update-function-code --function-name audio-pd-excel-generator-dev --zip-file fileb://lambda/excel-generator/deployment-package.zip

python create_evaluation_package.py
aws lambda update-function-code --function-name audio-pd-evaluation-dev --zip-file fileb://lambda/evaluation/deployment-package.zip

# Scorecard templates: each version's S3 keys are listed in scorecards/registry.json
aws s3 cp templates_version_1.0/BCM_PD_Audio_Scorecard_V2.1_Simple.xlsx s3://audio-pd-reports-dev/scorecards/v1/bcm_pd_scorecard.xlsx
```

## Environment Variables

| Lambda | Variable | Value |
|--------|----------|-------|
| evaluation | `TRANSCRIPTS_BUCKET` | `audio-pd-transcripts-dev` |
| evaluation | `REPORTS_BUCKET` | `audio-pd-reports-dev` |
| evaluation | `DYNAMODB_TABLE` | `audio-pd-evaluations-dev` |
| evaluation | `BEDROCK_MODEL_ID` | `apac.anthropic.claude-3-5-sonnet-20240620-v1:0` |
| evaluation | `EXCEL_GENERATOR_LAMBDA_ARN` | ARN of excel-generator Lambda |
| evaluation | `SCORECARD_VERSION` | optional — pin a scorecard version (default: `current` in `scorecards/registry.json`) |
| excel-generator | `REPORTS_BUCKET` | `audio-pd-reports-dev` |
| excel-generator | `TEMPLATE_KEY` | `templates/prodegee_template.xlsx` |
| excel-generator | `DYNAMODB_TABLE` | `audio-pd-evaluations-dev` |
