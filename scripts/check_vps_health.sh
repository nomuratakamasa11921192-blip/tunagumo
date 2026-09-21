#!/bin/bash
# ===========================================================================
# check_vps_health.sh - 本番VPSの健康診断（AIを使わない）
#
# 2026-09-22作成。DBバックアップのcronが実は一度も動いていなかった
# (ubuntuユーザーで/var/logへ書けない・実行権限なし等)ことが判明したため、
# 「動いているつもりで動いていない」を毎日見つけるための点検。
#
# 点検項目:
#   1. 最新のDBバックアップが26時間以内にあり、暗号化済み(Salted__)で、小さすぎない
#   2. ディスク使用率が85%未満
#   3. api / scheduler / db のコンテナが動いている
#   4. 公開ページがHTTP 200を返す
#
# 終了コード: 0=全て正常 / 1=どこかに異常あり
# 本番は読むだけで、何も変更しない。
# ===========================================================================
set -uo pipefail

VPS="${VPS_HOST:-tsunagumo-vps}"
MAX_BACKUP_AGE_HOURS=26
MIN_BACKUP_BYTES=10000
MAX_DISK_PERCENT=85

problems=0
ok()  { echo "[OK] $*"; }
ng()  { echo "[NG] $*"; problems=$((problems + 1)); }

remote=$(ssh -o BatchMode=yes -o ConnectTimeout=20 "$VPS" '
  cd /opt/tsunagumo/saas/docker 2>/dev/null || { echo "NODIR"; exit 0; }
  f=$(ls -t backups/tsunagumo_*.sql.gz.enc 2>/dev/null | head -1)
  if [ -n "$f" ]; then
    echo "BACKUP_FILE=$f"
    echo "BACKUP_AGE_SEC=$(( $(date +%s) - $(stat -c %Y "$f") ))"
    echo "BACKUP_BYTES=$(stat -c %s "$f")"
    echo "BACKUP_HEADER=$(head -c 8 "$f")"
  else
    echo "BACKUP_FILE="
  fi
  echo "DISK_PERCENT=$(df --output=pcent / | tail -1 | tr -dc 0-9)"
  for c in docker-api-1 docker-scheduler-1 docker-db-1; do
    echo "CONTAINER_$c=$(docker inspect -f "{{.State.Running}}" "$c" 2>/dev/null || echo missing)"
  done
' 2>&1)

if [ $? -ne 0 ] || [ -z "$remote" ]; then
  ng "VPSへSSH接続できませんでした: $(echo "$remote" | tail -1)"
  echo "結果: 異常 ${problems}件"
  exit 1
fi
if echo "$remote" | grep -q "^NODIR"; then
  ng "本番ディレクトリ /opt/tsunagumo/saas/docker が見つかりません"
fi

val() { echo "$remote" | grep "^$1=" | head -1 | cut -d= -f2-; }

# 1. バックアップ
file=$(val BACKUP_FILE)
if [ -z "$file" ]; then
  ng "DBバックアップが1件もありません"
else
  age_h=$(( $(val BACKUP_AGE_SEC) / 3600 ))
  bytes=$(val BACKUP_BYTES)
  header=$(val BACKUP_HEADER)
  if [ "$age_h" -ge "$MAX_BACKUP_AGE_HOURS" ]; then
    ng "最新のDBバックアップが ${age_h}時間前です（${MAX_BACKUP_AGE_HOURS}時間以内のはず）: $file"
  elif [ "$bytes" -lt "$MIN_BACKUP_BYTES" ]; then
    ng "最新のDBバックアップが小さすぎます(${bytes}バイト)。中身が空の可能性: $file"
  elif [ "$header" != "Salted__" ]; then
    ng "最新のDBバックアップが暗号化されていない形式です: $file"
  else
    ok "DBバックアップ: ${age_h}時間前 / ${bytes}バイト / 暗号化済み"
  fi
fi

# 2. ディスク
disk=$(val DISK_PERCENT)
if [ -z "$disk" ]; then
  ng "ディスク使用率を取得できませんでした"
elif [ "$disk" -ge "$MAX_DISK_PERCENT" ]; then
  ng "ディスク使用率が ${disk}% です（${MAX_DISK_PERCENT}%未満のはず）"
else
  ok "ディスク使用率: ${disk}%"
fi

# 3. コンテナ
for c in docker-api-1 docker-scheduler-1 docker-db-1; do
  state=$(val "CONTAINER_$c")
  if [ "$state" = "true" ]; then
    ok "コンテナ $c: 稼働中"
  else
    ng "コンテナ $c が動いていません（状態: ${state:-不明}）"
  fi
done

# 4. 公開ページ
for url in https://app.tunagumo.com/index.html https://tunagumo.com/; do
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 "$url")
  if [ "$code" = "200" ]; then
    ok "$url: $code"
  else
    ng "$url が $code を返しています"
  fi
done

if [ "$problems" -eq 0 ]; then
  echo "結果: 全て正常"
  exit 0
fi
echo "結果: 異常 ${problems}件"
exit 1
