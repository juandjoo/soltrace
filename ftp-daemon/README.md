# SolTrace FTP Daemon

proftpd 로그를 실시간으로 파싱하여 SolTrace WAS로 전송하는 데몬.

---

## 목차

- [요구사항](#요구사항)
- [설치 — Rocky Linux 8 / 9](#설치--rocky-linux-8--9)
- [설치 — CentOS 7 (EOL)](#설치--centos-7-eol)
- [설정](#설정)
- [서비스 등록 및 운영](#서비스-등록-및-운영)
- [과거 로그 일괄 전송](#과거-로그-일괄-전송)
- [웹 UI에서 데몬 업데이트](#웹-ui에서-데몬-업데이트)
- [문제 해결](#문제-해결)

---

## 요구사항

| 항목 | 최소 |
|------|------|
| Python | 3.8 이상 |
| proftpd | TransferLog + ExtendedLog 설정 필요 |
| 네트워크 | WAS HTTPS 및 GitHub raw URL 접근 가능 |
| 권한 | root 또는 proftpd 로그 파일 읽기 권한 |

---

## 설치 — Rocky Linux 8 / 9

Python 3.8이 기본 제공되므로 별도 설정 없이 설치 가능.

### 1. 스크립트로 자동 설치 (권장)

```bash
curl -fsSL https://raw.githubusercontent.com/juandjoo/soltrace/main/ftp-daemon/install.sh | sudo bash
```

### 2. 수동 설치

```bash
# 빌드 도구 설치
sudo dnf install -y python3 python3-pip gcc python3-devel git

# 설치 디렉터리 생성 및 파일 복사
sudo mkdir -p /opt/soltrace-daemon
sudo git clone https://github.com/juandjoo/soltrace.git /tmp/soltrace
sudo cp /tmp/soltrace/ftp-daemon/{soltrace_daemon.py,soltrace_bulk.py,requirements.txt,soltrace-daemon.service,config.ini.example} \
        /opt/soltrace-daemon/
sudo cp /opt/soltrace-daemon/config.ini.example /opt/soltrace-daemon/config.ini
rm -rf /tmp/soltrace

# 가상환경 생성 및 패키지 설치
cd /opt/soltrace-daemon
sudo python3 -m venv venv
sudo venv/bin/pip install --upgrade pip
sudo venv/bin/pip install -r requirements.txt
```

---

## 설치 — CentOS 7 (EOL)

> **주의**: CentOS 7은 2024년 6월 EOL. 공식 미러가 중단되어 yum 저장소 수동 수정이 필요.

### 1. yum 저장소를 vault.centos.org로 교체

```bash
# base/updates/extras를 아카이브 서버로 변경
sudo tee /etc/yum.repos.d/CentOS-Base.repo > /dev/null << 'EOF'
[base]
name=CentOS-7 - Base
baseurl=https://vault.centos.org/centos/7/os/$basearch/
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-CentOS-7
enabled=1

[updates]
name=CentOS-7 - Updates
baseurl=https://vault.centos.org/centos/7/updates/$basearch/
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-CentOS-7
enabled=1

[extras]
name=CentOS-7 - Extras
baseurl=https://vault.centos.org/centos/7/extras/$basearch/
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-CentOS-7
enabled=1
EOF

sudo yum clean all
sudo yum makecache
```

### 2. Python 3.8 설치 (SCL)

CentOS 7 기본 Python은 3.6이며 `requests >= 2.28`이 **Python 3.8 이상**을 요구하므로 SCL을 통해 3.8을 설치.

```bash
# SCL 설치 (extras repo 필요)
sudo yum install -y centos-release-scl

# SCL repo도 vault로 교체
sudo tee /etc/yum.repos.d/CentOS-SCLo-scl-rh.repo > /dev/null << 'EOF'
[centos-sclo-rh]
name=CentOS-7 - SCLo rh
baseurl=https://vault.centos.org/centos/7/sclo/$basearch/rh/
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-CentOS-SIG-SCLo
enabled=1

[centos-sclo-sclo]
name=CentOS-7 - SCLo sclo
baseurl=https://vault.centos.org/centos/7/sclo/$basearch/sclo/
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-CentOS-SIG-SCLo
enabled=1
EOF

sudo yum install -y rh-python38 gcc python3-devel
```

### 3. 스크립트로 자동 설치

```bash
curl -fsSL https://raw.githubusercontent.com/juandjoo/soltrace/main/ftp-daemon/install.sh | sudo bash
```

`install.sh`이 SCL Python 3.8 경로(`/opt/rh/rh-python38/root/usr/bin/python3.8`)를 자동 감지하여 venv를 생성.

### 4. urllib3 다운그레이드 (OpenSSL 1.0.2k 대응)

CentOS 7의 OpenSSL은 1.0.2k로, urllib3 v2와 호환되지 않음. pip 설치 후 반드시 확인:

```bash
cd /opt/soltrace-daemon
sudo venv/bin/pip install "urllib3<2"
```

> pip이 requirements.txt의 `urllib3<2` 제약을 무시하고 기존 v2를 유지하는 경우가 있으므로 명시적으로 실행.

확인:

```bash
sudo venv/bin/pip show urllib3 | grep Version
# Version: 1.26.x 여야 함
```

---

## 설정

```bash
sudo vi /opt/soltrace-daemon/config.ini
```

### 필수 항목

```ini
[daemon]
# SolTrace WAS 주소 (HTTPS 권장)
was_url = https://soltrace.mbone.net

# proftpd 로그 경로 (실제 경로 확인 필요)
transfer_log = /var/log/proftpd/xferlog
extended_log  = /var/log/proftpd/extended.log
```

### proftpd 로그 경로 확인

```bash
grep -i "TransferLog\|ExtendedLog" /etc/proftpd.conf /etc/proftpd/*.conf 2>/dev/null
```

### 주요 옵션

| 항목 | 기본값 | 설명 |
|------|--------|------|
| `was_url` | — | WAS 서버 주소 |
| `update_url` | GitHub raw URL | 웹 UI 업데이트 시 파일 다운로드 경로 |
| `transfer_log` | — | xferlog 경로 (업로드/다운로드/삭제) |
| `extended_log` | — | ExtendedLog 경로 (로그인/로그아웃/이름변경) |
| `batch_size` | `200` | 1회 전송 최대 건수 |
| `poll_interval` | `10` | 로그 파일 폴링 주기(초) |
| `heartbeat_interval` | `60` | WAS 생존 신호 주기(초) |
| `ssl_verify` | `true` | `false` = 자체 서명 인증서 허용 |
| `log_level` | `INFO` | `DEBUG` / `INFO` / `WARNING` |

---

## 서비스 등록 및 운영

```bash
# systemd 등록
sudo cp /opt/soltrace-daemon/soltrace-daemon.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable soltrace-daemon
sudo systemctl start soltrace-daemon

# 상태 확인
sudo systemctl status soltrace-daemon

# 실시간 로그
sudo journalctl -u soltrace-daemon -f

# 파일 로그
tail -f /var/log/soltrace-daemon.log
```

WAS 웹 UI에서 **설정 → 장비관리** 접속 후 해당 장비를 **확인** 처리해야 로그 전송이 활성화됨.

---

## 과거 로그 일괄 전송

데몬 설치 이전의 기존 로그를 소급 전송할 때 사용.

```bash
# dry-run (실제 전송 없이 파싱 결과만 확인)
sudo /opt/soltrace-daemon/venv/bin/python3 /opt/soltrace-daemon/soltrace_bulk.py \
  --date-from 2026-06-01 --dry-run

# 실제 전송
sudo /opt/soltrace-daemon/venv/bin/python3 /opt/soltrace-daemon/soltrace_bulk.py \
  --date-from 2026-06-01 --date-to 2026-06-17
```

---

## 웹 UI에서 데몬 업데이트

1. SolTrace 웹 UI → **설정 → 장비관리**
2. 대상 장비의 **↻** (업데이트) 버튼 클릭
3. 다음 하트비트(최대 60초)에서 데몬이 자동으로:
   - GitHub에서 최신 파일 다운로드
   - `pip install -r requirements.txt` 실행
   - `systemctl restart soltrace-daemon`으로 재시작

> `config.ini`의 `update_url`이 GitHub에 접근 가능해야 함. 내부망 환경은 별도 미러 URL로 변경.

---

## 문제 해결

### `ImportError: urllib3 v2 only supports OpenSSL 1.1.1+`

CentOS 7의 OpenSSL(1.0.2k)이 urllib3 v2와 호환되지 않음.

```bash
sudo /opt/soltrace-daemon/venv/bin/pip install "urllib3<2"
sudo systemctl restart soltrace-daemon
```

### `Cannot open: https://repo.ius.io/...` / yum 저장소 오류

CentOS 7 EOL로 인한 미러 중단. [위의 vault 교체 절차](#1-yum-저장소를-vaultcentosorgo로-교체) 참고.

### `404 Not Found` — WAS 등록 실패

`config.ini`의 `was_url`이 `http://`인 경우 nginx가 HTTPS로 리다이렉트하면서 POST 본문이 소실될 수 있음.

```ini
was_url = https://soltrace.mbone.net   # http → https 로 변경
```

### 로그 시간이 9시간 빠르게 표시됨

데몬이 로컬 시간(KST)을 UTC로 잘못 태깅하는 버그. 최신 버전(2026-06-17 이후)에서 수정됨.

```bash
# 최신 버전으로 업데이트
curl -fsSL https://raw.githubusercontent.com/juandjoo/soltrace/main/ftp-daemon/soltrace_daemon.py \
  -o /opt/soltrace-daemon/soltrace_daemon.py
sudo systemctl restart soltrace-daemon
```

### SELinux로 인한 로그 파일 접근 오류

```bash
getenforce   # Enforcing이면 문제 가능

# 임시 비활성화
sudo setenforce 0

# 영구 비활성화 (/etc/selinux/config → SELINUX=disabled 후 reboot)
```
