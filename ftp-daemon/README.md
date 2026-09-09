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

> **데몬 코드는 Python 3.6 호환을 지킨다.** 운영 장비에 CentOS 7(3.6)이 남아 있다.
> `subprocess.run(capture_output=..., text=...)`, 워러스(`:=`), `list[str]` 같은 3.7+ 문법을
> 쓰면 그 장비에서만 터진다. 실제로 1.1.1~1.1.3 이 `capture_output` 때문에 자가 업데이트
> 재시작이 통째로 실패했다. 대신 `stdout=subprocess.PIPE, universal_newlines=True` 를 쓴다.
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

설치 중 **WAS 서버 주소**를 입력받아 `config.ini` 의 `was_url` 에 기록한다.

- 엔터만 누르면 기존 `config.ini` 값(재설치 시) 또는 기본값을 유지한다.
- `curl | sudo bash` 로 실행해도 `/dev/tty` 에서 직접 입력받으므로 프롬프트가 정상 동작한다.
- 비대화형 설치(터미널 없음)는 환경변수로 지정한다. 미지정 시 기본값이 쓰이고 경고가 출력된다.

```bash
curl -fsSL .../install.sh | sudo SOLTRACE_WAS_URL=https://soltrace.example.com bash
```

`http://` / `https://` 로 시작하지 않으면 설치가 중단되고, 입력한 주소에 연결되지 않으면 경고만 출력한 뒤 설치는 계속된다.

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
WAS 주소 입력 프롬프트는 Rocky 설치와 동일하다([1. 스크립트로 자동 설치](#1-스크립트로-자동-설치-권장) 참고).

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
| `was_url` | — | WAS 서버 주소 (설치 시 입력 / `SOLTRACE_WAS_URL`) |
| `transfer_log` | — | xferlog 경로 (업로드/다운로드/삭제) |
| `extended_log` | — | ExtendedLog 경로 (로그인/로그아웃/이름변경/CWD/전송 거부) |
| `batch_size` | `200` | 1회 전송 최대 건수 |
| `poll_interval` | `10` | 로그 파일 폴링 주기 (초) |
| `heartbeat_interval` | `60` | WAS 생존 신호 주기 (초) |
| `max_buffer_lines` | `50000` | 전송 실패 시 로컬 버퍼 최대 줄 수 |
| `buffer_file` | `/var/lib/soltrace/buffer.jsonl` | 로컬 버퍼 파일 경로 |
| `state_dir` | `/var/lib/soltrace` | 타일러 위치 파일 저장 디렉터리 |
| `update_url` | GitHub raw URL | 자가 업데이트 시 파일 다운로드 기준 경로 |
| `ssl_verify` | `true` | `false` = 자체 서명 인증서 허용 |
| `log_level` | `INFO` | `DEBUG` / `INFO` / `WARNING` |
| `log_file` | `/var/log/soltrace-daemon/daemon.log` | 데몬 로그 파일 경로 |
| `log_max_mb` | `10` | 로그 파일 1개 최대 크기(MB), `0` = 회전 안 함 |
| `log_backup_count` | `5` | 보관할 회전 파일 개수 (최대 사용량 = `log_max_mb` × 6) |
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
tail -f /var/log/soltrace-daemon/daemon.log
```

파일 로그는 데몬이 직접 회전한다(`RotatingFileHandler`) — 10MB 를 넘으면 `daemon.log.1` ~ `.5` 로
밀려나고 오래된 것부터 삭제되므로 **logrotate 설정이 필요 없다**. 최대 사용량은 기본 60MB.
크기·개수는 `config.ini` 의 `log_max_mb` / `log_backup_count` 로 조정한다.
회전은 파일이 아니라 디렉터리에 쓰기 권한이 필요하므로 로그는 데몬 계정 소유의
`/var/log/soltrace-daemon/` 에 둔다(`install.sh` 및 서비스의 `LogsDirectory=` 가 생성).
구버전 경로(`/var/log/soltrace-daemon.log`)를 쓰는 설치는 `install.sh` 재실행 시 자동 이전되며,
자가 업데이트만 한 경우에는 회전 가능한 `state_dir` 로 자동 대체하고 경고를 남긴다.

WAS 웹 UI → **장비 관리** → 해당 장비 **확인** 처리 후 로그 전송이 활성화된다.

---

## 동작 방식

### 로그 파싱

| 파일 | 파싱 항목 |
|------|-----------|
| `TransferLog` | 업로드(i), 다운로드(o), 삭제(d) — 완료 여부(completion) 포함 |
| `ExtendedAllLog` | 로그인 성공(PASS 230), 로그인 실패(PASS 530 — 계정은 같은 세션의 `USER` 명령에서 가져온다), 로그아웃(QUIT), 이름변경(RNTO 250), 폴더생성(MKD 257), 폴더삭제(RMD 250), 디렉토리 이동 실패(CWD 550), 전송 거부(RETR·STOR 4xx·5xx), 클라이언트 오류(`client_error`) |

- 로그인 실패: proftpd 는 인증이 끝나기 전이라 PASS 실패 행의 username 자리에 `-` 만 남긴다.
  같은 세션(pid)의 `USER` 명령 인자를 기억해 두었다가 그 계정으로 기록한다(비밀번호 오류 포함).
  `USER` 조차 없어 계정을 알 수 없는 건은 스캔성 노이즈로 판단해 제외
- 전송 거부: **데이터 전송이 시작되기 전에 거절된** RETR/STOR. 스토리지 I/O 오류(451), 권한
  없음(550), 용량 부족(452), 데이터 연결 실패(425) 등이 여기 해당하며, 전송이 시작되지 않았으므로
  `TransferLog` 에는 행 자체가 남지 않는다. `download`/`upload` + `status=fail` 로 기록되어
  전송 실패율 지표에 그대로 반영된다. 아래 세 가지는 제외한다.
  - **426**(전송 중 연결 끊김) — 전송이 시작된 뒤라 `TransferLog` 에 incomplete 로 이미 남는다(이중 집계 방지)
  - username이 `-` 인 건 — 로그인 실패와 같은 기준(스캔성 노이즈)
  - RETR 의 `No such file or directory` — 없는 파일 조회는 클라이언트 탐색 노이즈.
    STOR 의 같은 오류는 상위 디렉토리가 없다는 뜻이라 진짜 업로드 실패이므로 기록한다

- 클라이언트가 **잘못 보낸 요청**은 `client_error` 로 따로 센다(1.1.6+). 서버·스토리지 상태와
  무관하게 그 요청으로는 성공할 수 없는 것들이라, 전송 실패율에 섞이면 장비를 의심하며 쫓게 된다.
  `cwd_fail` 과 같은 취지로 **로그에서는 보이되 지표·알림에는 들어가지 않는다.**
  - 파일명 없는 전송 — `STOR /vod//` 처럼 디렉터리와 파일명을 이어 붙이다 파일명이 비어 나간 요청
    (정규화 후 경로가 `/` 로 끝난다). 디렉터리에는 저장할 수 없으니 늘 실패한다
  - 명령 문법·인자 오류 — 500 / 501 / 502 / 504

  경로를 해석하기도 전에 실패하면(데이터 연결 실패 425 등) proftpd 가 경로 필드에 `-` 만
  남긴다. 그런 행은 화면에서 **무엇이** 실패했는지 알 수 없으므로, 클라이언트가 보낸 명령
  문자열(`STOR /up/a.mp4`)에서 인자를 꺼내 채운다(1.1.3+). 이때 채워지는 경로는 클라이언트
  기준(chroot 상대)이라, 해석에 성공한 행의 절대경로와 표기가 다를 수 있다.
- 이름변경: RNFR(원본 경로)과 RNTO(대상 경로)를 세션별로 매칭하여 `from_path -> to_path` 형태로 기록
- 경로: 확장로그의 경로 자리는 따옴표로 감싸여 오므로 **공백이 든 파일명도 통째로** 읽는다
  (1.1.5 이전에는 `\S+` 로만 받아 공백이 있으면 그 줄 전체가 매치되지 않고 조용히 버려졌다).
  겹친 슬래시(`/vod//a.mp4`)는 하나로 줄여 xferlog 쪽 경로와 같은 줄로 집계되게 한다.
  파일명 없이 `/vod//` 로 오는 요청은 `/vod/` 로 남아 '파일명이 비었다'는 것이 그대로 보인다
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

어떤 방식으로 눌러도 **세우는 플래그는 하나**(`Device.update_requested`)라 동작과 적용 시점이
다르지 않다. 서버 쪽 구현도 한 모듈(`was/app/daemon_update.py`)을 함께 쓴다.

| 방식 | 위치 |
|------|------|
| 장비 하나 | **장비 관리** → 대상 장비의 **↻** |
| 장비 여러 대 | **장비 관리** → 왼쪽 체크 → 상단 **선택 업데이트** |
| 그룹 하나 | **그룹 관리** → 그룹 행의 **↻** |
| 그룹 여러 개 | **그룹 관리** → 왼쪽 체크 → 상단 **선택 업데이트** |

소속 장비가 없는 그룹은 **↻** 가 비활성이다. 두 그룹에 겹쳐 속한 장비는 한 번만 요청된다.

어느 쪽이든 다음 하트비트(최대 60초)에서 데몬이 자동으로:
- GitHub에서 최신 파일 다운로드 (`update_url` 기준)
- 내려받은 `.py` 는 `ast.parse` 로 구문 검사 (손상·불완전 다운로드 차단)
- `pip install -r requirements.txt` 실행
- `systemctl restart soltrace-daemon`으로 재시작

> 내부망 환경은 `config.ini`의 `update_url`을 내부 미러 URL로 변경한다.

### 데몬 버전

버전은 `soltrace_daemon.py` 의 `DAEMON_VERSION` **한 곳**에만 적는다. 파싱·전송 동작이 바뀌면
올린다.

- 데몬은 이 값을 하트비트로 보고하고, 장비 관리 화면의 호스트명 아래에 그대로 보인다.
- WAS 는 **배포된 저장소의 같은 파일**(`<repo_dir>/ftp-daemon/soltrace_daemon.py`)에서 읽은
  값을 '최신'으로 삼아, 보고된 값과 다른 장비를 **구버전**(⚠︎)으로 표시한다. 데몬이 자가
  업데이트로 받아 가는 파일과 WAS 가 기준으로 삼는 파일이 같으므로, 배포만 하면 기준이 저절로
  맞는다. WAS 설정에 최신 버전을 따로 적어 두면 데몬을 올릴 때마다 두 곳을 맞춰야 하고
  한쪽만 고쳐지면 조용히 어긋난다.
- 판정은 **낮은 쪽만** 본다. '다르면 구버전' 으로 두면 WAS 배포가 데몬보다 뒤처졌을 때
  (장비가 먼저 자가 업데이트로 올라간 경우) 더 새 버전이 ⚠︎ 로 표시된다.
- 아직 한 번도 버전을 보고하지 않은 장비, 버전 형식을 알 수 없는 장비는 판정하지 않는다.
- 보고는 **등록(register)과 하트비트 양쪽**에서 한다. 등록에만 실으면 시작 시 WAS 가 잠깐
  안 떠 있었을 때 옛 버전이 굳어 버린다. 값이 바뀔 때만 전송되므로 평소 payload 는 늘지 않는다.

### 장비에 남는 VERSION 파일

데몬은 **시작할 때** 설치 디렉터리에 `VERSION` 을 쓴다(쓸 수 없으면 `state_dir`).
자가 업데이트 직후가 아니라 **다시 뜬 뒤에** 생긴다 — 이 파일이 가리키는 것은 디스크에 있는
파일의 버전이 아니라 지금 메모리에서 도는 버전이기 때문이다. 그래서 1.1.1 로 처음 올라갈 때는
파일이 아직 없고, 재시작이 한 번 일어난 뒤부터 보인다.

```
$ cat /opt/soltrace-daemon/VERSION
v1.1.1
started: 2026-09-07T13:52:04+09:00
```

시작 시각을 같이 적는 이유 — 자가 업데이트가 파일만 바꾸고 재시작에 실패하면 `soltrace_daemon.py`
의 버전과 실제로 도는 버전이 갈린다. 이 파일은 **시작할 때만** 쓰이므로, `soltrace_daemon.py` 의
`DAEMON_VERSION` 은 새것인데 `VERSION` 의 시작 시각이 옛날이면 재시작이 안 된 것이다.

| 버전 | 변경 |
|------|------|
| `1.1.6` | 클라이언트가 잘못 보낸 요청(파일명 없는 전송, 명령 문법 오류)을 `client_error` 로 분리 — 전송 실패율에서 빠진다 |
| `1.1.5` | 경로에 공백이 있으면 확장로그 줄이 통째로 유실되던 것 수정, 겹친 슬래시(`/vod//a.mp4`) 정규화 |
| `1.1.4` | 재시작이 Python 3.6 장비에서 터지던 것 수정(`capture_output` 제거). 재시작 중 어떤 예외가 나도 자기 종료 경로로 넘어간다 |
| `1.1.3` | 경로를 해석하지 못한 전송 실패도 명령 문자열에서 경로를 꺼내 기록 (업로드·다운로드 공통) |
| `1.1.2` | 비특권 계정에서도 자가 업데이트가 재시작되게 — systemctl 이 막히면 스스로 종료(exit 42)하고 systemd 가 다시 띄운다. 내려받은 파일의 버전도 로그에 남김 |
| `1.1.1` | 시작 시 `VERSION` 파일 기록, 하트비트에도 버전 보고, 자가 업데이트 재시작 결과를 로그에 남김 |
| `1.1.0` | 전송이 시작되기 전에 거부된 RETR/STOR 을 실패로 수집 |
| `1.0.0` | 최초 |

---

## 문제 해결

### 업데이트를 눌렀는데 화면의 데몬 버전이 그대로다

**1.1.2 미만에서 알려진 문제다.** 파일은 바뀌었는데 프로세스가 재시작되지 않은 것이다.

원인 — 데몬은 유닛이 `User=soltrace` 로 지정한 **비특권 프로세스**인데 자가 업데이트 코드가
`systemctl restart` 를 직접 호출했다. 폴리킷이 이를 막고, 호출이 `check=False` 라 실패가
어디에도 남지 않았다. 그래서 디스크는 새 코드, 도는 프로세스는 옛 코드인 상태가 조용히 이어졌다.

```bash
# 파일 버전(내려받은 것) / 실행 중 버전을 나눠서 본다
grep "^DAEMON_VERSION" /opt/soltrace-daemon/soltrace_daemon.py   # 파일 — 여기가 새것이면 다운로드는 성공
cat /opt/soltrace-daemon/VERSION                                  # 실제로 시작된 버전·시각 (1.1.1+)
grep "daemon starting" /var/log/soltrace-daemon/daemon.log | tail -3
```

파일은 새것인데 `daemon starting` 이 업데이트 시각보다 이전이면 재시작이 안 된 것이다.
**한 번만 수동으로 올리면 된다.**

```bash
sudo systemctl restart soltrace-daemon
```

1.1.2 부터는 권한이 필요 없다. `systemctl` 을 먼저 시도하고, 막히면 **스스로 종료(exit 42)**
해 유닛의 `Restart=on-failure` 가 `RestartSec=10` 뒤에 다시 띄운다. 어느 경로를 탔는지는
로그에 남는다(`Restart requested via systemctl` / `systemctl restart 거부됨 (rc=...)`).

> 1.1.2 자체를 적용하려면 그 한 번의 수동 재시작이 필요하다 — 재시작을 고치는 코드가
> 재시작되어야 도는 구조라 어쩔 수 없다. 그 뒤로는 자동이다.

> 유닛의 `StartLimitBurst=3` / `StartLimitIntervalSec=300` 때문에 5분 안에 세 번 넘게
> 재시작하면 systemd 가 더 띄우지 않는다. 업데이트 버튼을 연타하지 않는다.
> 막혔다면 `systemctl reset-failed soltrace-daemon && systemctl start soltrace-daemon`.

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
