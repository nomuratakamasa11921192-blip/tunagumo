#!/bin/bash
# Postgresの日次論理バックアップ(8-6)。pg_dumpの出力をgzip圧縮し、AES-256で暗号化して
# ローカルディスクに保存する。
#
# 重要: このスクリプト自体はローカル保存までしか行わない。「別の場所（別リージョン・
# 別サービス）に保管」の要件を満たすには、保存後にrclone等でオブジェクトストレージへ
# 同期すること(このVPS自体が壊れた/消えた場合に備えるため)。
#
# 使い方: docker/backup_db.sh
# cron例(README.md参照): 0 3 * * * cd /opt/tsunagumo/saas/docker && ./backup_db.sh >> /var/log/tsunagumo_backup.log 2>&1

set -euo pipefail
cd "$(dirname "$0")"

if [ -z "${BACKUP_ENCRYPTION_PASSPHRASE:-}" ] && [ -f ../.env ]; then
  set -a
  source ../.env
  set +a
fi

if [ -z "${BACKUP_ENCRYPTION_PASSPHRASE:-}" ]; then
  echo "エラー: BACKUP_ENCRYPTION_PASSPHRASEが設定されていません(.envに追加してください)" >&2
  exit 1
fi

BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
OUT_FILE="$BACKUP_DIR/tsunagumo_${TIMESTAMP}.sql.gz.enc"

START=$(date +%s)
docker compose exec -T db pg_dump -U tsunagumo tsunagumo \
  | gzip \
  | openssl enc -aes-256-cbc -pbkdf2 -salt -pass env:BACKUP_ENCRYPTION_PASSPHRASE \
  > "$OUT_FILE"
END=$(date +%s)

SIZE=$(du -h "$OUT_FILE" | cut -f1)
echo "バックアップ完了: $OUT_FILE (${SIZE}, $((END - START))秒)"

# 保持期間を過ぎた古いバックアップを削除する(ディスクを埋めないため。8-6のディスク監視と対になる)
find "$BACKUP_DIR" -name 'tsunagumo_*.sql.gz.enc' -mtime "+${RETENTION_DAYS}" -delete
