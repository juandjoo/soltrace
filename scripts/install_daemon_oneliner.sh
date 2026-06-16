#!/usr/bin/env bash
# SolTrace FTP Daemon 원클릭 설치 (Rocky Linux 8 / CentOS / RHEL)
# git clone 없이 GitHub에서 직접 다운로드 후 설치
#
# 실행:
#   curl -fsSL https://raw.githubusercontent.com/juandjoo/soltrace/main/scripts/install_daemon_oneliner.sh | sudo bash

set -euo pipefail

GITHUB_RAW="https://raw.githubusercontent.com/juandjoo/soltrace/main"
INSTALL_DIR="/opt/soltrace-daemon"
SERVICE_FILE="/etc/systemd/system/soltrace-daemon.service"

echo "=== SolTrace FTP Daemon 설치 ==="

# ── [1/5] Python 3.11 ───────────────────────────────────────────────────────
echo "[1/5] Python 3.11 설치 중..."
if command -v python3.11 &>/dev/null; then
    echo "  >> 이미 설치됨: $(python3.11 --version)"
else
    if command -v dnf &>/dev/null; then
        dnf install -y epel-release 2>/dev/null || true
        dnf install -y python3.11 python3.11-devel
    elif command -v yum &>/dev/null; then
        yum install -y epel-release 2>/dev/null || true
        yum install -y python3.11 python3.11-devel
    else
        echo "ERROR: dnf/yum 없음 — Python 3.11을 수동으로 설치하세요."
        exit 1
    fi
    echo "  >> $(python3.11 --version)"
fi

# ── [2/5] 파일 다운로드 ──────────────────────────────────────────────────────
echo "[2/5] 파일 다운로드 중 (GitHub)..."
mkdir -p "$INSTALL_DIR" /var/lib/soltrace

_dl() {
    local src="$1" dst="$2"
    if command -v curl &>/dev/null; then
        curl -fsSL "$src" -o "$dst"
    else
        wget -q "$src" -O "$dst"
    fi
}

_dl "$GITHUB_RAW/ftp-daemon/soltrace_daemon.py"    "$INSTALL_DIR/soltrace_daemon.py"
_dl "$GITHUB_RAW/ftp-daemon/soltrace_bulk.py"      "$INSTALL_DIR/soltrace_bulk.py"
_dl "$GITHUB_RAW/ftp-daemon/requirements.txt"      "$INSTALL_DIR/requirements.txt"
_dl "$GITHUB_RAW/ftp-daemon/soltrace-daemon.service" /tmp/soltrace-daemon.service

if [ ! -f "$INSTALL_DIR/config.ini" ]; then
    _dl "$GITHUB_RAW/ftp-daemon/config.ini.example" "$INSTALL_DIR/config.ini"
    echo "  >> $INSTALL_DIR/config.ini 생성됨 — was_url을 실제 WAS 주소로 수정하세요."
fi

# ── [3/5] 가상환경 + 패키지 ──────────────────────────────────────────────────
echo "[3/5] Python 패키지 설치 중..."
python3.11 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install -q --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"

# ── [4/5] proftpd 로그 경로 확인 ────────────────────────────────────────────
echo "[4/5] proftpd 로그 경로 확인 중..."
for LOG_PATH in "/usr/service/logs/proftpd/TransferLog" "/usr/service/logs/proftpd/ExtendedAllLog"; do
    if [ ! -f "$LOG_PATH" ]; then
        echo "  >> 주의: $LOG_PATH 가 없습니다. config.ini 경로를 확인하세요."
    fi
done

# ── [5/5] systemd 서비스 등록 ────────────────────────────────────────────────
echo "[5/5] systemd 서비스 등록 중..."
cp /tmp/soltrace-daemon.service "$SERVICE_FILE"
systemctl daemon-reload
systemctl enable soltrace-daemon
systemctl restart soltrace-daemon

echo ""
echo "=== 설치 완료 ==="
echo "서비스 상태: systemctl status soltrace-daemon"
echo "실시간 로그: journalctl -u soltrace-daemon -f"
echo "설정 파일:   $INSTALL_DIR/config.ini"
echo ""
echo "  was_url을 실제 WAS 주소로 수정 후 재시작:"
echo "  vi $INSTALL_DIR/config.ini"
echo "  systemctl restart soltrace-daemon"
echo ""
echo "=== 과거 데이터 일괄 전송 ==="
echo "  $INSTALL_DIR/venv/bin/python3 $INSTALL_DIR/soltrace_bulk.py --help"
