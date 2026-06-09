#!/usr/bin/env bash
# SolTrace WAS 업데이트 스크립트 (EC2에서 실행)
# 사용법: bash scripts/update.sh [브랜치명]  (기본: main)

set -euo pipefail

BRANCH="${1:-main}"
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE="docker compose"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

cd "$APP_DIR"

log ">> 코드 pull: origin/$BRANCH"
git fetch origin
git checkout "$BRANCH"
git reset --hard "origin/$BRANCH"

log ">> WAS 이미지 빌드"
$COMPOSE build --no-cache was

log ">> DB 준비 확인"
$COMPOSE up -d db
timeout 60 bash -c 'until docker compose exec -T db pg_isready -U soltrace -q; do sleep 2; done'

log ">> WAS / Nginx 재시작"
$COMPOSE up -d --no-deps was
$COMPOSE up -d --no-deps nginx

log ">> 헬스체크 대기..."
for i in $(seq 1 30); do
    if curl -sf http://localhost/api/docs > /dev/null 2>&1; then
        log "   헬스체크 OK (${i}초)"; break
    fi
    [ "$i" -eq 30 ] && { log "ERROR: 헬스체크 실패. docker compose logs was 확인"; exit 1; }
    sleep 2
done

log "=============================="
log "업데이트 완료: branch=$BRANCH"
$COMPOSE ps
log "=============================="
