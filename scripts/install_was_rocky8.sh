#!/usr/bin/env bash
# SolTrace WAS 설치 스크립트 (Rocky Linux 8)
# Docker 없이 systemd 서비스로 직접 운영
# 실행: sudo bash scripts/install_was_rocky8.sh

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="/opt/soltrace"
APP_USER="soltrace"

# 랜덤 문자열 생성. head 가 파이프를 닫을 때 tr 의 SIGPIPE(141)로
# set -o pipefail + set -e 가 죽지 않도록 서브셸에서 pipefail 을 끈다.
_rand() { ( set +o pipefail; LC_ALL=C tr -dc 'a-zA-Z0-9' < /dev/urandom | head -c "$1" ); }

# nginx.conf 렌더링(도메인·인증서 경로 치환) 공통 로직
# shellcheck source=scripts/nginx_conf.sh
source "$SCRIPT_DIR/scripts/nginx_conf.sh"

echo "=== SolTrace WAS 설치 시작 (Rocky Linux 8) ==="

# ── 설치 옵션 입력 (도메인 / HTTPS) ─────────────────────────────────────────
# 긴 패키지 설치 전에 먼저 물어본다. 비대화형 설치는 환경변수로 지정:
#   SOLTRACE_DOMAIN / SOLTRACE_LE_EMAIL / SOLTRACE_SETUP_SSL=yes|no
DEFAULT_DOMAIN=$(soltrace_current_domain)
DOMAIN="${SOLTRACE_DOMAIN:-}"
LE_EMAIL="${SOLTRACE_LE_EMAIL:-}"
SETUP_SSL="${SOLTRACE_SETUP_SSL:-}"

if ( : < /dev/tty ) 2>/dev/null; then   # 제어 터미널이 실제로 열리는지 확인
    if [ -z "$DOMAIN" ]; then
        {
            echo ""
            echo "서비스 도메인을 입력하세요 (예: soltrace.example.com)"
            printf "  [엔터 = %s]: " "$DEFAULT_DOMAIN"
        } > /dev/tty
        read -r DOMAIN < /dev/tty || DOMAIN=""
    fi
    if [ -z "$SETUP_SSL" ]; then
        printf "Let's Encrypt 인증서를 발급하고 자동 갱신을 설정할까요? (Y/n): " > /dev/tty
        read -r SETUP_SSL < /dev/tty || SETUP_SSL=""
        [ -z "$SETUP_SSL" ] && SETUP_SSL="yes"
    fi
    case "${SETUP_SSL,,}" in y|yes) SETUP_SSL=yes ;; *) SETUP_SSL=no ;; esac
    if [ "$SETUP_SSL" = "yes" ] && [ -z "$LE_EMAIL" ]; then
        printf "인증서 만료 알림 이메일 (엔터 = 생략): " > /dev/tty
        read -r LE_EMAIL < /dev/tty || LE_EMAIL=""
    fi
else
    # 터미널이 없으면 인증서 발급을 자동으로 하지 않는다 —
    # 잘못된 도메인으로 발급을 시도하면 Let's Encrypt 발급 한도(주당 5회)만 소모된다.
    case "${SETUP_SSL,,}" in y|yes) SETUP_SSL=yes ;; *) SETUP_SSL=no ;; esac
fi

DOMAIN=$(printf '%s' "$DOMAIN" | tr -d ' \t\r\n')
LE_EMAIL=$(printf '%s' "$LE_EMAIL" | tr -d ' \t\r\n')
[ -z "$DOMAIN" ] && DOMAIN="$DEFAULT_DOMAIN"
DOMAIN="${DOMAIN#http://}"; DOMAIN="${DOMAIN#https://}"; DOMAIN="${DOMAIN%%/*}"

# IP 주소에는 Let's Encrypt 인증서를 발급할 수 없다 (자체서명으로 동작)
if [[ "$DOMAIN" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] && [ "$SETUP_SSL" = "yes" ]; then
    echo "  [WARN] IP($DOMAIN)로는 Let's Encrypt 인증서를 발급할 수 없습니다 — 인증서 발급을 건너뜁니다."
    SETUP_SSL=no
fi
echo "  도메인: $DOMAIN / Let's Encrypt 발급: $SETUP_SSL"

# ── [1/8] 패키지 설치 ───────────────────────────────────────────────────────
echo "[1/8] 패키지 설치 중..."

# PostgreSQL 16 공식 저장소
if ! rpm -q pgdg-redhat-repo &>/dev/null; then
    dnf install -y https://download.postgresql.org/pub/repos/yum/reporpms/EL-8-x86_64/pgdg-redhat-repo-latest.noarch.rpm
    dnf -qy module disable postgresql
fi

# EPEL (nginx, python3.11)
dnf install -y epel-release

# PowerTools/CRB 활성화 (pgdg의 *-devel 이 요구하는 perl-IPC-Run 등이 여기 있음)
dnf install -y dnf-plugins-core
for repo in powertools crb codeready-builder-for-rhel-8-x86_64-rpms; do
    if dnf repolist all 2>/dev/null | grep -qi "^${repo}\b"; then
        dnf config-manager --set-enabled "$repo" && break
    fi
done

# nginx: Rocky 8 기본 모듈 스트림은 1.14(2018년) — 가능한 최신 스트림으로 올린다.
# (1.25.1+ 로 더 올릴 경우 nginx.conf 의 'listen 443 ssl http2' 를 'http2 on' 으로 바꿔야 함)
if ! rpm -q nginx &>/dev/null; then
    dnf module reset -y nginx 2>/dev/null || true
    for stream in 1.24 1.22 1.20; do
        dnf module enable -y "nginx:$stream" 2>/dev/null && break
    done
fi

dnf install -y \
    postgresql16-server postgresql16 postgresql16-contrib \
    python3.11 python3.11-devel \
    nginx \
    gcc \
    git \
    libpq-devel

echo "  >> $(nginx -v 2>&1)"

# ── [2/8] PostgreSQL 초기화 ─────────────────────────────────────────────────
echo "[2/8] PostgreSQL 초기화 중..."

if [ ! -f /var/lib/pgsql/16/data/PG_VERSION ]; then
    /usr/pgsql-16/bin/postgresql-16-setup initdb
fi

# pg_hba.conf: soltrace 유저 → password 인증
PG_HBA="/var/lib/pgsql/16/data/pg_hba.conf"
if ! grep -q "soltrace" "$PG_HBA"; then
    sed -i '/^# TYPE/a local   soltrace        soltrace                                md5\nhost    soltrace        soltrace        127.0.0.1/32            md5' "$PG_HBA"
fi

# postgresql.conf 튜닝 — 서버 RAM 비율로 계산 (scripts/tune_pg_rocky8.sh)
# 아직 기동 전이므로 재시작 불필요. 이후 update_rocky8.sh 가 변경 시에만 재적용·재시작한다.
bash "$SCRIPT_DIR/scripts/tune_pg_rocky8.sh"

systemctl enable --now postgresql-16

# ── [3/8] DB·유저 생성 ──────────────────────────────────────────────────────
echo "[3/8] DB 유저 및 스키마 생성 중..."

# .env에서 DB_PASSWORD 읽거나 새로 생성
ENV_FILE="$APP_DIR/.env"
if [ -f "$ENV_FILE" ]; then
    DB_PASSWORD=$(grep "^DB_PASSWORD=" "$ENV_FILE" | cut -d= -f2)
    SECRET_KEY=$(grep "^SECRET_KEY=" "$ENV_FILE" | cut -d= -f2)
    ADMIN_PASSWORD=$(grep "^ADMIN_PASSWORD=" "$ENV_FILE" | cut -d= -f2)
else
    DB_PASSWORD=$(_rand 32)
    SECRET_KEY=$(_rand 48)
    ADMIN_PASSWORD=$(_rand 16)
fi

# psql로 유저/DB 생성 (이미 존재하면 무시)
sudo -u postgres psql -v ON_ERROR_STOP=0 <<SQL
DO \$\$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'soltrace') THEN
        CREATE USER soltrace WITH PASSWORD '${DB_PASSWORD}';
    ELSE
        ALTER USER soltrace WITH PASSWORD '${DB_PASSWORD}';
    END IF;
END \$\$;
SELECT 'CREATE DATABASE soltrace' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'soltrace')\gexec
GRANT ALL PRIVILEGES ON DATABASE soltrace TO soltrace;
SQL

# 스키마 적용 (확장 생성 등은 슈퍼유저 필요 → postgres 로 적용)
sudo -u postgres psql -d soltrace -f "$SCRIPT_DIR/postgres/init.sql"

# 소유권/권한 이관: 앱이 soltrace 로 접속해 런타임에 파티션을 생성하므로
# (PG15+ public 스키마 CREATE 기본 회수 + 파티션 추가는 부모 테이블 소유자 필요)
# DB·스키마 소유권과 init.sql 로 생성된 모든 객체를 soltrace 로 넘긴다.
# REASSIGN OWNED BY postgres 는 슈퍼유저 시스템 객체까지 걸려 거부되므로,
# public 스키마의 앱 테이블·시퀀스만 골라 소유권을 옮긴다.
sudo -u postgres psql -d soltrace <<'SQL'
ALTER DATABASE soltrace OWNER TO soltrace;
GRANT ALL ON SCHEMA public TO soltrace;
ALTER SCHEMA public OWNER TO soltrace;
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT tablename FROM pg_tables WHERE schemaname='public' LOOP
    EXECUTE format('ALTER TABLE public.%I OWNER TO soltrace', r.tablename);
  END LOOP;
  FOR r IN SELECT sequencename FROM pg_sequences WHERE schemaname='public' LOOP
    EXECUTE format('ALTER SEQUENCE public.%I OWNER TO soltrace', r.sequencename);
  END LOOP;
END $$;
GRANT ALL ON ALL TABLES IN SCHEMA public TO soltrace;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO soltrace;
SQL

# ── [4/8] 앱 유저·디렉터리 준비 ─────────────────────────────────────────────
echo "[4/8] 앱 유저 및 디렉터리 준비 중..."

id "$APP_USER" &>/dev/null || useradd -r -s /sbin/nologin -d "$APP_DIR" "$APP_USER"

mkdir -p "$APP_DIR"
cp -r "$SCRIPT_DIR/was/app" "$APP_DIR/"
cp -r "$SCRIPT_DIR/was/static" "$APP_DIR/"
cp "$SCRIPT_DIR/was/requirements.txt" "$APP_DIR/"

# Python 가상환경
if [ ! -f "$APP_DIR/venv/bin/activate" ]; then
    python3.11 -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

# .env 생성 (최초 설치 시만)
if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" <<EOF
DATABASE_URL=postgresql://soltrace:${DB_PASSWORD}@127.0.0.1:5432/soltrace
SECRET_KEY=${SECRET_KEY}
ADMIN_PASSWORD=${ADMIN_PASSWORD}
EOF
    echo "  ┌─────────────────────────────────────────"
    echo "  │ DB_PASSWORD   : ${DB_PASSWORD}"
    echo "  │ ADMIN_PASSWORD: ${ADMIN_PASSWORD}"
    echo "  │ SECRET_KEY    : ${SECRET_KEY}"
    echo "  └─────────────────────────────────────────"
    echo "  ※ 위 값을 안전한 곳에 보관하세요."
fi

chmod 600 "$ENV_FILE"
mkdir -p /var/log/soltrace
chown -R "$APP_USER:$APP_USER" "$APP_DIR" /var/log/soltrace

# ── [5/8] systemd 서비스 등록 ────────────────────────────────────────────────
echo "[5/8] systemd 서비스 등록 중..."

cat > /etc/systemd/system/soltrace-was.service <<'UNIT'
[Unit]
Description=SolTrace WAS (Gunicorn + Uvicorn)
After=network.target postgresql-16.service
Requires=postgresql-16.service

[Service]
Type=notify
User=soltrace
Group=soltrace
WorkingDirectory=/opt/soltrace
EnvironmentFile=/opt/soltrace/.env
ExecStart=/opt/soltrace/venv/bin/gunicorn app.main:app \
    -w 2 \
    -k uvicorn.workers.UvicornWorker \
    --bind 127.0.0.1:8000 \
    --worker-tmp-dir /dev/shm \
    --timeout 60 \
    --graceful-timeout 30 \
    --max-requests 2000 \
    --max-requests-jitter 200 \
    --access-logfile /var/log/soltrace/access.log \
    --error-logfile /var/log/soltrace/error.log
ExecReload=/bin/kill -s HUP $MAINPID
KillMode=mixed
TimeoutStopSec=10
PrivateTmp=true
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable soltrace-was

# ── [6/8] nginx 설정 ─────────────────────────────────────────────────────────
echo "[6/8] nginx 설정 중..."

# 도메인/인증서 경로 치환 + docker upstream(was:8000) → localhost.
# 인증서가 아직 없으면 임시 자체서명으로 렌더링된다 (nginx 가 떠 있어야 LE 챌린지가 가능).
soltrace_save_domain "$DOMAIN"
soltrace_render_nginx "$SCRIPT_DIR" "$DOMAIN"

# 설정 문법 검사
nginx -t

systemctl enable nginx

# ── [7/8] 서비스 시작 ────────────────────────────────────────────────────────
echo "[7/8] 서비스 시작 중..."

systemctl restart soltrace-was
systemctl restart nginx

# 방화벽 허용 (firewalld 사용 시)
if systemctl is-active --quiet firewalld; then
    firewall-cmd --permanent --add-service=http
    firewall-cmd --permanent --add-service=https
    firewall-cmd --reload
fi

# SELinux: nginx → localhost:8000 연결 허용
if command -v setsebool &>/dev/null; then
    setsebool -P httpd_can_network_connect 1
fi

# 월별 파티션 자동 생성 cron 등록 (매월 1일 00:30)
chmod +x "$SCRIPT_DIR/scripts/create_partitions.sh"
cat > /etc/cron.d/soltrace-partitions <<CRON
# SolTrace ftp_logs 월별 파티션 자동 생성
30 0 1 * * root bash $SCRIPT_DIR/scripts/create_partitions.sh >> /var/log/soltrace/partitions.log 2>&1
CRON
chmod 644 /etc/cron.d/soltrace-partitions

# DB 증분 백업 cron 등록 (매일 03:00, 최대 3년치 보관)
chmod +x "$SCRIPT_DIR/scripts/backup_db.sh"
cat > /etc/cron.d/soltrace-backup <<CRON
# SolTrace DB 증분 백업 (월별 파티션 기반, 3년 보관)
0 3 * * * root bash $SCRIPT_DIR/scripts/backup_db.sh >> /var/log/soltrace/backup.log 2>&1
CRON
chmod 644 /etc/cron.d/soltrace-backup

# ── 웹 설정 페이지: git 자가 업데이트 래퍼 + sudoers ─────────────────────────
# 래퍼는 repo 밖(root 소유, soltrace 수정 불가)에 두고, soltrace 는 sudo 로
# "인자 없이"만 실행 가능하도록 제한한다.
echo "자가 업데이트 래퍼 및 sudoers 등록 중..."
install -m 0755 -o root -g root \
    "$SCRIPT_DIR/scripts/soltrace-selfupdate.sh" /usr/local/sbin/soltrace-selfupdate
# 래퍼가 참조하는 nginx 렌더링 라이브러리 (root 소유 경로에 둔다)
install -D -m 0644 -o root -g root \
    "$SCRIPT_DIR/scripts/nginx_conf.sh" /usr/local/lib/soltrace/nginx_conf.sh

cat > /etc/sudoers.d/soltrace-update <<'SUDO'
# WAS(soltrace) 가 웹 설정 페이지에서 자가 업데이트만 트리거하도록 허용.
# 인자 없이 호출하는 형태만 허용한다.
soltrace ALL=(root) NOPASSWD: /usr/local/sbin/soltrace-selfupdate ""
SUDO
chmod 440 /etc/sudoers.d/soltrace-update
# 문법 검증 (실패 시 잘못된 규칙 제거)
visudo -cf /etc/sudoers.d/soltrace-update || rm -f /etc/sudoers.d/soltrace-update

# WAS(soltrace) 가 git 으로 버전 정보를 읽을 수 있도록 안전 디렉터리 등록
sudo -u "$APP_USER" git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true
# root 가 저장소에서 git(업데이트/셀프업데이트) 실행 시 dubious ownership 방지 (HOME 비의존)
git config --system --add safe.directory "$APP_DIR" 2>/dev/null || true

# ── [8/8] HTTPS 인증서 (Let's Encrypt) ──────────────────────────────────────
echo "[8/8] HTTPS 인증서 설정 중..."

if [ "$SETUP_SSL" = "yes" ]; then
    # 실패해도 설치 자체는 계속한다 (자체서명 인증서로 서비스는 동작)
    bash "$SCRIPT_DIR/scripts/setup_ssl.sh" "$DOMAIN" "$LE_EMAIL" || {
        echo "  [WARN] 인증서 발급 실패 — 임시 자체서명 인증서로 동작합니다."
        echo "         원인 해결 후: sudo bash $SCRIPT_DIR/scripts/setup_ssl.sh $DOMAIN admin@example.com"
    }
else
    echo "  Let's Encrypt 발급을 건너뜁니다 — 임시 자체서명 인증서로 동작합니다(브라우저 경고)."
    echo "  나중에 발급: sudo bash $SCRIPT_DIR/scripts/setup_ssl.sh $DOMAIN admin@example.com"
fi

echo ""
echo "=== 설치 완료 ==="
echo "상태 확인: systemctl status soltrace-was nginx postgresql-16"
echo "WAS 로그:  journalctl -u soltrace-was -f"
echo "앱 로그:   tail -f /var/log/soltrace/error.log"
echo "WAS URL:   https://$DOMAIN"
if soltrace_have_le_cert "$DOMAIN"; then
    echo "인증서:    Let's Encrypt (자동 갱신 등록됨 — certbot certificates 로 확인)"
else
    echo "인증서:    임시 자체서명 (발급: sudo bash $SCRIPT_DIR/scripts/setup_ssl.sh $DOMAIN)"
fi
echo ""
echo "초기 관리자 비밀번호: $(grep ADMIN_PASSWORD $ENV_FILE | cut -d= -f2)"
