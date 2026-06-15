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

echo "=== SolTrace WAS 설치 시작 (Rocky Linux 8) ==="

# ── [1/7] 패키지 설치 ───────────────────────────────────────────────────────
echo "[1/7] 패키지 설치 중..."

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

dnf install -y \
    postgresql16-server postgresql16 postgresql16-contrib \
    python3.11 python3.11-devel \
    nginx \
    gcc \
    git \
    libpq-devel

# ── [2/7] PostgreSQL 초기화 ─────────────────────────────────────────────────
echo "[2/7] PostgreSQL 초기화 중..."

if [ ! -f /var/lib/pgsql/16/data/PG_VERSION ]; then
    /usr/pgsql-16/bin/postgresql-16-setup initdb
fi

# pg_hba.conf: soltrace 유저 → password 인증
PG_HBA="/var/lib/pgsql/16/data/pg_hba.conf"
if ! grep -q "soltrace" "$PG_HBA"; then
    sed -i '/^# TYPE/a local   soltrace        soltrace                                md5\nhost    soltrace        soltrace        127.0.0.1/32            md5' "$PG_HBA"
fi

# postgresql.conf 튜닝 (docker-compose.yml 설정과 동일)
PG_CONF="/var/lib/pgsql/16/data/postgresql.conf"
sed -i \
    -e "s/^#*shared_buffers.*/shared_buffers = 256MB/" \
    -e "s/^#*work_mem.*/work_mem = 4MB/" \
    -e "s/^#*effective_cache_size.*/effective_cache_size = 512MB/" \
    -e "s/^#*maintenance_work_mem.*/maintenance_work_mem = 64MB/" \
    -e "s/^#*wal_buffers.*/wal_buffers = 8MB/" \
    -e "s/^#*checkpoint_completion_target.*/checkpoint_completion_target = 0.9/" \
    -e "s/^#*random_page_cost.*/random_page_cost = 1.1/" \
    -e "s/^#*log_min_duration_statement.*/log_min_duration_statement = 1000/" \
    "$PG_CONF"

systemctl enable --now postgresql-16

# ── [3/7] DB·유저 생성 ──────────────────────────────────────────────────────
echo "[3/7] DB 유저 및 스키마 생성 중..."

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

# ── [4/7] 앱 유저·디렉터리 준비 ─────────────────────────────────────────────
echo "[4/7] 앱 유저 및 디렉터리 준비 중..."

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

# ── [5/7] systemd 서비스 등록 ────────────────────────────────────────────────
echo "[5/7] systemd 서비스 등록 중..."

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

# ── [6/7] nginx 설정 ─────────────────────────────────────────────────────────
echo "[6/7] nginx 설정 중..."

# docker upstream(was:8000) → localhost
sed 's/server was:8000/server 127.0.0.1:8000/' \
    "$SCRIPT_DIR/nginx/nginx.conf" > /etc/nginx/nginx.conf

# 설정 문법 검사
nginx -t

systemctl enable nginx

# ── [7/7] 서비스 시작 ────────────────────────────────────────────────────────
echo "[7/7] 서비스 시작 중..."

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

echo ""
echo "=== 설치 완료 ==="
echo "상태 확인: systemctl status soltrace-was nginx postgresql-16"
echo "WAS 로그:  journalctl -u soltrace-was -f"
echo "앱 로그:   tail -f /var/log/soltrace/error.log"
echo "WAS URL:   http://$(hostname -I | awk '{print $1}')"
echo ""
echo "초기 관리자 비밀번호: $(grep ADMIN_PASSWORD $ENV_FILE | cut -d= -f2)"
