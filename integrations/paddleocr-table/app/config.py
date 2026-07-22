from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    device: str
    ocr_tier: str
    work_dir: Path
    max_file_size_mb: int
    max_pages: int
    max_pending_jobs: int

    @classmethod
    def from_environment(cls) -> "Settings":
        device = os.getenv("OCR_DEVICE", "cpu").strip().lower()
        ocr_tier = os.getenv("OCR_TIER", "medium").strip().lower()
        if device not in {"cpu", "gpu"}:
            raise ValueError("OCR_DEVICE must be either 'cpu' or 'gpu'")
        if ocr_tier not in {"tiny", "small", "medium"}:
            raise ValueError("OCR_TIER must be one of: tiny, small, medium")

        max_file_size_mb = int(os.getenv("OCR_MAX_FILE_SIZE_MB", "200"))
        max_pages = int(os.getenv("OCR_MAX_PAGES", "50"))
        max_pending_jobs = int(os.getenv("OCR_MAX_PENDING_JOBS", "2"))
        if max_file_size_mb < 1:
            raise ValueError("OCR_MAX_FILE_SIZE_MB must be positive")
        if max_pending_jobs < 1:
            raise ValueError("OCR_MAX_PENDING_JOBS must be positive")
        if max_pages < 1:
            raise ValueError("OCR_MAX_PAGES must be positive")

        return cls(
            device=device,
            ocr_tier=ocr_tier,
            work_dir=Path(os.getenv("OCR_WORK_DIR", "/work")),
            max_file_size_mb=max_file_size_mb,
            max_pages=max_pages,
            max_pending_jobs=max_pending_jobs,
        )
