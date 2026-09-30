"""
Excel Generator Lambda — Prodigee Finance Audio PD
Reads the evaluation-result.json produced by the Evaluation Lambda and fills
two sheets in the Excel template of the scorecard version the result was
scored on (scorecards/registry.json):
  - Scorecard
  - My Result

Templates in S3 (TEMPLATE_BUCKET), per version:
  v1     → scorecards/v1/{bcm_pd,bm_fi,rcm_audio_pd}_scorecard.xlsx
  legacy → templates/{bcm_pd,bm_fi,rcm_audio_pd}_template.xlsx

Only the evaluator's inputs are written; the template's formulas are kept.
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

try:
    import scorecards
except ImportError:  # running from the repo (tests / tools), not the Lambda zip
    import sys as _sys
    _sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
    import scorecards

s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')

REPORTS_BUCKET  = os.environ['REPORTS_BUCKET']
TEMPLATE_BUCKET = os.environ.get('TEMPLATE_BUCKET', REPORTS_BUCKET)
DYNAMODB_TABLE  = os.environ['DYNAMODB_TABLE']

# Criteria, sections and the template come from the scorecard version the
# result was scored on — never from the current version — so a legacy result
# regenerated today still lands in the sheet it was marked against.
def get_config(call_type: str, version: str = None):
    cfg = scorecards.config(call_type, version or scorecards.current_version())
    return cfg['criteria'], cfg['section_pts'], cfg['section_names']


# ── Helpers ───────────────────────────────────────────────────────────────────

def safe_set(ws, cell_ref: str, value):
    """
    Write a value to a cell, resolving merged-cell anchors — but NEVER over a
    formula.

    Two bugs came out of the old version. Writing to a merged cell resolved to
    its anchor, so `D4` landed on `C4` and wiped the printed header label
    ("Recording length (minutes)"). And writing computed numbers over the
    template's formulas turned a live scorecard into a dead one. Both were
    raised in the client's review, so both are blocked here rather than relying
    on every call site to remember.
    """
    cell = ws[cell_ref]
    if isinstance(cell, MergedCell):
        for mr in ws.merged_cells.ranges:
            if cell.coordinate in mr:
                anchor = ws.cell(row=mr.min_row, column=mr.min_col)
                if isinstance(anchor, MergedCell):
                    return
                if isinstance(anchor.value, str) and anchor.value.startswith('='):
                    print(f"  skip {cell_ref}: would overwrite formula at {anchor.coordinate}")
                    return
                anchor.value = value
                return
        return
    if isinstance(cell.value, str) and cell.value.startswith('='):
        print(f"  skip {cell_ref}: template formula preserved")
        return
    cell.value = value


def find_item_rows(ws) -> dict:
    """
    Scan column A for criterion IDs like A1, B2, G4, I3 etc.
    MUST items are annotated in some sheets (e.g. "My Result") with a
    trailing star marker — "A1 ★" / "A1*" — which must still match the
    plain id, otherwise MUST-item rows are silently skipped.
    Returns {id: row_number} (1-based).
    """
    rows = {}
    for row in range(1, 200):
        val = ws.cell(row=row, column=1).value
        if val and isinstance(val, str):
            m = re.match(r'^([A-J]\d{1,2})\s*[★*]?\s*$', val.strip())
            if m:
                rows[m.group(1)] = row
    print(f"  Item rows found: {len(rows)}")
    return rows


def find_summary_rows(ws) -> dict:
    """
    Scan every cell in the first 8 columns for summary keyword labels.
    Returns {keyword_key: row_number}.
    Keywords detected:
      recording_valid, points_applicable, points_scored, score_100,
      must_count, must_list, result
    """
    mapping = {}
    patterns = {
        'recording_valid':    re.compile(r'recording\s+valid', re.I),
        'points_applicable':  re.compile(r'points\s+applicable', re.I),
        'points_scored':      re.compile(r'points\s+scored', re.I),
        'score_100':          re.compile(r'score\s*\(?\s*out\s+of\s+100', re.I),
        'must_count':         re.compile(r'must\s+items\s+not\s+fully\s+covered\s*\(?\s*count', re.I),
        'must_list':          re.compile(r'must\s+items\s+missed', re.I),
        'result':             re.compile(r'^result\s*$', re.I),
    }
    for row in range(1, 200):
        for col in range(1, 9):
            val = ws.cell(row=row, column=col).value
            if not val or not isinstance(val, str):
                continue
            v = val.strip()
            for key, pat in patterns.items():
                if key not in mapping and pat.search(v):
                    mapping[key] = row
    print(f"  Summary rows found: {list(mapping.keys())}")
    return mapping


def score_symbol(score_str: str) -> str:
    if score_str == 'Yes':  return '✔'
    if score_str == 'Half': return '½'
    return '✘'


# Column used for the audit trail on each sheet. Both are unused by the
# templates (Scorecard runs A-H, My Result A-F).
EVIDENCE_COL = {'scorecard': 'I', 'my_result': 'G'}


def format_evidence(item: dict) -> str:
    """
    Render the proof cell: the timestamp in the recording plus the words spoken
    there, so a reviewer can jump to that point and hear it for themselves.

    The timestamp is computed by the evaluation Lambda by locating the quoted
    words in the diarised segments, so it points at real audio. When the quote
    could not be located we say so explicitly rather than showing a time that
    might not be real.
    """
    quote = (item.get('evidence') or '').strip()
    if not quote:
        return ''
    # The evaluator occasionally pastes a long passage; keep the cell readable.
    # The full quote is always preserved in evaluation-result.json.
    if len(quote) > 280:
        quote = quote[:277].rstrip() + '…'
    ts       = item.get('evidence_timestamp') or ''
    verified = item.get('evidence_verified')
    speaker  = item.get('evidence_speaker') or ''
    if verified and ts:
        who = f" {speaker}" if speaker else ''
        return f'[{ts}]{who} "{quote}"'
    return f'[time not located] "{quote}" — could not be matched to the recording; verify manually'


def write_evidence_header(ws, col: str, row: int, width: float = 60):
    """Label the evidence column and make it readable without manual resizing."""
    from openpyxl.styles import Alignment, Font
    cell = ws[f'{col}{row}']
    if not isinstance(cell, MergedCell):
        cell.value = 'Evidence — timestamp & exact words from the recording'
        try:
            header_ref = ws[f'A{row}']
            if not isinstance(header_ref, MergedCell) and header_ref.font:
                cell.font = Font(bold=True, size=header_ref.font.size or 11)
            cell.alignment = Alignment(wrap_text=True, vertical='center')
        except Exception:
            pass
    try:
        ws.column_dimensions[col].width = width
    except Exception:
        pass


# Mirrors normalise_score() in the evaluation Lambda. Results written by older
# versions (or a future evaluator that answers "Full"/"partial") must not be
# silently read as zero here.
_SCORE_ALIASES = {
    'yes': 'Yes', 'full': 'Yes', 'fully': 'Yes', 'fully covered': 'Yes', 'complete': 'Yes',
    'half': 'Half', 'partial': 'Half', 'partially': 'Half', 'partially covered': 'Half',
    'no': 'No', 'none': 'No', 'not covered': 'No', 'missing': 'No', 'absent': 'No',
    'n/a': 'N/A', 'na': 'N/A', 'not applicable': 'N/A',
}


def normalise_score(value) -> str:
    if not isinstance(value, str):
        return 'No'
    v = value.strip()
    if v in ('Yes', 'Half', 'No', 'N/A'):
        return v
    return _SCORE_ALIASES.get(v.lower(), 'No')


def validate_template_rows(item_rows: dict, criteria: list, sheet_label: str) -> list:
    """
    The scorecard is filled by scanning column A for criterion ids, so a renamed
    or reordered template row is silently skipped and its cells stay blank (this
    is exactly how every MUST-item row on the 'My Result' sheet went unfilled
    until the "A1 ★" marker was accounted for). Report the drift instead.
    """
    expected = {c['id'] for c in criteria}
    found    = set(item_rows.keys())
    missing  = sorted(expected - found)
    extra    = sorted(found - expected)
    warnings = []
    if missing:
        warnings.append(f"{sheet_label}: {len(missing)} criterion row(s) not found in template "
                        f"and left unfilled: {', '.join(missing)}")
    if extra:
        warnings.append(f"{sheet_label}: template has row(s) for unknown criteria: {', '.join(extra)}")
    for w in warnings:
        print(f"  TEMPLATE WARNING — {w}")
    return warnings


def safe_float(val) -> float:
    if val is None: return 0.0
    try: return float(val)
    except: return 0.0


# ── Score computation ─────────────────────────────────────────────────────────

def compute_scores(items: list, criteria: list, section_pts: dict, section_names: dict) -> dict:
    """
    Recompute everything from items list (as stored in evaluation-result.json).
    items: list of {id, score, pts_scored, note}
    Returns dict with all derived data needed for both sheets.
    """
    items_by_id = {i['id']: {**i, 'score': normalise_score(i.get('score'))} for i in items}
    crit_map    = {c['id']: c for c in criteria}

    # Per-section totals
    sections = {}
    for sid in section_pts:
        sections[sid] = {
            'name':       section_names[sid],
            'max':        0,
            'applicable': 0,
            'scored':     0.0,
        }
    for c in criteria:
        sid  = c['sec']
        item = items_by_id.get(c['id'], {})
        score_str = item.get('score', 'No')
        pts = c['pts']
        sections[sid]['max'] += pts
        if score_str != 'N/A':
            sections[sid]['applicable'] += pts
            if score_str == 'Yes':
                sections[sid]['scored'] += pts
            elif score_str == 'Half':
                sections[sid]['scored'] += pts / 2.0

    total_applicable = sum(s['applicable'] for s in sections.values())
    total_scored     = sum(s['scored']     for s in sections.values())
    total_score      = round(total_scored / total_applicable * 100, 1) if total_applicable > 0 else 0.0

    # Section percentages and comments
    for sid, s in sections.items():
        pct = round(s['scored'] / s['applicable'] * 100) if s['applicable'] > 0 else 0
        s['pct'] = pct
        if pct == 100:   s['comment'] = 'Fully covered'
        elif pct >= 80:  s['comment'] = 'Good — small gaps'
        elif pct >= 50:  s['comment'] = 'Weak — see items below'
        else:            s['comment'] = 'Mostly missed'

    # MUST failures
    must_failures = []
    for c in criteria:
        if c['must']:
            item = items_by_id.get(c['id'], {})
            s    = item.get('score', 'No')
            if s in ('Half', 'No'):
                must_failures.append(c['id'])

    # Verdict
    if must_failures:
        missed_str = ', '.join(must_failures)
        verdict = f'NOT ACCEPTED — MUST items not fully covered: {missed_str}'
        grade   = 'NOT ACCEPTED'
    elif total_score >= 90:
        verdict = 'EXCELLENT — Model recording'
        grade   = 'EXCELLENT'
    elif total_score >= 75:
        verdict = 'ACCEPTED'
        grade   = 'ACCEPTED'
    else:
        verdict = 'NOT ACCEPTED — Score below 75'
        grade   = 'NOT ACCEPTED'

    # All gaps sorted by points lost (descending)
    gaps = []
    for c in criteria:
        item  = items_by_id.get(c['id'], {})
        score = item.get('score', 'No')
        if score in ('No', 'Half'):
            lost = c['pts'] if score == 'No' else c['pts'] / 2.0
            gaps.append({
                'id':       c['id'],
                'text':     item.get('note', '')[:100],
                'pts_lost': lost,
                'score':    score,
            })
    gaps.sort(key=lambda x: -x['pts_lost'])

    return {
        'items_by_id':       items_by_id,
        'sections':          sections,
        'total_applicable':  total_applicable,
        'total_scored':      total_scored,
        'total_score':       total_score,
        'grade':             grade,
        'verdict':           verdict,
        'must_failures':     must_failures,
        'gaps':              gaps,
        'top3_gaps':         gaps[:3],
    }


# ── Sheet 1: Scorecard ────────────────────────────────────────────────────────

def fill_scorecard(ws, result: dict, derived: dict, criteria: list, call_type: str,
                   e6_input: str = None):
    """
    Fill ONLY the cells a human evaluator would fill, and nothing else.

    The client's templates are live spreadsheets: 58-77 formulas on Scorecard
    and 164-215 on My Result compute every score, subtotal, MUST list and
    verdict from a handful of inputs. Our previous version hard-coded values
    over all of them and wrote the header values on top of the C-column
    labels, which is what came back as "Bell Blaze overwrote the header ...
    hard-coded every formula in the Scorecard sheet".

    Inputs we own:
      B4:B7  application id, customer, officer, date
      E4     recording length in minutes
      E6     BCM: farm/dairy/other family income? (legacy: drives section C;
             v1: for record only) | BM & RCM: both sides audible?
      E7     evaluator
      E/G/I  per criterion: the mark, the note, and the evidence
    E5 (uploaded to PRAGATI the same day) is deliberately left blank - it
    cannot be known from the audio, and asserting it was a documented mistake.
    """
    header = result.get('header', {})
    evaluated_at = result.get('evaluated_at', datetime.utcnow().isoformat())
    try:
        date_str = datetime.fromisoformat(evaluated_at.replace('Z', '')).strftime('%d-%b-%Y')
    except Exception:
        date_str = datetime.utcnow().strftime('%d-%b-%Y')

    safe_set(ws, 'B4', header.get('application_id', 'N/A'))
    safe_set(ws, 'B5', header.get('customer_name', 'N/A'))
    safe_set(ws, 'B6', header.get('officer_name', 'N/A'))
    safe_set(ws, 'B7', date_str)

    # Values belong in the merged E:G block; C:D holds the printed label.
    dur_secs = result.get('duration_seconds')
    if dur_secs:
        safe_set(ws, 'E4', round(dur_secs / 60, 1))

    e6_input = e6_input or ('other_income' if call_type == 'BCM_PHYSICAL_PD' else 'both_sides_audible')
    if e6_input == 'other_income':
        # Legacy BCM templates compute "=100-IF(E6=\"No\",10,0)" from this;
        # from v1 the cell is informational and section C is always scored.
        safe_set(ws, 'E6', 'Yes' if result.get('other_income', True) else 'No')
    elif (result.get('speaker_count') or 0) >= 2 and (result.get('transcript_chars') or 0) > 500:
        # Both sides audible: two speakers were transcribed at length.
        safe_set(ws, 'E6', 'Yes')

    safe_set(ws, 'E7', 'AI evaluation (Prodigee Audio PD)')

    # ── Item rows: the mark, the note and the evidence only ──────────────────
    item_rows = find_item_rows(ws)
    derived.setdefault('template_warnings', []).extend(
        validate_template_rows(item_rows, criteria, 'Scorecard'))
    items_by_id = derived['items_by_id']
    filled = 0
    for cid, row in item_rows.items():
        item = items_by_id.get(cid, {})
        safe_set(ws, f'E{row}', item.get('score', 'No'))
        safe_set(ws, f'G{row}', (item.get('note') or '')[:250])
        safe_set(ws, f"{EVIDENCE_COL['scorecard']}{row}", format_evidence(item))
        filled += 1

    header_row = min(item_rows.values()) - 2 if item_rows else 9
    write_evidence_header(ws, EVIDENCE_COL['scorecard'], header_row)
    if item_rows:
        from openpyxl.utils import column_index_from_string
        ev_col = column_index_from_string(EVIDENCE_COL['scorecard'])
        # Styled after the note column (G): H beside it is the template's
        # unstyled "(helper)" column.
        for row in range(header_row, max(item_rows.values()) + 1):
            match_neighbour_style(ws, row, ev_col, src_col=7)
    print(f"  Scorecard inputs filled: {filled} items (formulas left intact)")

    write_red_flags_block(ws, result, max(item_rows.values()) if item_rows else 60)


def write_red_flags_block(ws, result: dict, last_item_row: int):
    """
    Write the red flags below the summary block.

    Added because the client's review put it plainly: "the evaluator only fills
    what has a cell, and right now red flags have no cell" - and on a call full
    of them, none were recorded.
    """
    from openpyxl.styles import Alignment, Font

    flags = result.get('red_flags') or []
    # Find the end of the template's own summary block so we sit below it.
    row = last_item_row + 1
    for r in range(last_item_row + 1, last_item_row + 25):
        if any(ws.cell(row=r, column=c).value not in (None, '') for c in range(1, 4)):
            row = r
    row += 2

    title = ws.cell(row=row, column=1)
    if isinstance(title, MergedCell):
        return
    title.value = 'RED FLAGS OBSERVED (auto-detected from the recording)'
    title.font = Font(bold=True)
    row += 1
    for col, head in ((1, 'RF code'), (2, 'What was observed'), (3, 'Time'),
                      (4, 'Exact words from the recording')):
        cell = ws.cell(row=row, column=col)
        if not isinstance(cell, MergedCell):
            cell.value = head
            cell.font = Font(bold=True)
    row += 1

    if not flags:
        cell = ws.cell(row=row, column=1)
        if not isinstance(cell, MergedCell):
            cell.value = 'None detected'
        return

    for f in flags:
        for col, val in ((1, f.get('code', '')),
                         (2, f"{f.get('title','')} — {f.get('detail','')}".strip(' —')),
                         (3, f.get('timestamp', '')),
                         (4, f.get('evidence', ''))):
            cell = ws.cell(row=row, column=col)
            if not isinstance(cell, MergedCell):
                cell.value = val
                cell.alignment = Alignment(wrap_text=True, vertical='top')
        row += 1
    print(f"  Red flags written: {len(flags)}")


def cache_formula_values(xlsx_bytes: bytes, sheet_name: str, values: dict) -> bytes:
    """
    Give formula cells a cached result, so the sheet shows its numbers
    everywhere — not only in desktop Excel.

    A formula cell written by openpyxl carries the formula but no cached value.
    Desktop Excel recalculates on open and shows the right number, but every
    preview pane, browser viewer and programmatic reader shows a BLANK score —
    which is how "the score is missing in the Excel" happens.

    Writing plain values instead would fix that but kill the live sheet the
    client asked us to stop destroying. So we keep the formula AND inject the
    value we already computed in Python as its cached result: the sheet reads
    correctly immediately, and still recalculates the moment anyone edits a mark.
    """
    import re as _re
    import zipfile as _zip

    def sheet_path(zin):
        wb_xml = zin.read('xl/workbook.xml').decode('utf-8')
        m = _re.search(rf'<sheet[^>]*name="{_re.escape(sheet_name)}"[^>]*r:id="(rId\d+)"', wb_xml)
        if not m:
            return None
        rels = zin.read('xl/_rels/workbook.xml.rels').decode('utf-8')
        # Attribute order varies between writers, so find the whole
        # <Relationship> element for this id and pull Target out of it.
        rel = next((r for r in _re.findall(r'<Relationship\b[^>]*/?>', rels)
                    if f'Id="{m.group(1)}"' in r), None)
        if not rel:
            return None
        m2 = _re.search(r'Target="([^"]+)"', rel)
        if not m2:
            return None
        target = m2.group(1)
        # Target is sometimes absolute ("/xl/worksheets/sheet1.xml") and
        # sometimes relative to xl/ ("worksheets/sheet1.xml").
        return target.lstrip('/') if target.startswith('/') else 'xl/' + target

    def inject(xml: str) -> str:
        for ref, val in values.items():
            pattern = _re.compile(
                rf'<c r="{ref}"((?:\s+[a-zA-Z:]+="[^"]*")*)\s*>((?:(?!</c>).)*?)</c>', _re.S)

            def repl(m):
                attrs, body = m.group(1), m.group(2)
                if '<f' not in body:
                    return m.group(0)                      # not a formula cell
                body = _re.sub(r'<v\s*/>|<v[^>]*>.*?</v>', '', body, flags=_re.S)
                attrs = _re.sub(r'\s+t="[^"]*"', '', attrs)  # drop any existing type
                if isinstance(val, str):
                    attrs += ' t="str"'                    # formula returning text
                return f'<c r="{ref}"{attrs}>{body}<v>{_escape(val)}</v></c>'

            xml = pattern.sub(repl, xml, count=1)
        return xml

    src, out = BytesIO(xlsx_bytes), BytesIO()
    with _zip.ZipFile(src) as zin:
        path = sheet_path(zin)
        if not path or path not in zin.namelist():
            raise KeyError(f'could not locate sheet XML for {sheet_name!r}')
        with _zip.ZipFile(out, 'w', _zip.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == path:
                    data = inject(data.decode('utf-8')).encode('utf-8')
                zout.writestr(item, data)
    return out.getvalue()


def _escape(s: str) -> str:
    return (str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def build_cached_values(ws, result: dict, derived: dict, criteria: list) -> dict:
    """The Scorecard values we already know, keyed by cell reference."""
    crit_map = {c['id']: c for c in criteria}
    items_by_id = derived['items_by_id']
    values = {}

    item_rows = find_item_rows(ws)
    for cid, row in item_rows.items():
        item = items_by_id.get(cid, {})
        pts = crit_map.get(cid, {}).get('pts', 0)
        score = item.get('score', 'No')
        values[f'F{row}'] = pts if score == 'Yes' else (pts / 2.0 if score == 'Half' else 0)

    summary = find_summary_rows(ws)
    musts = derived.get('must_failures') or []
    mapping = {
        'points_scored':   round(float(derived['total_scored']), 1),
        'score_100':       float(derived['total_score']),
        'must_count':      len(musts),
        'must_list':       ', '.join(musts) if musts else 'None',
        'result':          derived['verdict'],
        'recording_valid': 'YES' if not result.get('scoring', {}).get('recording_invalid') else
                           'NO — not accepted',
    }
    for key, val in mapping.items():
        if key in summary:
            values[f'C{summary[key]}'] = val
    return values


def link_my_result_evidence(ws, scorecard_name: str):
    """
    The My Result sheet is entirely formula-driven off Scorecard, so the only
    thing to add is an evidence column that follows the same pattern.
    """
    from openpyxl.styles import Font

    item_rows = find_item_rows(ws)
    if not item_rows:
        return 0
    # Each checklist row already pulls its note from Scorecard via a formula;
    # mirror that for evidence instead of copying values in.
    written = 0
    for _cid, row in item_rows.items():
        src = ws.cell(row=row, column=6).value  # col F: =IF(Scorecard!G11="","",Scorecard!G11)
        m = re.search(r"Scorecard!G(\d+)", str(src or ''))
        if not m:
            continue
        cell = ws.cell(row=row, column=7)
        if isinstance(cell, MergedCell):
            continue
        cell.value = f'=IF(Scorecard!I{m.group(1)}="","",Scorecard!I{m.group(1)})'
        written += 1
    header_row = min(item_rows.values()) - 2
    hdr = ws.cell(row=header_row, column=7)
    if not isinstance(hdr, MergedCell):
        hdr.value = 'Evidence — timestamp & exact words'
        hdr.font = Font(bold=True)
    # The added column had no styling, so the quotes looked detached from
    # their rows. Give each cell the look of the one beside it: the header its
    # blue band, section rows their band, item rows their box.
    for row in range(header_row, max(item_rows.values()) + 1):
        match_neighbour_style(ws, row, 7)
    try:
        ws.column_dimensions['G'].width = 60
    except Exception:
        pass
    print(f"  My Result evidence links: {written}")
    return written


def match_neighbour_style(ws, row: int, col: int, src_col: int = None):
    """Style cell (row, col) like the cell to its left — or src_col — or that merge's anchor."""
    from copy import copy
    from openpyxl.styles import Alignment
    cell = ws.cell(row=row, column=col)
    if isinstance(cell, MergedCell):
        return
    src = ws.cell(row=row, column=src_col or col - 1)
    if isinstance(src, MergedCell):
        src = next((ws.cell(row=m.min_row, column=m.min_col) for m in ws.merged_cells.ranges
                    if src.coordinate in m), src)
    cell.border = copy(src.border)
    if src.fill is not None and src.fill.fill_type:
        cell.fill = copy(src.fill)
        cell.font = copy(src.font)
    cell.alignment = Alignment(wrap_text=True, vertical=src.alignment.vertical or 'center')



# ── Lambda handler ────────────────────────────────────────────────────────────

def lambda_handler(event, context):
    print(f"Excel request: {json.dumps(event)}")
    try:
        evaluation_id  = event['evaluation_id']
        result_s3_key  = event['result_s3_key']
        created_at     = event.get('created_at')
        call_type_hint = event.get('call_type', '')

        # Mark generating
        table = dynamodb.Table(DYNAMODB_TABLE)
        table.update_item(
            Key={'evaluation_id': evaluation_id, 'created_at': created_at},
            UpdateExpression='SET #s = :s, excel_generation_started_at = :t',
            ExpressionAttributeNames={'#s': 'status'},
            ExpressionAttributeValues={':s': 'EXCEL_GENERATING', ':t': datetime.utcnow().isoformat()}
        )

        # ── Load evaluation result ────────────────────────────────────────────
        resp = s3_client.get_object(Bucket=REPORTS_BUCKET, Key=result_s3_key)
        result = json.loads(resp['Body'].read())

        # ── Determine call type ───────────────────────────────────────────────
        call_type = (
            result.get('call_type') or
            call_type_hint or
            'BCM_PHYSICAL_PD'
        )
        # Normalise legacy / shorthand values
        ct_map = {
            'BCM': 'BCM_PHYSICAL_PD', 'BCM_PD': 'BCM_PHYSICAL_PD',
            'BM':  'BM_AUDIO_FI',     'BM_FI':  'BM_AUDIO_FI',
            'RCM': 'RCM_AUDIO_PD',    'RCM_PD': 'RCM_AUDIO_PD', 'RCM_TELE_PD': 'RCM_AUDIO_PD',
        }
        call_type = ct_map.get(call_type, call_type)
        result['call_type'] = call_type
        print(f"Call type: {call_type}")

        # Results written before versioning carry no version: they were scored
        # on the legacy templates and must be regenerated into those.
        version = scorecards.resolve_version(result.get('scorecard_version')
                                             or event.get('scorecard_version'))
        rubric = scorecards.config(call_type, version)
        print(f"Scorecard version: {version} ({scorecards.label(version)})")
        criteria, section_pts, section_names = get_config(call_type, version)

        # ── Items from the result ─────────────────────────────────────────────
        items = result.get('items', [])
        if not items:
            raise ValueError("No items found in evaluation result — cannot generate scorecard")

        # ── Compute derived scores ────────────────────────────────────────────
        derived = compute_scores(items, criteria, section_pts, section_names)
        derived['template_warnings'] = []
        # The evaluation Lambda's scoring is authoritative — it is the one the
        # UI shows and the one aligned to the template formulas. Recomputing it
        # here a second way is how the sheet and the dashboard end up
        # disagreeing, so prefer the stored values and only fall back when an
        # older result has none.
        stored = result.get('scoring') or {}
        if stored.get('total_score') is not None:
            derived['total_score'] = float(stored['total_score'])
            derived['grade']       = stored.get('grade', derived['grade'])
            derived['verdict']     = stored.get('verdict', derived['verdict'])
        print(f"Score: {derived['total_score']} | {derived['grade']} | must_failures={derived['must_failures']}")
        if result.get('needs_review'):
            print(f"Evaluation flagged NEEDS REVIEW: {result.get('review_reasons')}")

        # ── Load template ─────────────────────────────────────────────────────
        template_key = rubric['template_s3_key']
        print(f"Template: s3://{TEMPLATE_BUCKET}/{template_key}")
        tmpl_obj      = s3_client.get_object(Bucket=TEMPLATE_BUCKET, Key=template_key)
        template_bytes = tmpl_obj['Body'].read()

        wb = openpyxl.load_workbook(BytesIO(template_bytes))
        print(f"Sheets: {wb.sheetnames}")

        # ── Find the two sheets ───────────────────────────────────────────────
        # Scorecard sheet: first sheet that has "SCORE" in its name, else wb.sheetnames[0]
        scorecard_name = next(
            (n for n in wb.sheetnames if 'SCORE' in n.upper()), wb.sheetnames[0]
        )
        # My Result sheet: sheet with "RESULT" or "MY" in its name
        my_result_name = next(
            (n for n in wb.sheetnames if 'RESULT' in n.upper() or 'MY' in n.upper()),
            wb.sheetnames[1] if len(wb.sheetnames) > 1 else None
        )

        print(f"Scorecard sheet: '{scorecard_name}' | My Result sheet: '{my_result_name}'")

        # ── Fill Scorecard (inputs only — every formula is left to Excel) ─────
        fill_scorecard(wb[scorecard_name], result, derived, criteria, call_type,
                       rubric.get('e6_input'))

        # ── My Result is entirely formula-driven; only link the evidence column ─
        if my_result_name:
            link_my_result_evidence(wb[my_result_name], scorecard_name)
        else:
            print("  WARNING: My Result sheet not found — skipping")

        # ── Save workbook ─────────────────────────────────────────────────────
        cached = build_cached_values(wb[scorecard_name], result, derived, criteria)
        output = BytesIO()
        wb.save(output)
        # Formulas alone render blank outside desktop Excel, so give the cells
        # we can compute a cached result while leaving the formulas in place.
        xlsx = output.getvalue()
        try:
            xlsx = cache_formula_values(xlsx, scorecard_name, cached)
            print(f"  Cached values injected for {len(cached)} formula cells")
        except Exception as ex:
            print(f"  Value caching skipped ({ex}) — formulas will compute on open")
        output = BytesIO(xlsx)
        output.seek(0)

        app_id    = result.get('application_id', evaluation_id)
        ct_label  = {'BCM_PHYSICAL_PD': 'BCM_PD', 'BM_AUDIO_FI': 'BM_FI', 'RCM_AUDIO_PD': 'RCM_PD'}.get(call_type, call_type)
        excel_key = f"evaluations/{evaluation_id}/scorecard.xlsx"
        ver_tag   = '' if version == scorecards.LEGACY_VERSION else f"_{scorecards.label(version)}"
        filename  = f"{ct_label}_Scorecard{ver_tag}_{app_id}_{datetime.utcnow().strftime('%d%b%Y')}.xlsx"

        s3_client.put_object(
            Bucket=REPORTS_BUCKET,
            Key=excel_key,
            Body=output.getvalue(),
            ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            ContentDisposition=f'attachment; filename="{filename}"',
        )
        print(f"Excel saved: {excel_key} ({filename})")

        # ── Update DynamoDB ───────────────────────────────────────────────────
        table.update_item(
            Key={'evaluation_id': evaluation_id, 'created_at': created_at},
            UpdateExpression=(
                'SET excel_s3_key = :k, #s = :s, completed_at = :t, '
                'total_score = :sc, grade_band = :gb, verdict = :v, call_type = :ct, '
                'template_warnings = :tw, scorecard_version = :sv'
            ),
            ExpressionAttributeNames={'#s': 'status'},
            ExpressionAttributeValues={
                ':k':  excel_key,
                ':s':  'COMPLETED',
                ':t':  datetime.utcnow().isoformat(),
                ':sc': Decimal(str(derived['total_score'])),
                ':gb': derived['grade'],
                ':v':  derived['verdict'],
                ':ct': call_type,
                ':tw': derived.get('template_warnings', []),
                ':sv': version,
            }
        )
        print(f"DynamoDB updated: COMPLETED | score={derived['total_score']} | {derived['grade']}")

        return {
            'statusCode': 200,
            'body': json.dumps({
                'evaluation_id': evaluation_id,
                'excel_s3_key':  excel_key,
                'call_type':     call_type,
                'total_score':   derived['total_score'],
                'grade':         derived['grade'],
                'message':       'Excel scorecard generated successfully',
            })
        }

    except Exception as e:
        print(f"Excel generator error: {e}")
        import traceback; traceback.print_exc()
        try:
            dynamodb.Table(DYNAMODB_TABLE).update_item(
                Key={'evaluation_id': event['evaluation_id'], 'created_at': event.get('created_at')},
                UpdateExpression='SET #s = :s, error_message = :e',
                ExpressionAttributeNames={'#s': 'status'},
                ExpressionAttributeValues={':s': 'EXCEL_FAILED', ':e': str(e)}
            )
        except Exception:
            pass
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e), 'evaluation_id': event.get('evaluation_id')})
        }
