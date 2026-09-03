#!/usr/bin/env bash
# nginx.conf 렌더링 공통 라이브러리 (source 전용, 단독 실행 아님)
#
# 왜 라이브러리인가:
#   install_was_rocky8.sh / update_rocky8.sh / soltrace-selfupdate.sh / setup_ssl.sh
#   네 곳이 모두 nginx/nginx.conf 를 /etc/nginx/nginx.conf 로 렌더링한다.
#   도메인·인증서 경로 치환이 한 곳에만 있지 않으면, 업데이트 한 번에 도메인이
#   템플릿 기본값으로 되돌아가 인증서 경로가 어긋나고 nginx 가 뜨지 않는다.
#
# 사용:
#   source scripts/nginx_conf.sh
#   soltrace_apply_nginx <repo_dir> [domain]

SOLTRACE_TEMPLATE_DOMAIN="soltrace.mbone.net"   # nginx/nginx.conf 에 박혀 있는 기준 도메인
SOLTRACE_DOMAIN_FILE="/etc/soltrace/domain"     # 설치 시 선택한 실제 도메인
SOLTRACE_ACME_WEBROOT="/var/www/letsencrypt"    # nginx.conf 의 acme-challenge root 와 동일해야 함
SOLTRACE_SELF_CERT_DIR="/etc/pki/soltrace"      # LE 발급 전 임시 자체서명 인증서
SOLTRACE_LE_DIR="/etc/letsencrypt/live"         # certbot 인증서 위치
SOLTRACE_NGINX_CONF="/etc/nginx/nginx.conf"     # 렌더링 결과

# 현재 도메인: 저장값 > 배포된 nginx.conf 의 server_name(구버전 설치본) > 템플릿 기본값
soltrace_current_domain() {
    if [ -s "$SOLTRACE_DOMAIN_FILE" ]; then
        tr -d ' \t\r\n' < "$SOLTRACE_DOMAIN_FILE"
        return
    fi
    local d=""
    if [ -f /etc/nginx/nginx.conf ]; then
        d=$({ grep -m1 -E '^[[:space:]]*server_name[[:space:]]' /etc/nginx/nginx.conf || true; } \
            | awk '{print $2}' | tr -d ';')
    fi
    if [ -n "$d" ] && [ "$d" != "_" ]; then
        printf '%s' "$d"
    else
        printf '%s' "$SOLTRACE_TEMPLATE_DOMAIN"
    fi
}

soltrace_save_domain() {
    mkdir -p "$(dirname "$SOLTRACE_DOMAIN_FILE")"
    printf '%s\n' "$1" > "$SOLTRACE_DOMAIN_FILE"
    chmod 644 "$SOLTRACE_DOMAIN_FILE"
}

soltrace_have_le_cert() { [ -s "$SOLTRACE_LE_DIR/$1/fullchain.pem" ]; }

# LE 인증서가 나오기 전까지 쓸 임시 자체서명 인증서.
# 인증서 파일이 없으면 nginx 가 아예 기동되지 않는데, 정작 LE 발급(webroot 챌린지)은
# 80 포트를 서빙하는 nginx 를 필요로 한다 — 그 닭-달걀 문제를 끊는 용도.
soltrace_ensure_self_signed() {
    local domain="$1"
    [ -s "$SOLTRACE_SELF_CERT_DIR/fullchain.pem" ] && [ -s "$SOLTRACE_SELF_CERT_DIR/privkey.pem" ] && return 0
    mkdir -p "$SOLTRACE_SELF_CERT_DIR"
    openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
        -subj "/CN=${domain}" \
        -keyout "$SOLTRACE_SELF_CERT_DIR/privkey.pem" \
        -out    "$SOLTRACE_SELF_CERT_DIR/fullchain.pem" >/dev/null 2>&1
    cp "$SOLTRACE_SELF_CERT_DIR/fullchain.pem" "$SOLTRACE_SELF_CERT_DIR/chain.pem"
    chmod 600 "$SOLTRACE_SELF_CERT_DIR/privkey.pem"
    chmod 644 "$SOLTRACE_SELF_CERT_DIR/fullchain.pem" "$SOLTRACE_SELF_CERT_DIR/chain.pem"
}

soltrace_nginx_version() {
    { nginx -v 2>&1 || true; } | sed -n 's|.*nginx/\([0-9][0-9.]*\).*|\1|p'
}

# nginx 1.25.1+ 는 'listen ... http2' 파라미터가 폐지 예고 → 'http2 on;' 지시어로 분리한다.
# 반대로 1.25.1 미만에서 'http2 on;' 을 쓰면 unknown directive 로 nginx 가 아예 뜨지 않으므로,
# 템플릿은 옛 형식으로 두고 설치된 버전에 맞춰 여기서 변환한다.
_soltrace_http2_filter() {
    local ver
    ver=$(soltrace_nginx_version)
    if [ -n "$ver" ] && [ "$(printf '%s\n%s\n' 1.25.1 "$ver" | sort -V | head -1)" = "1.25.1" ]; then
        awk '/^[[:space:]]*listen 443 ssl http2;[[:space:]]*$/ {
                 indent = $0; sub(/[^[:space:]].*/, "", indent)
                 print indent "listen 443 ssl;"
                 print indent "http2 on;"
                 next
             }
             { print }'
    else
        cat
    fi
}

# /etc/nginx/nginx.conf 생성. LE 인증서가 있으면 그것을, 없으면 자체서명을 가리킨다.
# 사용법: soltrace_render_nginx <repo_dir> [domain]
soltrace_render_nginx() {
    local repo="$1"
    local domain="${2:-$(soltrace_current_domain)}"
    local cert_dir stapling_sed

    if soltrace_have_le_cert "$domain"; then
        cert_dir="$SOLTRACE_LE_DIR/$domain"
        stapling_sed='s|^\([[:space:]]*\)#\(ssl_stapling\)|\1\2|'
    else
        soltrace_ensure_self_signed "$domain"
        cert_dir="$SOLTRACE_SELF_CERT_DIR"
        # 자체서명은 OCSP 응답자가 없어 스테이플링이 error_log 만 채운다 → 주석 처리
        stapling_sed='s|^\([[:space:]]*\)\(ssl_stapling\)|\1#\2|'
    fi

    mkdir -p "$SOLTRACE_ACME_WEBROOT/.well-known/acme-challenge"
    chmod -R 755 "$SOLTRACE_ACME_WEBROOT"

    # 인증서 경로 치환이 먼저다 — 두 패턴 모두 템플릿 도메인을 포함하므로 순서가 바뀌면 어긋난다
    sed -e 's|server was:8000|server 127.0.0.1:8000|' \
        -e "s|/etc/letsencrypt/live/${SOLTRACE_TEMPLATE_DOMAIN}/|${cert_dir}/|g" \
        -e "s|${SOLTRACE_TEMPLATE_DOMAIN}|${domain}|g" \
        -e "$stapling_sed" \
        "$repo/nginx/nginx.conf" | _soltrace_http2_filter > "$SOLTRACE_NGINX_CONF"
}

# 렌더링 + 문법검사 + 반영. nginx 가 떠 있으면 reload, 아니면 호출측이 start 한다.
soltrace_apply_nginx() {
    local repo="$1"
    local domain="${2:-$(soltrace_current_domain)}"
    soltrace_render_nginx "$repo" "$domain"
    nginx -t
    if systemctl is-active --quiet nginx; then
        systemctl reload nginx
    fi
}
