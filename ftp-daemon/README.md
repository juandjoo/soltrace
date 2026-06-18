# SolTrace FTP Daemon

proftpd 로그를 실시간으로 파싱하여 SolTrace WAS로 전송하는 데몬.

---

## 목차

- [요구사항](#요구사항)
- [설치 — Rocky Linux 8 / 9](#설치--rocky-linux-8--9)
- [설치 — CentOS 7 (EOL)](#설치--centos-7-eol)
- [설정](#설정)
- [서비스 등록 및 운영](#서비스-등록-및-운영)
- [동작 방식](#동작-방식)
- [과거 로그 일괄 전송](#과거-로그-일괄-전송)
- [웹 UI에서 데몬 업데이트](#웹-ui에서-데몬-업데이트)
- [문제 해결](#문제-해결)

---

## 요구사항

| 항목 | 최소 |
|------|------|
| Python | 3.6 이상 (CentOS 7 기본 포함) |
| proftpd | TransferLog + ExtendedLog 설정 필요 |
| 네트워크 | WAS HTTPS 접근 가능 (자가 업데이트 시 GitHub raw URL 접근 필요) |
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
sudo dnf install -y python3 python3-pip gcc python3-devel

sudo mkdir -p /opt/soltrace-daemon
sudo curl -fsSL https://raw.githubusercontent.com/juandjoo/soltrace/main/ftp-daemon/soltrace_daemon.py \
    -o /opt/soltrace-daemon/soltrace_daemon.py
sudo curl -fsSL https://raw.githubusercontent.com/juandjoo/soltrace/main/ftp-daemon/soltrace_bulk.py \
    -o /opt/soltrace-daemon/soltrace_bulk.py
sudo curl -fsSL https://raw.githubusercontent.com/juandjoo/soltrace/main/ftp-daemon/requirements.txt \
    -o /opt/soltrace-daemon/requirements.txt
sudo curl -fsSL https://raw.githubusercontent.com/juandjoo/soltrace/main/ftp-daemon/config.ini.example \
    -o /opt/soltrace-daemon/config.ini

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

CentOS 7 기본 Python은 3.6이며 `requests >= 2.28`이 Python 3.8 이상을 요구하므로 SCL을 통해 3.8을 설치.

```bash
sudo yum install -y centos-release-scl

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

CentOS 7의 OpenSSL은 1.0.2k로, urllib3 v2와 호환되지 않음.

```bash
sudo /opt/soltrace-daemon/venv/bin/pip install "urllib3<2"
sudo systemctl restart soltrace-daemon
```

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
was_url = https://soltrace.example.com

# proftpd 로그 경로 (실제 경로 확인 필요)
transfer_log = /var/log/proftpd/xferlog
extended_log  = /var/log/proftpd/extended.log
```

### proftpd 로그 경로 확인

```bash
grep -i "TransferLog\|ExtendedLog" /etc/proftpd.conf /etc/proftpd/*.conf 2>/dev/null
```

### 전체 옵션

| 항목 | 기본값 | 설명 |
|------|--------|------|
| `was_url` | — | WAS 서버 주소 |
| `transfer_log` | — | xferlog 경로 (업로드/다운로드/삭제) |
| `extended_log` | — | ExtendedLog 경로 (로그인/로그아웃/이름변경/CWD) |
| `batch_size` | `200` | 1회 전송 최대 건수 |
| `poll_interval` | `10` | 로그 파일 폴링 주기 (초) |
| `heartbeat_interval` | `60` | WAS 생존 신호 주기 (초) |
| `max_buffer_lines` | `50000` | 전송 실패 시 로컬 버퍼 최대 줄 수 |
| `buffer_file` | `/var/lib/soltrace/buffer.jsonl` | 로컬 버퍼 파일 경로 |
| `state_dir` | `/var/lib/soltrace` | 타일러 위치 파일 저장 디렉터리 |
| `update_url` | GitHub raw URL | 자가 업데이트 시 파일 다운로드 기준 경로 |
| `ssl_verify` | `true` | `false` = 자체 서명 인증서 허용 |
| `log_level` | `INFO` | `DEBUG` / `INFO` / `WARNING` |
| `skip_login_logout` | `false` | `true` = login/logout 이벤트 전송 제외 |

---

## 서비스 등록 및 운영

```bash
# systemd 등록 (자동 설치 시 이미 완료됨)
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

WAS 웹 UI → **장비 관리** → 해당 장비 **확인** 처리 후 로그 전송이 활성화된다.

---

## 동작 방식

### 로그 파싱

| 파일 | 파싱 항목 |
|------|-----------|
| `TransferLog` | 업로드(i), 다운로드(o), 삭제(d) — 완료 여부(completion) 포함 |
| `ExtendedAllLog` | 로그인 성공(PASS 230), 로그인 실패(PASS 530, 식별된 계정 한정), 로그아웃(QUIT), 이름변경(RNTO 250), 폴더생성(MKD 257), 폴더삭제(RMD 250), 디렉토리 이동 실패(CWD 550) |

- 로그인 실패: username이 `-`(익명·미확인)인 경우는 스캔성 노이즈로 판단해 제외
- 이름변경: RNFR(원본 경로)과 RNTO(대상 경로)를 세션별로 매칭하여 `from_path -> to_path` 형태로 기록
- 중복 방지: `row_hash`(MD5, 8개 필드 기반) 기반 `ON CONFLICT DO NOTHING`

### WAS 장애 대응

전송 실패 시 데몬이 종료되지 않고 자동으로 복구된다.

| 상황 | 동작 |
|------|------|
| 전송 3회 실패 (네트워크 오류 / 502 등) | 항목을 로컬 버퍼에 저장, 타일러 위치 확정, 지수 백오프(30초→최대 5분) 후 재시도 |
| 시작 시 버퍼 존재 | 버퍼 재전송 시도, WAS 미응답이어도 계속 실행 |
| WAS 복구 | 버퍼 자동 재전송 후 정상 운영 재개 |

### WAS의 장비 상태에 따른 처리

| WAS 응답 | 원인 | 데몬 동작 |
|----------|------|-----------|
| `403 Forbidden` | 장비 비활성화 | 버퍼 없이 타일러 롤백, 5분 대기 후 재시도. 재활성화 시 해당 시간대 로그 자동 재전송 |
| `401` / `404` | 장비 삭제 | 즉시 종료 (safe shutdown) |

---

## 과거 로그 일괄 전송

데몬 설치 이전 로그나 압축 아카이브를 소급 전송할 때 사용.

```bash
# 압축 일별 로그만 복구 (glob 패턴, TransferLog 제외)
sudo /opt/soltrace-daemon/venv/bin/python3 /opt/soltrace-daemon/soltrace_bulk.py \
    --no-transfer \
    --extended-log "/usr/service/logs/proftpd/ExtendedAllLog.*.gz"

# 특정 기간
sudo /opt/soltrace-daemon/venv/bin/python3 /opt/soltrace-daemon/soltrace_bulk.py \
    --date-from 2026-05-01 --date-to 2026-05-31 \
    --no-transfer \
    --extended-log "/usr/service/logs/proftpd/ExtendedAllLog.*.gz"

# 파싱 테스트 (전송 없음)
sudo /opt/soltrace-daemon/venv/bin/python3 /opt/soltrace-daemon/soltrace_bulk.py \
    --dry-run --no-transfer \
    --extended-log "/usr/service/logs/proftpd/ExtendedAllLog.*.gz"
```

| 옵션 | 설명 |
|------|------|
| `--transfer-log PATH` | TransferLog 경로 또는 glob 패턴 |
| `--extended-log PATH` | ExtendedAllLog 경로 또는 glob 패턴 |
| `--date-from YYYY-MM-DD` | 시작 날짜 (포함) |
| `--date-to YYYY-MM-DD` | 종료 날짜 (포함) |
| `--batch-size N` | 배치 크기 (기본: 500) |
| `--dry-run` | 전송 없이 파싱 결과만 확인 |
| `--no-transfer` | TransferLog 무시 |
| `--no-extended` | ExtendedAllLog 무시 |

진행 상황은 50,000줄마다 로그에 출력된다.

---

## 웹 UI에서 데몬 업데이트

1. SolTrace 웹 UI → **장비 관리**
2. 대상 장비의 **↻** (업데이트) 버튼 클릭
3. 다음 하트비트(최대 60초)에서 데몬이 자동으로:
   - GitHub에서 최신 파일 다운로드 (`update_url` 기준)
   - `pip install -r requirements.txt` 실행
   - `systemctl restart soltrace-daemon`으로 재시작

> 내부망 환경은 `config.ini`의 `update_url`을 내부 미러 URL로 변경한다.

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
was_url = https://soltrace.example.com   # http → https 로 변경
```

### WAS 점검 후 데몬 상태가 "저하"로 표시됨

WAS 점검 중 전송 실패가 발생해 `daemon_status=degraded`로 기록된 경우.  
WAS 복구 후 다음 하트비트(최대 60초)에서 자동으로 `running`으로 회복된다.  
120초 이상 하트비트가 없으면 웹 UI에서 "미확인"으로 표시된다.

### 데몬이 WAS 점검 후 자동으로 재시작되지 않음

과거 버전(2026-06-18 이전)의 데몬은 전송 3회 실패 시 종료(`safe_shutdown`)되었음.  
최신 버전은 종료 없이 버퍼 저장 후 자동 재시도한다. 설치 스크립트로 업데이트:

```bash
curl -fsSL https://raw.githubusercontent.com/juandjoo/soltrace/main/ftp-daemon/install.sh | sudo bash
```

### 로그 시간이 9시간 빠르게 표시됨

데몬이 로컬 시간(KST)을 UTC로 잘못 태깅하는 버그. 2026-06-17 이후 버전에서 수정됨.  
최신 버전으로 업데이트하면 해결된다.

### SELinux로 인한 로그 파일 접근 오류

```bash
getenforce   # Enforcing이면 문제 가능

# 임시 비활성화
sudo setenforce 0

# 영구 비활성화 (/etc/selinux/config → SELINUX=disabled 후 reboot)
```
