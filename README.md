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

**Criteria hardcoded in evaluation handler** — the 30 criteria with exact max_points match `Prodigee_Template.xlsx` Scorecard rows 11-48. No runtime Excel parsing needed.

## Deployment

```powershell
# Excel Generator (has openpyxl dependency)
python create_excel_package.py
aws lambda update-function-code --function-name audio-pd-excel-generator-dev --zip-file fileb://lambda/excel-generator/deployment-package.zip

# Evaluation Lambda (boto3 only — Lambda runtime provides it)
cd lambda/evaluation
python -c "import zipfile; z=zipfile.ZipFile('deployment-package.zip','w',zipfile.ZIP_DEFLATED); z.write('handler.py'); z.close()"
aws lambda update-function-code --function-name audio-pd-evaluation-dev --zip-file fileb://deployment-package.zip
```

## Environment Variables

| Lambda | Variable | Value |
|--------|----------|-------|
| evaluation | `TRANSCRIPTS_BUCKET` | `audio-pd-transcripts-dev` |
| evaluation | `REPORTS_BUCKET` | `audio-pd-reports-dev` |
| evaluation | `DYNAMODB_TABLE` | `audio-pd-evaluations-dev` |
| evaluation | `BEDROCK_MODEL_ID` | `apac.anthropic.claude-3-5-sonnet-20240620-v1:0` |
| evaluation | `EXCEL_GENERATOR_LAMBDA_ARN` | ARN of excel-generator Lambda |
| excel-generator | `REPORTS_BUCKET` | `audio-pd-reports-dev` |
| excel-generator | `TEMPLATE_KEY` | `templates/prodegee_template.xlsx` |
| excel-generator | `DYNAMODB_TABLE` | `audio-pd-evaluations-dev` |
