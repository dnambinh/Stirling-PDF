FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/home/ocr \
    OCR_WORK_DIR=/work

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        libsm6 \
        libxext6 \
        libxrender1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install \
        paddlepaddle==3.2.0 \
        --index-url https://www.paddlepaddle.org.cn/packages/stable/cpu/ \
    && python -m pip install \
        "paddleocr[doc-parser]==3.7.0" \
        -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

RUN groupadd --gid 10001 ocr \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/ocr ocr \
    && mkdir -p /app /work /home/ocr/.paddlex /home/ocr/.paddleocr \
    && chown -R ocr:ocr /app /work /home/ocr

WORKDIR /app
COPY --chown=ocr:ocr app /app/app

USER 10001:10001

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
