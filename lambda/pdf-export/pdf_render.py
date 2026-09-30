"""
Render a scorecard workbook to PDF — every visible sheet, in workbook order.

The scorecard is a live spreadsheet: the Scorecard sheet carries cached
values for its formulas, but "My Result" is 50-70 formulas with no cached
result at all (see cache_formula_values in the Excel generator). Reading it
with data_only would print a blank sheet, so every formula is evaluated here
with pycel — the PDF shows exactly what Excel shows on open.

Transcript quotes are Hindi, so text is shaped with HarfBuzz (fpdf2
set_text_shaping). Without shaping, Devanagari matras and conjuncts render
in the wrong order.
"""
import datetime
import os
import re
import tempfile

import openpyxl
from fontTools.ttLib import TTFont
from fpdf import FPDF
from openpyxl.utils import get_column_letter
from pycel import ExcelCompiler

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts')

PAGE_W, PAGE_H = 297.0, 210.0          # A4 landscape, mm
MARGIN = 10.0
HEADER_H = 8.0                          # running header band
FOOTER_H = 7.0
CONTENT_W = PAGE_W - 2 * MARGIN
PAD = 0.9                               # cell padding, mm
MIN_FONT_PT = 6.0
MAX_FONT_PT = 9.0
DEFAULT_COL_WIDTH = 8.43                # Excel default, in characters
DEFAULT_ROW_PT = 15.0
PT_TO_MM = 0.3528
GRID = (191, 191, 191)


_LATIN_CMAP = None


def _family_for(text: str) -> str:
    """
    Noto Sans for Latin text (Devanagari comes in through the fallback font).
    Symbol-only runs such as "✘ MISSED" are set wholly in DejaVu: a fallback
    switch mid-line under text shaping garbles the Latin that follows it.
    """
    global _LATIN_CMAP
    if _LATIN_CMAP is None:
        _LATIN_CMAP = set(TTFont(os.path.join(FONT_DIR, 'NotoSans-Regular.ttf')).getBestCmap())
    if any(0x0900 <= ord(ch) <= 0x097F for ch in text):
        return 'noto'
    if all(ord(ch) in _LATIN_CMAP for ch in text if not ch.isspace()):
        return 'noto'
    return 'dejavu'


def _px(width_chars: float) -> float:
    """Excel column width (characters) → pixels, as Excel itself converts."""
    return width_chars * 7 + 5


def _rgb(color):
    """openpyxl Color → (r, g, b), or None for theme/indexed/auto colours."""
    try:
        if color is None or color.type != 'rgb' or not isinstance(color.rgb, str):
            return None
        argb = color.rgb
        if len(argb) == 8:
            if argb[:2] == '00' and argb[2:] == '000000':
                return None                 # "no colour"
            argb = argb[2:]
        return tuple(int(argb[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return None


def _format_value(value, number_format: str) -> str:
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'TRUE' if value else 'FALSE'
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.strftime('%d-%b-%Y')
    if isinstance(value, (int, float)):
        fmt = number_format or 'General'
        if '%' in fmt:
            decimals = len(fmt.split('.')[1].rstrip('%')) if '.' in fmt else 0
            return f'{value * 100:.{decimals}f}%'
        m = re.search(r'0\.(0+)', fmt)
        if m:
            return f'{value:.{len(m.group(1))}f}'
        if float(value).is_integer():
            return str(int(value))
        return f'{value:.2f}'.rstrip('0').rstrip('.')
    return str(value)


class _PDF(FPDF):
    def __init__(self, doc_title: str):
        super().__init__(orientation='L', unit='mm', format='A4')
        self.doc_title = doc_title
        self.sheet_title = ''
        self.set_auto_page_break(False)
        self.set_margins(MARGIN, MARGIN, MARGIN)
        for family, style, file in [
            ('noto', '', 'NotoSans-Regular.ttf'), ('noto', 'B', 'NotoSans-Bold.ttf'),
            ('deva', '', 'NotoSansDevanagari-Regular.ttf'), ('deva', 'B', 'NotoSansDevanagari-Bold.ttf'),
            ('dejavu', '', 'DejaVuSans.ttf'), ('dejavu', 'B', 'DejaVuSans-Bold.ttf'),
        ]:
            self.add_font(family, style, os.path.join(FONT_DIR, file))
        # Latin first; Devanagari for quotes; DejaVu for ✔ ✘ ★ ½ ₹ and the rest.
        self.set_fallback_fonts(['deva', 'dejavu'], exact_match=False)
        self.set_text_shaping(True)

    def header(self):
        self.set_font('noto', 'B', 8)
        self.set_text_color(31, 56, 100)
        self.set_xy(MARGIN, MARGIN - 4)
        self.cell(CONTENT_W / 2, 5, self.doc_title, align='L')
        self.set_font('noto', '', 8)
        self.set_text_color(110, 110, 110)
        self.cell(CONTENT_W / 2, 5, self.sheet_title, align='R')
        self.set_draw_color(210, 214, 222)
        self.line(MARGIN, MARGIN + 1.5, PAGE_W - MARGIN, MARGIN + 1.5)

    def footer(self):
        self.set_font('noto', '', 7)
        self.set_text_color(130, 130, 130)
        self.set_xy(MARGIN, PAGE_H - MARGIN - 2)
        self.cell(CONTENT_W, 4, f'Page {self.page_no()} of {{nb}}', align='C')


def _sheet_layout(ws):
    """Visible columns (skipping the template's '(helper)' column) and widths."""
    max_col = ws.max_column
    helper_cols = set()
    for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row, 20)):
        for c in row:
            if isinstance(c.value, str) and '(helper)' in c.value.lower():
                helper_cols.add(c.column)
    cols, widths = [], []
    for ci in range(1, max_col + 1):
        dim = ws.column_dimensions.get(get_column_letter(ci))
        if (dim is not None and dim.hidden) or ci in helper_cols:
            continue
        w = dim.width if dim is not None and dim.width else DEFAULT_COL_WIDTH
        cols.append(ci)
        widths.append(_px(w))
    return cols, widths


def _conditional_rules(ws) -> list:
    """
    The sheet's conditional-format fills: [(min_row, min_col, max_row, max_col,
    rule)], in priority order.

    The scorecards colour their marks and verdicts this way (Yes / ✔ green,
    Half / ½ yellow, No / ✘ red, "Weak" / "NOT ACCEPTED" red). Only fills are
    used, and only the two rule shapes the templates contain: cellIs equal /
    notEqual, and expressions built from LEFT(), =, <> and OR / AND.
    """
    rules = []
    for cf in ws.conditional_formatting:
        for cr in cf.sqref.ranges:
            for rule in cf.rules:
                fill = rule.dxf.fill if rule.dxf else None
                colour = None
                if fill is not None:
                    colour = _rgb(fill.bgColor) or _rgb(fill.fgColor)
                if colour:
                    rules.append((cr.min_row, cr.min_col, cr.max_row, cr.max_col, rule, colour))
    return sorted(rules, key=lambda r: r[4].priority or 0)


_REF = re.compile(r'\$?[A-Z]{1,3}\$?\d+')


def _rule_matches(rule, text: str) -> bool:
    try:
        if rule.type == 'cellIs' and rule.formula:
            target = str(rule.formula[0]).strip().strip('"')
            if rule.operator == 'equal':
                return text == target
            if rule.operator == 'notEqual':
                return text != target
            return False
        if rule.type == 'expression' and rule.formula:
            # The rule refers to its range's top-left cell; applied to each
            # cell, that reference is the cell itself.
            expr = _REF.sub('v', str(rule.formula[0]))
            expr = re.sub(r'LEFT\(\s*v\s*,\s*(\d+)\s*\)', r'v[:\1]', expr, flags=re.I)
            expr = expr.replace('<>', '!=')
            expr = re.sub(r'(?<![<>!=])=(?!=)', '==', expr)
            expr = re.sub(r'\bOR\(', '_or(', expr, flags=re.I)
            expr = re.sub(r'\bAND\(', '_and(', expr, flags=re.I)
            return bool(eval(expr, {'__builtins__': {}},
                             {'v': text, '_or': lambda *a: any(a), '_and': lambda *a: all(a)}))
    except Exception:
        return False
    return False


def _conditional_fill(rules: list, r: int, c: int, text: str):
    for min_r, min_c, max_r, max_c, rule, colour in rules:
        if min_r <= r <= max_r and min_c <= c <= max_c and _rule_matches(rule, text):
            return colour
    return None


def _evidence_columns(ws) -> dict:
    """{column: header row} for evidence columns the Excel generator adds."""
    found = {}
    for row in ws.iter_rows():
        for c in row:
            if isinstance(c.value, str) and c.value.strip().lower().startswith('evidence'):
                found.setdefault(c.column, c.row)
    return found


def _last_row(ws) -> int:
    last = 0
    for row in ws.iter_rows():
        for c in row:
            if c.value not in (None, ''):
                last = max(last, c.row)
    return last


def _render_sheet(pdf: _PDF, ws, xl: ExcelCompiler):
    cols, widths_px = _sheet_layout(ws)
    if not cols:
        return
    scale = CONTENT_W / sum(widths_px)                      # mm per px
    col_x, x = {}, MARGIN
    col_w = {}
    for ci, wpx in zip(cols, widths_px):
        col_x[ci], col_w[ci] = x, wpx * scale
        x += wpx * scale
    # Excel prints 11pt text across ~1 px per 0.2646 mm; follow the same shrink.
    font_scale = (scale / 0.2646)

    merges = {}
    covered = set()
    for rng in ws.merged_cells.ranges:
        merges[(rng.min_row, rng.min_col)] = rng
        for r in range(rng.min_row, rng.max_row + 1):
            for c in range(rng.min_col, rng.max_col + 1):
                if (r, c) != (rng.min_row, rng.min_col):
                    covered.add((r, c))

    cf_rules = _conditional_rules(ws)
    evidence_cols = _evidence_columns(ws)

    bottom = PAGE_H - MARGIN - FOOTER_H
    top = MARGIN + HEADER_H - 4
    # The page is opened before layout: measuring wrapped text needs one.
    pdf.sheet_title = ws.title
    pdf.add_page()
    rows = []
    for r in range(1, _last_row(ws) + 1):
        rd = ws.row_dimensions.get(r)
        if rd is not None and rd.hidden:
            continue
        cells = []
        for ci in cols:
            if (r, ci) in covered:
                continue
            cell = ws.cell(row=r, column=ci)
            rng = merges.get((r, ci))
            span = [c for c in cols if rng and rng.min_col <= c <= rng.max_col] or [ci]
            value = cell.value
            if isinstance(value, str) and value.startswith('='):
                try:
                    value = xl.evaluate(f"'{ws.title}'!{cell.coordinate}")
                except Exception:
                    value = '#ERROR'
            text = _format_value(value, cell.number_format)
            size = cell.font.sz or 11
            # A merged range takes its right border from its last cell.
            edge = (cell, ws.cell(row=r, column=span[-1]))
            w = sum(col_w[c] for c in span)
            base_fill = _rgb(cell.fill.fgColor) if cell.fill and cell.fill.fill_type == 'solid' else None
            cells.append({
                'x': col_x[span[0]], 'w': w, 'text_w': w, 'text': text, 'cell': cell,
                'col': ci, 'merged': rng is not None,
                'pt': max(MIN_FONT_PT, min(MAX_FONT_PT * size / 11, size * font_scale)),
                'bold': bool(cell.font.b),
                'base_fill': base_fill,
                # Conditional formatting wins over the cell's own fill, as in Excel.
                'fill': _conditional_fill(cf_rules, r, ci, text) or base_fill,
                'color': _rgb(cell.font.color) or (0, 0, 0),
                'border': {
                    'L': bool(edge[0].border.left.style), 'R': bool(edge[1].border.right.style),
                    'T': bool(cell.border.top.style), 'B': bool(cell.border.bottom.style),
                },
                'family': _family_for(text),
                'numeric': isinstance(value, (int, float)) and not isinstance(value, bool),
            })

        # The evidence column is added by the Excel generator without the
        # template's styling, so its quotes looked detached from their rows.
        # Box each evidence cell like the cell beside it, and carry a heading
        # or section band's fill across.
        for i, c in enumerate(cells):
            header_row = evidence_cols.get(c['col'])
            if header_row is None or r < header_row or i == 0:
                continue
            left = cells[i - 1]
            if any(left['border'].values()):
                c['border'] = {'L': True, 'R': True, 'T': True, 'B': True}
            if left['base_fill'] and not c['base_fill']:
                c['base_fill'] = c['fill'] = left['base_fill']
                c['color'], c['bold'] = left['color'], left['bold']

        # Unwrapped text runs on into empty cells to its right, as in Excel —
        # the red-flag quotes sit in a narrow column and rely on it.
        for i, c in enumerate(cells):
            if not c['text'] or c['merged'] or c['numeric'] or c['cell'].alignment.wrap_text:
                continue
            for nxt in cells[i + 1:]:
                if nxt['text'] or nxt['merged']:
                    break
                c['text_w'] += nxt['w']

        # Row height: tallest wrapped cell, never below the sheet's own height.
        h = ((rd.height if rd is not None and rd.height else DEFAULT_ROW_PT)
             * PT_TO_MM * min(1.0, font_scale))
        for c in cells:
            if not c['text']:
                continue
            pdf.set_font(c['family'], 'B' if c['bold'] else '', c['pt'])
            c['line_h'] = c['pt'] * PT_TO_MM * 1.35
            c['lines'] = pdf.multi_cell(c['text_w'] - 2 * PAD, c['line_h'], c['text'],
                                        dry_run=True, output='LINES')
            h = max(h, len(c['lines']) * c['line_h'] + 2 * PAD)
        rows.append({'cells': cells, 'h': min(h, bottom - top),
                     'banner': any(c['base_fill'] for c in cells)})

    y = top
    for i, row in enumerate(rows):
        h = row['h']
        # Keep a filled heading row on the same page as the row under it.
        after = rows[i + 1]['h'] if row['banner'] and i + 1 < len(rows) else 0
        if y + h > bottom or (y > top and y + h + after > bottom):
            pdf.add_page()
            y = top
        for c in row['cells']:
            if c['fill']:
                pdf.set_fill_color(*c['fill'])
                pdf.rect(c['x'], y, c['w'], h, style='F')
            pdf.set_draw_color(*GRID)
            pdf.set_line_width(0.15)
            b = c['border']
            if b['L']: pdf.line(c['x'], y, c['x'], y + h)
            if b['R']: pdf.line(c['x'] + c['w'], y, c['x'] + c['w'], y + h)
            if b['T']: pdf.line(c['x'], y, c['x'] + c['w'], y)
            if b['B']: pdf.line(c['x'], y + h, c['x'] + c['w'], y + h)
        for c in row['cells']:
            if not c['text']:
                continue
            al = c['cell'].alignment
            horiz = {'center': 'C', 'centerContinuous': 'C', 'right': 'R',
                     'justify': 'J'}.get(al.horizontal, 'R' if c['numeric'] else 'L')
            # A cell taller than a whole page is cut, not spilled over the footer.
            max_lines = int((h - 2 * PAD) / c['line_h'] + 1e-6) or 1
            lines = c['lines'][:max_lines]
            block = len(lines) * c['line_h']
            if al.vertical == 'top':
                ty = y + PAD
            elif al.vertical == 'bottom':
                ty = y + h - PAD - block
            else:
                ty = y + (h - block) / 2
            pdf.set_font(c['family'], 'B' if c['bold'] else '', c['pt'])
            pdf.set_text_color(*c['color'])
            pdf.set_xy(c['x'] + PAD, ty)
            pdf.multi_cell(c['text_w'] - 2 * PAD, c['line_h'], '\n'.join(lines), align=horiz,
                           new_x='LEFT', new_y='NEXT')
        y += h


def _sheets_for_pdf(wb) -> list:
    """
    The PDF is the officer's "My Result" page — the sheet the checklist
    documents say is sent back to him. The Scorecard sheet stays in the Excel.
    A workbook without a My Result sheet falls back to every visible sheet.
    """
    visible = [ws for ws in wb.worksheets if ws.sheet_state == 'visible']
    result = [ws for ws in visible if 'result' in ws.title.lower()]
    return result or visible


def workbook_to_pdf(xlsx_bytes: bytes, doc_title: str = 'Scorecard') -> bytes:
    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as f:
        f.write(xlsx_bytes)
        path = f.name
    try:
        wb = openpyxl.load_workbook(path)
        xl = ExcelCompiler(filename=path)
        pdf = _PDF(doc_title)
        for ws in _sheets_for_pdf(wb):
            _render_sheet(pdf, ws, xl)
        return bytes(pdf.output())
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
