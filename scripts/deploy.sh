#!/usr/bin/env bash
# SolTrace WAS 배포 스크립트
# 사용법: bash scripts/deploy.sh [브랜치명]  (기본: main)
# EC2 WAS 서버에서 실행

set -euo pipefail

BRANCH="${1:-main}"
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE="docker compose"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

cd "$APP_DIR"

# ── 1. 코드 업데이트 ─────────────────────────────────────────────────────────
log ">> 코드 pull: origin/$BRANCH"
git fetch origin
git checkout "$BRANCH"
git reset --hard "origin/$BRANCH"

# ── 2. 환경설정 확인 ─────────────────────────────────────────────────────────
if [ ! -f .env ]; then
    log "ERROR: .env 파일이 없습니다. cp .env.example .env 후 값을 설정하세요."
    exit 1
fi

# ── 3. 이미지 빌드 ───────────────────────────────────────────────────────────
log ">> WAS 이미지 빌드"
$COMPOSE build --no-cache was

# ── 4. DB 컨테이너 먼저 기동 (이미 실행 중이면 skip) ─────────────────────────
log ">> DB 기동 확인"
$COMPOSE up -d db
log "   DB 준비 대기 중..."
timeout 60 bash -c 'until docker compose exec -T db pg_isready -U soltrace -q; do sleep 2; done'

# ── 5. WAS 무중단 재시작 ─────────────────────────────────────────────────────
log ">> WAS 재시작"
$COMPOSE up -d --no-deps was

# ── 6. Nginx 재시작 (설정 변경 시) ───────────────────────────────────────────
log ">> Nginx 재시작"
$COMPOSE up -d --no-deps nginx

# ── 7. 헬스체크 ─────────────────────────────────────────────────────────────
log ">> 헬스체크 대기..."
MAX=30
for i in $(seq 1 $MAX); do
    if curl -sf http://localhost/api/docs > /dev/null 2>&1; then
        log "   헬스체크 OK (${i}초)"
        break
    fi
    if [ "$i" -eq "$MAX" ]; then
        log "ERROR: 헬스체크 실패. 로그를 확인하세요: docker compose logs was"
        exit 1
    fi
    sleep 2
done

# ── 8. 완료 ─────────────────────────────────────────────────────────────────
log "=============================="
log "배포 완료: branch=$BRANCH"
$COMPOSE ps
log "=============================="
