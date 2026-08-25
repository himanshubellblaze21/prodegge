"""
Excel-Template-Based Evaluation Handler
Criteria list exactly matches the Prodigee_Template.xlsx Scorecard sheet.
"""
import json
import boto3
from botocore.config import Config
import os
from datetime import datetime
from decimal import Decimal
from typing import Dict, Any, List

# Custom JSON encoder for Decimal types
class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super(DecimalEncoder, self).default(obj)

# Configure boto3 with extended timeouts for Bedrock
bedrock_config = Config(
    read_timeout=300,
    connect_timeout=60,
    retries={'max_attempts': 3, 'mode': 'adaptive'}
)

# Initialize AWS clients
bedrock_runtime = boto3.client(
    'bedrock-runtime',
    region_name=os.environ.get('AWS_REGION', 'ap-south-1'),
    config=bedrock_config
)
s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')

# Environment variables
TRANSCRIPTS_BUCKET = os.environ['TRANSCRIPTS_BUCKET']
REPORTS_BUCKET = os.environ['REPORTS_BUCKET']
DYNAMODB_TABLE = os.environ['DYNAMODB_TABLE']
BEDROCK_MODEL_ID = os.environ.get('BEDROCK_MODEL_ID', 'apac.anthropic.claude-3-5-sonnet-20240620-v1:0')

# ─────────────────────────────────────────────────────────────────────────────
# CRITERIA — exactly matching Prodigee_Template.xlsx Scorecard (rows 11-48)
# Section / ID / criterion text / max_points / critical
# ─────────────────────────────────────────────────────────────────────────────
TEMPLATE_CRITERIA = [
    # SECTION A — Recording Setup & Visit Identification
    {'id': 'A1', 'section': 'A', 'criterion': 'Case identified on record',
     'checklist': 'Application ID, customer name, village/town, product, loan amount applied — stated at the start. Confirm pre-visit preparation: CRIF/CIBIL bureau report, CPA remarks and BPS score reviewed on PRAGATI before the visit.',
     'max_points': 2, 'critical': False},
    {'id': 'A2', 'section': 'A', 'criterion': 'All visits confirmed — when and whom',
     'checklist': 'Date/time of each visit (business(es), residence, collateral — or same site), who was met at each, who accompanied. Borrower met personally at business.',
     'max_points': 2, 'critical': True},
    {'id': 'A3', 'section': 'A', 'criterion': 'Photo/document capture confirmed',
     'checklist': 'Geo-tagged photos — business premises, QR codes, hoarding/signboard, stock, bills — business-neighbour signboards, residence-neighbour contacts, dairy selfie, agri photos/GPS video (if agri >Rs.10k/mo) — confirmed taken & uploaded.',
     'max_points': 2, 'critical': False},
    {'id': 'A4', 'section': 'A', 'criterion': 'Same-day recording, correct duration',
     'checklist': 'Recorded same day after all visits; 15–30 minutes total.',
     'max_points': 2, 'critical': True},

    # SECTION B — Business Verification
    {'id': 'B1', 'section': 'B', 'criterion': 'Every business visited and described as seen',
     'checklist': 'For EACH declared income business — premises, stock, staff, foot traffic, equipment, signboard — concrete observations; each assessed with documentary evidence and photographed.',
     'max_points': 5, 'critical': True},
    {'id': 'B2', 'section': 'B', 'criterion': 'Borrower met at business; operations explained on site',
     'checklist': 'Borrower personally showed the business; day-to-day account summarised.',
     'max_points': 4, 'critical': True},
    {'id': 'B3', 'section': 'B', 'criterion': 'Income assessed per stream with arithmetic',
     'checklist': 'Daily/monthly sales, ANNUAL turnover, margin/profit %, seasonality, assessed monthly income per stream — profitability calculated; PRAGATI-model arithmetic aloud; totals stated.',
     'max_points': 4, 'critical': False},
    {'id': 'B4', 'section': 'B', 'criterion': 'Income documents asked and examined',
     'checklist': 'Receipts, sales/purchase bills, kata parchi, UPI/banking, dairy passbook — asked, produced, photographed; absence stated honestly. AA completeness confirmed.',
     'max_points': 4, 'critical': False},
    {'id': 'B5', 'section': 'B', 'criterion': 'Business-neighbour enquiry narrated',
     'checklist': '2+ neighbouring businesses spoken to — names/contacts, signboards photographed; substance of what they said.',
     'max_points': 4, 'critical': True},
    {'id': 'B6', 'section': 'B', 'criterion': 'Genuineness view — no staged setup',
     'checklist': 'Explicit view per stream that the setup is genuine (own stock, truly operating) — or suspicion stated and escalated (PD-RF-1).',
     'max_points': 3, 'critical': True},

    # SECTION C — Agricultural Income Verification
    {'id': 'C1', 'section': 'C', 'criterion': 'Land visited; ownership and acreage verified',
     'checklist': 'Land seen (or GPS video if distant & agri income >Rs.10k/mo); owner named, checked on Bhulekh/SatSure; acreage stated. Leased-in land ≠ agri income.',
     'max_points': 4, 'critical': False},
    {'id': 'C2', 'section': 'C', 'criterion': 'Crops and yield verified with proof',
     'checklist': 'Crops per season, what is standing/sown; mandi receipts (last 4 seasons avg) or sale proof examined; assessed agri income per model.',
     'max_points': 3, 'critical': False},
    {'id': 'C3', 'section': 'C', 'criterion': 'Cultivation genuineness + KCC noted',
     'checklist': 'Actually cultivating? KCC limit/obligation noted. False/overstated agri claim = PD-RF-5.',
     'max_points': 3, 'critical': False},

    # SECTION D — Obligations & Family Expenses
    {'id': 'D1', 'section': 'D', 'criterion': 'All obligations enumerated with amounts',
     'checklist': 'Existing EMIs of borrower AND co-borrowers — every loan in CRIF/CIBIL — MFI/SHG/JLG, KCC/society/PACS, gold, third-party and market/hand loans — lender + monthly amount each; justification for any default/overdue.',
     'max_points': 4, 'critical': True},
    {'id': 'D2', 'section': 'D', 'criterion': 'Family expenses and lifestyle assessed',
     'checklist': 'Family size, dependents, Rs.2,000–3,000/member norm, living standard (ELSI assets) consistent with claimed income.',
     'max_points': 3, 'critical': False},
    {'id': 'D3', 'section': 'D', 'criterion': 'Surplus arithmetic narrated',
     'checklist': 'Assessed income − expenses − existing EMIs = surplus vs proposed EMI; disposable floor checked aloud.',
     'max_points': 3, 'critical': False},
    {'id': 'D4', 'section': 'D', 'criterion': 'Recent loan enquiries (last 3 months) probed with outcomes',
     'checklist': 'Loan applications/enquiries of borrower & co-borrowers in LAST 3 MONTHS asked and narrated: lender, amount, purpose — and OUTCOME of each (approved/rejected/pending); cross-checked with bureau enquiry list.',
     'max_points': 2, 'critical': False},

    # SECTION E — Residence Visit, Family & Co-Borrower
    {'id': 'E1', 'section': 'E', 'criterion': 'Residence visited and described',
     'checklist': 'Ownership, years at address, construction/condition, household assets seen; utility bills/Patta cross-checked. Residence photos + ELSI asset captures taken and uploaded.',
     'max_points': 4, 'critical': False},
    {'id': 'E2', 'section': 'E', 'criterion': 'Family and co-borrower interviewed',
     'checklist': 'Each person spoken to named + substance: loan awareness, amount, end use, repayment intent. Co-borrower (wife/mother) MANDATORILY met in person and aware the collateral will be mortgaged. Samagra ID and KYC checked.',
     'max_points': 5, 'critical': True},
    {'id': 'E3', 'section': 'E', 'criterion': 'Residence-neighbour enquiry narrated',
     'checklist': '2+ residence neighbours — names/contacts; reputation, conduct, any adverse information.',
     'max_points': 4, 'critical': True},
    {'id': 'E4', 'section': 'E', 'criterion': 'Customer education confirmed',
     'checklist': 'Confirmed told: Business LAP with mortgage, indicative EMI, Clean Track Reward (last 3 EMIs waived), ZERO COMMISSION policy — Prodigee takes no commission.',
     'max_points': 3, 'critical': False},

    # SECTION F — Collateral Verification
    {'id': 'F1', 'section': 'F', 'criterion': 'Collateral visited and physically described',
     'checklist': 'Type, self-occupied/rented, construction quality, approx age (20-yr rule), carpet area vs 400/100 sq.ft, independent access, kitchen/toilet.',
     'max_points': 4, 'critical': False},
    {'id': 'F2', 'section': 'F', 'criterion': 'Original papers seen; owner and title chain',
     'checklist': 'Original title deeds sighted (or exact status), owner named, acquisition story, mutation, heirs/NOC; document type (Sale Deed/Patta; 1/2-pager not allowed; 5-pager Rs.10L cap).',
     'max_points': 4, 'critical': True},
    {'id': 'F3', 'section': 'F', 'criterion': 'Approximate market value with basis',
     'checklist': 'Value estimate with basis (local rates, transactions, neighbour input); LTV sense; distress-value view (80% cap).',
     'max_points': 3, 'critical': False},
    {'id': 'F4', 'section': 'F', 'criterion': 'Risk screen stated',
     'checklist': 'Lien-free/no-dispute, not ST/tribal land, not near HT line/riverbank/encroachment, no negative zone, no demolition risk. Issue = PD-RF-6.',
     'max_points': 3, 'critical': False},

    # SECTION G — End Use & Overall Assessment
    {'id': 'G1', 'section': 'G', 'criterion': 'End use verified on ground',
     'checklist': 'Stated purpose vs ground reality; required loan amount enquired and utilisation questioned thoroughly — how much, where, when; not a restricted use.',
     'max_points': 3, 'critical': False},
    {'id': 'G2', 'section': 'G', 'criterion': 'Overall credit view',
     'checklist': 'Total assessed income vs loan sought; capacity AND intent view; every red flag found — or explicit statement that none was found.',
     'max_points': 4, 'critical': False},
    {'id': 'G3', 'section': 'G', 'criterion': 'Clear recommendation with reasons',
     'checklist': 'Recommend / recommend-with-conditions / reject — with specific reasons for the PD Report and CAM. A reasoned negative recommendation scores Full. Borrower told: decision will be taken soon.',
     'max_points': 3, 'critical': False},

    # SECTION H — Narration Quality
    {'id': 'H1', 'section': 'H', 'criterion': 'Structured and specific',
     'checklist': 'Section order followed; names, numbers, observations — no vague filler.',
     'max_points': 2, 'critical': False},
    {'id': 'H2', 'section': 'H', 'criterion': 'Duration and audibility',
     'checklist': '15–30 minutes, single continuous recording, ≥90% intelligible.',
     'max_points': 2, 'critical': True},
    {'id': 'H3', 'section': 'H', 'criterion': 'Honest flagging of gaps',
     'checklist': 'Facts vs opinion separated; what could not be verified stated as such; discrepancies flagged.',
     'max_points': 2, 'critical': False},
]

# Gate mapping: which criterion drives which knockout gate
GATE_MAP = {
    'A4': 'K1',   # Same-day recording, correct duration → K1
    'B2': 'K2',   # Borrower met at business → K2
    'B1': 'K3',   # Every business visited → K3
    'E2': 'K4',   # Family and co-borrower interviewed → K4
    'B5': 'K5',   # Business-neighbour enquiry → K5 (also E3)
    'E3': 'K5',   # Residence-neighbour enquiry → K5
    'B6': 'K6',   # Genuineness view → K6
    'F2': 'K7',   # Original papers seen → K7
    'A3': 'K8',   # Photo/document capture confirmed → K8
}


def _call_bedrock(prompt: str, label: str) -> str:
    """
    Single Bedrock call. max_tokens=8192 (Claude 3.5 Sonnet hard limit).
    Raises if stop_reason is max_tokens (truncated response).
    """
    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 8192,
        "temperature": 0.0,
        "messages": [{"role": "user", "content": prompt}]
    }

    print(f"[{label}] Calling Bedrock — prompt {len(prompt)} chars")
    response = bedrock_runtime.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=json.dumps(request_body)
    )
    body = json.loads(response['body'].read())
    stop_reason = body.get('stop_reason', 'unknown')
    text = body['content'][0]['text']
    print(f"[{label}] stop_reason={stop_reason}, output={len(text)} chars")

    if stop_reason == 'max_tokens':
        raise Exception(f"[{label}] Bedrock hit max_tokens — response truncated. Reduce prompt or split further.")

    # Strip markdown fences
    t = text.strip()
    if t.startswith('```'):
        lines = t.split('\n')
        start = next((i for i, l in enumerate(lines) if l.strip().startswith('{')), 0)
        end = next((i for i in range(len(lines)-1, -1, -1) if lines[i].strip().endswith('}')), len(lines)-1)
        t = '\n'.join(lines[start:end+1])
    return t


def _build_criteria_prompt(transcript: str, case_context: Dict[str, Any]) -> str:
    """
    CALL 1 — Criteria scoring only.
    Returns header_info + all 30 criteria with coverage/points/evidence/flag.
    Kept lean so the response stays under 8192 tokens.
    """
    n = len(TEMPLATE_CRITERIA)

    # Compact criteria list — ID, criterion name, max pts, critical, checklist
    criteria_lines = []
    current_section = None
    section_names = {
        'A': 'SECTION A — RECORDING SETUP & VISIT IDENTIFICATION',
        'B': 'SECTION B — BUSINESS VERIFICATION',
        'C': 'SECTION C — AGRICULTURAL INCOME VERIFICATION',
        'D': 'SECTION D — OBLIGATIONS & FAMILY EXPENSES',
        'E': 'SECTION E — RESIDENCE VISIT, FAMILY & CO-BORROWER',
        'F': 'SECTION F — COLLATERAL VERIFICATION',
        'G': 'SECTION G — END USE & OVERALL ASSESSMENT',
        'H': 'SECTION H — NARRATION QUALITY',
    }
    for c in TEMPLATE_CRITERIA:
        if c['section'] != current_section:
            current_section = c['section']
            criteria_lines.append(f"\n## {section_names[current_section]}")
        tag = " [CRITICAL→gate]" if c['critical'] else ""
        criteria_lines.append(
            f"{c['id']} ({c['max_points']}pt){tag}: {c['criterion']}\n"
            f"   Checklist: {c['checklist']}"
        )
    criteria_block = "\n".join(criteria_lines)

    context_str = json.dumps(case_context, indent=2) if case_context else "None provided."

    return f"""You are the Audio PD Evaluation Agent for Prodigee Finance Limited.
Evaluate a BCM Physical PD self-audio transcript against exactly {n} criteria.
DO NOT compute totals, grade bands, or verdicts — only per-criterion scoring.

## COVERAGE RULES
Full = completely narrated with specifics (names, numbers) → max_points
Partial = mentioned but vague/incomplete → max_points × 0.5
None = not covered → 0
N/A = genuinely inapplicable (e.g. agri section when no agri income) → excluded from denominator

## GATE MAPPING (populate "flag" field for critical failures — do NOT state PASS/FAIL)
K1→A4  K2→B2  K3→B1  K4→E2  K5→B5+E3  K6→B6  K7→F2  K8→A3

## RULES
- Evidence from transcript only — no inference
- Garbled audio → "None" + note "Transcript unclear at [timestamp]"
- Timestamps: [MM:SS–MM:SS]
- Return ALL {n} criteria in rubric order (A1→H3)

## CRITERIA
{criteria_block}

## CASE CONTEXT (reference only)
{context_str}

## TRANSCRIPT
{transcript}

## OUTPUT — valid JSON only, no markdown fences
{{
  "call_type_detected": "BCM_PHYSICAL_PD",
  "evaluator_notes": "<transcript quality observations>",
  "header_info": {{
    "application_id_mentioned": "<stated ID or 'Stated but digits inaudible — take from PRAGATI' or 'N/A'>",
    "customer_name_mentioned": "<name as spoken>",
    "bcm_rcm_name_mentioned": "<BCM name as spoken>",
    "loan_amount_mentioned": "<amount as spoken>",
    "duration_mentioned": "<duration as spoken or 'N/A'>"
  }},
  "criteria": [
    {{
      "id": "A1",
      "coverage": "Full|Partial|None|N/A",
      "points_scored": <number>,
      "max_points": 2,
      "evidence": "<[MM:SS–MM:SS] paraphrase or MISSING: what is absent>",
      "flag": "<K1-K8 or null>"
    }}
    // ALL {n} criteria — A1 A2 A3 A4 B1 B2 B3 B4 B5 B6 C1 C2 C3 D1 D2 D3 D4 E1 E2 E3 E4 F1 F2 F3 F4 G1 G2 G3 H1 H2 H3
  ]
}}
Return complete JSON only. Include all {n} criteria."""


def _build_summary_prompt(transcript: str, criteria_result: Dict[str, Any],
                          case_context: Dict[str, Any]) -> str:
    """
    CALL 2 — Call Summary extraction only.
    Uses the criteria result from Call 1 as context (so it can reference coverage outcomes).
    """
    # Pass a compact version of criteria results (just id+coverage+evidence)
    compact_criteria = [
        {"id": c["id"], "coverage": c["coverage"], "evidence": c.get("evidence", "")[:120]}
        for c in criteria_result.get("criteria", [])
    ]
    header = criteria_result.get("header_info", {})
    context_str = json.dumps(case_context, indent=2) if case_context else "None provided."

    return f"""You are the Audio PD Evaluation Agent for Prodigee Finance Limited.
Based on the transcript and the already-completed criteria scoring below,
extract a comprehensive Call Summary for management review.

## HEADER (already extracted from criteria evaluation)
{json.dumps(header, indent=2)}

## CRITERIA SCORING SUMMARY (for reference)
{json.dumps(compact_criteria, indent=2)}

## CASE CONTEXT
{context_str}

## TRANSCRIPT
{transcript}

## EXTRACTION RULES
- Every figure must be SPOKEN on the recording — include timestamp
- Not stated on call → null (numbers) or "Not stated" (strings)
- stated_on_call format: "Yes — [MM:SS–MM:SS]" / "Partly — [MM:SS–MM:SS]" / "No"
- Red flags: use PD-RF-X codes (PD-RF-1 through PD-RF-8) with timestamps
- family_size: only if explicitly stated as a number on call (not inferred)
- executive_gist: 200–250 words covering borrower profile, income stack, obligations,
  family/co-borrower, collateral, neighbour checks, gate failures, BCM recommendation,
  and what management must know before sanction

## OUTPUT — valid JSON only, no markdown fences
{{
  "call_summary": {{
    "executive_gist": "<200-250 word management paragraph>",
    "income_streams": [
      {{
        "description": "<business type, location, how long running>",
        "run_by": "<name — relation>",
        "stated_on_call": "Yes — [MM:SS–MM:SS]|Partly — [MM:SS–MM:SS]|No",
        "stated_monthly": <number or null>,
        "assessed_monthly": <number or null>,
        "proof_cited": "<bills/UPI/passbook/mandi receipts/cash-box check etc.>",
        "timestamp_remarks": "[MM:SS–MM:SS] <one concise note>"
      }}
    ],
    "income_summary": {{
      "total_stated": <number or null>,
      "total_assessed": <number or null>,
      "missed_streams": "<income sources not probed>",
      "all_sources_covered": "YES|NO|UNKNOWN — declared list not available"
    }},
    "obligations": [
      {{
        "lender_type": "<lender name + loan type>",
        "borrower_name": "<in whose name>",
        "outstanding": <number or null>,
        "emi_monthly": <number or null>,
        "in_cam": "Unknown — CAM not provided|Yes — per CAM|No — not in CAM",
        "in_bureau": "Yes|No|Unknown — bureau not provided",
        "overdue": "<overdue/bounce history or 'None mentioned'>",
        "timestamp_remarks": "[MM:SS–MM:SS] <one concise note>"
      }}
    ],
    "obligations_summary": {{
      "total_outstanding": <number or null>,
      "total_emi": <number or null>,
      "undisclosed_found": "YES — PD-RF-4 triggered|NO|CANNOT CONFIRM — household-wide probes not done",
      "recent_enquiries": "<loan enquiries last 3 months — lender, amount, outcome per call>"
    }},
    "family_members": [
      {{
        "name": "<name if stated else 'Wife'/'Son' etc.>",
        "relation": "<relation to borrower>",
        "age": <number or null>,
        "occupation": "<occupation or income stream>",
        "met_in_person": "Yes|No|Not stated",
        "aware_of_loan": "Yes|No|Not stated",
        "remarks": "<repayment intent, objections, health, student status>"
      }}
    ],
    "family_summary": {{
      "family_size": <number spoken on call or null>,
      "dependents_count": <number spoken on call or null>,
      "earning_members": <number spoken on call or null>,
      "monthly_expense": <number spoken on call or null>,
      "co_borrower_name": "<name and relation or 'Not identified on recording'>",
      "co_borrower_aware": "YES|NO|Not established on this recording"
    }},
    "other_key_facts": {{
      "end_use": "<stated purpose + verification status + how-much/where/when>",
      "collateral_details": "<type, owner, size, papers seen Y/N, approx value with basis, LTV note>",
      "neighbour_checks": "<business neighbours — what said | residence neighbours — what said | 'NONE DONE — Gate K5'>",
      "red_flags": "<PD-RF-X code — description [timestamp] or 'NONE raised by BCM'>",
      "discrepancies": "<call vs application/bureau/photos or 'None noted; reconciliation requires CAM/photos'>",
      "bcm_recommendation": "<Recommend/Recommend-with-conditions/Reject + reasons, or 'NOT STATED — recording ends without recommendation'>",
      "evaluators_note": "<what management must act on before sanction — missing items, re-record triggers>"
    }}
  }}
}}
Return complete JSON only."""


def invoke_bedrock(transcript: str, case_context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Two sequential Bedrock calls to stay within the 8192-token output limit:
      Call 1 — criteria scoring + header_info
      Call 2 — call summary (uses Call 1 results as context)
    Results are merged into a single evaluation dict.
    """
    # ── Call 1: Criteria ──────────────────────────────────────────────────
    prompt1 = _build_criteria_prompt(transcript, case_context)
    text1 = _call_bedrock(prompt1, "CRITERIA")

    try:
        result1 = json.loads(text1)
    except json.JSONDecodeError as e:
        print(f"Criteria JSON parse error: {e}\nFirst 300: {text1[:300]}\nLast 300: {text1[-300:]}")
        raise Exception(f"Criteria JSON parsing failed: {e}")

    n_got = len(result1.get('criteria', []))
    n_exp = len(TEMPLATE_CRITERIA)
    if n_got < n_exp:
        print(f"WARNING: Got {n_got} criteria, expected {n_exp}")
    else:
        print(f"✓ Call 1 complete — {n_got} criteria returned")

    # ── Call 2: Call Summary ──────────────────────────────────────────────
    prompt2 = _build_summary_prompt(transcript, result1, case_context)
    text2 = _call_bedrock(prompt2, "SUMMARY")

    try:
        result2 = json.loads(text2)
    except json.JSONDecodeError as e:
        print(f"Summary JSON parse error: {e}\nFirst 300: {text2[:300]}\nLast 300: {text2[-300:]}")
        raise Exception(f"Summary JSON parsing failed: {e}")

    print("✓ Call 2 complete — call summary returned")

    # ── Merge ─────────────────────────────────────────────────────────────
    merged = {
        "call_type_detected": result1.get("call_type_detected", "BCM_PHYSICAL_PD"),
        "evaluator_notes":    result1.get("evaluator_notes", ""),
        "header_info":        result1.get("header_info", {}),
        "criteria":           result1.get("criteria", []),
        "call_summary":       result2.get("call_summary", {}),
    }
    return merged


def calculate_scores(ai_evaluation: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculate section subtotals and determine knockout gate status.
    Gate PASS/FAIL is derived from the Coverage value of the mapped criterion.
    """
    criteria = ai_evaluation.get('criteria', [])

    # Build lookup by ID
    criteria_by_id = {c['id']: c for c in criteria}

    # Section accumulation
    sections = {}
    for c_def in TEMPLATE_CRITERIA:
        sid = c_def['section']
        if sid not in sections:
            sections[sid] = {
                'max_points': Decimal('0'),
                'applicable_points': Decimal('0'),
                'scored_points': Decimal('0'),
            }
        c_eval = criteria_by_id.get(c_def['id'], {})
        coverage = c_eval.get('coverage', 'None')
        max_pts = Decimal(str(c_def['max_points']))
        scored = Decimal(str(c_eval.get('points_scored', 0)))

        sections[sid]['max_points'] += max_pts
        if coverage != 'N/A':
            sections[sid]['applicable_points'] += max_pts
            sections[sid]['scored_points'] += scored

    # Total score
    total_applicable = sum(s['applicable_points'] for s in sections.values())
    total_scored = sum(s['scored_points'] for s in sections.values())
    total_score = float(total_scored / total_applicable * 100) if total_applicable > 0 else 0
    total_score = round(total_score, 1)

    # Knockout gate status
    # Gates K1-K8 are determined by the criterion listed in GATE_MAP
    # A gate FAILS if the mapped criterion has coverage None OR (for K5: both B5 and E3 must pass)
    gate_results = {}
    for gate_id in ['K1', 'K2', 'K3', 'K4', 'K5', 'K6', 'K7', 'K8']:
        gate_results[gate_id] = 'PASS'  # default

    for crit_id, gate_id in GATE_MAP.items():
        c_eval = criteria_by_id.get(crit_id, {})
        coverage = c_eval.get('coverage', 'None')
        if coverage == 'None':
            gate_results[gate_id] = 'FAIL'
        # Partial on critical gates → still FAIL for gates
        elif coverage == 'Partial' and c_eval.get('flag'):
            gate_results[gate_id] = 'FAIL'

    # Build critical failures list
    critical_failures = []
    for gate_id, status in gate_results.items():
        if status == 'FAIL':
            # Find which criterion drove this
            driving_criteria = [cid for cid, gid in GATE_MAP.items() if gid == gate_id]
            for crit_id in driving_criteria:
                c_eval = criteria_by_id.get(crit_id, {})
                c_def = next((c for c in TEMPLATE_CRITERIA if c['id'] == crit_id), {})
                critical_failures.append({
                    'flag': gate_id,
                    'criterion_id': crit_id,
                    'criterion': c_def.get('criterion', ''),
                    'coverage': c_eval.get('coverage', 'None'),
                })

    # Grade band and verdict (deterministic — matches template formulas)
    any_gate_fail = any(v == 'FAIL' for v in gate_results.values())
    if any_gate_fail:
        grade_band = 'D — Fail'
        verdict = 'INVALID — RE-RECORD NARRATION'
    elif total_score >= 90:
        grade_band = 'A — Excellent'
        verdict = 'FI ACCEPTED — BM decision stands'
    elif total_score >= 75:
        grade_band = 'B — Pass'
        verdict = 'FI ACCEPTED — BM decision stands'
    elif total_score >= 60:
        grade_band = 'C — Conditional'
        verdict = 'SUPPLEMENTARY CALL before CPA'
    else:
        grade_band = 'D — Fail'
        verdict = 'RE-CONDUCT FI'

    print(f"Score: {total_score}/100 | Gates: {sum(1 for v in gate_results.values() if v=='FAIL')} failed | {grade_band}")

    return {
        'sections': sections,
        'total_score': Decimal(str(total_score)),
        'total_scored': total_scored,
        'total_applicable': total_applicable,
        'gate_results': gate_results,
        'critical_failures': critical_failures,
        'grade_band': grade_band,
        'verdict': verdict,
    }


def lambda_handler(event, context):
    """Main Lambda handler."""
    try:
        print(f"Evaluation request: {json.dumps(event)}")

        evaluation_id = event['evaluation_id']
        application_id = event['application_id']
        call_type = event.get('call_type', 'BCM_PHYSICAL_PD')
        transcript_key = event['transcript_s3_key']
        case_context = event.get('case_context', {})
        created_at = event.get('created_at')

        # Update status
        table = dynamodb.Table(DYNAMODB_TABLE)
        table.update_item(
            Key={'evaluation_id': evaluation_id, 'created_at': created_at},
            UpdateExpression='SET #status = :s, evaluation_started_at = :t',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={':s': 'EVALUATING', ':t': datetime.utcnow().isoformat()}
        )

        # Load transcript
        print(f"Loading transcript: s3://{TRANSCRIPTS_BUCKET}/{transcript_key}")
        resp = s3_client.get_object(Bucket=TRANSCRIPTS_BUCKET, Key=transcript_key)
        transcript_data = json.loads(resp['Body'].read())
        transcript_text = transcript_data.get('transcript', '')
        print(f"Transcript loaded: {len(transcript_text)} chars")

        # Invoke AI evaluation
        ai_evaluation = invoke_bedrock(transcript_text, case_context)

        # Calculate scores
        scoring = calculate_scores(ai_evaluation)

        # Build final result
        final_result = {
            'evaluation_id': evaluation_id,
            'application_id': application_id,
            'call_type': call_type,
            'evaluated_at': datetime.utcnow().isoformat(),
            'ai_evaluation': ai_evaluation,
            'scoring': scoring,
            'criteria_count': len(TEMPLATE_CRITERIA),
            'status': 'COMPLETED'
        }

        # Save to S3
        result_key = f"evaluations/{evaluation_id}/evaluation-result.json"
        s3_client.put_object(
            Bucket=REPORTS_BUCKET,
            Key=result_key,
            Body=json.dumps(final_result, indent=2, cls=DecimalEncoder),
            ContentType='application/json'
        )
        print(f"Result saved: s3://{REPORTS_BUCKET}/{result_key}")

        # Update DynamoDB
        table.update_item(
            Key={'evaluation_id': evaluation_id, 'created_at': created_at},
            UpdateExpression='SET #status = :s, total_score = :sc, grade_band = :gb, verdict = :v, evaluated_at = :t, result_s3_key = :k',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':s': 'COMPLETED',
                ':sc': scoring['total_score'],
                ':gb': scoring['grade_band'],
                ':v': scoring['verdict'],
                ':t': datetime.utcnow().isoformat(),
                ':k': result_key,
            }
        )

        # Trigger Excel generator
        try:
            lambda_client = boto3.client('lambda')
            lambda_client.invoke(
                FunctionName=os.environ.get('EXCEL_GENERATOR_LAMBDA_ARN'),
                InvocationType='Event',
                Payload=json.dumps({
                    'evaluation_id': evaluation_id,
                    'result_s3_key': result_key,
                    'created_at': created_at
                })
            )
            print(f"Excel generation triggered for {evaluation_id}")
        except Exception as ex:
            print(f"Excel trigger failed (non-fatal): {ex}")

        return {
            'statusCode': 200,
            'body': json.dumps({
                'evaluation_id': evaluation_id,
                'status': 'COMPLETED',
                'score': float(scoring['total_score']),
                'grade_band': scoring['grade_band'],
                'result_s3_key': result_key,
            })
        }

    except Exception as e:
        print(f"Evaluation error: {e}")
        import traceback
        traceback.print_exc()
        try:
            dynamodb.Table(DYNAMODB_TABLE).update_item(
                Key={'evaluation_id': event['evaluation_id'], 'created_at': event.get('created_at')},
                UpdateExpression='SET #status = :s, error_message = :e',
                ExpressionAttributeNames={'#status': 'status'},
                ExpressionAttributeValues={':s': 'FAILED', ':e': str(e)}
            )
        except:
            pass
        return {'statusCode': 500, 'body': json.dumps({'error': str(e), 'evaluation_id': event.get('evaluation_id')})}
