#!/usr/bin/env bash
# SolTrace WAS 설치 스크립트 (Amazon Linux 2023)
# 실행: sudo bash install_was.sh

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== SolTrace WAS 설치 시작 ==="

# Docker 설치
if ! command -v docker &>/dev/null; then
    echo "[1/4] Docker 설치 중..."
    dnf install -y docker
    systemctl enable --now docker
    usermod -aG docker ec2-user 2>/dev/null || true
else
    echo "[1/4] Docker 이미 설치됨"
fi

# Docker Compose 설치
if ! command -v docker-compose &>/dev/null && ! docker compose version &>/dev/null 2>&1; then
    echo "[2/4] Docker Compose 설치 중..."
    COMPOSE_VERSION="v2.29.7"
    curl -SL "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-x86_64" \
        -o /usr/local/bin/docker-compose
    chmod +x /usr/local/bin/docker-compose
else
    echo "[2/4] Docker Compose 이미 설치됨"
fi

# 환경설정 파일 생성
cd "$SCRIPT_DIR"
if [ ! -f .env ]; then
    echo "[3/4] .env 파일 생성 중..."
    _rand() { cat /dev/urandom | tr -dc 'a-zA-Z0-9' | fold -w "$1" | head -n 1; }
    DB_PASSWORD=$(_rand 32)
    SECRET_KEY=$(_rand 48)
    ADMIN_PASSWORD=$(_rand 16)
    cat > .env <<EOF
DB_PASSWORD=${DB_PASSWORD}
SECRET_KEY=${SECRET_KEY}
ADMIN_PASSWORD=${ADMIN_PASSWORD}
LISTEN_PORT=80
EOF
    echo "  >> .env 생성됨 (모든 키 자동 생성)"
    echo "  ┌─────────────────────────────────────────"
    echo "  │ DB_PASSWORD   : ${DB_PASSWORD}"
    echo "  │ ADMIN_PASSWORD: ${ADMIN_PASSWORD}"
    echo "  │ SECRET_KEY    : ${SECRET_KEY}"
    echo "  └─────────────────────────────────────────"
    echo "  ※ 위 값을 안전한 곳에 보관하세요."
else
    echo "[3/4] .env 이미 존재함 (스킵)"
fi

# 서비스 시작
echo "[4/4] SolTrace 서비스 시작 중..."
docker compose pull db nginx 2>/dev/null || true
docker compose up -d --build

echo ""
echo "=== 설치 완료 ==="
echo "상태 확인: docker compose ps"
echo "로그 확인: docker compose logs -f was"
echo "WAS URL:   http://$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || echo 'YOUR_SERVER_IP')"
echo ""
echo "초기 비밀번호: .env 파일의 ADMIN_PASSWORD 값"
