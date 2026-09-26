#!/usr/bin/env bash
# Single-file UI release. Run only after explicit approval for this document-scope release.
# Run from a private staging directory containing the reviewed and tested index.html.
set -Eeuo pipefail
umask 077
STAGE=$(cd "$(dirname "$0")" && pwd -P)
TARGET=/opt/tsunagumo/saas/frontend
OLD=373b6392e2339728aa4d2b76983208f2e5f7326ef151a2230d043f36ab107442
NEW=8ec49e90a46471b2ae9d0706b92d455a9a297928966728ab6893f12341c22c37
EXPECTED_IMAGE=sha256:df1a8ff3f101f2bd19105dc25b258500b07de204fddd99d86c4812b3b4da7d63
RUNNING_IMAGE=sha256:90ab225e8826d758747ee0fc70a0a58ffb10424de5f491e348c173fc86f3e0b5
STAMP=$(date +%Y%m%d_%H%M%S)
BK="$STAGE/backup-$STAMP"
ROLLBACK="tsunagumo-document-scope-rollback:$STAMP"
RELEASE="tsunagumo-document-scope-ui:$STAMP"
hash_file() { sha256sum "$1" | cut -d' ' -f1; }
hash_live() { docker exec docker-api-1 sha256sum /app/frontend/index.html | cut -d' ' -f1; }
[ "$(hostname)" = tk2-119-60133 ]
[ -f "$STAGE/index.html" ]
[ "$(hash_file "$STAGE/index.html")" = "$NEW" ]
[ "$(hash_file "$TARGET/index.html")" = "$OLD" ]
[ "$(hash_live)" = "$OLD" ]
[ "$(docker inspect -f '{{.Image}}' docker-api-1)" = "$RUNNING_IMAGE" ]
[ "$(docker image inspect -f '{{.Id}}' docker-api:latest)" = "$EXPECTED_IMAGE" ]
mkdir -m 700 "$BK"
cp "$TARGET/index.html" "$BK/index.html"
docker tag "$EXPECTED_IMAGE" "$ROLLBACK"
docker inspect -f '{{.Name}} {{.Id}} {{.State.Status}} {{.RestartCount}}' docker-api-1 docker-scheduler-1 docker-db-1 > "$BK/containers-before.txt"
printf '%s\n' "$EXPECTED_IMAGE" > "$BK/image-before.txt"
# Build only from the existing local image and one reviewed HTML file; no network or ENV.
mkdir -m 700 "$STAGE/build-$STAMP"
cp "$STAGE/index.html" "$STAGE/build-$STAMP/index.html"
printf 'FROM %s\nCOPY --chown=appuser:appuser index.html /app/frontend/index.html\n' "$ROLLBACK" > "$STAGE/build-$STAMP/Dockerfile"
docker build --pull=false --network=none -t "$RELEASE" "$STAGE/build-$STAMP" > "$BK/build.log" 2>&1
atomic_write='import hashlib,os,pathlib,sys; p=pathlib.Path(sys.argv[1]); data=sys.stdin.buffer.read(); assert hashlib.sha256(data).hexdigest()==sys.argv[2]; t=p.with_name(".document-scope-release.tmp"); t.write_bytes(data); t.chmod(0o644); os.replace(t,p)'
write_host() {
  # Root is confined to the frontend bind mount; no host root or Docker socket is mounted.
  docker run --rm -i --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
    --user 0 --mount "type=bind,src=$TARGET,dst=/target" --entrypoint python "$EXPECTED_IMAGE" \
    -c "$atomic_write" /target/index.html "$2" < "$1"
}
write_live() {
  # Existing appuser owns this application directory. No container restart is needed.
  docker exec -i docker-api-1 python -c "$atomic_write" /app/frontend/index.html "$2" < "$1"
}
rollback() {
  trap - ERR
  set +e
  write_host "$BK/index.html" "$OLD"
  write_live "$BK/index.html" "$OLD"
  docker tag "$ROLLBACK" docker-api:latest
  echo "FAILED: rollback attempted; verify $BK"
  exit 1
}
trap rollback ERR
write_host "$STAGE/index.html" "$NEW"
write_live "$STAGE/index.html" "$NEW"
docker tag "$RELEASE" docker-api:latest
[ "$(hash_file "$TARGET/index.html")" = "$NEW" ]
[ "$(hash_live)" = "$NEW" ]
docker run --rm --network none --read-only --cap-drop ALL --entrypoint sha256sum "$RELEASE" /app/frontend/index.html > "$BK/image-content.sha256"
[ "$(cut -d' ' -f1 "$BK/image-content.sha256")" = "$NEW" ]
curl --fail --silent --show-error --max-time 25 https://app.tunagumo.com/ -o "$BK/public.html"
[ "$(hash_file "$BK/public.html")" = "$NEW" ]
docker inspect -f '{{.Name}} {{.Id}} {{.State.Status}} {{.RestartCount}}' docker-api-1 docker-scheduler-1 docker-db-1 > "$BK/containers-after.txt"
cmp "$BK/containers-before.txt" "$BK/containers-after.txt"
trap - ERR
printf 'DEPLOYED=%s\nBACKUP=%s\nROLLBACK_IMAGE=%s\nRELEASE_IMAGE=%s\n' "$NEW" "$BK" "$ROLLBACK" "$RELEASE"
