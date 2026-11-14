"""Minimal read-only legacy XLS (BIFF8) reader for reviewed one-sheet tables.

Mirrors ``xlsx_table.read_single_sheet``: one sheet, row/column budgets, no macros,
formulas rejected unless a reviewed profile collects them. Values are returned as
strings in the same form the XLSX reader yields, so profiles can share parsers.
"""

import io
import struct

import xlrd
import xlrd.compdoc

from ingestion.transport import FetchError
from ingestion.xlsx_table import MAX_COLUMNS, MAX_ROWS

OLE_SIGNATURE = bytes.fromhex("d0cf11e0a1b11ae1")
FORMULA_RECORD = 0x0006
MACRO_STREAMS = ("_VBA_PROJECT_CUR", "VBA", "_VBA_PROJECT", "Macros")


def _cell_ref(row, column):
    letters = ""
    column += 1
    while column:
        column, remainder = divmod(column - 1, 26)
        letters = chr(65 + remainder) + letters
    return f"{letters}{row + 1}"


def _formula_cells(body):
    """Return references of FORMULA records in the BIFF8 workbook stream."""
    try:
        document = xlrd.compdoc.CompDoc(body, logfile=io.StringIO())
        names = [entry.name for entry in document.dirlist]
        memory, base, size = document.locate_named_stream("Workbook")
    except (xlrd.compdoc.CompDocError, AssertionError, IndexError, struct.error) as exc:
        raise FetchError("XLS container invalid") from exc
    if memory is None:
        raise FetchError("XLS workbook stream missing")
    if any(name in MACRO_STREAMS for name in names):
        raise FetchError("XLS macros present")
    refs, position, end = [], base, base + size
    while position + 4 <= end:
        kind, length = struct.unpack_from("<HH", memory, position)
        if kind == FORMULA_RECORD:
            if length < 4 or position + 8 > end:
                raise FetchError("XLS formula record invalid")
            row, column = struct.unpack_from("<HH", memory, position + 4)
            refs.append(_cell_ref(row, column))
        position += 4 + length
    if position != end:
        raise FetchError("XLS record stream truncated")
    return refs


def _text(cell):
    if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
        return ""
    if cell.ctype == xlrd.XL_CELL_TEXT:
        return cell.value
    if cell.ctype in (xlrd.XL_CELL_NUMBER, xlrd.XL_CELL_DATE):
        value = float(cell.value)
        if value.is_integer() and abs(value) < 1e15:
            return str(int(value))
        return repr(value)
    raise FetchError("XLS cell type not accepted")


def read_single_sheet(body, max_rows=MAX_ROWS, max_columns=MAX_COLUMNS, formula_cells=None):
    """Return rows as lists of strings; blank cells are ''. Rejects multi-sheet workbooks.

    Numbers and date serials keep the raw value (integers without ``.0``), as in XLSX.
    Formulas are rejected unless a reviewed profile passes ``formula_cells`` (a list):
    then only the value cached by the producer is read and each reference is appended.
    """
    if not body.startswith(OLE_SIGNATURE):
        raise FetchError("XLS signature invalid")
    formulas = _formula_cells(body)
    if formulas:
        if formula_cells is None:
            raise FetchError("XLS formulas are not accepted")
        formula_cells.extend(formulas)
    try:
        book = xlrd.open_workbook(file_contents=body, logfile=io.StringIO(), verbosity=0)
    except (xlrd.XLRDError, xlrd.compdoc.CompDocError, AssertionError, struct.error) as exc:
        raise FetchError("XLS workbook invalid") from exc
    if book.biff_version != 80:
        raise FetchError("XLS must be BIFF8")
    if book.nsheets != 1:
        raise FetchError("Reviewed XLS must contain exactly one sheet")
    sheet = book.sheet_by_index(0)
    if sheet.nrows > max_rows:
        raise FetchError("XLS row budget exceeded")
    if sheet.ncols > max_columns:
        raise FetchError("XLS column budget exceeded")
    return [[_text(cell) for cell in sheet.row(number)] for number in range(sheet.nrows)]
