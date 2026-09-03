#!/usr/bin/env bash
# SolTrace HTTPS 설정 — Let's Encrypt 인증서 발급 + 자동 갱신 등록
#
# 사용법: sudo bash scripts/setup_ssl.sh [도메인] [이메일]
#   도메인 생략 시 /etc/soltrace/domain (설치 시 입력값) 사용
#   환경변수 SOLTRACE_DOMAIN / SOLTRACE_LE_EMAIL 로도 지정 가능
#
# 전제 조건:
#   1. 도메인 A레코드 → 이 서버 공인 IP
#   2. 80 포트가 외부(0.0.0.0/0)에 열려 있을 것 — Let's Encrypt 챌린지 접속 경로
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/nginx_conf.sh
source "$REPO_DIR/scripts/nginx_conf.sh"

RENEW_LOG="/var/log/soltrace/ssl_renew.log"

[ "$(id -u)" -eq 0 ] || { echo "[ERROR] root 권한이 필요합니다: sudo bash $0 ..."; exit 1; }

DOMAIN="${1:-${SOLTRACE_DOMAIN:-$(soltrace_current_domain)}}"
EMAIL="${2:-${SOLTRACE_LE_EMAIL:-}}"

if [ -z "$DOMAIN" ] || [ "$DOMAIN" = "_" ]; then
    echo "[ERROR] 도메인을 지정하세요: sudo bash $0 soltrace.example.com admin@example.com"
    exit 1
fi

echo "=== SolTrace HTTPS 설정: $DOMAIN ==="

# ── certbot 설치 ────────────────────────────────────────────────────────────
if ! command -v certbot &>/dev/null; then
    echo "[1/6] certbot 설치 중..."
    dnf install -y epel-release &>/dev/null || true
    dnf install -y certbot
else
    echo "[1/6] certbot 확인: $(certbot --version 2>&1)"
fi

# ── nginx 기동 확인 (webroot 챌린지는 80 포트 서빙이 전제) ──────────────────
echo "[2/6] nginx 기동 확인..."
soltrace_save_domain "$DOMAIN"
soltrace_apply_nginx "$REPO_DIR" "$DOMAIN"
systemctl is-active --quiet nginx || systemctl start nginx

# 방화벽 (firewalld 사용 시에만)
if systemctl is-active --quiet firewalld; then
    firewall-cmd --permanent --add-service=http &>/dev/null || true
    firewall-cmd --permanent --add-service=https &>/dev/null || true
    firewall-cmd --reload &>/dev/null || true
fi

# ── DNS 확인 (불일치해도 중단하지 않고 경고만) ──────────────────────────────
echo "[3/6] DNS 확인..."
PUBLIC_IP=$(curl -sf --max-time 5 https://checkip.amazonaws.com/ 2>/dev/null | tr -d ' \r\n' || true)
RESOLVED_IP=$(getent hosts "$DOMAIN" 2>/dev/null | awk '{print $1}' | head -1 || true)
if [ -z "$RESOLVED_IP" ]; then
    echo "  [WARN] DNS 조회 실패: $DOMAIN — A레코드 설정/전파를 확인하세요."
elif [ -n "$PUBLIC_IP" ] && [ "$PUBLIC_IP" != "$RESOLVED_IP" ]; then
    echo "  [WARN] IP 불일치 — 서버 공인 IP: $PUBLIC_IP / 도메인 해석 IP: $RESOLVED_IP"
else
    echo "  [OK] $DOMAIN → $RESOLVED_IP"
fi

# ── 인증서 발급 ─────────────────────────────────────────────────────────────
echo "[4/6] Let's Encrypt 인증서 발급 중... (동일 도메인 주당 5회 발급 제한 주의)"

# --nginx 플러그인 대신 --webroot 를 쓴다: 플러그인 방식은 '갱신할 때마다'
# python3-certbot-nginx 와 정상 동작하는 nginx -t 를 요구해서, nginx 를 손대다 플러그인이
# 빠지면 타이머 갱신이 조용히 실패하고 90일 뒤 인증서 만료로 사이트가 죽는다(그때까지 무증상).
# nginx.conf 에 /.well-known/acme-challenge/ → $SOLTRACE_ACME_WEBROOT 가 이미 있다.
CERTBOT_ARGS=(certonly --webroot -w "$SOLTRACE_ACME_WEBROOT"
              -d "$DOMAIN" --cert-name "$DOMAIN"
              --agree-tos --no-eff-email --non-interactive --keep-until-expiring)

if [ -n "$EMAIL" ]; then
    CERTBOT_ARGS+=(--email "$EMAIL")
else
    echo "  [WARN] 이메일 미지정 — 만료 임박 알림 메일을 받지 못합니다."
    echo "         이메일 등록: sudo bash $0 $DOMAIN admin@example.com"
    CERTBOT_ARGS+=(--register-unsafely-without-email)
fi

if certbot "${CERTBOT_ARGS[@]}"; then
    echo "  [OK] 인증서 발급 완료"
else
    echo ""
    echo "  [ERROR] 인증서 발급 실패 — 임시 자체서명 인증서로 HTTPS 를 유지합니다."
    echo "          확인 사항: DNS A레코드 / 80 포트 외부 개방 / 방화벽·보안그룹"
    echo "          로그: /var/log/letsencrypt/letsencrypt.log"
    echo "          해결 후 재실행: sudo bash $0 $DOMAIN ${EMAIL:-admin@example.com}"
    exit 1
fi

# ── 자동 갱신 ───────────────────────────────────────────────────────────────
echo "[5/6] 자동 갱신 설정 중..."

# 갱신 성공 시 nginx 에 새 인증서를 물린다 (reload 없으면 옛 인증서로 계속 서빙)
mkdir -p /etc/letsencrypt/renewal-hooks/deploy /var/log/soltrace
cat > /etc/letsencrypt/renewal-hooks/deploy/soltrace-reload-nginx.sh <<HOOK
#!/usr/bin/env bash
systemctl reload nginx 2>/dev/null || true
logger "[soltrace-certbot] nginx reloaded after cert renewal"
echo "[\$(date '+%F %T')] cert renewed (\${RENEWED_DOMAINS:-?}), nginx reloaded" >> $RENEW_LOG
HOOK
chmod 755 /etc/letsencrypt/renewal-hooks/deploy/soltrace-reload-nginx.sh

# Rocky 8 의 certbot 패키지는 certbot-renew.timer 를 제공한다. 없으면 cron 으로 대체.
TIMER=$({ systemctl list-unit-files 2>/dev/null || true; } | awk '/^certbot.*\.timer/ {print $1; exit}')
if [ -n "$TIMER" ]; then
    systemctl enable --now "$TIMER"
    echo "  [OK] systemd timer 활성화: $TIMER"
    rm -f /etc/cron.d/soltrace-certbot
else
    cat > /etc/cron.d/soltrace-certbot <<CRON
# SolTrace Let's Encrypt 자동 갱신 (만료 30일 전부터 실제 갱신됨)
17 3,15 * * * root certbot renew --quiet >> $RENEW_LOG 2>&1
CRON
    chmod 644 /etc/cron.d/soltrace-certbot
    echo "  [OK] cron 갱신 등록 (/etc/cron.d/soltrace-certbot)"
fi

# 발급된 인증서를 가리키도록 nginx.conf 재렌더 (자체서명 → LE 경로)
soltrace_apply_nginx "$REPO_DIR" "$DOMAIN"

# ── 갱신 리허설 ─────────────────────────────────────────────────────────────
echo "[6/6] 갱신 리허설(dry-run)..."
if certbot renew --dry-run --quiet; then
    echo "  [OK] 자동 갱신 정상 동작"
else
    echo "  [WARN] 갱신 리허설 실패 — 지금 인증서는 유효하나 자동 갱신은 확인 필요"
    echo "         확인: certbot renew --dry-run  /  journalctl -u ${TIMER:-crond}"
fi

EXPIRY=$(openssl x509 -enddate -noout -in "/etc/letsencrypt/live/$DOMAIN/cert.pem" 2>/dev/null | cut -d= -f2 || true)
echo ""
echo "=== HTTPS 설정 완료 ==="
echo " 접속:      https://$DOMAIN"
echo " 인증서:    /etc/letsencrypt/live/$DOMAIN/ (만료: ${EXPIRY:-확인 실패})"
echo " 자동 갱신: ${TIMER:-/etc/cron.d/soltrace-certbot} + deploy 훅(nginx reload)"
echo " 갱신 로그: $RENEW_LOG"
echo " 상태 확인: certbot certificates"
