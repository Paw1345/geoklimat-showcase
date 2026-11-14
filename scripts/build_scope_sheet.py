"""Build the Epic 4.5 scope sheet (plan step U6) from repository documents.

Reads the K6 matrix snapshot, the basket proposal and the decision register,
and writes one XLSX and one CSV. No database and no network.
One-off helper: needs openpyxl, which the application image does not install.
Run: python scripts/build_scope_sheet.py
"""

import ast
import csv
import re
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.worksheet.table import Table, TableStyleInfo

ROOT = Path(__file__).resolve().parent.parent
MATRIX = ROOT / "docs/EPIC45-K6-MATRIX-2026-10-22.md"
BASKETS = ROOT / "docs/EPIC45-K6-SCOPE-PROPOSAL-2026-10-21.md"
REGISTER = ROOT / "ingestion/epic45_acceptance.py"
OUT = ROOT / "data/GeoKlimat_EPIC45_zakres_2026-11-13"
HEADERS = [
    "ID",
    "Pozycja",
    "Wymagany zakres (workbook)",
    "Obszar / interwał",
    "Pakiet",
    "Stan rejestru",
    "Kanały informacyjne",
    "Kanały danych",
    "Koszyk (propozycja)",
    "Uzasadnienie koszyka",
    "Odbiór",
    "Data decyzji",
    "Wyjątki",
    "Uwaga",
]


def rows(path):
    for line in path.read_text(encoding="utf-8").splitlines():
        if re.match(r"\| \d+ \|", line):
            cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", line)[1:-1]]
            yield int(cells[0]), [cell.replace("\\|", "|") for cell in cells[1:]]


def decisions():
    """Registry ids and exception texts, read from the source without importing Django."""
    tree = ast.parse(REGISTER.read_text(encoding="utf-8"))
    dates = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            dates[node.targets[0].id] = node.value.value
    stamp = {
        "_imgw": dates["IMGW_DECIDED_AT"],
        "_group_a": dates["GROUP_A_DECIDED_AT"],
        "_spatial": dates["SPATIAL_DECIDED_AT"],
    }
    for node in tree.body:
        if isinstance(node, ast.Assign) and node.targets[0].id == "DECISIONS":
            return {
                key.value: (stamp[call.func.id], ast.literal_eval(call.args[0]))
                for key, call in zip(node.value.keys, node.value.values, strict=True)
            }
    raise SystemExit("DECISIONS not found")


def build():
    baskets = {key: cells for key, cells in rows(BASKETS)}
    accepted = decisions()
    table = []
    for key, cells in rows(MATRIX):
        name, scope, area, package, info, data, _ = cells
        pack, _, state = package.partition(" / ")
        date, exceptions = accepted.get(key, ("", ""))
        table.append(
            [
                key,
                name,
                scope,
                area,
                pack,
                state,
                info,
                data,
                baskets[key][2],
                baskets[key][3],
                "odebrane z wyjątkami" if key in accepted else "nieocenione",
                date,
                exceptions,
                "",
            ]
        )
    if len(table) != 219 or len(accepted) != 12 or set(accepted) - {row[0] for row in table}:
        raise SystemExit(f"Unexpected counts: {len(table)} rows, {len(accepted)} decisions")
    return table


def main():
    table = build()
    with open(f"{OUT}.csv", "w", newline="", encoding="utf-8-sig") as handle:
        csv.writer(handle).writerows([HEADERS, *table])
    book = Workbook()
    sheet = book.active
    sheet.title = "Zakres 4.5"
    sheet.append(HEADERS)
    for row in table:
        sheet.append(row)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.freeze_panes = "C2"
    widths = (6, 40, 34, 22, 9, 18, 30, 30, 10, 60, 22, 22, 60, 40)
    for column, width in zip("ABCDEFGHIJKLMN", widths, strict=True):
        sheet.column_dimensions[column].width = width
    ref = f"A1:N{len(table) + 1}"
    style = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
    grid = Table(displayName="Zakres45", ref=ref, tableStyleInfo=style)
    sheet.add_table(grid)
    summary = book.create_sheet("Podsumowanie")
    summary.append(["Miara", "Liczba"])
    summary.append(["Pozycje", "=COUNTA('Zakres 4.5'!A2:A220)"])
    for basket in "RMP":
        summary.append([f"Koszyk {basket}", f"=COUNTIF('Zakres 4.5'!I2:I220,\"{basket}\")"])
    summary.append(
        ["Odebrane z wyjątkami", "=COUNTIF('Zakres 4.5'!K2:K220,\"odebrane z wyjątkami\")"]
    )
    summary.append(["Nieocenione", "=COUNTIF('Zakres 4.5'!K2:K220,\"nieocenione\")"])
    summary.append([])
    summary.append(
        ["Źródła: macierz K6 z 2026-10-22, propozycja koszyków z 2026-10-21 (PROPOSED),"]
    )
    summary.append(
        ["rejestr decyzji ingestion/epic45_acceptance.py. Stany kanałów mają datę macierzy."]
    )
    summary.column_dimensions["A"].width = 30
    for cell in summary[1]:
        cell.font = Font(bold=True)
    book.save(f"{OUT}.xlsx")
    counts = {basket: sum(row[8] == basket for row in table) for basket in "RMP"}
    print(len(table), "rows;", counts, "; accepted", sum(bool(row[11]) for row in table))


if __name__ == "__main__":
    main()
