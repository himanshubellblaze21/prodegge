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

# Gate mapping: which criterion drives which knockout gate — BCM
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

# ─────────────────────────────────────────────────────────────────────────────
# RCM TELE PD CRITERIA — from rcm-audio-pd-rubric.json
# ─────────────────────────────────────────────────────────────────────────────
RCM_CRITERIA = [
    # SECTION A — Call Opening, Identity & Consent
    {'id': 'A1', 'section': 'A', 'criterion': 'Recording disclosure made at start of call',
     'max_points': 2, 'critical': True},
    {'id': 'A2', 'section': 'A', 'criterion': 'Identity validated — speaking to actual applicant',
     'max_points': 2, 'critical': True},
    {'id': 'A3', 'section': 'A', 'criterion': 'Customer confirms having applied for the loan',
     'max_points': 2, 'critical': False},
    {'id': 'A4', 'section': 'A', 'criterion': 'Language comfort and audibility established',
     'max_points': 1, 'critical': False},
    {'id': 'A5', 'section': 'A', 'criterion': 'Co-borrower(s) identified and covered',
     'max_points': 1, 'critical': False},
    # SECTION B — Loan Request & Terms Awareness
    {'id': 'B1', 'section': 'B', 'criterion': 'Loan amount stated by customer and matches CAM',
     'max_points': 3, 'critical': True},
    {'id': 'B2', 'section': 'B', 'criterion': 'Tenure and approximate EMI awareness confirmed',
     'max_points': 3, 'critical': False},
    {'id': 'B3', 'section': 'B', 'criterion': 'Rate of interest communicated / confirmed',
     'max_points': 2, 'critical': False},
    {'id': 'B4', 'section': 'B', 'criterion': 'Processing fee and upfront charges awareness',
     'max_points': 2, 'critical': False},
    {'id': 'B5', 'section': 'B', 'criterion': 'Customer understands property will be MORTGAGED',
     'max_points': 2, 'critical': True},
    # SECTION C — End Use Verification
    {'id': 'C1', 'section': 'C', 'criterion': 'End use stated by customer in their own words',
     'max_points': 4, 'critical': True},
    {'id': 'C2', 'section': 'C', 'criterion': 'End use screened against restricted list',
     'max_points': 3, 'critical': True},
    {'id': 'C3', 'section': 'C', 'criterion': 'Utilisation plan probed (how much, where, when)',
     'max_points': 3, 'critical': False},
    # SECTION D — Income & Business Validation
    {'id': 'D1', 'section': 'D', 'criterion': 'Nature of main business/occupation established in detail',
     'max_points': 3, 'critical': False},
    {'id': 'D2', 'section': 'D', 'criterion': 'Business vintage confirmed',
     'max_points': 2, 'critical': False},
    {'id': 'D3', 'section': 'D', 'criterion': 'Income quantum stated by customer and consistent with CAM',
     'max_points': 5, 'critical': True},
    {'id': 'D4', 'section': 'D', 'criterion': 'All additional income sources in CAM verified on call',
     'max_points': 4, 'critical': False},
    {'id': 'D5', 'section': 'D', 'criterion': 'Business scale cross-checks asked (staff/customers/stock)',
     'max_points': 3, 'critical': False},
    {'id': 'D6', 'section': 'D', 'criterion': 'Household income contributors mapped',
     'max_points': 3, 'critical': False},
    # SECTION E — Obligations & Liabilities Probing
    {'id': 'E1', 'section': 'E', 'criterion': 'Existing loans enumerated and reconciled with CAM',
     'max_points': 5, 'critical': True},
    {'id': 'E2', 'section': 'E', 'criterion': 'Specific probe for microfinance / group loans',
     'max_points': 3, 'critical': False},
    {'id': 'E3', 'section': 'E', 'criterion': 'Specific probe for KCC, society/PACS, gold and hand loans',
     'max_points': 2, 'critical': False},
    {'id': 'E4', 'section': 'E', 'criterion': 'Repayment track / bounce history acknowledged',
     'max_points': 2, 'critical': False},
    # SECTION F — Family Structure & Co-Borrower Awareness
    {'id': 'F1', 'section': 'F', 'criterion': 'Family size and members confirmed vs CAM',
     'max_points': 2, 'critical': False},
    {'id': 'F2', 'section': 'F', 'criterion': 'Spouse/female co-borrower confirmed aware and consenting',
     'max_points': 3, 'critical': True},
    {'id': 'F3', 'section': 'F', 'criterion': 'Property-owner and legal-heir structure confirmed',
     'max_points': 3, 'critical': False},
    # SECTION G — Collateral Property Validation
    {'id': 'G1', 'section': 'G', 'criterion': 'Property identified, ownership/acquisition confirmed',
     'max_points': 3, 'critical': False},
    {'id': 'G2', 'section': 'G', 'criterion': 'Occupancy and possession status confirmed',
     'max_points': 2, 'critical': False},
    {'id': 'G3', 'section': 'G', 'criterion': 'No-dispute and no-existing-mortgage confirmation taken',
     'max_points': 3, 'critical': True},
    # SECTION H — Repayment Capacity & Intent
    {'id': 'H1', 'section': 'H', 'criterion': 'Customer explains HOW the EMI will be serviced',
     'max_points': 3, 'critical': False},
    {'id': 'H2', 'section': 'H', 'criterion': 'Surplus / affordability sanity-check discussed',
     'max_points': 2, 'critical': False},
    {'id': 'H3', 'section': 'H', 'criterion': 'Repayment commitment and NACH awareness confirmed',
     'max_points': 3, 'critical': False},
    # SECTION I — Mandatory Disclosures
    {'id': 'I1', 'section': 'I', 'criterion': 'Penal & bounce charges disclosed',
     'max_points': 2, 'critical': False},
    {'id': 'I2', 'section': 'I', 'criterion': 'Prepayment / foreclosure rules disclosed',
     'max_points': 2, 'critical': False},
    {'id': 'I3', 'section': 'I', 'criterion': 'Mandatory insurance premiums disclosed',
     'max_points': 2, 'critical': False},
    {'id': 'I4', 'section': 'I', 'criterion': 'KFS / total-cost honesty and grievance channel',
     'max_points': 2, 'critical': False},
    # SECTION J — Call Conduct & Professional Quality
    {'id': 'J1', 'section': 'J', 'criterion': 'Audio quality and completeness of recording',
     'max_points': 2, 'critical': True},
    {'id': 'J2', 'section': 'J', 'criterion': 'Open, non-leading questioning technique',
     'max_points': 2, 'critical': True},
    {'id': 'J3', 'section': 'J', 'criterion': 'Courteous, structured and complete flow',
     'max_points': 2, 'critical': False},
]

# RCM Gate mapping
RCM_GATE_MAP = {
    'J1': 'K1',   # Audio quality → K1
    'A2': 'K2',   # Identity validated → K2
    'A1': 'K3',   # Recording disclosure → K3
    'B5': 'K4',   # Mortgage understood → K4
    'B1': 'K5',   # Loan amount awareness → K5
    'C2': 'K6',   # Restricted end use → K6
    'J2': 'K7',   # No mis-selling → K7
}

# ─────────────────────────────────────────────────────────────────────────────
# BM AUDIO FI CRITERIA — Field Investigation narration
# ─────────────────────────────────────────────────────────────────────────────
BM_CRITERIA = [
    # SECTION A — Investigation Setup
    {'id': 'A1', 'section': 'A', 'criterion': 'Visit identification: date, location, purpose stated',
     'max_points': 2, 'critical': False},
    {'id': 'A2', 'section': 'A', 'criterion': 'BM personally met borrower and verified identity',
     'max_points': 2, 'critical': True},
    {'id': 'A3', 'section': 'A', 'criterion': 'Documentation capture confirmed (photos, copies)',
     'max_points': 2, 'critical': False},
    {'id': 'A4', 'section': 'A', 'criterion': 'Same-day recording confirmed',
     'max_points': 2, 'critical': True},
    # SECTION B — Business/Income Verification
    {'id': 'B1', 'section': 'B', 'criterion': 'Business/income site visited and described on ground',
     'max_points': 5, 'critical': True},
    {'id': 'B2', 'section': 'B', 'criterion': 'Income quantum verified with supporting observations',
     'max_points': 4, 'critical': False},
    {'id': 'B3', 'section': 'B', 'criterion': 'Income documents examined (bills, passbooks, receipts)',
     'max_points': 3, 'critical': False},
    # SECTION C — End Use & Purpose
    {'id': 'C1', 'section': 'C', 'criterion': 'End use verified on ground against declared purpose',
     'max_points': 3, 'critical': False},
    {'id': 'C2', 'section': 'C', 'criterion': 'No restricted use or discrepancy found',
     'max_points': 3, 'critical': True},
    # SECTION D — Obligations Verification
    {'id': 'D1', 'section': 'D', 'criterion': 'Existing obligations confirmed on ground',
     'max_points': 4, 'critical': False},
    {'id': 'D2', 'section': 'D', 'criterion': 'Household expenses and lifestyle assessed',
     'max_points': 3, 'critical': False},
    # SECTION E — Family & Co-Borrower Verification
    {'id': 'E1', 'section': 'E', 'criterion': 'Family members met, loan/mortgage awareness confirmed',
     'max_points': 4, 'critical': True},
    {'id': 'E2', 'section': 'E', 'criterion': 'Co-borrower met in person and consenting',
     'max_points': 3, 'critical': False},
    # SECTION F — Collateral/Property Verification
    {'id': 'F1', 'section': 'F', 'criterion': 'Property physically inspected and described',
     'max_points': 4, 'critical': True},
    {'id': 'F2', 'section': 'F', 'criterion': 'Original title documents verified',
     'max_points': 4, 'critical': True},
    {'id': 'F3', 'section': 'F', 'criterion': 'Market value estimated with basis',
     'max_points': 2, 'critical': False},
    {'id': 'F4', 'section': 'F', 'criterion': 'Risk screen: lien, encroachment, negative zone',
     'max_points': 2, 'critical': False},
    # SECTION G — Overall Assessment
    {'id': 'G1', 'section': 'G', 'criterion': 'Discrepancies between documents and ground reality noted',
     'max_points': 3, 'critical': False},
    {'id': 'G2', 'section': 'G', 'criterion': 'Overall verification view and recommendation stated',
     'max_points': 3, 'critical': False},
    # SECTION H — Recording Quality
    {'id': 'H1', 'section': 'H', 'criterion': 'Structured, specific narration with names and numbers',
     'max_points': 2, 'critical': False},
    {'id': 'H2', 'section': 'H', 'criterion': 'Duration adequate, audibility ≥90%',
     'max_points': 2, 'critical': True},
]

# BM Gate mapping
BM_GATE_MAP = {
    'A4': 'K1',   # Same-day recording → K1
    'A2': 'K2',   # BM met borrower → K2
    'F1': 'K3',   # Property inspected → K3
    'B1': 'K4',   # Business verified → K4
    'G2': 'K5',   # No discrepancies → K5
}

def get_criteria_for_call_type(call_type: str):
    """Return the right criteria list and gate map for the detected call type."""
    if call_type == 'RCM_TELE_PD':
        return RCM_CRITERIA, RCM_GATE_MAP
    elif call_type == 'BM_AUDIO_FI':
        return BM_CRITERIA, BM_GATE_MAP
    else:  # BCM_PHYSICAL_PD (default)
        return TEMPLATE_CRITERIA, GATE_MAP

def _call_bedrock(prompt: str, label: str) -> str:
    """
    Single Bedrock call. max_tokens=8192 (Claude 3.5 Sonnet hard limit).
    Raises if stop_reason is max_tokens (truncated response).
    """
    request_body = {
        "messages": [{"role": "user", "content": [{"text": prompt}]}],
        "inferenceConfig": {
            "max_new_tokens": 5120,
            "temperature": 0.0,
            "top_p": 0.9
        }
    }

    print(f"[{label}] Calling Bedrock — prompt {len(prompt)} chars")
    response = bedrock_runtime.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=json.dumps(request_body)
    )
    response_body = json.loads(response['body'].read())
    stop_reason = response_body.get('stopReason', 'unknown')
    # Nova Pro response format: output.message.content[0].text
    ai_output = response_body['output']['message']['content'][0]['text']

    print(f"[{label}] stop_reason={stop_reason}, output={len(ai_output)} chars")

    if stop_reason == 'max_tokens':
        raise Exception(f"[{label}] Nova Pro hit max_tokens — response truncated. Reduce prompt or split further.")

    # Strip markdown fences
    t = ai_output.strip()
    if t.startswith('```'):
        lines = t.split('\n')
        start = next((i for i, l in enumerate(lines) if l.strip().startswith('{')), 0)
        end = next((i for i in range(len(lines)-1, -1, -1) if lines[i].strip().endswith('}')), len(lines)-1)
        t = '\n'.join(lines[start:end+1])
    return t


def _build_criteria_prompt(transcript: str, case_context: Dict[str, Any],
                           audio_duration_seconds: int = None) -> str:
    """
    CALL 1 — Criteria scoring: returns coverage + evidence per criterion.
    Points are NOT returned by AI — computed deterministically by calculate_scores().
    Prompt is intentionally concise to stay within model token budget.
    """
    n = len(TEMPLATE_CRITERIA)
    context_str = json.dumps(case_context, indent=2) if case_context else "None provided."

    if audio_duration_seconds is not None:
        mins = audio_duration_seconds // 60
        secs = audio_duration_seconds % 60
        duration_line = f"AUDIO FILE DURATION (measured): {mins}:{secs:02d}"
    else:
        duration_line = "AUDIO FILE DURATION: Not provided — use what BCM states on recording."

    return f"""You are the Audio PD Evaluation Agent for Prodigee Finance Limited.
Evaluate a BCM Physical PD self-audio transcript against exactly {n} criteria.
Return ONLY coverage + evidence. Do NOT compute scores, totals, points, or verdicts.

{duration_line}

COVERAGE DEFINITIONS
Full    = ALL checklist items present with specifics (names, numbers, timestamps)
Partial = criterion addressed BUT at least one checklist item missing or vague
None    = criterion not addressed anywhere in the transcript
N/A     = criterion genuinely inapplicable (e.g. Section C when no agri income)
Default: doubt Full/Partial → choose Partial. Doubt Partial/None → choose None.

KEY GATE RULES
K1 (A4/H2): If measured duration ≥ 15:00, duration sub-item is MET. Do not score None just because BCM didn't announce it.
K2 (B2): MAIN BORROWER (loan applicant) must be met AT business. Son/relative at shop = Partial.
K3 (B1): If BCM admits any site not visited (e.g. dairy), B1 = Partial max.
K5 (B5+E3): Count distinct persons per side. Same person cannot count as both business AND residence neighbour.
K6 (B6): BCM openly doubting/excluding an income = Full (K6 PASS). K6 fails only when suspicion smoothed over.
K8 (A3): No PRAGATI uploads supplied → set flag = "NOT_TESTED", not K8.

CRITERIA CHECKLIST (what Full requires for each)
NOTE: Transcript may be in Hindi/Hinglish. Match content by meaning, not exact English words.
Hindi equivalents: टीवी=TV, फ्रिज=fridge, कूलर=cooler, बाइक=bike, प्रेस=iron/press (ELSI assets)
                   सैलरी=salary, पॉलिसी=policy, इनकम=income, खर्च=expenses, किराना=kirana/grocery
                   ज़मीन/खेत=land/farm, फसल=crops, डेयरी=dairy, भैंस/गाय=buffalo/cow
                   मकान/घर=house, किराया=rent, CIBIL/ब्यूरो=bureau, लोन/EMI=loan/EMI

A1: App ID + customer name + village + product + loan amount at start + PRAGATI/bureau prep confirmed.
A2: Date/time of EACH visit + who met at each site + companion named.
A3: Geo-tagged photos confirmed taken AND uploaded to PRAGATI.
A4: Same-day recording confirmed + duration 15-30 min. Use measured file duration if provided.
B1: EVERY declared income site (all businesses, agri land, dairy) physically visited + concrete on-site observations.
B2: MAIN BORROWER (applicant) personally met AT business, operations explained. Son/relative ≠ Full.
B3: Daily sales + monthly income + annual turnover + margin % + seasonality per stream + arithmetic aloud.
B4: Bills/UPI/passbook/receipts asked + produced or honestly absent + Account Aggregator completeness confirmed.
B5: ≥2 DISTINCT business neighbours named with contacts + substance of what each said.
B6: Explicit per-stream genuineness view OR suspicion openly raised/income excluded for lack of proof.
    IMPORTANT: BCM excluding a salary claim for lack of proof (e.g. no letter pad, no salary slip) IS a Full genuineness statement.
    BCM saying "I doubted this income and set it aside" = Full. Do NOT require separate "genuine" declaration.
C1: Land visited/GPS video + owner named + Bhulekh checked + acreage stated. N/A if no agri.
C2: Crops per season named + any proof/receipts examined OR honestly stated absent + income figure stated. N/A if no agri.
    Partial if crops named but no receipts/proof — do not score None just because no mandi receipts.
C3: Cultivation confirmed + KCC/PACS obligation noted. N/A if no agri.
    Score None only if cultivation AND KCC both completely unmentioned.
D1: EVERY loan of borrower AND co-borrowers individually named (lender + EMI each). Vague bureau mention = None.
D2: Family size + dependents + household expenses stated + ELSI assets seen (टीवी/TV, फ्रिज/fridge, कूलर/cooler, बाइक/bike etc.) + consistency.
    IMPORTANT: If BCM lists household assets like TV, fridge, cooler, bike at the residence = D2 Full if expenses and family also stated.
D3: Income − expenses − EMIs = surplus aloud + proposed EMI vs surplus + disposable floor checked.
D4: All loan enquiries last 3 months with outcomes — OR explicit "no enquiries" statement (= Full).
E1: Ownership + construction/condition + ELSI assets seen (टीवी/TV/फ्रिज/कूलर/बाइक) + description of residence.
    IMPORTANT: If BCM lists TV, fridge, cooler, bike seen at residence = ELSI satisfied. Mark Full if ownership + condition + ELSI assets all covered.
    Partial only if one sub-item (e.g. years at address OR Patta check) is missing.
E2: Each family member named with role + loan/mortgage awareness. Co-borrower met in person + told property mortgaged. Samagra/KYC checked.
E3: ≥2 DISTINCT residence neighbours named with contacts + substance. Same person as B5 ≠ counted here.
E4: All four confirmed on recording: mortgage LAP + indicative EMI figure (number) + Clean Track Reward + Zero Commission.
F1: Type + self-occupied/rented + construction quality + age (20-yr rule) + carpet area vs 400/100 sq.ft + access + kitchen/toilet.
F2: Original title deeds physically sighted (confirmed) + owner + acquisition story + mutation + heirs/NOC + document type.
F3: Market value estimate with basis (local rates/transactions) + LTV sense + distress value (80% cap).
    Score None if NO value figure is given. Score Partial only if a value IS mentioned but lacks basis/LTV.
G1: End use stated + ground reality checked + how-much/where/when utilisation + restricted-use screen.
G2: Total income vs loan policy compared + capacity view + intent view.
    IMPORTANT: BCM stating "policy requires Rs 30,000/month but customer income is only Rs 16,000-18,000" IS a Full credit view with capacity assessment.
    If income vs policy threshold is explicitly compared AND intent assessed = Full.
G3: Clear Recommend/Recommend-with-conditions/Reject with specific reasons. Reasoned reduced sanction = Full.
H1: Section order followed + names/numbers throughout + no vague filler + no major section skipped.
H2: Duration 15-30 min + single continuous + ≥90% intelligible. Use measured file duration.
H3: Facts vs opinion separated + what couldn't be verified stated + discrepancies flagged.
    IMPORTANT: BCM explicitly stating what was not visited, what proof was absent, what income was doubted = Full H3.

RULES
1. Evidence from transcript only — never infer or copy from application file.
2. Timestamps must be within the recording duration. Mark uncertain ones as [~MM:SS].
3. For G3: copy exact BCM words — "reduced sanction" ≠ "reject".
4. For D1: vague "CIBIL dekha" without listing loans = None.
5. The transcript is in Hindi/Hinglish — evaluate by meaning, not by matching English checklist words.

## CASE CONTEXT
{context_str}

## TRANSCRIPT
{transcript}

## OUTPUT — valid JSON only, no markdown fences
{{
  "call_type_detected": "BCM_PHYSICAL_PD",
  "evaluator_notes": "<transcript quality, garbled sections, language notes>",
  "header_info": {{
    "application_id_mentioned": "<stated ID or 'N/A'>",
    "customer_name_mentioned": "<name as spoken>",
    "bcm_rcm_name_mentioned": "<BCM name or 'Not stated on recording'>",
    "loan_amount_mentioned": "<exact amount as spoken or 'N/A'>",
    "bcm_recommendation_mentioned": "<exact recommendation words or 'Not stated'>",
    "duration_mentioned": "<duration as spoken or 'Not stated on recording'>",
    "audio_duration_measured": "<from programmatic measurement e.g. '15:18' or 'Not provided'>"
  }},
  "criteria": [
    {{
      "id": "A1",
      "coverage": "Full|Partial|None|N/A",
      "evidence": "<[MM:SS] what was said; or MISSING: what sub-items are absent>",
      "gate_evidence": "<for gate-critical criteria: one-line pass/fail evidence or null>",
      "flag": "<K1-K8 if gate failure, NOT_TESTED if K8 without uploads, else null>"
    }}
  ]
}}
Include ALL {n} criteria in order A1→H3. Do NOT include points_scored."""


def _detect_call_type_from_speakers(speaker_count: int) -> str:
    """
    Detect call type from the number of distinct speakers in the transcript.
    This uses Amazon Transcribe's speaker diarization — already computed, free, deterministic.

    2+ speakers → RCM_TELE_PD (two-party phone call)
    1 speaker   → BCM_PHYSICAL_PD (default for single-speaker narration)

    BCM vs BM distinction: both are single-speaker; use keyword fallback only for this.
    """
    if speaker_count >= 2:
        return 'RCM_TELE_PD'
    return 'BCM_PHYSICAL_PD'


def _detect_bm_vs_bcm(transcript: str) -> str:
    """
    For single-speaker recordings, distinguish BCM Physical PD from BM Audio FI.
    BM narrations focus on document verification; BCM narrations focus on field visits.
    """
    t = transcript.lower()
    bm_signals = [
        'verify kiya', 'document check', 'property inspect',
        'field investigation', 'site visit', 'kagaz dekhe',
        'original document verify', 'bm report', 'legal verify',
    ]
    bcm_signals = [
        'visit kiya', 'gaye the', 'hum gaye', 'maine dekha',
        'dukan par', 'ghar gaya', 'observation report',
        'pd observation', 'padosi se baat', 'neighbour',
    ]
    bm_score = sum(1 for s in bm_signals if s in t)
    bcm_score = sum(1 for s in bcm_signals if s in t)

    if bm_score > bcm_score and bm_score >= 2:
        return 'BM_AUDIO_FI'
    return 'BCM_PHYSICAL_PD'


def _detect_call_type_fast(transcript: str, speaker_count: int = None) -> str:
    """
    Detect call type using speaker count from Transcribe (primary signal)
    with keyword fallback for BCM vs BM distinction.
    """
    if speaker_count is not None:
        if speaker_count >= 2:
            print(f"Call type: RCM_TELE_PD (speaker count={speaker_count})")
            return 'RCM_TELE_PD'
        else:
            # Single speaker — distinguish BCM from BM
            call_type = _detect_bm_vs_bcm(transcript)
            print(f"Call type: {call_type} (speaker count={speaker_count})")
            return call_type

    # Fallback: no speaker count available — use keyword heuristics
    print("Speaker count not available — using keyword heuristic")
    t = transcript.lower()
    rcm_signals = ['haan sir', 'ji haan', 'ji sir', 'haan ji', 'recording par hai',
                   'call record', 'aapne loan', 'aap khud', 'customer:', 'rcm:']
    rcm_score = sum(1 for s in rcm_signals if s in t)
    if rcm_score >= 2:
        return 'RCM_TELE_PD'
    return _detect_bm_vs_bcm(transcript)


def _build_rcm_criteria_prompt(transcript: str, case_context: Dict[str, Any],
                                audio_duration_seconds: int = None) -> str:
    """Criteria scoring prompt for RCM_TELE_PD calls."""
    n = len(RCM_CRITERIA)
    context_str = json.dumps(case_context, indent=2) if case_context else "None provided."

    if audio_duration_seconds is not None:
        mins = audio_duration_seconds // 60
        secs = audio_duration_seconds % 60
        duration_line = f"CALL DURATION (measured): {mins}:{secs:02d}"
    else:
        duration_line = "CALL DURATION: Not provided."

    return f"""You are the Audio PD Evaluation Agent for Prodigee Finance Limited.
Evaluate an RCM Telephone PD call transcript against exactly {n} criteria.
This is a TWO-PARTY PHONE CALL between the RCM and the customer/borrower.
Return ONLY coverage + evidence. Do NOT compute scores, totals, or verdicts.

{duration_line}

CALL TYPE: RCM_TELE_PD — Two-party telephone Personal Discussion
The RCM is on a phone call with the customer. The RCM asks questions; the customer answers.
There are NO physical visits, NO photos, NO field inspections in this call.

COVERAGE DEFINITIONS
Full    = ALL checklist items present with specifics (names, numbers, timestamps)
Partial = criterion addressed BUT at least one item missing or vague
None    = criterion not addressed anywhere in the transcript
N/A     = genuinely inapplicable to this case
Default: doubt Full/Partial → Partial. Doubt Partial/None → None.

KEY GATE RULES
K1 (J1): Valid recording — both voices audible, duration ≥ 10 min
K2 (A2): RCM confirmed speaking to actual applicant (not relative/agent/DSA)
K3 (A1): Recording disclosure made at start — customer told call is being recorded
K4 (B5): Customer clearly understands property will be MORTGAGED
K5 (B1): Customer states loan amount themselves (RCM must NOT lead with the figure)
K6 (C2): No restricted end use found (speculation, milch animals, illegal activity)
K7 (J2): No mis-selling — RCM used open questions, no coaching, no false promises

CRITERIA CHECKLIST (what Full requires for each)
NOTE: Transcript is in Hindi/Hinglish. Match content by meaning, not English words.
Hindi: रिकॉर्डिंग=recording, लोन=loan, EMI=EMI, प्रॉपर्टी/मकान=property, गिरवी=mortgage
       आमदनी/कमाई=income, खर्च=expense, परिवार=family, किस्त=instalment

A1 (Recording disclosure): RCM explicitly tells customer the call is being recorded at start. → flag K3
A2 (Identity validated): RCM confirms they are speaking to the actual applicant, not a relative/agent. → flag K2
A3 (Loan awareness): Customer voluntarily confirms they applied for a loan with Prodigee.
A4 (Language/audibility): Both parties communicate clearly in preferred language.
A5 (Co-borrower covered): Co-borrowers identified; spoken to on call or separate call noted.
B1 (Loan amount): Customer states the loan amount themselves without being prompted with the figure. → flag K5
B2 (Tenure/EMI awareness): Customer knows approximate tenure and EMI.
B3 (Rate of interest): Customer informed of RoI and that it is a FIXED rate.
B4 (Processing fee): Customer knows processing fee (4%+GST) and other charges.
B5 (Mortgage understood): Customer explicitly understands property will be mortgaged and original title papers deposited. → flag K4
C1 (End use): Customer states end use in own words — open question asked. → flag K6
C2 (Restricted use screen): End use screened — no speculation, no milch animals, no illegal activity. → flag K6
C3 (Utilisation plan): How much, where, when questioned for the loan utilisation.
D1 (Business nature): Main business/occupation explained in detail.
D2 (Business vintage): How long running the business — years confirmed.
D3 (Income quantum): Customer states income figures themselves; consistent with CAM if available.
D4 (Additional income sources): All income streams in CAM probed — dairy (animals count), agri (acres), rent, etc.
D5 (Business scale checks): Staff, customer footfall, stock levels cross-checked to validate income claim.
D6 (Household income): All earning family members identified and their income noted.
E1 (Existing loans): Customer enumerates all running loans (bank, NBFC, MFI, gold, hand loans) with lender + EMI. → flag K5 if major undisclosed
E2 (MFI/group loan probe): Specific probe for microfinance, SHG, JLG, bhishi loans including spouse.
E3 (KCC/gold/hand loan probe): KCC, cooperative society, PACS, gold loan, market/hand loans specifically asked.
E4 (Repayment track): Any EMI bounce, overdue, or late payment history acknowledged.
F1 (Family size): Family members and their count confirmed.
F2 (Co-borrower consent): Spouse/female co-borrower confirmed aware, on call or noted for separate call. → flag K4
F3 (Property heir structure): Who owns property, any heirs/siblings with share, NOC arrangement.
G1 (Property identification): Property identified, ownership and acquisition confirmed.
G2 (Occupancy status): Who lives in/uses the property confirmed.
G3 (No dispute/mortgage): Customer confirms no existing dispute, court case, or prior mortgage. → flag K6
H1 (EMI servicing plan): Customer explains which income source will service the EMI.
H2 (Affordability check): Income minus expense minus existing EMIs checked against proposed EMI.
H3 (NACH commitment): Customer agrees to NACH auto-debit and confirms account has funds on EMI date.
I1 (Penal charges): Penal interest on overdue EMI and ₹1,000+GST per bounce disclosed.
I2 (Prepayment rules): 20% free part-payment per year (after 12 EMIs), foreclosure charges disclosed.
I3 (Insurance): Mandatory accidental/disability insurance premium disclosed.
I4 (KFS/grievance): Customer told they will receive Key Fact Statement; grievance channel explained.
J1 (Audio quality): Both voices clearly audible throughout, call not abruptly cut. → flag K1
J2 (Open questioning): Material facts elicited with open questions — RCM did NOT lead/coach answers. → flag K7
J3 (Professional flow): Respectful tone, logical section order, proper closure with next steps.

RULES
1. Evidence from transcript only.
2. For B1: if RCM stated the amount first, B1 = Partial at best (customer must state it).
3. For A2: if customer mentions a relative/agent spoke, A2 = None.
4. Timestamps must be within call duration. Mark uncertain ones as [~MM:SS].

## CASE CONTEXT
{context_str}

## TRANSCRIPT
{transcript}

## OUTPUT — valid JSON only, no markdown fences
{{
  "call_type_detected": "RCM_TELE_PD",
  "evaluator_notes": "<call quality, garbled sections, language notes>",
  "header_info": {{
    "application_id_mentioned": "<stated ID or 'N/A'>",
    "customer_name_mentioned": "<name as spoken>",
    "rcm_name_mentioned": "<RCM name or 'Not stated'>",
    "loan_amount_mentioned": "<amount as customer stated or 'N/A'>",
    "bcm_recommendation_mentioned": "N/A — RCM tele call",
    "duration_mentioned": "<duration as stated or 'Not stated'>",
    "audio_duration_measured": "<from programmatic measurement e.g. '15:18' or 'Not provided'>"
  }},
  "criteria": [
    {{
      "id": "A1",
      "coverage": "Full|Partial|None|N/A",
      "evidence": "<[MM:SS] what was said; or MISSING: what sub-items are absent>",
      "gate_evidence": "<for gate-critical criteria: one-line pass/fail evidence or null>",
      "flag": "<K1-K9 if gate failure, else null>"
    }}
  ]
}}
Include ALL {n} criteria in order A1→J3. Do NOT include points_scored."""


def _build_bm_criteria_prompt(transcript: str, case_context: Dict[str, Any],
                               audio_duration_seconds: int = None) -> str:
    """Criteria scoring prompt for BM_AUDIO_FI calls."""
    n = len(BM_CRITERIA)
    context_str = json.dumps(case_context, indent=2) if case_context else "None provided."

    if audio_duration_seconds is not None:
        mins = audio_duration_seconds // 60
        secs = audio_duration_seconds % 60
        duration_line = f"AUDIO FILE DURATION (measured): {mins}:{secs:02d}"
    else:
        duration_line = "AUDIO FILE DURATION: Not provided."

    return f"""You are the Audio PD Evaluation Agent for Prodigee Finance Limited.
Evaluate a BM Field Investigation audio transcript against exactly {n} criteria.
This is a SINGLE-SPEAKER narration by a BM describing their field verification activities.
Return ONLY coverage + evidence. Do NOT compute scores, totals, or verdicts.

{duration_line}

CALL TYPE: BM_AUDIO_FI — BM narrating field verification activities
The BM visited places, verified documents, met people, and is narrating what they found.
Focus on: document verification, property inspection, business verification, discrepancy detection.

COVERAGE DEFINITIONS
Full    = ALL checklist items present with specifics (names, numbers, timestamps)
Partial = criterion addressed BUT at least one item missing or vague
None    = criterion not addressed anywhere in the transcript
N/A     = genuinely inapplicable
Default: doubt Full/Partial → Partial. Doubt Partial/None → None.

KEY GATE RULES
K1 (A4): Valid recording — same-day, audible
K2 (A2): BM personally met borrower and verified identity
K3 (F1): Property physically inspected and documented
K4 (B1): Business/income site visited and verified on ground
K5 (G2): No discrepancies between documents and ground reality left unreported

CRITERIA CHECKLIST (what Full requires for each)
NOTE: Transcript is in Hindi/Hinglish. Match content by meaning.

A1 (Visit identification): Date, location, purpose of field visit stated at start.
A2 (BM met borrower): BM confirms personally meeting the borrower and verifying identity. → flag K2
A3 (Documentation captured): Photos, document copies, verification records captured.
A4 (Same-day recording): Recording made on day of field visit. → flag K1
B1 (Business site visited): Income-generating site physically visited, observations narrated. → flag K4
B2 (Income verified): Income quantum verified through on-ground observations.
B3 (Documents examined): Bills, passbooks, receipts examined for income verification.
C1 (End use verified): Stated end use verified against ground reality.
C2 (No restricted use): No restricted use, discrepancy, or red flag found.
D1 (Obligations verified): Existing obligations confirmed through field enquiry.
D2 (Lifestyle assessed): Household expenses and living standard observed and narrated.
E1 (Family met): Family members met, loan and mortgage awareness confirmed. → flag K2
E2 (Co-borrower met): Co-borrower met in person and consenting.
F1 (Property inspected): Property physically inspected — type, condition, area, access. → flag K3
F2 (Title documents verified): Original title documents sighted, owner and acquisition confirmed. → flag K3
F3 (Market value estimated): Value estimate with basis given.
F4 (Risk screen): Lien, encroachment, negative zone, demolition risk checked.
G1 (Discrepancies noted): Any discrepancy between documents and ground reality explicitly stated.
G2 (Overall view stated): BM gives overall verification view and recommendation. → flag K5
H1 (Structured narration): Logical structure, names and numbers, no vague filler.
H2 (Duration/audibility): Duration adequate, ≥90% intelligible. → flag K1

RULES
1. Evidence from transcript only.
2. Timestamps must be within recording duration.
3. For G2: if discrepancies exist but are NOT reported = K5 fails.

## CASE CONTEXT
{context_str}

## TRANSCRIPT
{transcript}

## OUTPUT — valid JSON only, no markdown fences
{{
  "call_type_detected": "BM_AUDIO_FI",
  "evaluator_notes": "<recording quality, garbled sections, language notes>",
  "header_info": {{
    "application_id_mentioned": "<stated ID or 'N/A'>",
    "customer_name_mentioned": "<name as spoken>",
    "bcm_rcm_name_mentioned": "<BM name or 'Not stated'>",
    "loan_amount_mentioned": "<amount as spoken or 'N/A'>",
    "bcm_recommendation_mentioned": "<BM recommendation or 'Not stated'>",
    "duration_mentioned": "<duration as spoken or 'Not stated'>",
    "audio_duration_measured": "<from programmatic measurement e.g. '15:18' or 'Not provided'>"
  }},
  "criteria": [
    {{
      "id": "A1",
      "coverage": "Full|Partial|None|N/A",
      "evidence": "<[MM:SS] what was said; or MISSING: what sub-items are absent>",
      "gate_evidence": "<for gate-critical criteria: one-line pass/fail evidence or null>",
      "flag": "<K1-K5 if gate failure, else null>"
    }}
  ]
}}
Include ALL {n} criteria in order A1→H2. Do NOT include points_scored."""


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

## EXTRACTION RULES — NON-NEGOTIABLE
1. TIMESTAMPS ARE MANDATORY on every figure. Format: [MM:SS–MM:SS].
   If a figure has no timestamp, write "Not stated" — do NOT write the figure without a timestamp.
   Exception: totals that are computed from spoken items may cite the range of source timestamps.
2. ONLY figures SPOKEN on the recording. Never copy from the application file or CAM.
   If a figure is not in the transcript, write null (for numbers) or "Not stated" (for strings).
3. Timestamps MUST be within the recorded audio duration. Do NOT generate timestamps beyond end of recording.
4. BCM recommendation: copy the EXACT words spoken (Recommend / Recommend reduced sanction / Reject).
   Do not reinterpret — "reduce sanction to Rs 2L" is NOT "reject".
5. CONSISTENCY CHECKS (enforced):
   - If F3 criteria coverage = None → collateral_details value must be "Not stated on recording"
   - If D1 criteria coverage = None → obligations table must be empty or nil-confirmed only
   - If E2 criteria says co-borrower not identified → co_borrower_name must be "Not identified on recording"
   - If K8 = NOT_TESTED → discrepancies field must include "K8 not testable — PRAGATI uploads not supplied"
6. "Undisclosed obligation found?" answer: use ONLY "YES — PD-RF-4", "NO — probed and confirmed nil",
   or "CANNOT CONFIRM — not probed on this recording". Never write bare "NO" unless nil was confirmed.
7. Repayment snapshot cells (FOIR, surplus cover, disposable floor): if proposed EMI not stated on recording,
   write "Cannot be tested — proposed EMI not stated on recording" instead of leaving blank.
8. executive_gist: 200–250 words. Must include: borrower profile, income stack, PD failures (which sites
   not visited, which gates failed), BCM recommendation as actually stated, and what management must do.
9. evaluators_note: state the engine name/version for audit trail. Include all re-record triggers and
   missing items that management must act on before sanction.

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


def invoke_bedrock(transcript: str, case_context: Dict[str, Any],
                   audio_duration_seconds: int = None,
                   speaker_count: int = None) -> Dict[str, Any]:
    """
    Two sequential Bedrock calls:
      Call 1 — criteria scoring (routed by call type)
      Call 2 — call summary
    speaker_count: from Amazon Transcribe diarization (number of distinct speakers).
    """
    # ── Detect call type using speaker count (primary) ────────────────────
    detected_call_type = _detect_call_type_fast(transcript, speaker_count)

    # ── Call 1: Criteria (call-type-specific prompt) ──────────────────────
    if detected_call_type == 'RCM_TELE_PD':
        prompt1 = _build_rcm_criteria_prompt(transcript, case_context, audio_duration_seconds)
    elif detected_call_type == 'BM_AUDIO_FI':
        prompt1 = _build_bm_criteria_prompt(transcript, case_context, audio_duration_seconds)
    else:
        prompt1 = _build_criteria_prompt(transcript, case_context, audio_duration_seconds)

    text1 = _call_bedrock(prompt1, "CRITERIA")

    try:
        result1 = json.loads(text1)
    except json.JSONDecodeError as e:
        print(f"Criteria JSON parse error: {e}\nFirst 300: {text1[:300]}\nLast 300: {text1[-300:]}")
        raise Exception(f"Criteria JSON parsing failed: {e}")

    n_got = len(result1.get('criteria', []))
    # Expected count depends on call type
    active_criteria, _ = get_criteria_for_call_type(
        result1.get('call_type_detected', detected_call_type)
    )
    n_exp = len(active_criteria)
    if n_got < n_exp:
        print(f"WARNING: Got {n_got} criteria, expected {n_exp}")
    else:
        print(f"✓ Call 1 complete — {n_got} criteria returned ({result1.get('call_type_detected', '?')})")

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
        "engine_version":     "Prodigee-AI-v1.3",
    }
    return merged


def calculate_scores(ai_evaluation: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculate section subtotals and determine knockout gate status.
    Routes to the right criteria/gate map based on detected call type.
    Points are always computed deterministically: Full=max, Partial=max/2, None=0.
    """
    detected_call_type = ai_evaluation.get('call_type_detected', 'BCM_PHYSICAL_PD')
    criteria_defs, active_gate_map = get_criteria_for_call_type(detected_call_type)
    print(f"Scoring with call type: {detected_call_type}, {len(criteria_defs)} criteria, {len(active_gate_map)} gates")

    criteria = ai_evaluation.get('criteria', [])
    criteria_by_id = {c['id']: c for c in criteria}

    # ── Section accumulation using the right criteria for this call type ──
    sections = {}
    for c_def in criteria_defs:
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

        # Deterministic points — never trust AI's points_scored
        if coverage == 'Full':
            scored = max_pts
        elif coverage == 'Partial':
            scored = max_pts / 2
        else:
            scored = Decimal('0')

        # Write corrected points back so S3/Excel are consistent
        c_eval['points_scored'] = float(scored)

        sections[sid]['max_points'] += max_pts
        if coverage != 'N/A':
            sections[sid]['applicable_points'] += max_pts
            sections[sid]['scored_points'] += scored

    # ── Total score ───────────────────────────────────────────────────────
    total_applicable = sum(s['applicable_points'] for s in sections.values())
    total_scored = sum(s['scored_points'] for s in sections.values())
    total_score = float(total_scored / total_applicable * 100) if total_applicable > 0 else 0
    total_score = round(total_score, 1)

    # ── Gate logic — generic: apply active_gate_map ───────────────────────
    def cov(crit_id):
        return criteria_by_id.get(crit_id, {}).get('coverage', 'None')

    def get_flag(crit_id):
        return criteria_by_id.get(crit_id, {}).get('flag', None)

    # Build gate IDs from the active gate map
    all_gate_ids = sorted(set(active_gate_map.values()))
    gate_results = {gid: 'PASS' for gid in all_gate_ids}

    if detected_call_type == 'BCM_PHYSICAL_PD':
        # BCM-specific gate logic (with special rules for K2, K5, K6, K8)
        if cov('A4') == 'None':
            gate_results['K1'] = 'FAIL'

        if cov('B2') in ('None', 'Partial'):
            gate_results['K2'] = 'FAIL'

        if cov('B1') == 'None' or (cov('B1') == 'Partial' and get_flag('B1') == 'K3'):
            gate_results['K3'] = 'FAIL'

        if cov('E2') == 'None':
            gate_results['K4'] = 'FAIL'

        # K5: both B5 and E3 must be at least Partial (2+2 distinct neighbours)
        if cov('B5') in ('None', 'Partial') or cov('E3') in ('None', 'Partial'):
            gate_results['K5'] = 'FAIL'

        # K6: only fails on None (honest doubt = pass)
        if cov('B6') == 'None':
            gate_results['K6'] = 'FAIL'

        if cov('F2') == 'None' or (cov('F2') == 'Partial' and get_flag('F2') == 'K7'):
            gate_results['K7'] = 'FAIL'

        a3_flag = criteria_by_id.get('A3', {}).get('flag', None)
        if a3_flag == 'NOT_TESTED':
            gate_results['K8'] = 'NOT_TESTED'
        elif cov('A3') == 'None':
            gate_results['K8'] = 'FAIL'

    else:
        # Generic gate logic for RCM and BM: any None on a gate-mapped criterion = FAIL
        for crit_id, gate_id in active_gate_map.items():
            if cov(crit_id) == 'None':
                gate_results[gate_id] = 'FAIL'
            elif get_flag(crit_id) and get_flag(crit_id) not in (None, 'null'):
                gate_results[gate_id] = 'FAIL'

    # ── Build critical failures list ──────────────────────────────────────
    critical_failures = []
    for gate_id, status in gate_results.items():
        if status in ('FAIL', 'NOT_TESTED'):
            driving_criteria = [cid for cid, gid in active_gate_map.items() if gid == gate_id]
            for crit_id in driving_criteria:
                c_eval = criteria_by_id.get(crit_id, {})
                c_def = next((c for c in criteria_defs if c['id'] == crit_id), {})
                evidence = c_eval.get('gate_evidence') or c_eval.get('evidence', '')
                critical_failures.append({
                    'flag': gate_id,
                    'status': status,
                    'criterion_id': crit_id,
                    'criterion': c_def.get('criterion', ''),
                    'coverage': c_eval.get('coverage', 'None'),
                    'evidence': evidence[:300] if evidence else '',
                })

    # ── Grade band and verdict ─────────────────────────────────────────────
    any_fail = any(v == 'FAIL' for v in gate_results.values())

    # Verdict labels differ slightly by call type
    if detected_call_type == 'RCM_TELE_PD':
        if any_fail:
            grade_band = 'D — Fail'
            verdict = 'INVALID — RE-CONDUCT AUDIO PD'
        elif total_score >= 90:
            grade_band = 'A — Excellent'
            verdict = 'AUDIO PD ACCEPTED'
        elif total_score >= 75:
            grade_band = 'B — Pass'
            verdict = 'AUDIO PD ACCEPTED'
        elif total_score >= 60:
            grade_band = 'C — Conditional'
            verdict = 'SUPPLEMENTARY CALL REQUIRED'
        else:
            grade_band = 'D — Fail'
            verdict = 'RE-CONDUCT AUDIO PD'
    elif detected_call_type == 'BM_AUDIO_FI':
        if any_fail:
            grade_band = 'D — Fail'
            verdict = 'INVALID — RE-VISIT REQUIRED'
        elif total_score >= 90:
            grade_band = 'A — Excellent'
            verdict = 'FI ACCEPTED'
        elif total_score >= 75:
            grade_band = 'B — Pass'
            verdict = 'FI ACCEPTED'
        elif total_score >= 60:
            grade_band = 'C — Conditional'
            verdict = 'SUPPLEMENTARY FI REQUIRED'
        else:
            grade_band = 'D — Fail'
            verdict = 'RE-CONDUCT FI'
    else:  # BCM_PHYSICAL_PD
        if any_fail:
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

    gates_failed = sum(1 for v in gate_results.values() if v == 'FAIL')
    gates_not_tested = sum(1 for v in gate_results.values() if v == 'NOT_TESTED')
    print(f"Score: {total_score}/100 | Gates failed: {gates_failed} | Not tested: {gates_not_tested} | {grade_band}")

    return {
        'sections': sections,
        'total_score': Decimal(str(total_score)),
        'total_scored': float(total_scored),
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
        # Audio duration in seconds — measured programmatically by transcription Lambda
        audio_duration_seconds = event.get('audio_duration_seconds', None)

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

        # Extract speaker count from Transcribe diarization data
        segments = transcript_data.get('segments', [])
        unique_speakers = set(seg.get('speaker') for seg in segments if seg.get('speaker'))
        speaker_count = len(unique_speakers) if unique_speakers else None
        print(f"Speaker count from diarization: {speaker_count} ({unique_speakers})")

        # Invoke AI evaluation
        ai_evaluation = invoke_bedrock(transcript_text, case_context,
                                       audio_duration_seconds, speaker_count)

        # Calculate scores
        scoring = calculate_scores(ai_evaluation)

        # Build final result
        final_result = {
            'evaluation_id': evaluation_id,
            'application_id': application_id,
            'call_type': ai_evaluation.get('call_type_detected', call_type),
            'evaluated_at': datetime.utcnow().isoformat(),
            'ai_evaluation': ai_evaluation,
            'scoring': scoring,
            'criteria_count': len(get_criteria_for_call_type(
                ai_evaluation.get('call_type_detected', call_type))[0]),
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
