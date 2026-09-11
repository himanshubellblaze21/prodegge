"""
Excel Generator Lambda — Prodigee Finance
Fills Prodigee_Template.xlsx with AI evaluation data.

KEY DESIGN DECISION:
openpyxl saves formulas as strings but does NOT evaluate them.
To produce a file that shows correct values when opened (without Excel recalculating),
we REPLACE every formula cell with the pre-computed Python value.
This affects: H col (points), section subtotals, score/grade/verdict,
Call Summary header, repayment snapshot, and Gap & Coaching Report.
"""

import json
import boto3
import os
import re
from datetime import datetime
from io import BytesIO
from decimal import Decimal
import openpyxl
from openpyxl.cell.cell import MergedCell
from typing import Dict, Any, Optional

# AWS clients
s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')

# Environment
REPORTS_BUCKET = os.environ['REPORTS_BUCKET']
TEMPLATE_BUCKET = os.environ.get('TEMPLATE_BUCKET', REPORTS_BUCKET)
TEMPLATE_KEY = os.environ.get('TEMPLATE_KEY', 'templates/prodegee_template.xlsx')
DYNAMODB_TABLE = os.environ['DYNAMODB_TABLE']

# Criteria max points — matches template exactly (verified from Prodigee_Template.xlsx)
CRITERIA_MAX_PTS = {
    'A1': 2, 'A2': 2, 'A3': 2, 'A4': 2,
    'B1': 5, 'B2': 4, 'B3': 4, 'B4': 4, 'B5': 4, 'B6': 3,
    'C1': 4, 'C2': 3, 'C3': 3,
    'D1': 4, 'D2': 3, 'D3': 3, 'D4': 2,
    'E1': 4, 'E2': 5, 'E3': 4, 'E4': 3,
    'F1': 4, 'F2': 4, 'F3': 3, 'F4': 3,
    'G1': 3, 'G2': 4, 'G3': 3,
    'H1': 2, 'H2': 2, 'H3': 2,
}

# Section to criteria mapping
SECTION_CRITERIA = {
    'A': ['A1','A2','A3','A4'],
    'B': ['B1','B2','B3','B4','B5','B6'],
    'C': ['C1','C2','C3'],
    'D': ['D1','D2','D3','D4'],
    'E': ['E1','E2','E3','E4'],
    'F': ['F1','F2','F3','F4'],
    'G': ['G1','G2','G3'],
    'H': ['H1','H2','H3'],
}

SECTION_NAMES = {
    'A': 'A — Recording Setup & Visit Identification',
    'B': 'B — Business Verification (every income stream)',
    'C': 'C — Agricultural Income Verification',
    'D': 'D — Obligations & Family Expenses',
    'E': 'E — Residence, Family & Co-Borrower',
    'F': 'F — Collateral Verification',
    'G': 'G — End Use & Overall Assessment',
    'H': 'H — Narration Quality',
}


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def safe_set(ws, cell_ref: str, value: Any):
    """Write a plain value to a cell, resolving merged cells to their anchor."""
    cell = ws[cell_ref]
    if isinstance(cell, MergedCell):
        for mr in ws.merged_cells.ranges:
            if cell.coordinate in mr:
                ws[mr.start_cell.coordinate] = value
                return
    else:
        cell.value = value


def find_criterion_rows(ws) -> Dict[str, int]:
    """Map criterion ID (A1…H3) → row number by scanning col A."""
    rows = {}
    for row in range(1, 100):
        val = ws[f'A{row}'].value
        if val and isinstance(val, str):
            m = re.match(r'^([A-H]\d{1,2})\s*$', val.strip())
            if m:
                rows[m.group(1)] = row
    print(f"Found {len(rows)} criterion rows")
    return rows


def find_gate_rows(ws) -> Dict[str, int]:
    """Map gate ID (K1…K8) → row number by scanning col A."""
    rows = {}
    for row in range(1, 100):
        val = ws[f'A{row}'].value
        if val and isinstance(val, str) and val.strip() in ['K1','K2','K3','K4','K5','K6','K7','K8']:
            rows[val.strip()] = row
    print(f"Found {len(rows)} gate rows")
    return rows


def pts_for_coverage(coverage: str, max_pts: float) -> float:
    """Compute points exactly as the Excel formula: Full→max, Partial→max/2, else 0."""
    if coverage == 'Full':
        return max_pts
    if coverage == 'Partial':
        return max_pts / 2.0
    return 0.0  # None or N/A


# ─────────────────────────────────────────────────────────────────────────────
# COMPUTE DERIVED VALUES
# ─────────────────────────────────────────────────────────────────────────────

def compute_derived(evaluation_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pre-compute all values that the template normally derives via formula:
      - per-criterion points scored
      - section applicable/scored/coverage%
      - total score
      - knockout status, grade band, final verdict
      - repayment snapshot figures
      - gap items
    Returns a flat dict for use by fill functions.
    """
    criteria = evaluation_data.get('ai_evaluation', {}).get('criteria', [])
    criteria_by_id = {c['id']: c for c in criteria}

    gate_results = evaluation_data.get('scoring', {}).get('gate_results', {})
    if not gate_results:
        # Fallback from critical_failures
        failures = {f.get('flag') for f in evaluation_data.get('scoring', {}).get('critical_failures', [])}
        gate_results = {f'K{i}': ('FAIL' if f'K{i}' in failures else 'PASS') for i in range(1, 9)}

    # ── per-criterion points — always computed deterministically, never from AI ──
    criterion_points = {}
    for cid, max_pts in CRITERIA_MAX_PTS.items():
        c = criteria_by_id.get(cid, {})
        coverage = c.get('coverage', 'None')
        if coverage == 'N/A':
            criterion_points[cid] = None  # excluded from scoring
        else:
            criterion_points[cid] = float(pts_for_coverage(coverage, max_pts))

    # ── section subtotals ─────────────────────────────────────────────────
    sections = {}
    for sid, cids in SECTION_CRITERIA.items():
        max_total = sum(CRITERIA_MAX_PTS[c] for c in cids)
        applicable = sum(CRITERIA_MAX_PTS[c] for c in cids
                        if criterion_points.get(c) is not None)
        scored = sum(criterion_points[c] for c in cids
                    if criterion_points.get(c) is not None)
        coverage_pct = round(scored / applicable * 100) if applicable > 0 else 'N/A'
        sections[sid] = {
            'max': max_total,
            'applicable': applicable,
            'scored': scored,
            'coverage_pct': coverage_pct,
        }

    # ── total score (normalised to 100) ───────────────────────────────────
    total_applicable = sum(s['applicable'] for s in sections.values())
    total_scored = sum(s['scored'] for s in sections.values())
    total_score = round(total_scored / total_applicable * 100, 1) if total_applicable > 0 else 0

    # ── knockout, grade, verdict (matches template formulas exactly) ──────
    any_fail = any(v == 'FAIL' for v in gate_results.values())
    any_not_tested = any(v == 'NOT_TESTED' for v in gate_results.values())
    if any_fail:
        knockout_status = 'FAILED — INVALID'
        grade_band = 'D — Fail'
        verdict = 'INVALID — RE-RECORD NARRATION'
    elif any_not_tested:
        knockout_status = 'CONDITIONAL — K8 NOT TESTED (uploads not supplied)'
        # Still grade on score, but note the untested gate
        if total_score >= 90:
            grade_band = 'A — Excellent'
            verdict = 'FI ACCEPTED — pending K8 verification'
        elif total_score >= 75:
            grade_band = 'B — Pass'
            verdict = 'FI ACCEPTED — pending K8 verification'
        elif total_score >= 60:
            grade_band = 'C — Conditional'
            verdict = 'SUPPLEMENTARY CALL before CPA'
        else:
            grade_band = 'D — Fail'
            verdict = 'RE-CONDUCT FI'
    elif total_score >= 90:
        knockout_status = 'ALL PASSED'
        grade_band = 'A — Excellent'
        verdict = 'FI ACCEPTED — BM decision stands'
    elif total_score >= 75:
        knockout_status = 'ALL PASSED'
        grade_band = 'B — Pass'
        verdict = 'FI ACCEPTED — BM decision stands'
    elif total_score >= 60:
        knockout_status = 'ALL PASSED'
        grade_band = 'C — Conditional'
        verdict = 'SUPPLEMENTARY CALL before CPA'
    else:
        knockout_status = 'ALL PASSED'
        grade_band = 'D — Fail'
        verdict = 'RE-CONDUCT FI'

    # ── repayment snapshot ────────────────────────────────────────────────
    cs = evaluation_data.get('ai_evaluation', {}).get('call_summary', {})
    inc_sum = cs.get('income_summary', {})
    ob_sum = cs.get('obligations_summary', {})
    fam_sum = cs.get('family_summary', {})

    def safe_num(val):
        """Convert val to float safely — returns 0 for None, strings, or non-numeric."""
        if val is None:
            return 0
        try:
            return float(val)
        except (TypeError, ValueError):
            return 0

    total_income = safe_num(inc_sum.get('total_assessed'))
    monthly_expense = safe_num(fam_sum.get('monthly_expense'))
    total_emi = safe_num(ob_sum.get('total_emi'))

    # Also sum from income_streams if total_assessed is zero/null
    if not total_income:
        streams = cs.get('income_streams', [])
        total_income = sum(
            safe_num(s.get('assessed_monthly') or s.get('stated_monthly'))
            for s in streams if isinstance(s, dict)
        )

    # Also sum from obligations if total_emi is zero/null
    if not total_emi:
        obs = cs.get('obligations', [])
        total_emi = sum(
            safe_num(o.get('emi_monthly'))
            for o in obs if isinstance(o, dict)
        )

    monthly_surplus = total_income - monthly_expense - total_emi

    CRITERIA_NAMES_MAP = {
        'A1': 'Case identified on record', 'A2': 'All visits confirmed — when and whom',
        'A3': 'Photo/document capture confirmed', 'A4': 'Same-day recording, correct duration',
        'B1': 'Every business visited and described as seen', 'B2': 'Borrower met at business',
        'B3': 'Income assessed per stream with arithmetic', 'B4': 'Income documents asked and examined',
        'B5': 'Business-neighbour enquiry narrated', 'B6': 'Genuineness view — no staged setup',
        'C1': 'Land visited; ownership and acreage verified', 'C2': 'Crops and yield verified with proof',
        'C3': 'Cultivation genuineness + KCC noted',
        'D1': 'All obligations enumerated with amounts', 'D2': 'Family expenses and lifestyle assessed',
        'D3': 'Surplus arithmetic narrated', 'D4': 'Recent loan enquiries probed with outcomes',
        'E1': 'Residence visited and described', 'E2': 'Family and co-borrower interviewed',
        'E3': 'Residence-neighbour enquiry narrated', 'E4': 'Customer education confirmed',
        'F1': 'Collateral visited and physically described', 'F2': 'Original papers seen; owner and title chain',
        'F3': 'Approximate market value with basis', 'F4': 'Risk screen stated',
        'G1': 'End use verified on ground', 'G2': 'Overall credit view',
        'G3': 'Clear recommendation with reasons',
        'H1': 'Structured and specific', 'H2': 'Duration and audibility', 'H3': 'Honest flagging of gaps',
    }

    # ── gap items ─────────────────────────────────────────────────────────
    gap_items = []
    for cid, max_pts in CRITERIA_MAX_PTS.items():
        c = criteria_by_id.get(cid, {})
        coverage = c.get('coverage', 'None')
        if coverage in ('None', 'Partial'):
            criterion_name = CRITERIA_NAMES_MAP.get(cid, cid)
            points_lost = max_pts if coverage == 'None' else max_pts / 2.0
            prefix = 'NOT NARRATED' if coverage == 'None' else 'INCOMPLETE'
            gap_items.append({
                'id': cid,
                'text': f"{prefix} — {criterion_name}",
                'points_lost': points_lost,
            })

    total_points_lost = sum(g['points_lost'] for g in gap_items)

    return {
        'criterion_points': criterion_points,
        'sections': sections,
        'total_score': total_score,
        'total_applicable': total_applicable,
        'total_scored': total_scored,
        'knockout_status': knockout_status,
        'grade_band': grade_band,
        'verdict': verdict,
        'gate_results': gate_results,
        'total_income': total_income,
        'monthly_expense': monthly_expense,
        'total_emi': total_emi,
        'monthly_surplus': monthly_surplus,
        'gap_items': gap_items,
        'total_points_lost': total_points_lost,
        'points_to_90': max(0, round(90 - total_score, 1)),
    }


# ─────────────────────────────────────────────────────────────────────────────
# SCORECARD SHEET
# ─────────────────────────────────────────────────────────────────────────────

def fill_scorecard(ws, evaluation_data: Dict[str, Any], derived: Dict[str, Any]):
    """Fill the entire Scorecard sheet."""

    # ── Header B2:B7 ──────────────────────────────────────────────────────
    header_info = evaluation_data.get('ai_evaluation', {}).get('header_info', {})
    evaluated_at = evaluation_data.get('evaluated_at', datetime.utcnow().isoformat())
    try:
        date_str = datetime.fromisoformat(evaluated_at.replace('Z', '')).strftime('%d-%b-%Y')
    except:
        date_str = datetime.utcnow().strftime('%d-%b-%Y')

    ws['B2'] = header_info.get('application_id_mentioned', 'N/A')
    ws['B3'] = header_info.get('customer_name_mentioned', 'N/A')
    ws['B4'] = header_info.get('bcm_rcm_name_mentioned', 'N/A')
    ws['B5'] = date_str
    ws['B6'] = header_info.get('duration_mentioned', 'N/A')
    ws['B7'] = header_info.get('loan_amount_mentioned', 'N/A')
    print(f"Header: {ws['B3'].value} | {ws['B4'].value} | {ws['B7'].value}")

    # ── Criteria: G=Coverage, H=Points (pre-computed), I=Evidence ─────────
    criteria_rows = find_criterion_rows(ws)
    ai_criteria = evaluation_data.get('ai_evaluation', {}).get('criteria', [])
    criteria_by_id = {c['id']: c for c in ai_criteria}
    criterion_points = derived['criterion_points']

    filled = 0
    for cid, row in criteria_rows.items():
        c = criteria_by_id.get(cid, {})
        coverage = c.get('coverage', 'None')
        # Prefer gate_evidence for critical criteria, fall back to evidence
        evidence = c.get('gate_evidence') or c.get('evidence', '')

        # G: Coverage
        safe_set(ws, f'G{row}', coverage)

        # H: Replace formula with computed value
        pts = criterion_points.get(cid)
        if pts is not None:
            safe_set(ws, f'H{row}', pts)
        else:
            safe_set(ws, f'H{row}', 0)  # N/A criteria

        # I: Evidence
        safe_set(ws, f'I{row}', evidence)
        filled += 1

    print(f"Filled {filled} criteria (G=coverage, H=computed points, I=evidence)")

    # ── Gates E63:E70 — status + evidence text ────────────────────────────
    # Build a lookup of gate evidence from critical_failures
    critical_failures = evaluation_data.get('scoring', {}).get('critical_failures', [])
    gate_evidence_map = {}
    for cf in critical_failures:
        gid = cf.get('flag')
        ev = cf.get('evidence', '')
        if gid and ev:
            gate_evidence_map[gid] = ev[:200]

    gate_rows = find_gate_rows(ws)
    for gid, status in derived['gate_results'].items():
        row = gate_rows.get(gid)
        if row:
            safe_set(ws, f'E{row}', status)
            # Write gate evidence in the adjacent column F (if it exists)
            evidence_text = gate_evidence_map.get(gid, '')
            if evidence_text:
                safe_set(ws, f'F{row}', evidence_text)
    print(f"Filled {len(gate_rows)} gates with status and evidence")

    # ── Section subtotals rows 52-59 (replace formulas with values) ───────
    section_data_rows = {
        'A': 52, 'B': 53, 'C': 54, 'D': 55, 'E': 56, 'F': 57, 'G': 58, 'H': 59
    }
    for sid, row in section_data_rows.items():
        s = derived['sections'].get(sid, {})
        safe_set(ws, f'D{row}', s.get('applicable', 0))
        safe_set(ws, f'E{row}', s.get('scored', 0))
        pct = s.get('coverage_pct', 'N/A')
        safe_set(ws, f'F{row}', pct)
    print("Filled section subtotals (rows 52-59)")

    # ── Summary rows 72-75 (replace formulas with values) ─────────────────
    safe_set(ws, 'E72', derived['total_score'])
    safe_set(ws, 'E73', derived['knockout_status'])
    safe_set(ws, 'E74', derived['grade_band'])
    safe_set(ws, 'E75', derived['verdict'])
    print(f"Score={derived['total_score']} | {derived['grade_band']} | {derived['verdict']}")


# ─────────────────────────────────────────────────────────────────────────────
# CALL SUMMARY SHEET
# ─────────────────────────────────────────────────────────────────────────────

def fill_call_summary(wb, evaluation_data: Dict[str, Any], derived: Dict[str, Any]):
    """Fill the Call Summary sheet."""
    if 'Call Summary' not in wb.sheetnames:
        print("WARNING: Call Summary sheet not found")
        return

    ws = wb['Call Summary']
    cs = evaluation_data.get('ai_evaluation', {}).get('call_summary', {})
    if not cs:
        print("WARNING: No call_summary data")
        return

    header_info = evaluation_data.get('ai_evaluation', {}).get('header_info', {})
    evaluated_at = evaluation_data.get('evaluated_at', datetime.utcnow().isoformat())
    try:
        date_str = datetime.fromisoformat(evaluated_at.replace('Z', '')).strftime('%d-%b-%Y')
    except:
        date_str = datetime.utcnow().strftime('%d-%b-%Y')

    # ── Header rows 4-8: replace cross-sheet formulas with values ─────────
    # C4=AppID, G4=Score, C5=Customer, G5=Grade, C6=BCM, G6=KnockoutStatus
    # C7=Date, G7=Verdict, C8=LoanAmount, G8=Duration
    safe_set(ws, 'C4', header_info.get('application_id_mentioned', 'N/A'))
    safe_set(ws, 'G4', derived['total_score'])
    safe_set(ws, 'C5', header_info.get('customer_name_mentioned', 'N/A'))
    safe_set(ws, 'G5', derived['grade_band'])
    safe_set(ws, 'C6', header_info.get('bcm_rcm_name_mentioned', 'N/A'))
    safe_set(ws, 'G6', derived['knockout_status'])
    safe_set(ws, 'C7', date_str)
    safe_set(ws, 'G7', derived['verdict'])
    safe_set(ws, 'C8', header_info.get('loan_amount_mentioned', 'N/A'))
    safe_set(ws, 'G8', header_info.get('duration_mentioned', 'N/A'))
    print("Call Summary header filled")

    # ── Executive gist A13 ────────────────────────────────────────────────
    gist = cs.get('executive_gist', '')
    if gist:
        safe_set(ws, 'A13', gist)
        print(f"Executive gist: {len(gist)} chars")

    # ── Income rows 21-28 ─────────────────────────────────────────────────
    streams = cs.get('income_streams', [])
    for i, stream in enumerate(streams[:8]):
        row = 21 + i
        if not isinstance(stream, dict):
            safe_set(ws, f'B{row}', str(stream))
            continue
        safe_set(ws, f'B{row}', stream.get('description', ''))
        safe_set(ws, f'C{row}', stream.get('run_by', ''))
        safe_set(ws, f'D{row}', stream.get('stated_on_call', ''))
        if stream.get('stated_monthly') is not None:
            safe_set(ws, f'E{row}', stream['stated_monthly'])
        if stream.get('assessed_monthly') is not None:
            safe_set(ws, f'F{row}', stream['assessed_monthly'])
        safe_set(ws, f'G{row}', stream.get('proof_cited', ''))
        safe_set(ws, f'H{row}', stream.get('timestamp_remarks', ''))

    # Income totals row 29: replace =SUM formulas with computed values
    inc_sum = cs.get('income_summary', {})
    total_stated = inc_sum.get('total_stated') or sum(
        s.get('stated_monthly') or 0 for s in streams if isinstance(s, dict)
    ) or None
    total_assessed = derived['total_income'] or None
    if total_stated:
        safe_set(ws, 'E29', total_stated)
    if total_assessed:
        safe_set(ws, 'F29', total_assessed)

    safe_set(ws, 'B30', inc_sum.get('missed_streams', ''))
    safe_set(ws, 'C31', inc_sum.get('all_sources_covered', ''))
    print(f"Income: {len(streams)} streams | stated={total_stated} | assessed={total_assessed}")

    # ── Obligations rows 36-43 ────────────────────────────────────────────
    obligations = cs.get('obligations', [])
    for i, ob in enumerate(obligations[:8]):
        row = 36 + i
        if not isinstance(ob, dict):
            safe_set(ws, f'B{row}', str(ob))
            continue
        safe_set(ws, f'B{row}', ob.get('lender_type', ''))
        safe_set(ws, f'C{row}', ob.get('borrower_name', ''))
        if ob.get('outstanding') is not None:
            safe_set(ws, f'E{row}', ob['outstanding'])
        if ob.get('emi_monthly') is not None:
            safe_set(ws, f'F{row}', ob['emi_monthly'])
        safe_set(ws, f'G{row}', ob.get('in_bureau', ''))
        safe_set(ws, f'H{row}', ob.get('timestamp_remarks', ''))

    # Obligations totals row 44: replace =SUM formulas
    ob_sum = cs.get('obligations_summary', {})
    total_outstanding = ob_sum.get('total_outstanding') or sum(
        o.get('outstanding') or 0 for o in obligations if isinstance(o, dict)
    ) or None
    total_emi_val = derived['total_emi'] or None
    if total_outstanding:
        safe_set(ws, 'E44', total_outstanding)
    if total_emi_val:
        safe_set(ws, 'F44', total_emi_val)

    safe_set(ws, 'C45', ob_sum.get('undisclosed_found', ''))
    safe_set(ws, 'C46', ob_sum.get('recent_enquiries', ''))
    print(f"Obligations: {len(obligations)} | outstanding={total_outstanding} | emi={total_emi_val}")

    # ── Family rows 51-56 ─────────────────────────────────────────────────
    members = cs.get('family_members', [])
    for i, member in enumerate(members[:6]):
        row = 51 + i
        if not isinstance(member, dict):
            safe_set(ws, f'B{row}', str(member))
            continue
        safe_set(ws, f'B{row}', member.get('name', ''))
        safe_set(ws, f'C{row}', member.get('relation', ''))
        if member.get('age') is not None:
            safe_set(ws, f'D{row}', member['age'])
        safe_set(ws, f'E{row}', member.get('occupation', ''))
        safe_set(ws, f'F{row}', member.get('met_in_person', ''))
        safe_set(ws, f'G{row}', member.get('aware_of_loan', ''))
        safe_set(ws, f'H{row}', member.get('remarks', ''))

    fam = cs.get('family_summary', {})
    if fam.get('family_size') is not None:
        safe_set(ws, 'C57', fam['family_size'])
    if fam.get('dependents_count') is not None:
        safe_set(ws, 'C58', fam['dependents_count'])
    if fam.get('earning_members') is not None:
        safe_set(ws, 'C59', fam['earning_members'])
    if fam.get('monthly_expense') is not None:
        safe_set(ws, 'C60', fam['monthly_expense'])
    safe_set(ws, 'C61', fam.get('co_borrower_name', ''))
    safe_set(ws, 'C62', fam.get('co_borrower_aware', ''))
    print(f"Family: {len(members)} members | size={fam.get('family_size')} | expense={fam.get('monthly_expense')}")

    # ── Repayment snapshot rows 65-72: replace formulas with computed values
    safe_set(ws, 'E65', derived['total_income'] or '')
    safe_set(ws, 'E66', derived['monthly_expense'] or '')
    safe_set(ws, 'E67', derived['total_emi'] or '')
    safe_set(ws, 'E68', derived['monthly_surplus'] or '')
    # E69 = proposed EMI (user fills manually, leave blank)
    # E70 = surplus cover — leave blank (needs proposed EMI)
    # E71 = FOIR — leave blank (needs proposed EMI)
    print(f"Repayment: income={derived['total_income']} expense={derived['monthly_expense']} EMI={derived['total_emi']} surplus={derived['monthly_surplus']}")

    # ── Other key facts C75:C82 ───────────────────────────────────────────
    facts = cs.get('other_key_facts', {})
    if facts:
        safe_set(ws, 'C75', facts.get('end_use', ''))
        safe_set(ws, 'C76', facts.get('collateral_details', ''))
        safe_set(ws, 'C77', facts.get('neighbour_checks', ''))
        safe_set(ws, 'C78', facts.get('red_flags', ''))
        safe_set(ws, 'C79', facts.get('discrepancies', ''))
        safe_set(ws, 'C80', facts.get('bcm_recommendation', ''))
        safe_set(ws, 'C81', facts.get('evaluators_note', ''))

        red_flags = facts.get('red_flags', '')
        bcm_rec = facts.get('bcm_recommendation', '')
        needs_attention = ('PD-RF' in red_flags or 'NOT STATED' in bcm_rec.upper()
                          or 'INVALID' in bcm_rec.upper())
        safe_set(ws, 'C82', 'YES — route to RCM/CCRO with the note above' if needs_attention else 'NO')
        print("Other key facts filled")

    print("Call Summary complete")


# ─────────────────────────────────────────────────────────────────────────────
# GAP & COACHING REPORT SHEET
# ─────────────────────────────────────────────────────────────────────────────

def fill_gap_report(wb, evaluation_data: Dict[str, Any], derived: Dict[str, Any]):
    """
    Fill the Gap & Coaching Report sheet.
    The template has formulas referencing Scorecard — we replace them with computed values
    so the sheet shows correctly without Excel recalculation.
    """
    sheet_name = next((n for n in wb.sheetnames if 'GAP' in n.upper()), None)
    if not sheet_name:
        print("WARNING: Gap report sheet not found")
        return

    ws = wb[sheet_name]
    criteria = evaluation_data.get('ai_evaluation', {}).get('criteria', [])
    criteria_by_id = {c['id']: c for c in criteria}

    # ── Score section rows 4-5 ────────────────────────────────────────────
    safe_set(ws, 'C4', derived['total_score'])
    safe_set(ws, 'C5', derived['points_to_90'])
    print(f"Gap report score: {derived['total_score']} | points to 90+: {derived['points_to_90']}")

    # ── Gates rows 8-15 ──────────────────────────────────────────────────
    gate_order = ['K1','K2','K3','K4','K5','K6','K7','K8']
    gate_requirements = {
        'K1': 'Valid recording — ≥ 15 minutes (MANDATORY), not beyond 30; audible; recorded the SAME DAY as the visits, after all sites were covered.',
        'K2': 'The BCM personally met the MAIN BORROWER at the business premises — not by phone, not through a relative or third party.',
        'K3': 'EVERY declared income-generating business/site physically visited (all streams; dairy shed and agri land included where considered).',
        'K4': 'Residence AND collateral visited (or confirmed same site); family members and the co-borrower spoken to.',
        'K5': 'Neighbour verification done — at least 2 business neighbours and 2 residence neighbours enquired, names/contacts collected.',
        'K6': 'No staged/fake setup left unreported — any suspicion stated and escalated, not smoothed over.',
        'K7': 'Collateral papers physically seen — original title documents sighted (or exact status stated), actual owner identified.',
        'K8': 'Narration consistent with geo-tagged photographs and documents uploaded on PRAGATI — no material contradiction.',
    }
    for i, gid in enumerate(gate_order):
        row = 8 + i
        status = derived['gate_results'].get(gid, 'PASS')
        safe_set(ws, f'A{row}', gid)
        if status == 'FAIL':
            safe_set(ws, f'B{row}', f"GATE FAILED — {gate_requirements[gid]}")
        else:
            safe_set(ws, f'B{row}', '—')
        safe_set(ws, f'C{row}', status)

    # ── Gap items rows 18-48 ─────────────────────────────────────────────
    # Template has one row per criterion (30 rows = rows 18-47, plus row 48 for H3)
    criteria_order = [
        'A1','A2','A3','A4',
        'B1','B2','B3','B4','B5','B6',
        'C1','C2','C3',
        'D1','D2','D3','D4',
        'E1','E2','E3','E4',
        'F1','F2','F3','F4',
        'G1','G2','G3',
        'H1','H2','H3',
    ]
    # Criterion checklist text for "Cover:" guidance
    checklist_text = {
        'A1': 'Application ID, customer name, village/town, product, loan amount — stated at start; confirm bureau/PRAGATI prep.',
        'A2': 'Date/time of each visit, who was met, who accompanied; borrower met personally at business.',
        'A3': 'Geo-tagged photos — premises, signboard, stock, bills, neighbour signboards, ELSI assets — confirmed taken & uploaded.',
        'A4': 'Recorded same day after all visits; 15–30 minutes total.',
        'B1': 'For EACH income stream: premises, stock, staff, foot traffic, equipment, signboard — concrete observations + documented.',
        'B2': 'Borrower personally showed the business; day-to-day operations explained on site.',
        'B3': 'Daily/monthly sales, annual turnover, margin %, seasonality — PRAGATI-model arithmetic aloud; totals stated.',
        'B4': 'Bills/UPI/passbook asked, produced, photographed; absence stated honestly. AA completeness confirmed.',
        'B5': '2+ neighbouring businesses spoken to — names/contacts, signboards photographed; substance of what they said.',
        'B6': 'Explicit view per stream that setup is genuine (own stock, truly operating) — or suspicion escalated (PD-RF-1).',
        'C1': 'Land seen (or GPS video); owner named, checked on Bhulekh/SatSure; acreage stated. Leased land ≠ agri income.',
        'C2': 'Crops per season, what is standing/sown; mandi receipts (last 4 seasons avg) or sale proof examined.',
        'C3': 'Actually cultivating? KCC limit/obligation noted. False agri claim = PD-RF-5.',
        'D1': 'ALL EMIs of borrower AND co-borrowers — every CRIF/CIBIL loan, MFI/SHG/KCC/gold/hand loans — lender + monthly amount each.',
        'D2': 'Family size, dependents, Rs.2,000–3,000/member norm, ELSI living standard consistent with income.',
        'D3': 'Income − expenses − EMIs = surplus vs proposed EMI; disposable floor checked aloud.',
        'D4': 'Loan enquiries last 3 months — lender, amount, purpose, and OUTCOME of each; cross-checked with bureau.',
        'E1': 'Ownership, years at address, construction/condition, household assets; utility bills/Patta; ELSI photos uploaded.',
        'E2': 'Each person named + loan awareness, end use, repayment intent; co-borrower MANDATORILY met in person + mortgage-aware; Samagra KYC checked.',
        'E3': '2+ residence neighbours — names/contacts; reputation, conduct, any adverse information.',
        'E4': 'Confirmed told: Business LAP, indicative EMI, Clean Track Reward, ZERO COMMISSION policy.',
        'F1': 'Type, self-occupied/rented, construction quality, age (20-yr rule), carpet area vs 400/100 sq.ft, independent access, kitchen/toilet.',
        'F2': 'Original title deeds sighted (or exact status stated), owner named, acquisition story, mutation, heirs/NOC.',
        'F3': 'Value estimate with basis (local rates, transactions, neighbour input); LTV sense; distress-value view (80% cap).',
        'F4': 'Lien-free, no dispute, not ST/tribal, not near HT line/riverbank/encroachment, no negative zone, no demolition risk.',
        'G1': 'Stated purpose vs ground reality; how much/where/when questioned; not a restricted use.',
        'G2': 'Total income vs loan sought; capacity AND intent view; every red flag found — or explicit statement none found.',
        'G3': 'Recommend/with conditions/reject — with specific reasons. Borrower told decision timeline.',
        'H1': 'Section order followed; names, numbers, observations — no vague filler.',
        'H2': '15–30 minutes, single continuous recording, ≥90% intelligible.',
        'H3': 'Facts vs opinion separated; what could not be verified stated; discrepancies flagged.',
    }

    # Criterion name lookup from our own definition
    CRITERIA_NAMES = {
        'A1': 'Case identified on record', 'A2': 'All visits confirmed — when and whom',
        'A3': 'Photo/document capture confirmed', 'A4': 'Same-day recording, correct duration',
        'B1': 'Every business visited and described as seen', 'B2': 'Borrower met at business; operations explained on site',
        'B3': 'Income assessed per stream with arithmetic', 'B4': 'Income documents asked and examined',
        'B5': 'Business-neighbour enquiry narrated', 'B6': 'Genuineness view — no staged setup',
        'C1': 'Land visited; ownership and acreage verified', 'C2': 'Crops and yield verified with proof',
        'C3': 'Cultivation genuineness + KCC noted',
        'D1': 'All obligations enumerated with amounts', 'D2': 'Family expenses and lifestyle assessed',
        'D3': 'Surplus arithmetic narrated', 'D4': 'Recent loan enquiries (last 3 months) probed with outcomes',
        'E1': 'Residence visited and described', 'E2': 'Family and co-borrower interviewed',
        'E3': 'Residence-neighbour enquiry narrated', 'E4': 'Customer education confirmed',
        'F1': 'Collateral visited and physically described', 'F2': 'Original papers seen; owner and title chain',
        'F3': 'Approximate market value with basis', 'F4': 'Risk screen stated',
        'G1': 'End use verified on ground', 'G2': 'Overall credit view',
        'G3': 'Clear recommendation with reasons',
        'H1': 'Structured and specific', 'H2': 'Duration and audibility', 'H3': 'Honest flagging of gaps',
    }

    for i, cid in enumerate(criteria_order):
        row = 18 + i
        c = criteria_by_id.get(cid, {})
        coverage = c.get('coverage', 'None')
        max_pts = CRITERIA_MAX_PTS.get(cid, 0)
        criterion_name = CRITERIA_NAMES.get(cid, cid)

        safe_set(ws, f'A{row}', cid)

        if coverage == 'None':
            safe_set(ws, f'B{row}', f"NOT NARRATED — {criterion_name} | Cover: {checklist_text.get(cid, '')}")
            safe_set(ws, f'C{row}', max_pts)
        elif coverage == 'Partial':
            safe_set(ws, f'B{row}', f"INCOMPLETE — {criterion_name} | Asked but not fully probed/confirmed.")
            safe_set(ws, f'C{row}', max_pts / 2.0)
        elif coverage == 'N/A':
            safe_set(ws, f'B{row}', '—')
            safe_set(ws, f'C{row}', '')
        else:  # Full
            safe_set(ws, f'B{row}', '')
            safe_set(ws, f'C{row}', '')

    # ── Total points lost row 49 ─────────────────────────────────────────
    safe_set(ws, 'C49', derived['total_points_lost'])
    print(f"Gap report: {len(derived['gap_items'])} gaps | total lost={derived['total_points_lost']}")


# ─────────────────────────────────────────────────────────────────────────────
# LAMBDA HANDLER
# ─────────────────────────────────────────────────────────────────────────────

def lambda_handler(event, context):
    try:
        print(f"Excel request: {json.dumps(event)}")

        evaluation_id = event['evaluation_id']
        result_s3_key = event['result_s3_key']
        created_at    = event.get('created_at')

        # Mark generating
        table = dynamodb.Table(DYNAMODB_TABLE)
        table.update_item(
            Key={'evaluation_id': evaluation_id, 'created_at': created_at},
            UpdateExpression='SET #status = :s, excel_generation_started_at = :t',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={':s': 'EXCEL_GENERATING', ':t': datetime.utcnow().isoformat()}
        )

        # Load evaluation result
        resp = s3_client.get_object(Bucket=REPORTS_BUCKET, Key=result_s3_key)
        evaluation_data = json.loads(resp['Body'].read())

        # Download template
        print(f"Downloading template: s3://{TEMPLATE_BUCKET}/{TEMPLATE_KEY}")
        tmpl = s3_client.get_object(Bucket=TEMPLATE_BUCKET, Key=TEMPLATE_KEY)
        template_bytes = tmpl['Body'].read()

        # Pre-compute all derived values
        # Use scores already calculated by evaluation Lambda where available,
        # only recompute what is needed for Excel filling (section subtotals, gap items etc.)
        derived = compute_derived(evaluation_data)
        
        # Override total_score/grade_band/verdict with what evaluation Lambda stored
        # to keep Excel consistent with DynamoDB and history page
        stored_scoring = evaluation_data.get('scoring', {})
        if stored_scoring.get('total_score') is not None:
            stored_score = float(stored_scoring['total_score'])
            derived['total_score'] = stored_score
            derived['points_to_90'] = max(0, round(90 - stored_score, 1))
        if stored_scoring.get('grade_band'):
            derived['grade_band'] = stored_scoring['grade_band']
        if stored_scoring.get('verdict'):
            derived['verdict'] = stored_scoring['verdict']
        if stored_scoring.get('gate_results'):
            derived['gate_results'] = stored_scoring['gate_results']
        
        print(f"Derived: score={derived['total_score']} | {derived['grade_band']} | gaps={len(derived['gap_items'])}")

        # Load workbook
        wb = openpyxl.load_workbook(BytesIO(template_bytes))

        # Fill all sheets
        fill_scorecard(wb['Scorecard'], evaluation_data, derived)
        fill_call_summary(wb, evaluation_data, derived)
        fill_gap_report(wb, evaluation_data, derived)

        # Save
        output = BytesIO()
        wb.save(output)
        output.seek(0)

        app_id = evaluation_data.get('application_id', evaluation_id)
        excel_key = f"evaluations/{evaluation_id}/scorecard.xlsx"
        s3_client.put_object(
            Bucket=REPORTS_BUCKET,
            Key=excel_key,
            Body=output.getvalue(),
            ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            ContentDisposition=f'attachment; filename="Audio_PD_Scorecard_{app_id}.xlsx"'
        )
        print(f"Excel saved: s3://{REPORTS_BUCKET}/{excel_key}")

        if created_at:
            table.update_item(
                Key={'evaluation_id': evaluation_id, 'created_at': created_at},
                UpdateExpression='SET excel_s3_key = :k, #status = :s, completed_at = :t, total_score = :sc, grade_band = :gb, verdict = :v',
                ExpressionAttributeNames={'#status': 'status'},
                ExpressionAttributeValues={
                    ':k': excel_key,
                    ':s': 'COMPLETED',
                    ':t': datetime.utcnow().isoformat(),
                    ':sc': Decimal(str(derived['total_score'])),
                    ':gb': derived['grade_band'],
                    ':v': derived['verdict'],
                }
            )
            print(f"DynamoDB synced: score={derived['total_score']} | {derived['grade_band']}")

        return {
            'statusCode': 200,
            'body': json.dumps({'evaluation_id': evaluation_id, 'excel_s3_key': excel_key,
                                'message': 'Excel scorecard generated successfully'})
        }

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e), 'evaluation_id': event.get('evaluation_id')})
        }
