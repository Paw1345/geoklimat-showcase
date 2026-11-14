from pathlib import Path

from django.test import SimpleTestCase

from ingestion.transport import FetchError
from ingestion.xls_table import _cell_ref, read_single_sheet

FIXTURES = Path(__file__).parent / "fixtures"


class XlsTableTests(SimpleTestCase):
    def body(self, name):
        return (FIXTURES / f"xls_reader_{name}.xls").read_bytes()

    def test_reads_single_sheet_like_xlsx_reader(self):
        rows = read_single_sheet(self.body("plain"))
        self.assertEqual(
            rows,
            [
                ["L.p.", "Nazwa", "Powierzchnia [ha]"],
                ["1", "Rezerwat „Łężczok”", "12.5"],
                ["2", "", "400"],
                ["3", "Bór", ""],
            ],
        )

    def test_budgets_fail_closed(self):
        with self.assertRaisesMessage(FetchError, "row budget"):
            read_single_sheet(self.body("plain"), max_rows=3)
        with self.assertRaisesMessage(FetchError, "column budget"):
            read_single_sheet(self.body("plain"), max_columns=2)

    def test_formulas_rejected_unless_collected(self):
        with self.assertRaisesMessage(FetchError, "formulas are not accepted"):
            read_single_sheet(self.body("formula"))
        formulas = []
        rows = read_single_sheet(self.body("formula"), formula_cells=formulas)
        self.assertEqual(formulas, ["B2"])
        self.assertEqual(rows, [["a", "b"], ["1", "2"]])

    def test_rejects_multiple_sheets_and_non_xls(self):
        with self.assertRaisesMessage(FetchError, "exactly one sheet"):
            read_single_sheet(self.body("two_sheets"))
        for body in (b"not an xls", b"PK\x03\x04zip", self.body("plain")[:600]):
            with self.subTest(body=body[:8]), self.assertRaises(FetchError):
                read_single_sheet(body)

    def test_cell_reference(self):
        self.assertEqual(
            [_cell_ref(0, 0), _cell_ref(9, 25), _cell_ref(0, 26)], ["A1", "Z10", "AA1"]
        )
