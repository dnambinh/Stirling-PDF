#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run with sudo'; exit 1; }
base=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
for f in deploy-handlepdf backup-handlepdf-container; do install -m 755 "$base/$f" "/usr/local/sbin/$f"; done
for f in handlepdf-status handlepdf-logs handlepdf-help fev-help; do install -m 755 "$base/$f" "/usr/local/bin/$f"; done
for f in handlepdf-container-backup.service handlepdf-container-backup.timer; do install -m 644 "$base/$f" /etc/systemd/system/; done
cat > /etc/update-motd.d/99-fev-app-help <<'MOTD'
#!/bin/sh
exec /usr/local/bin/fev-help
MOTD
chmod 755 /etc/update-motd.d/99-fev-app-help
systemctl daemon-reload
systemctl enable --now handlepdf-container-backup.timer
