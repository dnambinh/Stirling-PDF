# HandlePDF on FEV-APP-01

Management: `172.16.10.235`. Production URL after cutover: `http://172.16.10.232:8080`.

## Deploy code

Merge reviewed changes into `production/handlepdf` in `dnambinh/Stirling-PDF`, then SSH to FEV-APP-01 and run:

```sh
sudo deploy-handlepdf
```

The command fetches this branch, refuses local edits and concurrent deployments, verifies pinned runtimes, runs `task backend:check`, builds Java/frontend and OCR images, runs OCR unit tests and tests XLSX/CSV extraction with disposable containers and a synthetic PDF. It then drains services, backs up persistent data, replaces images and checks health. Failed replacement restores previous images. Startup configuration/data changes are retained; recover those from the backup if necessary.

`main` follows a different upstream version and requires a separate upgrade review. Production environment files and user documents stay outside Git.

## Inspect and back up

```sh
sudo handlepdf-status
sudo handlepdf-logs
handlepdf-help
sudo backup-handlepdf-container
```

Daily backups run at 03:45 Vietnam time under `/srv/backups/handlepdf/daily`. They are local to this VM. Copy completed backup directories to independent storage. Each contains settings, model weights, retained input/output files and checksums. Unchanged model archives share disk space through hard links. Transient processing directories are excluded. No automatic backup deletion is configured.

Keep `/srv/backups/handlepdf/migration/runtime-images.tar.gz`. Builds use the exact installed PDF-tool and PaddleOCR runtimes pinned in `runtime-images.json`. Updating runtime dependencies requires separately rebuilding and reviewing those pins.

## Recovery

Stop HandlePDF before restoring data. Verify `SHA256SUMS` in a complete daily backup. Preserve current data separately, extract `data.tar.gz` and `models.tar.gz` into `/srv/apps/handlepdf`, and restore the backed-up Compose override/environment files with their original ownership. Restore matching images or rebuild the recorded commit. Start the project and verify health plus a sample extraction before opening access. The migration archive retains the original configuration and runtime images.

Base Compose exposes only `127.0.0.1:18032`. Production adds local `compose.public.yaml` binding `172.16.10.232:8080:8080`; the host must own that IP first. Preserve `.235`, `.230` and `.229` in Netplan. The installer updates SSH help and installs the backup timer; it does not change host networking.
