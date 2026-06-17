#!/usr/bin/env bash
# SolTrace FTP Daemon 설치 스크립트
# 사용법: curl -fsSL <raw_url>/install.sh | sudo bash
#        또는: sudo bash install.sh
set -euo pipefail

INSTALL_DIR=/opt/soltrace-daemon
REPO_RAW="https://raw.githubusercontent.com/juandjoo/soltrace/main/ftp-daemon"
SERVICE_NAME=soltrace-daemon

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

# ── 디렉터리 생성 ────────────────────────────────────────────────
mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

# ── 파일 다운로드 ────────────────────────────────────────────────
echo "[INFO] 파일 다운로드 중..."
for f in soltrace_daemon.py requirements.txt soltrace-daemon.service config.ini.example; do
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

# ── systemd 서비스 등록 ──────────────────────────────────────────
cp soltrace-daemon.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
echo "[INFO] 서비스 등록 완료 (자동 시작 활성화)"

echo ""
echo "======================================"
echo " SolTrace 데몬 설치 완료"
echo "======================================"
echo " 설정 파일: $INSTALL_DIR/config.ini"
echo ""
echo " 1. config.ini 편집:"
echo "    vi $INSTALL_DIR/config.ini"
echo ""
echo " 2. 서비스 시작:"
echo "    systemctl start $SERVICE_NAME"
echo ""
echo " 3. 로그 확인:"
echo "    journalctl -u $SERVICE_NAME -f"
echo "======================================"
