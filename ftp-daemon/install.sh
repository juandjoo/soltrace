#!/usr/bin/env bash
# SolTrace FTP Daemon 설치 스크립트
# 사용법: curl -fsSL <raw_url>/install.sh | sudo bash
#        또는: sudo bash install.sh
set -euo pipefail

INSTALL_DIR=/opt/soltrace-daemon
REPO_RAW="https://raw.githubusercontent.com/juandjoo/soltrace/main/ftp-daemon"
SERVICE_NAME=soltrace-daemon
DAEMON_USER=soltrace
STATE_DIR=/var/lib/soltrace
LOG_FILE=/var/log/soltrace-daemon.log

# ── Python 3.8 경로 탐색 ──────────────────────────────────────────
find_python() {
    for py in \
        /opt/rh/rh-python38/root/usr/bin/python3.8 \
        /usr/bin/python3.8 \
        /usr/local/bin/python3.8 \
        python3.8 python3
    do
        if command -v "$py" &>/dev/null; then
            echo "$py"
            return
        fi
    done
    echo ""
}

PYTHON=$(find_python)
if [ -z "$PYTHON" ]; then
    echo "[ERROR] Python 3.8 이상을 찾을 수 없습니다."
    echo "  CentOS 7: yum install rh-python38 (SCL 필요)"
    exit 1
fi

PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "[INFO] Python $PY_VER 사용: $PYTHON"

# ── 전용 시스템 계정 생성 ────────────────────────────────────────
echo "[INFO] 시스템 계정 생성 중: $DAEMON_USER"
useradd -r -M -s /sbin/nologin "$DAEMON_USER" 2>/dev/null || true

# ── 디렉터리 생성 ────────────────────────────────────────────────
mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

# ── 파일 다운로드 ────────────────────────────────────────────────
echo "[INFO] 파일 다운로드 중..."
for f in soltrace_daemon.py soltrace_bulk.py requirements.txt soltrace-daemon.service config.ini.example; do
    curl -fsSL "$REPO_RAW/$f" -o "$f"
done

# config.ini가 없으면 example 복사
if [ ! -f config.ini ]; then
    cp config.ini.example config.ini
    echo "[INFO] config.ini 생성됨 — 설정 후 서비스를 시작하세요."
fi

# ── 가상환경 생성 및 패키지 설치 ─────────────────────────────────
echo "[INFO] 가상환경 생성 중..."
"$PYTHON" -m venv venv
venv/bin/pip install --upgrade pip -q
venv/bin/pip install -r requirements.txt -q
echo "[INFO] 패키지 설치 완료"

# ── 파일 권한 설정 ───────────────────────────────────────────────
echo "[INFO] 파일 권한 설정 중..."
chown -R "$DAEMON_USER:$DAEMON_USER" "$INSTALL_DIR"
chmod 750 "$INSTALL_DIR"
chmod 640 "$INSTALL_DIR/config.ini"   # 소유자 rw, 그룹 r, 타 접근 불가

# 상태/버퍼 디렉터리
mkdir -p "$STATE_DIR"
chown "$DAEMON_USER:$DAEMON_USER" "$STATE_DIR"
chmod 700 "$STATE_DIR"

# 로그 파일
touch "$LOG_FILE"
chown "$DAEMON_USER:$DAEMON_USER" "$LOG_FILE"
chmod 640 "$LOG_FILE"

# ── FTP 로그 읽기 권한 ───────────────────────────────────────────
# config.ini의 transfer_log/extended_log 경로에 soltrace 계정 읽기 권한 부여
_raw=$(grep -i '^\s*transfer_log\s*=' "$INSTALL_DIR/config.ini" 2>/dev/null | head -1 | awk -F= '{print $2}' | sed 's/#.*//' | tr -d ' \t\r\n')
FTP_LOG_DIR=$(dirname "$_raw" 2>/dev/null || true)
FTP_LOG_DIR="${FTP_LOG_DIR:-/usr/service/logs/proftpd}"

_grant_log_access() {
    # 상위 디렉터리 순회(x) 권한
    _p="$FTP_LOG_DIR"
    while [ "$_p" != "/" ]; do
        _p=$(dirname "$_p")
        [ "$_p" != "/" ] && setfacl -m "u:${DAEMON_USER}:x" "$_p" 2>/dev/null || true
    done
    setfacl -m "u:${DAEMON_USER}:rx" "$FTP_LOG_DIR"
    # 파일에만 r 적용 (-R은 디렉터리 자체도 덮어써서 x를 제거하므로 사용 불가)
    find "$FTP_LOG_DIR" -maxdepth 1 -type f -exec setfacl -m "u:${DAEMON_USER}:r" {} +
    setfacl -d -m "u:${DAEMON_USER}:r" "$FTP_LOG_DIR"
}

if [ -d "$FTP_LOG_DIR" ]; then
    if command -v setfacl &>/dev/null; then
        _grant_log_access
        # 실제 접근 가능한지 검증
        if su -s /bin/bash "$DAEMON_USER" -c "ls '$FTP_LOG_DIR'" &>/dev/null; then
            echo "[INFO] FTP 로그 ACL 설정 완료 (접근 확인됨): $FTP_LOG_DIR"
        else
            echo "[WARN] ACL 설정 후에도 접근 실패: $FTP_LOG_DIR"
            echo "       파일시스템 마운트에 ACL 옵션이 없을 수 있습니다."
            echo "       확인: mount | grep \$(df '$FTP_LOG_DIR' | tail -1 | awk '{print \$1}')"
            echo "       대안: chmod o+x 로 other execute 권한 부여 또는 마운트 옵션에 acl 추가 후 재마운트"
        fi
    else
        echo "[WARN] setfacl 없음 — FTP 로그 읽기 권한 수동 설정 필요:"
        echo "       setfacl -m u:${DAEMON_USER}:x /usr/service /usr/service/logs"
        echo "       setfacl -m u:${DAEMON_USER}:rx $FTP_LOG_DIR"
        echo "       setfacl -R -m u:${DAEMON_USER}:r $FTP_LOG_DIR"
    fi
else
    echo "[WARN] FTP 로그 디렉터리를 찾을 수 없습니다: $FTP_LOG_DIR"
    echo "       config.ini의 transfer_log 경로에 $DAEMON_USER 읽기 권한을 직접 부여하세요."
fi

# ── systemd 서비스 등록 ──────────────────────────────────────────
cp soltrace-daemon.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl start "$SERVICE_NAME" || echo "[WARN] 서비스 시작 실패 — config.ini 설정 후 수동 시작하세요: systemctl start $SERVICE_NAME"
echo "[INFO] 서비스 등록 완료"

echo ""
echo "======================================"
echo " SolTrace 데몬 설치 완료"
echo "======================================"
echo " 실행 계정:  $DAEMON_USER (비root)"
echo " 설정 파일:  $INSTALL_DIR/config.ini"
echo " 상태 저장:  $STATE_DIR"
echo " 로그 파일:  $LOG_FILE"
echo ""
echo " 1. config.ini 편집 후 서비스 재시작:"
echo "    vi $INSTALL_DIR/config.ini"
echo "    systemctl restart $SERVICE_NAME"
echo ""
echo " 2. 로그 확인:"
echo "    journalctl -u $SERVICE_NAME -f"
echo "======================================"
