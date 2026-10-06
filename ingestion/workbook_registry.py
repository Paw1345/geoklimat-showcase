"""Read the user's registry without Excel execution or third-party runtime dependencies."""

import hashlib
import posixpath
import re
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
SHEETS = (
    "Master Registry",
    "Summary",
    "Taxonomy",
    "Technical Verification",
    "Verification Evidence",
    "Implementation Queue",
)


def xml(data):
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("XML entities are not accepted")
    return ET.fromstring(data)


def read_registry(path):
    path = Path(path)
    if path.stat().st_size > 5_000_000:
        raise ValueError("Workbook exceeds 5 MB")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with ZipFile(path) as archive:
        if sum(info.file_size for info in archive.infolist()) > 25_000_000:
            raise ValueError("Expanded workbook exceeds 25 MB")
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared = [
                "".join(node.itertext()) for node in xml(archive.read("xl/sharedStrings.xml"))
            ]
        relations = {
            row.attrib["Id"]: row.attrib["Target"]
            for row in xml(archive.read("xl/_rels/workbook.xml.rels"))
            if row.attrib.get("TargetMode") != "External"
        }
        workbook = xml(archive.read("xl/workbook.xml"))
        sheets = {}
        for sheet in workbook.find("s:sheets", NS):
            name = sheet.attrib["name"]
            target = relations[sheet.attrib[REL]]
            target = (
                target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
            )
            if not target.startswith("xl/worksheets/"):
                raise ValueError("Unexpected worksheet path")
            cells, height, width = {}, 0, 0
            for cell in xml(archive.read(target)).findall(".//s:sheetData/s:row/s:c", NS):
                address = cell.attrib["r"]
                match = re.fullmatch(r"([A-Z]+)([0-9]+)", address)
                if not match:
                    raise ValueError("Invalid cell reference")
                col = 0
                for char in match[1]:
                    col = col * 26 + ord(char) - 64
                row = int(match[2])
                if row > 5000 or col > 100:
                    raise ValueError("Registry dimension limit exceeded")
                if cell.find("s:f", NS) is not None:
                    raise ValueError("Registry inputs must be values, not formulas")
                value = cell.findtext("s:v", default=None, namespaces=NS)
                kind = cell.attrib.get("t", "")
                if kind == "inlineStr":
                    value = "".join(cell.find("s:is", NS).itertext())
                elif kind == "s":
                    value = shared[int(value)]
                elif value is not None and kind not in ("str", "e"):
                    value = float(value)
                    value = int(value) if value.is_integer() else value
                cells[row, col] = value
                height, width = max(height, row), max(width, col)
            sheets[name] = [
                [cells.get((r, c)) for c in range(1, width + 1)] for r in range(1, height + 1)
            ]
    if set(sheets) != set(SHEETS):
        raise ValueError("Expected all six registry worksheets")
    tables = {}
    for name in SHEETS:
        if name == "Summary":
            continue
        header, *rows = sheets[name]
        if len(header) != len(set(header)) or any(not isinstance(key, str) for key in header):
            raise ValueError(f"Invalid headers: {name}")
        tables[name] = [
            dict(zip(header, row, strict=True)) for row in rows if any(v is not None for v in row)
        ]
    masters, technical = tables["Master Registry"], tables["Technical Verification"]
    for rows in (masters, technical, tables["Implementation Queue"]):
        ids = [row["ID"] for row in rows]
        if any(type(value) is not int or value < 1 for value in ids) or len(ids) != len(set(ids)):
            raise ValueError("Invalid or duplicate registry IDs")
    ids = {row["ID"] for row in masters}
    if ids != {row["ID"] for row in technical}:
        raise ValueError("Technical verification IDs do not match registry")
    if not {row["ID"] for row in tables["Implementation Queue"]} <= ids:
        raise ValueError("Unknown implementation queue ID")
    for row in masters:
        if row["Priority"] not in ("P0", "P1", "P2", "P3"):
            raise ValueError("Invalid priority")
        if (
            type(row["Poll interval (min)"]) is not int
            or not 15 <= row["Poll interval (min)"] <= 10080
        ):
            raise ValueError("Invalid interval")
    return {"filename": path.name, "sha256": digest, "sheets": sheets, "tables": tables}
