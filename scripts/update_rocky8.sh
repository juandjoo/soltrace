#!/usr/bin/env bash
# SolTrace WAS 업데이트 스크립트 (Rocky Linux 8)
# 사용법: sudo bash scripts/update_rocky8.sh [브랜치명]  (기본: main)

set -euo pipefail

BRANCH="${1:-main}"
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY_DIR="/opt/soltrace"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

cd "$APP_DIR"

log ">> 코드 pull: origin/$BRANCH"
git fetch origin
git checkout "$BRANCH"
git reset --hard "origin/$BRANCH"

log ">> 앱 파일 배포"
cp -r was/app "$DEPLOY_DIR/"
cp -r was/static "$DEPLOY_DIR/"
cp was/requirements.txt "$DEPLOY_DIR/"
chown -R soltrace:soltrace "$DEPLOY_DIR/app" "$DEPLOY_DIR/static" "$DEPLOY_DIR/requirements.txt"

log ">> pip 패키지 업데이트"
"$DEPLOY_DIR/venv/bin/pip" install --quiet -r "$DEPLOY_DIR/requirements.txt"

log ">> DB 스키마 마이그레이션"
sudo -u postgres psql -d soltrace -f "$APP_DIR/postgres/init.sql"
# init.sql 은 postgres 로 적용되므로 새로 생긴 테이블/시퀀스 소유권을 soltrace 로 이관
# (앱이 soltrace 로 접속해 INSERT/UPDATE/DDL 하려면 소유권 필요 — 예: app_config)
sudo -u postgres psql -d soltrace -tAc "SELECT format('ALTER TABLE public.%I OWNER TO soltrace;', tablename) FROM pg_tables WHERE schemaname='public' UNION ALL SELECT format('ALTER SEQUENCE public.%I OWNER TO soltrace;', sequencename) FROM pg_sequences WHERE schemaname='public'" | sudo -u postgres psql -d soltrace

log ">> nginx 설정 반영"
sed 's/server was:8000/server 127.0.0.1:8000/' \
    "$APP_DIR/nginx/nginx.conf" > /etc/nginx/nginx.conf
nginx -t
systemctl reload nginx

log ">> WAS 재시작"
systemctl restart soltrace-was

log ">> 헬스체크 대기..."
for i in $(seq 1 30); do
    if curl -sf http://localhost/api/docs > /dev/null 2>&1; then
        log "   헬스체크 OK (${i}초)"; break
    fi
    [ "$i" -eq 30 ] && { log "ERROR: 헬스체크 실패. journalctl -u soltrace-was 확인"; exit 1; }
    sleep 2
done

log "=============================="
log "업데이트 완료: branch=$BRANCH"
systemctl status soltrace-was --no-pager -l
log "=============================="
