#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
[[ $EUID -eq 0 && $# -eq 0 ]] || { echo 'Usage: sudo deploy-handlepdf'; exit 1; }
APP_ROOT=${APP_ROOT:-/srv/apps/handlepdf}
CONFIG_ROOT=${CONFIG_ROOT:-/etc/handlepdf-deploy}
REPO_URL=${REPO_URL:-git@github.com:dnambinh/Stirling-PDF.git}
BRANCH=production/handlepdf
BACKUP_COMMAND=${BACKUP_COMMAND:-/usr/local/sbin/backup-handlepdf-container}
MAINTENANCE_FILE=${MAINTENANCE_FILE:-/run/handlepdf-maintenance}
cd "$APP_ROOT"
exec 9>deploy.lock
flock -n 9 || { echo 'HandlePDF deployment is already running'; exit 1; }
[[ ! -e $MAINTENANCE_FILE ]] || { echo 'HandlePDF is already in maintenance'; exit 1; }
[[ -f compose.yaml && -f core.env && -f ocr.env ]] || exit 1
[[ ! -f PRODUCTION || -f compose.public.yaml ]] || { echo 'Production binding file is missing'; exit 1; }
export GIT_SSH_COMMAND="ssh -F /dev/null -i $CONFIG_ROOT/github.key -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$CONFIG_ROOT/known_hosts -o HostKeyAlias=github.com -o HostName=ssh.github.com -p 443"
export GIT_TERMINAL_PROMPT=0
SOURCE="$APP_ROOT/source"
[[ -d $SOURCE/.git ]] || git clone --depth 1 --single-branch --branch "$BRANCH" "$REPO_URL" "$SOURCE"
[[ $(git -C "$SOURCE" remote get-url origin) == "$REPO_URL" ]] || exit 1
[[ -z $(git -C "$SOURCE" status --porcelain) ]] || { echo 'Source has local changes; save them first.'; exit 1; }
git -C "$SOURCE" fetch origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
git -C "$SOURCE" switch "$BRANCH"
git -C "$SOURCE" merge --ff-only "origin/$BRANCH"
sha=$(git -C "$SOURCE" rev-parse "origin/$BRANCH")
[[ $sha =~ ^[0-9a-f]{40}$ ]] || exit 1
compose() {
 local files=(-f "$APP_ROOT/compose.yaml")
 [[ ! -f "$APP_ROOT/compose.deploy.yaml" ]] || files+=(-f "$APP_ROOT/compose.deploy.yaml")
 [[ ! -f "$APP_ROOT/compose.public.yaml" ]] || files+=(-f "$APP_ROOT/compose.public.yaml")
 docker compose "${files[@]}" "$@"
}
health() { curl -fsS --max-time 15 http://127.0.0.1:18032/api/v1/info/status >/dev/null; }
if [[ -f DEPLOYED_COMMIT && $(cat DEPLOYED_COMMIT) == "$sha" ]] && health \
 && [[ $(docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$(compose ps -q core)") == "$sha" ]] \
 && [[ $(docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$(compose ps -q ocr)") == "$sha" ]]; then
 if [[ -f PRODUCTION ]]; then curl -fsS --max-time 15 http://172.16.10.232:8080/api/v1/info/status >/dev/null; fi
 echo "Already running $BRANCH at $sha"; exit 0
fi
python3 - "$SOURCE/deploy/fev/runtime-images.json" <<'PY'
import pathlib,json,subprocess,sys
for ref,want in json.loads(pathlib.Path(sys.argv[1]).read_text()).items():
 got=subprocess.check_output(['docker','image','inspect',ref,'--format','{{.Id}}'],text=True).strip()
 if got!=want:raise SystemExit('Pinned runtime image mismatch: '+ref)
PY
run_id="${sha:0:12}-$$"
work=$(mktemp -d "$APP_ROOT/.deploy-$run_id-XXXXXX")
check_project="handlepdf-checks-$run_id"
core_image="fev/handlepdf-core:git-$sha"
ocr_image="fev/handlepdf-ocr:git-$sha"
checks_image="fev/handlepdf-checks:git-$sha"
ocr_checks="fev/handlepdf-ocr-checks:git-$sha"
unit_container="handlepdf-unit-$run_id"
previous_core=$(docker inspect --format '{{.Config.Image}}' "$(compose ps --all -q core)")
previous_ocr=$(docker inspect --format '{{.Config.Image}}' "$(compose ps --all -q ocr)")
override=0; commit=0; switched=0; success=0
[[ ! -f compose.deploy.yaml ]] || { cp compose.deploy.yaml "$work/previous-compose.yaml"; override=1; }
[[ ! -f DEPLOYED_COMMIT ]] || { cp DEPLOYED_COMMIT "$work/previous-commit"; commit=1; }
export HANDLEPDF_CHECK_RUN=$run_id HANDLEPDF_CHECK_ROOT="$work/check"
export HANDLEPDF_CHECK_CORE_IMAGE=$core_image HANDLEPDF_CHECK_OCR_IMAGE=$ocr_image
check_compose() { docker compose -p "$check_project" -f "$work/source/deploy/fev/compose.checks.yaml" "$@"; }
cleanup() {
 local status=$?
 trap - EXIT
 set +e
 if [[ $switched == 1 && $success == 0 ]]; then
  echo 'Deployment failed; restoring previous HandlePDF images.' >&2
  if [[ $override == 1 ]]; then cp "$work/previous-compose.yaml" compose.deploy.yaml; else rm -f compose.deploy.yaml; fi
  if compose up -d --no-deps --no-build --wait --wait-timeout 600 core ocr && health; then
   if [[ $commit == 1 ]]; then cp "$work/previous-commit" DEPLOYED_COMMIT; else rm -f DEPLOYED_COMMIT; fi
   rm -f "$MAINTENANCE_FILE"
   echo 'Previous images restored; configuration/data migrations are retained.' >&2
  else echo 'Rollback health failed. Inspect logs and the pre-deploy backup.' >&2; fi
 fi
 if [[ -f "$work/source/deploy/fev/compose.checks.yaml" ]]; then
  check_compose down --timeout 20 --remove-orphans >/dev/null 2>&1 || status=1
 fi
 if [[ $(docker inspect --format '{{index .Config.Labels "com.fev.deploy-run"}}' "$unit_container" 2>/dev/null) == "$run_id" ]]; then
  docker stop --time 10 "$unit_container" >/dev/null 2>&1 || true
  docker rm "$unit_container" >/dev/null 2>&1 || true
 fi
 rm -rf -- "$work"
 exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
mkdir "$work/source"
git -C "$SOURCE" archive "$sha" | tar -xf - -C "$work/source"
build() { docker buildx build --builder default --load --progress plain --target "$1" --build-arg "DEPLOY_SHA=$sha" -t "$2" -f "$work/source/deploy/fev/Dockerfile" "$work/source"; }
build checks "$checks_image"
build ocr-checks "$ocr_checks"
timeout --foreground 180 docker run --rm --name "$unit_container" --label "com.fev.deploy-run=$run_id" --network none --memory=1g --cpus=2 "$ocr_checks"
build core "$core_image"
build ocr "$ocr_image"
mkdir -p "$work/check"/{paddleocr,cache,work,configs,tmp}
cp -a --reflink=auto "$APP_ROOT/ocr/models/paddlex" "$work/check/paddlex"
chown -R 10001:10001 "$work/check/paddleocr" "$work/check/cache" "$work/check/work" "$work/check/paddlex"
chown -R 1000:1000 "$work/check/configs" "$work/check/tmp"
check_compose up -d --wait --wait-timeout 600
check_core=$(check_compose ps -q core)
if [[ -n ${SMOKE_COMMAND:-} ]]; then "$SMOKE_COMMAND"; else
 python3 "$work/source/deploy/fev/smoke.py" --container "$check_core" --owner "$run_id" --fixture "$work/source/deploy/fev/fixtures/table.pdf"
fi
# All tests use disposable containers/configs and public model weights, never production env.
check_compose down --timeout 20
for image in "$core_image" "$ocr_image"; do
 [[ $(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image") == "$sha" ]] || exit 1
done
echo 'Checks, builds and real OCR passed. Draining HandlePDF and backing up.'
touch "$MAINTENANCE_FILE"
switched=1
compose stop --timeout 900 core
compose stop --timeout 900 ocr
"$BACKUP_COMMAND"
printf 'services:\n  core:\n    image: %s\n    pull_policy: never\n  ocr:\n    image: %s\n    pull_policy: never\n' "$core_image" "$ocr_image" > "$work/next-compose.yaml"
mv -T "$work/next-compose.yaml" compose.deploy.yaml
compose up -d --no-deps --no-build --wait --wait-timeout 600 ocr core
health
if [[ -f PRODUCTION ]]; then curl -fsS --max-time 15 http://172.16.10.232:8080/api/v1/info/status >/dev/null; fi
printf '%s\n' "$sha" > "$work/next-commit"
mv -T "$work/next-commit" DEPLOYED_COMMIT
python3 - "$previous_core" "$previous_ocr" <<'PY'
import sys,json,pathlib
pathlib.Path('PREVIOUS_IMAGES.json').write_text(json.dumps({'core':sys.argv[1],'ocr':sys.argv[2]}))
PY
rm "$MAINTENANCE_FILE"
success=1
# Only prune this application's old labelled tags; preserve current and rollback images.
for repository in fev/handlepdf-core fev/handlepdf-ocr fev/handlepdf-checks fev/handlepdf-ocr-checks; do
 count=0
 while IFS= read -r ref; do
  [[ $ref == "$repository":git-* ]] || continue
  count=$((count+1))
  if (( count > 3 )) && [[ $ref != "$core_image" && $ref != "$ocr_image" && $ref != "$checks_image" && $ref != "$ocr_checks" && $ref != "$previous_core" && $ref != "$previous_ocr" ]]; then docker image rm "$ref" >/dev/null || true; fi
 done < <(docker image ls "$repository" --filter label=com.fev.app=handlepdf --format '{{.Repository}}:{{.Tag}}')
done
echo "Deployed $BRANCH: $sha"
