#!/usr/bin/env bash
# User-approved company-plan release. Run as root from an isolated VPS Git checkout.
# Usage: bash scripts/deploy_company_plan_20260925.sh <reviewed-full-commit>
set -Eeuo pipefail
umask 077
STAGE=$(cd "$(dirname "$0")/.." && pwd -P)
GIT=(git -c safe.directory="$STAGE" -C "$STAGE")
REV=${1:?reviewed commit is required}
[[ "$REV" =~ ^[0-9a-f]{40}$ ]]
BASE=/opt/tsunagumo
WD=$BASE/saas/docker
BK=/root/tsunagumo-company-plan-$(date +%Y%m%d_%H%M%S)
COMPOSE=(docker compose -p docker --env-file "$WD/.env" -f "$WD/docker-compose.yml" -f "$WD/docker-compose.prod.yml")
[ "$(id -u)" = 0 ]
[ "$("${GIT[@]}" rev-parse HEAD)" = "$REV" ]
"${GIT[@]}" merge-base --is-ancestor d56e17b "$REV"
[ -z "$("${GIT[@]}" status --porcelain)" ]
[ "$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.project" }}' docker-api-1)" = docker ]
[ -f "$WD/docker-compose.prod.yml" ]
[ -f "$BASE/saas/.env" ]
mkdir -m 700 "$BK"
echo "BACKUP=$BK"
OLD_API=$(docker inspect -f '{{.Image}}' docker-api-1)
OLD_SCHEDULER=$(docker inspect -f '{{.Image}}' docker-scheduler-1)
docker tag "$OLD_API" "tsunagumo-company-rollback-api:$(basename "$BK")"
docker tag "$OLD_SCHEDULER" "tsunagumo-company-rollback-scheduler:$(basename "$BK")"
printf '%s\n%s\n' "$OLD_API" "$OLD_SCHEDULER" > "$BK/images.txt"
tar czf "$BK/previous-code.tar.gz" -C "$BASE" --exclude=saas/workspace --exclude=saas/docker/backups saas website
# Use the installed encrypted-backup procedure; capture output in root-only storage.
(cd "$WD" && bash ./backup_db.sh) > "$BK/db-backup.log" 2>&1
docker exec docker-db-1 psql -U tsunagumo -d tsunagumo -Atc \
  'SELECT plan, count(*) FROM tenants GROUP BY plan ORDER BY plan' > "$BK/plans-before.txt"
"${GIT[@]}" archive --format=tar.gz --output="$BK/release.tar.gz" "$REV" \
  saas/src saas/frontend/index.html saas/migrations/versions/0025_company_plans.py \
  website/index.html website/pricing.html website/faq.html website/law.html
sha256sum "$BK/release.tar.gz" > "$BK/release.sha256"
sha256sum "$BASE/saas/.env" "$WD/.env" > "$BK/env.sha256"
rollback() {
  trap - ERR
  echo 'RELEASE_FAILED: restoring previous code and images'
  tar xzf "$BK/previous-code.tar.gz" -C "$BASE" --no-same-owner
  docker tag "$OLD_API" docker-api:latest
  docker tag "$OLD_SCHEDULER" docker-scheduler:latest
  "${COMPOSE[@]}" up -d --no-deps --no-build api scheduler
  echo "ROLLBACK_FINISHED; additive schema retained; details=$BK"
  exit 1
}
trap rollback ERR
tar xzf "$BK/release.tar.gz" -C "$BASE" --no-same-owner
chmod 644 "$BASE"/website/{index,pricing,faq,law}.html "$BASE/saas/frontend/index.html"
"${COMPOSE[@]}" build api scheduler > "$BK/build.log" 2>&1
"${COMPOSE[@]}" run --rm --no-deps api alembic upgrade head > "$BK/migration.log" 2>&1
"${COMPOSE[@]}" up -d --no-deps --no-build api scheduler
ok=0
for i in $(seq 1 30); do
  if curl --fail --silent --show-error --max-time 10 https://app.tunagumo.com/ -o "$BK/app.html"; then ok=1; break; fi
  sleep 2
done
[ "$ok" = 1 ]
grep -q 'member-login-btn' "$BK/app.html"
grep -q 'activity-btn' "$BK/app.html"
sha256sum -c "$BK/env.sha256"
docker exec docker-api-1 python -c "from src.core.ai_budget import PLAN_MONTHLY_BUDGET_USD as p; assert p['basic']==30 and p['business']==60; print('COMPANY_ALLOWANCES_OK')"
[ "$(docker exec docker-db-1 psql -U tsunagumo -d tsunagumo -Atc 'SELECT version_num FROM alembic_version')" = 0025 ]
docker exec docker-db-1 psql -U tsunagumo -d tsunagumo -Atc \
  'SELECT plan, count(*) FROM tenants GROUP BY plan ORDER BY plan' > "$BK/plans-after.txt"
for page in index pricing faq law; do
  curl --fail --silent --show-error --max-time 20 "https://tunagumo.com/$page.html" -o "$BK/public-$page.html"
  cmp "$BASE/website/$page.html" "$BK/public-$page.html"
done
docker inspect -f '{{.Name}} {{.State.Status}} restarts={{.RestartCount}}' docker-api-1 docker-scheduler-1 docker-db-1
for container in docker-api-1 docker-scheduler-1 docker-db-1; do
  [ "$(docker inspect -f '{{.State.Running}}' "$container")" = true ]
done
trap - ERR
printf '%s\n' "$REV" > "$BK/DEPLOYED_COMMIT"
echo "DEPLOYED=$REV BACKUP=$BK"
