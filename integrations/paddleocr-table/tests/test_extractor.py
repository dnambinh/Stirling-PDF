from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from openpyxl import Workbook, load_workbook

from app.extractor import (
    NoTablesFoundError,
    PageLimitExceededError,
    TableExtractionRuntime,
)


class FakeResult:
    def __init__(self, rows: list[list[object]]) -> None:
        self._rows = rows

    def save_to_xlsx(self, save_path: str) -> None:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Table"
        for row in self._rows:
            sheet.append(row)
        workbook.save(Path(save_path) / "table.xlsx")
        workbook.close()


class FakePipeline:
    def __init__(self, pages: list[list[list[object]]]) -> None:
        self._pages = pages

    def predict(self, input_path: str):
        del input_path
        for rows in self._pages:
            yield FakeResult(rows)


class EmptyPipeline:
    def predict(self, input_path: str):
        del input_path
        return iter(())


class TableExtractionRuntimeTest(unittest.TestCase):
    def test_combines_pages_into_named_workbook_sheets(self) -> None:
        runtime = TableExtractionRuntime(
            FakePipeline(
                [
                    [["Item", "Qty"], ["A-100", 5]],
                    [["Item", "Qty"], ["B-200", 9]],
                ]
            ),
            "medium",
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            job_dir = Path(temporary_directory)
            input_path = job_dir / "input.pdf"
            input_path.write_bytes(b"pdf")
            output = runtime.extract(
                input_path,
                job_dir,
                "packing list.pdf",
                "xlsx",
                ["2", "5"],
            )

            self.assertEqual(output.filename, "packing_list_extracted.xlsx")
            self.assertEqual(output.pages_processed, 2)
            self.assertEqual(output.tables_extracted, 2)
            workbook = load_workbook(output.path, read_only=True, data_only=True)
            try:
                self.assertEqual(
                    workbook.sheetnames,
                    ["Page 2 Table 1", "Page 5 Table 1"],
                )
                self.assertEqual(workbook["Page 2 Table 1"]["A2"].value, "A-100")
                self.assertEqual(workbook["Page 5 Table 1"]["B2"].value, 9)
            finally:
                workbook.close()

    def test_returns_single_csv_with_utf8_bom(self) -> None:
        runtime = TableExtractionRuntime(
            FakePipeline([[['Mã hàng', "数量"], ["A-100", 5]]]),
            "medium",
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            job_dir = Path(temporary_directory)
            input_path = job_dir / "input.pdf"
            input_path.write_bytes(b"pdf")
            output = runtime.extract(
                input_path,
                job_dir,
                "invoice.pdf",
                "csv",
                ["1"],
            )

            self.assertEqual(output.filename, "invoice_extracted.csv")
            self.assertEqual(output.media_type, "text/csv; charset=utf-8")
            self.assertTrue(output.path.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_returns_zip_when_multiple_tables_exist(self) -> None:
        runtime = TableExtractionRuntime(
            FakePipeline([[['A']], [['B']]]),
            "medium",
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            job_dir = Path(temporary_directory)
            input_path = job_dir / "input.pdf"
            input_path.write_bytes(b"pdf")
            output = runtime.extract(
                input_path,
                job_dir,
                "invoice.pdf",
                "csv",
                ["1", "2"],
            )

            self.assertEqual(output.filename, "invoice_extracted.zip")
            self.assertEqual(output.media_type, "application/zip")
            with zipfile.ZipFile(output.path) as archive:
                self.assertEqual(
                    archive.namelist(),
                    ["invoice_table_1.csv", "invoice_table_2.csv"],
                )

    def test_raises_when_pipeline_emits_no_table_workbook(self) -> None:
        runtime = TableExtractionRuntime(EmptyPipeline(), "medium")

        with tempfile.TemporaryDirectory() as temporary_directory:
            job_dir = Path(temporary_directory)
            input_path = job_dir / "input.pdf"
            input_path.write_bytes(b"pdf")
            with self.assertRaises(NoTablesFoundError):
                runtime.extract(
                    input_path,
                    job_dir,
                    "empty.pdf",
                    "xlsx",
                    [],
                )

    def test_rejects_document_above_page_limit(self) -> None:
        runtime = TableExtractionRuntime(
            FakePipeline([[['A']], [['B']]]),
            "medium",
            max_pages=1,
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            job_dir = Path(temporary_directory)
            input_path = job_dir / "input.pdf"
            input_path.write_bytes(b"pdf")
            with self.assertRaises(PageLimitExceededError):
                runtime.extract(
                    input_path,
                    job_dir,
                    "large.pdf",
                    "xlsx",
                    [],
                )


if __name__ == "__main__":
    unittest.main()
