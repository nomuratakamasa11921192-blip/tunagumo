#!/usr/bin/env bash
# 2026-09-23 本番反映（ユーザー承認済み）: Higgsfield動画の新モデル + 文章生成をGPT-6 Solへ。
# さくらのrootコンソールで実行する:
#   sudo -u ubuntu git -C /home/ubuntu/tsunagumo-release-stage-20260919 pull --ff-only origin main
#   bash /home/ubuntu/tsunagumo-release-stage-20260919/scripts/deploy_20260923.sh
# dbは作り直さない(api・schedulerのみ)。失敗時は自動で元のコード・ENV・イメージへ戻す。
set -euo pipefail

STAGE=/home/ubuntu/tsunagumo-release-stage-20260919
BASE=/opt/tsunagumo
WD=$BASE/saas/docker
BK=/root/tsunagumo-release-20260923
EXPECT=2454efb
COMPOSE=(docker compose -p docker --env-file "$WD/.env" -f "$WD/docker-compose.yml" -f "$WD/docker-compose.prod.yml")

[ "$(id -u)" = 0 ] || { echo "rootで実行してください"; exit 1; }
git -C "$STAGE" merge-base --is-ancestor "$EXPECT" HEAD || { echo "取得先に $EXPECT が含まれていません(pullを確認)"; exit 1; }
grep -q '^OPENAI_API_KEY=.' "$BASE/saas/.env" || { echo "OPENAI_API_KEYが未設定のため中止"; exit 1; }

echo "== 1/5 退避"
mkdir -p -m 700 "$BK"
cp -a "$BASE/saas/.env" "$BK/saas.env"
tar czf "$BK/previous-code.tar.gz" -C "$BASE" --exclude=saas/workspace --exclude=saas/.env --exclude=saas/docker/.env saas
docker tag docker-api:latest tsunagumo-rollback-api:20260923
docker tag docker-scheduler:latest tsunagumo-rollback-scheduler:20260923
(cd "$WD" && bash ./backup_db.sh) >"$BK/backup.log" 2>&1 || { echo "DBバックアップ失敗のため中止($BK/backup.log)"; exit 1; }

rollback() {
  echo "!! 失敗したため元に戻します"
  tar xzf "$BK/previous-code.tar.gz" -C "$BASE" --no-same-owner
  cp -a "$BK/saas.env" "$BASE/saas/.env"
  docker tag tsunagumo-rollback-api:20260923 docker-api:latest
  docker tag tsunagumo-rollback-scheduler:20260923 docker-scheduler:latest
  "${COMPOSE[@]}" up -d --no-deps --no-build api scheduler
  echo "!! 切り戻し完了。原因を確認してください"
}
trap rollback ERR

echo "== 2/5 コード配置(ENV・workspaceは上書きしない)"
(cd "$STAGE" && tar cf - --exclude=saas/workspace --exclude=saas/.env --exclude=saas/docker/.env saas) | tar xf - -C "$BASE" --no-same-owner

echo "== 3/5 ENV更新(GPT-6 Sol)"
for kv in LLM_PROVIDER=openai LLM_MODEL=gpt-6-sol LLM_MODEL_LIGHT=gpt-6-sol OPENAI_MODEL=gpt-6-sol OPENAI_MODEL_LIGHT=gpt-6-sol; do
  k=${kv%%=*}
  if grep -q "^$k=" "$BASE/saas/.env"; then sed -i "s|^$k=.*|$kv|" "$BASE/saas/.env"; else echo "$kv" >>"$BASE/saas/.env"; fi
done

echo "== 4/5 api・scheduler 再作成"
"${COMPOSE[@]}" build api scheduler
"${COMPOSE[@]}" up -d --no-deps api scheduler

echo "== 5/5 確認"
ok=0
for i in $(seq 1 30); do
  if [ "$(curl -s -o /dev/null -w '%{http_code}' https://app.tunagumo.com/)" = 200 ]; then ok=1; break; fi
  sleep 2
done
[ "$ok" = 1 ] || false
sleep 20
docker exec docker-api-1 python -c "from src.core.config import active_llm_model, active_llm_model_light; print(active_llm_model(), active_llm_model_light())"
docker exec docker-api-1 grep -c 'kling-video/v2.5-turbo' /app/src/core/higgsfield_client.py
errs=$("${COMPOSE[@]}" logs --since 2m api scheduler 2>&1 | grep -c -E 'ERROR|Traceback' || true)
echo "直近2分のERROR/Traceback: $errs"
[ "$errs" = 0 ] || false
for u in https://app.tunagumo.com/index.html https://tunagumo.com/; do echo "$u $(curl -s -o /dev/null -w '%{http_code}' "$u")"; done
trap - ERR
echo "== 反映完了 $(git -C "$STAGE" rev-parse --short HEAD)"
