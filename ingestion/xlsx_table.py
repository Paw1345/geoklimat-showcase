"""Minimal read-only XLSX reader for reviewed one-sheet tables; no formulas, styles or macros."""

import io
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile

from ingestion.transport import FetchError

MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
DOC_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
MAX_PARTS = 64
MAX_UNPACKED_BYTES = 5 * 1024 * 1024
MAX_ROWS = 2000
MAX_COLUMNS = 40


def _xml(archive, name):
    try:
        body = archive.read(name)
    except (KeyError, zipfile.BadZipFile) as exc:
        raise FetchError("XLSX part missing or corrupt") from exc
    if b"<!DOCTYPE" in body or b"<!ENTITY" in body:
        raise FetchError("XLSX part declares a DTD")
    try:
        return ET.fromstring(body)
    except ET.ParseError as exc:
        raise FetchError("XLSX part is not well-formed") from exc


def _column(ref):
    match = re.fullmatch(r"([A-Z]{1,2})([1-9][0-9]{0,5})", ref or "")
    if not match:
        raise FetchError("XLSX cell reference invalid")
    index = 0
    for letter in match[1]:
        index = index * 26 + ord(letter) - 64
    return index - 1, int(match[2])


def read_single_sheet(
    body,
    max_rows=MAX_ROWS,
    max_columns=MAX_COLUMNS,
    formula_cells=None,
    max_unpacked=MAX_UNPACKED_BYTES,
):
    """Return rows as lists of strings; blank cells are ''. Rejects multi-sheet workbooks.

    Formulas are rejected unless a reviewed profile passes ``formula_cells`` (a list):
    then only the value cached by the producer is read, never recalculated, and each
    formula cell reference is appended for provenance.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(body))
        infos = archive.infolist()
    except (zipfile.BadZipFile, ValueError) as exc:
        raise FetchError("XLSX container invalid") from exc
    if (
        len(infos) > MAX_PARTS
        or sum(info.file_size for info in infos) > max_unpacked
        or any(name.startswith("xl/vbaProject") for name in archive.namelist())
    ):
        raise FetchError("XLSX part budget exceeded or macros present")
    sheets = _xml(archive, "xl/workbook.xml").find(MAIN + "sheets")
    if sheets is None or len(sheets) != 1:
        raise FetchError("Reviewed XLSX must contain exactly one sheet")
    rel_id = sheets[0].get(DOC_REL + "id")
    targets = {
        rel.get("Id"): rel.get("Target")
        for rel in _xml(archive, "xl/_rels/workbook.xml.rels").findall(REL + "Relationship")
    }
    target = targets.get(rel_id) or ""
    if target.startswith("/") or ".." in target or not target.startswith("worksheets/"):
        raise FetchError("XLSX worksheet relationship invalid")
    shared = []
    if "xl/sharedStrings.xml" in archive.namelist():
        shared = [
            "".join(node.text or "" for node in item.iter(MAIN + "t"))
            for item in _xml(archive, "xl/sharedStrings.xml").findall(MAIN + "si")
        ]
    rows = {}
    for row in _xml(archive, posixpath.join("xl", target)).iter(MAIN + "row"):
        for cell in row.findall(MAIN + "c"):
            column, number = _column(cell.get("r"))
            kind, value = cell.get("t"), cell.find(MAIN + "v")
            if cell.find(MAIN + "f") is not None:
                if formula_cells is None:
                    raise FetchError("XLSX formulas are not accepted")
                formula_cells.append(cell.get("r"))
            if kind == "inlineStr":
                text = "".join(node.text or "" for node in cell.iter(MAIN + "t"))
            elif value is None or value.text is None:
                text = ""
            elif kind == "s":
                try:
                    text = shared[int(value.text)]
                except (ValueError, IndexError) as exc:
                    raise FetchError("XLSX shared string missing") from exc
            elif kind in (None, "n", "str"):
                text = value.text
            else:
                raise FetchError("XLSX cell type not accepted")
            if column >= max_columns:
                raise FetchError("XLSX column budget exceeded")
            rows.setdefault(number, {})[column] = text
            if len(rows) > max_rows:
                raise FetchError("XLSX row budget exceeded")
    width = max((max(cells) + 1 for cells in rows.values() if cells), default=0)
    return [
        [rows.get(number, {}).get(column, "") for column in range(width)]
        for number in range(1, max(rows, default=0) + 1)
    ]
