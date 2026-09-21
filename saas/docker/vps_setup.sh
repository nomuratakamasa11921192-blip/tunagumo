#!/bin/bash
# さくらVPS(Ubuntu)上で、SaaS本体をゼロから起動するためのセットアップスクリプト。
# VPSにSSH接続した状態で実行する想定(root、またはsudo権限のあるユーザーで)。
#
# 使い方:
#   1. このスクリプトをVPSに転送する(scpや、SSH接続後にこの内容をコピペしてファイル作成)
#   2. chmod +x vps_setup.sh && sudo ./vps_setup.sh
#
# このスクリプトが終わったら、次にやること:
#   - git clone (またはscp)で saas/ 一式を /opt/tsunagumo/saas に転送
#   - /opt/tsunagumo/saas/.env を本番用に作成(prod_secrets.txtの値を使う)
#   - docker compose up --build -d
#   - docker compose exec api alembic upgrade head
#   - リバースプロキシ(Caddy)でHTTPS化(下記スクリプトに含む)
#   - crontab に承認期限スキャンを登録(下記スクリプトに含む)

set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

echo "=== 1. パッケージ更新 ==="
apt-get update -y
apt-get upgrade -y -o Dpkg::Options::="--force-confold"

echo "=== 2. Dockerのインストール ==="
if ! command -v docker &> /dev/null; then
  curl -fsSL https://get.docker.com | sh
  systemctl enable docker
  systemctl start docker
else
  echo "Dockerは既にインストール済みです"
fi

echo "=== 3. Docker Composeプラグインの確認 ==="
docker compose version

echo "=== 4. Caddy(リバースプロキシ・HTTPS自動化)のインストール ==="
if ! command -v caddy &> /dev/null; then
  apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y
  apt-get install -y caddy
else
  echo "Caddyは既にインストール済みです"
fi

echo "=== 5. 作業ディレクトリの作成 ==="
mkdir -p /opt/tsunagumo

echo "=== 6. ファイアウォールの基本設定(SSH・HTTP・HTTPSのみ許可) ==="
if command -v ufw &> /dev/null; then
  ufw allow OpenSSH
  ufw allow 80/tcp
  ufw allow 443/tcp
  ufw --force enable
fi

echo "=== 7. 自動セキュリティアップデートの有効化(8-3) ==="
apt-get install -y unattended-upgrades
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "=== 8. swapの設定(8-3: メモリが少ないプランでも安定させるため) ==="
# 既にswapがある場合は何もしない(2重に作らない)
if [ "$(swapon --show | wc -l)" -eq 0 ]; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo "/swapfile none swap sw 0 0" >> /etc/fstab
else
  echo "swapは既に設定済みです"
fi

echo ""
echo "=== セットアップ完了 ==="
echo "次のステップ:"
echo "  1. /opt/tsunagumo に saas/ 一式を転送してください"
echo "  2. /opt/tsunagumo/saas/.env を本番用に作成してください(prod_secrets.txtの値を使う)"
echo "     BACKUP_ENCRYPTION_PASSPHRASEも忘れずに設定してください(docker/backup_db.sh用)"
echo "  3. cd /opt/tsunagumo/saas/docker && docker compose up --build -d"
echo "  4. docker compose exec api alembic upgrade head"
echo "  5. Caddyfileを設定してHTTPS化(別途案内します)"
echo "  6. docker/backup_db.sh をcronに登録してください(README.mdのバックアップ手順を参照)"
