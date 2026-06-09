#!/usr/bin/env bash
# SolTrace FTP Daemon 설치 스크립트 (FTP 서버 - Amazon Linux 2023 / CentOS / RHEL)
# 실행: sudo bash install_daemon.sh

set -euo pipefail
INSTALL_DIR="/opt/soltrace-daemon"
SERVICE_FILE="/etc/systemd/system/soltrace-daemon.service"
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== SolTrace FTP Daemon 설치 ==="

# Python 3.9+ 확인
if ! command -v python3 &>/dev/null; then
    echo "[1/5] Python3 설치 중..."
    if command -v dnf &>/dev/null; then
        dnf install -y python3 python3-pip
    elif command -v yum &>/dev/null; then
        yum install -y python3 python3-pip
    fi
else
    echo "[1/5] Python3 이미 설치됨: $(python3 --version)"
fi

# 설치 디렉토리 생성
echo "[2/5] 파일 복사 중..."
mkdir -p "$INSTALL_DIR"
cp "$SCRIPT_DIR/ftp-daemon/soltrace_daemon.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/ftp-daemon/soltrace_bulk.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/ftp-daemon/requirements.txt" "$INSTALL_DIR/"
mkdir -p /var/lib/soltrace

# config.ini 생성 (없는 경우만)
if [ ! -f "$INSTALL_DIR/config.ini" ]; then
    cp "$SCRIPT_DIR/ftp-daemon/config.ini.example" "$INSTALL_DIR/config.ini"
    echo "  >> $INSTALL_DIR/config.ini 생성됨. was_url 등을 확인하세요."
fi

# 가상환경 + 패키지 설치
echo "[3/5] Python 패키지 설치 중..."
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install -q --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"

# 프로파일 로그 경로 확인
echo "[4/5] proftpd 로그 경로 확인 중..."
TRANSFER_LOG="/usr/service/logs/proftpd/TransferLog"
EXTENDED_LOG="/usr/service/logs/proftpd/ExtendedAllLog"
if [ ! -f "$TRANSFER_LOG" ]; then
    echo "  >> 주의: $TRANSFER_LOG 가 존재하지 않습니다."
    echo "     config.ini 의 transfer_log 를 실제 경로로 수정하세요."
fi

# systemd 서비스 등록
echo "[5/5] systemd 서비스 등록 중..."
cp "$SCRIPT_DIR/ftp-daemon/soltrace-daemon.service" "$SERVICE_FILE"
systemctl daemon-reload
systemctl enable soltrace-daemon
systemctl restart soltrace-daemon

echo ""
echo "=== 설치 완료 ==="
echo "서비스 상태: systemctl status soltrace-daemon"
echo "실시간 로그: journalctl -u soltrace-daemon -f"
echo "설정 파일:  $INSTALL_DIR/config.ini"
echo ""
echo "=== 과거 데이터 일괄 전송 ==="
echo "$INSTALL_DIR/venv/bin/python3 $INSTALL_DIR/soltrace_bulk.py --help"
