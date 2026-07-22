from __future__ import annotations

import csv
import re
import zipfile
from copy import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal, Protocol

from openpyxl import Workbook, load_workbook
from openpyxl.worksheet.worksheet import Worksheet

OutputFormat = Literal["xlsx", "csv"]


class StructureResult(Protocol):
    def save_to_xlsx(self, save_path: str) -> None: ...


class StructurePipeline(Protocol):
    def predict(self, input_path: str) -> Iterable[StructureResult]: ...


class NoTablesFoundError(RuntimeError):
    """Raised when PP-StructureV3 did not emit any usable workbook."""


class PageLimitExceededError(RuntimeError):
    """Raised when a document exceeds the configured OCR page limit."""


@dataclass(frozen=True)
class ExtractionOutput:
    path: Path
    filename: str
    media_type: str
    pages_processed: int
    tables_extracted: int


class TableExtractionRuntime:
    def __init__(
        self, pipeline: StructurePipeline, ocr_tier: str, max_pages: int = 50
    ) -> None:
        self._pipeline = pipeline
        self.ocr_tier = ocr_tier
        self.max_pages = max_pages

    @classmethod
    def create(
        cls, device: str, ocr_tier: str, max_pages: int
    ) -> "TableExtractionRuntime":
        # Importing PaddleOCR initializes native libraries, so keep it out of unit-test imports.
        from paddleocr import PPStructureV3

        pipeline = PPStructureV3(
            device=device,
            text_detection_model_name=f"PP-OCRv6_{ocr_tier}_det",
            text_recognition_model_name=f"PP-OCRv6_{ocr_tier}_rec",
            use_doc_orientation_classify=True,
            use_doc_unwarping=False,
            use_textline_orientation=True,
            use_formula_recognition=False,
            use_chart_recognition=False,
            use_seal_recognition=False,
        )
        return cls(pipeline=pipeline, ocr_tier=ocr_tier, max_pages=max_pages)

    def extract(
        self,
        input_path: Path,
        job_dir: Path,
        original_name: str,
        output_format: OutputFormat,
        page_labels: list[str],
    ) -> ExtractionOutput:
        raw_dir = job_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        pages_processed = 0
        workbooks: list[tuple[int, Path]] = []
        for pages_processed, result in enumerate(
            self._pipeline.predict(str(input_path)), start=1
        ):
            if pages_processed > self.max_pages:
                raise PageLimitExceededError(
                    f"PDF exceeds the {self.max_pages}-page OCR limit"
                )
            page_dir = raw_dir / f"page_{pages_processed:03d}"
            page_dir.mkdir(parents=True, exist_ok=True)
            result.save_to_xlsx(save_path=str(page_dir))
            workbooks.extend(
                (pages_processed, workbook_path)
                for workbook_path in sorted(page_dir.rglob("*.xlsx"))
            )

        if not workbooks:
            raise NoTablesFoundError("No usable table was detected by PP-StructureV3")

        base_name = _safe_base_name(original_name)
        combined_path = job_dir / f"{base_name}_extracted.xlsx"
        tables_extracted = _combine_workbooks(workbooks, combined_path, page_labels)
        if tables_extracted == 0:
            raise NoTablesFoundError("No usable table was detected by PP-StructureV3")

        if output_format == "xlsx":
            return ExtractionOutput(
                path=combined_path,
                filename=combined_path.name,
                media_type=(
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                ),
                pages_processed=pages_processed,
                tables_extracted=tables_extracted,
            )

        return _convert_workbook_to_csv(
            combined_path,
            job_dir,
            base_name,
            pages_processed,
            tables_extracted,
        )


def _combine_workbooks(
    workbooks: list[tuple[int, Path]],
    destination: Path,
    page_labels: list[str],
) -> int:
    target = Workbook()
    target.remove(target.active)
    page_table_counts: dict[int, int] = {}

    for processed_page, workbook_path in workbooks:
        source = load_workbook(workbook_path, data_only=False)
        try:
            for source_sheet in source.worksheets:
                if source_sheet.max_row == 1 and source_sheet.max_column == 1:
                    if source_sheet.cell(row=1, column=1).value is None:
                        continue

                table_number = page_table_counts.get(processed_page, 0) + 1
                page_table_counts[processed_page] = table_number
                page_label = (
                    page_labels[processed_page - 1]
                    if processed_page <= len(page_labels)
                    else str(processed_page)
                )
                sheet_name = _unique_sheet_name(
                    target, f"Page {page_label} Table {table_number}"
                )
                target_sheet = target.create_sheet(sheet_name)
                _copy_worksheet(source_sheet, target_sheet)
        finally:
            source.close()

    if not target.worksheets:
        target.close()
        return 0

    target.save(destination)
    table_count = len(target.worksheets)
    target.close()
    return table_count


def _copy_worksheet(source: Worksheet, target: Worksheet) -> None:
    for row in source.iter_rows():
        for source_cell in row:
            target_cell = target.cell(
                row=source_cell.row,
                column=source_cell.column,
                value=source_cell.value,
            )
            if source_cell.has_style:
                target_cell._style = copy(source_cell._style)
            if source_cell.number_format:
                target_cell.number_format = source_cell.number_format
            if source_cell.alignment:
                target_cell.alignment = copy(source_cell.alignment)

    for merged_range in source.merged_cells.ranges:
        target.merge_cells(str(merged_range))

    for column_letter, dimension in source.column_dimensions.items():
        target.column_dimensions[column_letter].width = dimension.width
        target.column_dimensions[column_letter].hidden = dimension.hidden

    for row_number, dimension in source.row_dimensions.items():
        target.row_dimensions[row_number].height = dimension.height
        target.row_dimensions[row_number].hidden = dimension.hidden


def _convert_workbook_to_csv(
    workbook_path: Path,
    job_dir: Path,
    base_name: str,
    pages_processed: int,
    tables_extracted: int,
) -> ExtractionOutput:
    workbook = load_workbook(workbook_path, read_only=True, data_only=True)
    try:
        csv_paths: list[Path] = []
        for index, sheet in enumerate(workbook.worksheets, start=1):
            csv_path = job_dir / f"{base_name}_table_{index}.csv"
            with csv_path.open("w", newline="", encoding="utf-8-sig") as csv_file:
                writer = csv.writer(csv_file)
                for row in sheet.iter_rows(values_only=True):
                    writer.writerow(["" if value is None else value for value in row])
            csv_paths.append(csv_path)
    finally:
        workbook.close()

    if len(csv_paths) == 1:
        output_path = job_dir / f"{base_name}_extracted.csv"
        csv_paths[0].replace(output_path)
        return ExtractionOutput(
            path=output_path,
            filename=output_path.name,
            media_type="text/csv; charset=utf-8",
            pages_processed=pages_processed,
            tables_extracted=tables_extracted,
        )

    output_path = job_dir / f"{base_name}_extracted.zip"
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for csv_path in csv_paths:
            archive.write(csv_path, arcname=csv_path.name)
    return ExtractionOutput(
        path=output_path,
        filename=output_path.name,
        media_type="application/zip",
        pages_processed=pages_processed,
        tables_extracted=tables_extracted,
    )


def _safe_base_name(filename: str) -> str:
    stem = Path(filename).stem
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._")
    return sanitized[:100] or "document"


def _unique_sheet_name(workbook: Workbook, requested: str) -> str:
    invalid_characters = re.compile(r"[\\/*?:\[\]]")
    base_name = invalid_characters.sub("_", requested)[:31] or "Table"
    candidate = base_name
    suffix = 1
    while candidate in workbook.sheetnames:
        suffix_text = f" ({suffix})"
        candidate = f"{base_name[: 31 - len(suffix_text)]}{suffix_text}"
        suffix += 1
    return candidate
