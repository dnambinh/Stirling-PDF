from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Callable, Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from starlette.background import BackgroundTask
from starlette.responses import FileResponse

from app.config import Settings
from app.extractor import (
    NoTablesFoundError,
    PageLimitExceededError,
    TableExtractionRuntime,
)

LOGGER = logging.getLogger("paddleocr-table")
RuntimeFactory = Callable[[Settings], TableExtractionRuntime]


class JobGate:
    def __init__(self, max_pending_jobs: int) -> None:
        self._max_pending_jobs = max_pending_jobs
        self._pending_jobs = 0
        self._counter_lock = asyncio.Lock()
        self.processing_lock = asyncio.Lock()

    async def enter(self) -> None:
        async with self._counter_lock:
            if self._pending_jobs >= self._max_pending_jobs:
                raise HTTPException(
                    status_code=429,
                    detail="The OCR queue is full; try again after the current job finishes",
                )
            self._pending_jobs += 1

    async def leave(self) -> None:
        async with self._counter_lock:
            self._pending_jobs = max(0, self._pending_jobs - 1)

    @property
    def pending_jobs(self) -> int:
        return self._pending_jobs


def _default_runtime_factory(settings: Settings) -> TableExtractionRuntime:
    return TableExtractionRuntime.create(
        settings.device, settings.ocr_tier, settings.max_pages
    )


def create_app(runtime_factory: RuntimeFactory = _default_runtime_factory) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings = Settings.from_environment()
        settings.work_dir.mkdir(parents=True, exist_ok=True)
        _remove_stale_jobs(settings.work_dir)
        LOGGER.info(
            "Loading PP-StructureV3 with PP-OCRv6_%s on %s",
            settings.ocr_tier,
            settings.device,
        )
        app.state.settings = settings
        app.state.runtime = runtime_factory(settings)
        app.state.job_gate = JobGate(settings.max_pending_jobs)
        LOGGER.info("PaddleOCR table service is ready")
        yield

    app = FastAPI(
        title="Stirling PDF table OCR",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health() -> dict[str, object]:
        runtime: TableExtractionRuntime = app.state.runtime
        gate: JobGate = app.state.job_gate
        return {
            "status": "UP",
            "parser": "PP-StructureV3",
            "ocrTier": runtime.ocr_tier,
            "maxPages": runtime.max_pages,
            "pendingJobs": gate.pending_jobs,
        }

    @app.post("/api/v1/table/extract", response_class=FileResponse)
    async def extract_tables(
        file: UploadFile = File(...),
        output_format: Literal["xlsx", "csv"] = Form("xlsx"),
        original_name: str = Form("document.pdf"),
        page_labels: str = Form(""),
    ) -> FileResponse:
        settings: Settings = app.state.settings
        runtime: TableExtractionRuntime = app.state.runtime
        gate: JobGate = app.state.job_gate

        if not (file.filename or "").lower().endswith(".pdf"):
            raise HTTPException(status_code=415, detail="Only PDF input is supported")

        await gate.enter()
        job_dir = settings.work_dir / f"job-{uuid.uuid4().hex}"
        input_path = job_dir / "input.pdf"

        try:
            job_dir.mkdir(parents=True, exist_ok=False)
            await _save_upload(file, input_path, settings.max_file_size_mb)
            labels = [label.strip() for label in page_labels.split(",") if label.strip()]
            async with gate.processing_lock:
                output = await run_in_threadpool(
                    runtime.extract,
                    input_path,
                    job_dir,
                    original_name,
                    output_format,
                    labels,
                )

            return FileResponse(
                output.path,
                media_type=output.media_type,
                filename=output.filename,
                headers={
                    "X-OCR-Pages": str(output.pages_processed),
                    "X-OCR-Tables": str(output.tables_extracted),
                },
                background=BackgroundTask(_remove_job, job_dir),
            )
        except HTTPException:
            _remove_job(job_dir)
            raise
        except NoTablesFoundError as error:
            _remove_job(job_dir)
            raise HTTPException(status_code=422, detail=str(error)) from error
        except PageLimitExceededError as error:
            _remove_job(job_dir)
            raise HTTPException(status_code=413, detail=str(error)) from error
        except Exception as error:
            _remove_job(job_dir)
            LOGGER.exception("Table OCR job failed")
            raise HTTPException(status_code=500, detail="Table OCR processing failed") from error
        finally:
            await file.close()
            await gate.leave()

    return app


async def _save_upload(file: UploadFile, destination: Path, max_size_mb: int) -> None:
    max_bytes = max_size_mb * 1024 * 1024
    bytes_written = 0
    with destination.open("wb") as output:
        while chunk := await file.read(1024 * 1024):
            bytes_written += len(chunk)
            if bytes_written > max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"PDF exceeds the {max_size_mb} MB service limit",
                )
            output.write(chunk)

    if bytes_written == 0:
        raise HTTPException(status_code=400, detail="Uploaded PDF is empty")


def _remove_stale_jobs(work_dir: Path) -> None:
    for path in work_dir.glob("job-*"):
        _remove_job(path)


def _remove_job(job_dir: Path) -> None:
    shutil.rmtree(job_dir, ignore_errors=True)


app = create_app()
