# PaddleOCR table extraction for Stirling PDF

This internal sidecar replaces Tabula behind Stirling's existing PDF-to-CSV and PDF-to-XLSX
endpoints when `tableOcr.enabled` is true. It runs PP-StructureV3 with PP-OCRv6 and is not exposed
to the LAN.

## Runtime contract

- `GET /health` reports model readiness and queue state.
- `POST /api/v1/table/extract` accepts multipart fields:
  - `file`: a PDF normalized and page-selected by Stirling.
  - `output_format`: `xlsx` or `csv`.
  - `original_name`: download-name source only; it is sanitized before use.
  - `page_labels`: comma-separated original page numbers for workbook sheet names.
- XLSX output is one workbook with one sheet per detected table.
- CSV output is one CSV for one table, or a ZIP of CSV files for multiple tables.
- A job directory is removed after the response is sent. Any stale `job-*` directory is removed on
  service startup.
- One OCR job runs at a time; by default, at most one additional request may wait.

## Production requirements

- 8 vCPU.
- 16 GB RAM minimum for Stirling plus one medium OCR job; 24 GB is preferred.
- Docker Engine with Compose v2.
- The sidecar is CPU-only and should never publish port 8000 to the host.

The existing 8-vCPU/7.2-GB host is suitable for isolated testing but not for running Stirling and
the medium model concurrently.

## Build on the server

```bash
cd /opt/Stirling-PDF

sudo docker build \
  -f docker/embedded/Dockerfile \
  --build-arg STIRLING_FLAVOR=core \
  --build-arg VERSION_TAG=v2.14.2-paddle-table \
  -t stirling-pdf-core:v2.14.2-paddle-table \
  .

sudo docker build \
  -f integrations/paddleocr-table/Dockerfile \
  -t internal/paddleocr-table:3.7.0-api-cpu \
  integrations/paddleocr-table
```

The first sidecar startup downloads the selected models. The model directories are persistent;
uploaded documents and generated outputs are not.

## Prepare host directories

The image runs as UID/GID 10001 rather than root:

```bash
sudo install -d -o 10001 -g 10001 \
  /opt/paddleocr-table-service/models/paddlex \
  /opt/paddleocr-table-service/models/paddleocr \
  /opt/paddleocr-table-service/models/cache \
  /opt/paddleocr-table-service/work

sudo chown -R 10001:10001 \
  /opt/paddleocr-table-service/models \
  /opt/paddleocr-table-service/work
```

If models were downloaded previously as root, the recursive `chown` is required before starting
the non-root production image.

## Deploy with the existing Stirling Compose file

Change the Stirling image in `/opt/stirling-deploy/docker-compose.yml`:

```yaml
image: stirling-pdf-core:v2.14.2-paddle-table
```

Copy the example environment file:

```bash
cd /opt/stirling-deploy
cp /opt/Stirling-PDF/integrations/paddleocr-table/.env.production.example .env.table-ocr
```

Validate the merged configuration without starting containers:

```bash
sudo docker compose \
  --env-file .env.table-ocr \
  -f docker-compose.yml \
  -f /opt/Stirling-PDF/integrations/paddleocr-table/compose.production.yml \
  config
```

Start the stack:

```bash
sudo docker compose \
  --env-file .env.table-ocr \
  -f docker-compose.yml \
  -f /opt/Stirling-PDF/integrations/paddleocr-table/compose.production.yml \
  up -d
```

Always use both `-f` arguments for later `ps`, `logs`, `up`, and `down` commands so Compose acts on
the same merged project.

## Verify

```bash
cd /opt/stirling-deploy

sudo docker compose \
  --env-file .env.table-ocr \
  -f docker-compose.yml \
  -f /opt/Stirling-PDF/integrations/paddleocr-table/compose.production.yml \
  ps

sudo docker compose \
  --env-file .env.table-ocr \
  -f docker-compose.yml \
  -f /opt/Stirling-PDF/integrations/paddleocr-table/compose.production.yml \
  logs --tail=200 paddleocr-table stirling-pdf

sudo docker exec stirling-pdf-core \
  curl -fsS http://paddleocr-table:8000/health
```

Then test PDF-to-XLSX and PDF-to-CSV at the normal Stirling URL. A successful response includes
`X-OCR-Pages` and `X-OCR-Tables` headers between the sidecar and Stirling; documents are returned to
the browser under sanitized names ending in `_extracted`.

## Roll back to Tabula

Set this in `/opt/stirling-deploy/.env.table-ocr` and redeploy with both Compose files:

```dotenv
TABLEOCR_ENABLED=false
```

No image rollback is required. The existing lattice-to-stream implementation remains compiled into
Stirling and is selected whenever `tableOcr.enabled` is false.
