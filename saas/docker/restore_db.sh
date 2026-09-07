#!/bin/bash
# バックアップからの復元(8-6: 「復元を実際に1回やって、所要時間を手順書に書く」)。
#
# 安全のため、デフォルトでは本番の"tsunagumo"データベースには復元せず、
# 別名のデータベース(tsunagumo_restore_test)に復元する。本番データベースを
# 上書きしたい場合のみ、明示的に --target tsunagumo を指定する(確認プロンプトが出る)。
#
# 使い方:
#   docker/restore_db.sh <暗号化バックアップファイル> [--target データベース名]
#
# 実測(2026-08-24、開発環境・tenants 122件/sessions 18件/documents 2件、約624KB):
#   バックアップ: 2秒 / 復元: 5秒(復元後、元DBとのレコード数一致を確認済み)
# (本番データが増えるとpg_dump/pg_restoreの時間は比例して伸びるため、
#  データ量が大きくなったら定期的に測り直すこと)

set -euo pipefail
cd "$(dirname "$0")"

BACKUP_FILE="${1:?使い方: restore_db.sh <バックアップファイル> [--target データベース名]}"
TARGET_DB="tsunagumo_restore_test"
if [ "${2:-}" == "--target" ]; then
  TARGET_DB="${3:?--target の後にデータベース名を指定してください}"
fi

if [ -z "${BACKUP_ENCRYPTION_PASSPHRASE:-}" ] && [ -f ../.env ]; then
  set -a
  source ../.env
  set +a
fi
if [ -z "${BACKUP_ENCRYPTION_PASSPHRASE:-}" ]; then
  echo "エラー: BACKUP_ENCRYPTION_PASSPHRASEが設定されていません" >&2
  exit 1
fi

if [ "$TARGET_DB" == "tsunagumo" ]; then
  echo "警告: 本番データベース(tsunagumo)を上書きします。既存データは失われます。"
  read -p "続行しますか？ (yes と入力): " CONFIRM
  if [ "$CONFIRM" != "yes" ]; then
    echo "中止しました"
    exit 1
  fi
fi

START=$(date +%s)

docker compose exec -T db psql -U tsunagumo -d postgres -c "DROP DATABASE IF EXISTS ${TARGET_DB};"
docker compose exec -T db psql -U tsunagumo -d postgres -c "CREATE DATABASE ${TARGET_DB};"

openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_ENCRYPTION_PASSPHRASE -in "$BACKUP_FILE" \
  | gunzip \
  | docker compose exec -T db psql -U tsunagumo -d "$TARGET_DB"

END=$(date +%s)
echo "復元完了: データベース '${TARGET_DB}' に復元しました ($((END - START))秒)"
