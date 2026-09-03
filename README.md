# SolTrace - FTP Log Analyzer

proftpd 로그를 수집·분석하는 시스템.  
FTP 장비의 데몬이 로그를 파싱해 WAS API로 전송하고, 웹 대시보드에서 조회한다.

---

## 아키텍처

```
FTP 서버 (proftpd, Rocky Linux 8 / CentOS 7)
  └─ soltrace_daemon.py  ──(HTTPS API)──▶  WAS 서버 (Rocky Linux 8)
  └─ soltrace_bulk.py    ──(일괄전송)──▶    └─ Nginx (80/443, 레이트리밋, gzip)
                                             └─ FastAPI + Gunicorn (2 worker)
                                             └─ PostgreSQL 16
                                                  └─ ftp_logs (월별 파티셔닝)
                                                  └─ service_metrics (5분 롤업)
                                                  └─ service_alerts
```

| 컴포넌트 | 스펙 |
|---|---|
| WAS | FastAPI + Gunicorn 2 worker, systemd 서비스 |
| DB | PostgreSQL 16 (`shared_buffers=256MB`, `max_connections=50`), 월별 파티션 + GIN 인덱스 |
| Proxy | Nginx (레이트리밋, gzip), systemd 서비스 |
| 데몬 | Python 3.6+, 폴링 10초 간격, WAS 다운 시 로컬 버퍼 유지·자동 재시도 |

---

## 디렉토리 구조

```
soltrace/
├── nginx/
│   └── nginx.conf
├── postgres/
│   └── init.sql
├── was/
│   ├── requirements.txt
│   └── app/
│       ├── main.py               # lifespan: 파티션 생성, 마이그레이션
│       ├── config.py             # DB 기반 설정 (알림 채널, 임계값 등)
│       ├── database.py
│       ├── models.py             # Device, Group, FtpLog, ServiceMetrics, ServiceAlerts
│       ├── schemas.py
│       ├── deps.py               # JWT 인증 의존성, Principal(role), device_scope 격리
│       ├── security.py           # 비밀번호 해시(pbkdf2), 관리자 자격증명, IP/CIDR 허용 검사
│       ├── write_buffer.py       # 비동기 DB 쓰기 버퍼 (3초 flush, 최대 2000건)
│       ├── notifier.py           # 웹훅 / HMS 알림 발송
│       ├── service_monitor.py    # 5분 주기 롤업 + 이상 감지 + 알림
│       └── routers/
│           ├── auth.py           # 로그인 (JWT)
│           ├── ingest.py         # 데몬 수신 API (register / heartbeat / logs)
│           ├── devices.py        # 장비 관리 (확인 / 비활성화 / 삭제 / 업데이트)
│           ├── groups.py         # 그룹 관리 (telco / service / other)
│           ├── logs.py           # 로그 조회 + CSV + XLSX 내보내기 (기본 90일)
│           ├── dashboard.py      # 대시보드 / 서비스 건강도 / 사용자별 추이
│           ├── settings.py       # 알림 설정 / 임계값 / 음소거 / DB 저장소 현황
│           ├── telcos.py         # 통신사 관리
│           └── users.py          # 고객사 계정 관리 (admin 전용)
│       ├── tests/                # 단위 테스트 (인증·격리 로직, DB 불필요)
│       └── static/
│           ├── index.html        # SPA (Bootstrap 5 + Chart.js 4)
│           ├── css/app.css
│           └── js/
│               ├── utils.js      # esc() XSS 이스케이프, api() fetch 래퍼
│               ├── dashboard.js  # 대시보드 차트 + 서비스 건강도 (기본 7일)
│               ├── logs.js       # 로그 조회 (기본 90일, 드릴다운 필터, 총건수 병렬 집계)
│               ├── devices.js    # 장비 목록 / 상태 배지 (하트비트 120초 임계)
│               ├── groups.js     # 그룹 관리
│               ├── settings.js   # 알림 설정 (웹훅 / HMS), 고객 계정, DB 저장소
│               └── demo-data.js  # 샘플 페이지용 API 스텁 (운영 화면에서는 미로드)
├── ftp-daemon/
│   ├── soltrace_daemon.py        # 실시간 로그 감시 데몬
│   ├── soltrace_bulk.py          # 과거 데이터 일괄 전송 (glob 지원)
│   ├── install.sh                # 자동 설치 스크립트
│   ├── config.ini.example
│   ├── requirements.txt
│   └── soltrace-daemon.service
└── scripts/
    ├── deploy_rocky8.sh              # 로컬 → GitLab + GitHub push + WAS 원격 업데이트
    ├── install_was_rocky8.sh         # WAS 최초 설치 (Rocky Linux 8)
    ├── update_rocky8.sh              # WAS pull + 재배포 (+ PG 튜닝 재적용)
    ├── tune_pg_rocky8.sh             # PostgreSQL 메모리/WAL 설정을 서버 RAM 비율로 계산·적용
    ├── build_demo.py                 # 샘플(데모) 페이지 생성 → samples/soltrace-demo.html
    ├── create_partitions.sh          # ftp_logs 월별 파티션 생성 (cron)
    ├── backup_db.sh                  # DB 증분 백업, 최대 3년 보관 (cron)
    ├── rebalance_default_partition.sql  # ftp_logs_default → 월 파티션 수동 이동
    └── soltrace-selfupdate.sh        # 웹 설정페이지 git 자가 업데이트 래퍼
```

---

## 설치

### WAS 서버 (Rocky Linux 8)

PostgreSQL 16, Python 3.11, nginx를 직접 설치하고 systemd 서비스로 등록한다.

```bash
git clone https://github.com/juandjoo/soltrace.git
cd soltrace
sudo bash scripts/install_was_rocky8.sh
```

설치 완료 후 출력되는 `ADMIN_PASSWORD`를 보관한다.  
접속: `http://<WAS_IP>`

### FTP 서버 데몬

> **OS별 상세 설치 가이드**: [ftp-daemon/README.md](ftp-daemon/README.md)  
> CentOS 7 (EOL 저장소 교체 포함), Rocky Linux 8/9 모두 다룬다.

**Rocky Linux 8 / 9 — 자동 설치:**

```bash
curl -fsSL https://raw.githubusercontent.com/juandjoo/soltrace/main/ftp-daemon/install.sh | sudo bash
```

**CentOS 7 (EOL) — 저장소 교체 + 자동 설치:**

```bash
# 1. yum 저장소를 vault.centos.org로 교체
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
sudo yum clean all && sudo yum makecache

# 2. Python 3.8 설치 (SCL — requests >= 2.28 요구사항)
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
sudo yum install -y rh-python38

# 3. 데몬 자동 설치
curl -fsSL https://raw.githubusercontent.com/juandjoo/soltrace/main/ftp-daemon/install.sh | sudo bash

# 4. urllib3 v2 호환성 패치 (OpenSSL 1.0.2k 환경)
sudo /opt/soltrace-daemon/venv/bin/pip install "urllib3<2"
sudo systemctl restart soltrace-daemon
```

설치 후 WAS 주소 설정 확인:

```bash
vi /opt/soltrace-daemon/config.ini     # was_url 반드시 https:// 로 설정
systemctl restart soltrace-daemon
journalctl -u soltrace-daemon -f
```

---

## 환경 변수 (`/opt/soltrace/.env`)

WAS 서버에 저장되며 설치 시 자동 생성된다.

| 변수 | 설명 |
|---|---|
| `DATABASE_URL` | PostgreSQL 접속 URL |
| `SECRET_KEY` | JWT 서명 키 (설치 시 자동 생성) |
| `ADMIN_PASSWORD` | 웹 UI 로그인 비밀번호 (설치 시 자동 생성) |

---

## 데몬 설정 (`config.ini`)

| 항목 | 기본값 | 설명 |
|---|---|---|
| `was_url` | — | WAS 주소 (`https://` 권장) |
| `transfer_log` | — | proftpd TransferLog 경로 |
| `extended_log` | — | proftpd ExtendedAllLog 경로 |
| `poll_interval` | `10` | 로그 파일 폴링 주기 (초) |
| `batch_size` | `200` | 1회 전송 최대 건수 |
| `heartbeat_interval` | `60` | WAS 생존 신호 주기 (초) |
| `max_buffer_lines` | `50000` | 전송 실패 시 로컬 버퍼 최대 줄 수 |
| `update_url` | GitHub raw URL | 웹 UI 업데이트 시 파일 다운로드 경로 |
| `ssl_verify` | `true` | `false` = 자체 서명 인증서 허용 |
| `skip_login_logout` | `false` | `true` = login/logout 이벤트 전송 제외 |

---

## 데몬 동작

### WAS 장애 대응

WAS 점검·재시작 등 일시적 연결 실패 시 데몬이 종료되지 않고 자동으로 복구된다.

| 상황 | 동작 |
|---|---|
| 전송 3회 실패 (502/503/타임아웃) | 항목을 로컬 버퍼(`buffer_file`)에 저장, 지수 백오프(30초→최대 5분) 후 재시도 |
| 재시작 시 버퍼 존재 | startup에서 버퍼 재전송 시도, 실패해도 계속 실행 (sender_loop에서 재시도) |
| WAS 복구 | 버퍼 항목 자동 재전송 후 정상 운영 재개 |

### 장비 상태에 따른 처리

| WAS 응답 | 원인 | 데몬 동작 |
|---|---|---|
| `403 Forbidden` | WAS에서 해당 장비 비활성화 | 버퍼 없이 타일러 롤백, 5분 대기 후 재시도. 재활성화 시 자동 재전송 |
| `401 Unauthorized` / `404 Not Found` | WAS에서 장비 삭제 | 즉시 `safe_shutdown` — 버퍼 저장 후 데몬 종료 |

### 하트비트 상태 표시 (웹 UI)

마지막 하트비트로부터 120초 이상 경과 시 데몬 상태를 아래와 같이 오버라이드한다.

| DB 상태 | 120초 초과 시 표시 |
|---|---|
| `running` | 저하 |
| `stopping` | 미확인 |
| `degraded` | 저하 |

---

## 웹 UI 기능

| 메뉴 | 기능 |
|---|---|
| 대시보드 | 기간별 업로드/다운로드 추이, 작업 유형 분포, 상위 사용자/그룹 (기본 7일) |
| 서비스 영향도 | 전송 실패율 / 로그인 실패율 / CWD 실패 도넛 차트, 장비별 상태, 알림 테이블 |
| 장비 관리 | 신규 장비 확인(Confirm), 그룹 배정, 비활성화, 삭제, 원격 업데이트 |
| 그룹 관리 | telco / service / other 유형으로 그룹 생성·수정·삭제 |
| 로그 조회 | 장비·그룹·사용자·IP·파일명·작업유형·기간 필터, 페이징, CSV / XLSX 내보내기 (기본 90일) |
| 설정 | 알림 채널 (웹훅 / HMS), 이상 감지 임계값, 알림 음소거, 고객 계정, DB 저장소 현황 |

### 고객사 계정 (데이터 격리)

관리자(admin) 외에 고객사별 조회 전용 계정을 둘 수 있다. 설정 > 고객 계정에서 관리한다.

- **격리 기준**: 계정의 `customer` 값과 `groups.customer` 가 정확히 일치하는 그룹에 속한 장비의 데이터만 보인다. 대시보드·로그 조회·장비 목록·그룹 목록 모두 동일한 `device_scope` 로 제한된다.
- **권한**: 고객 계정은 조회만 가능하며 설정 메뉴와 모든 관리 API(`require_admin`)가 차단된다.
- **IP 제한**: 계정별 허용 IP/CIDR 목록을 둘 수 있다(비어 있으면 제한 없음). admin 은 기존의 전역 접속 허용 IP 를 따른다.
- **주의**: customer 문자열이 공백·대소문자까지 같아야 하므로, 계정 생성 화면의 자동완성(그룹의 customer 목록)을 사용한다.

### 로그 조회 총건수

목록과 총건수(`/logs/count`)를 분리해 병렬로 요청한다. 대량 구간에서 COUNT 가 오래 걸려도 첫 페이지는 즉시 표시되고, 총건수는 집계가 끝나면 채워진다. 같은 필터로 페이지만 바꿀 때는 재집계하지 않는다. 총건수는 상한이나 추정 없이 정확히 센다.

### 서비스 영향도 감지

FTP 서버 부하가 실제 서비스에 영향을 주는지를 로그에서 직접 판정한다.

- **지표**: 전송 실패율(upload/download), 실효 전송속도(Σsize/Σtime), 로그인 실패율(식별된 계정 한정), CWD 실패 급증
- **집계**: WAS가 5분 주기로 `ftp_logs`를 10분 버킷(`service_metrics`)으로 롤업
- **판정**: 장비별 최근 7일 **median+MAD** baseline 대비 이탈을 `service_alerts`에 적재
- **알림**: 웹 UI 노출 + (설정 시) 웹훅 / HMS 발송
- **데몬 상태 반영**: `degraded` / `disabled` / `error` 상태 장비는 서비스 알림 없어도 건강도 `warning`으로 표시

> 임계값은 설정 페이지에서 조정 (`ALERT_MAD_K`, `ALERT_FAIL_RATE_FLOOR`, `ALERT_THROUGHPUT_DROP` 등)

### 드릴다운 필터

대시보드 서비스 영향도 차트에서 항목 클릭 시 로그 조회 페이지로 이동하며 자동으로 필터가 적용된다.

| 클릭 항목 | 적용 필터 |
|---|---|
| 전송 실패 | `action=upload/download` + `status=fail` (cwd_fail 제외) |
| 로그인 실패 | `action=login` + `status=fail` |
| CWD 실패 | `action=cwd_fail` |

---

## 보안

- **XSS 방지**: 사용자 입력·DB 데이터를 innerHTML에 삽입 시 `esc()` 헬퍼로 이스케이프
- **보안 헤더**: `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, `Content-Security-Policy`
- **인증**: JWT Bearer 토큰, 모든 API 엔드포인트에 `require_admin` 의존성
- **알림**: SMTP 미지원, 웹훅 / HMS만 지원

---

## 로그 파싱 대상

| 파일 | 파싱 항목 |
|---|---|
| `TransferLog` | 업로드(i), 다운로드(o), 삭제(d) — 완료 여부(completion) 포함 |
| `ExtendedAllLog` | 로그인 성공(PASS 230), 로그인 실패(PASS 530, 식별된 계정 한정), 로그아웃(QUIT), 이름변경(RNTO), 폴더생성(MKD 257), 폴더삭제(RMD 250), 디렉토리 이동 실패(CWD 550) |

---

## API

| 메서드 | 경로 | 설명 |
|---|---|---|
| `POST` | `/api/v1/auth/login` | 로그인 (JWT 발급, role/customer 클레임 포함) |
| `POST` | `/api/v1/auth/refresh` | 세션 연장 (토큰 재발급) |
| `POST` | `/api/v1/ingest/register` | 장비 등록 (데몬) |
| `POST` | `/api/v1/ingest/heartbeat` | 하트비트 + 데몬 상태 업데이트 (데몬) |
| `POST` | `/api/v1/ingest/logs` | 로그 배치 수신 (데몬) |
| `GET` | `/api/v1/devices` | 장비 목록 |
| `PUT` | `/api/v1/devices/{id}/status` | 장비 상태 변경 (confirmed / disabled) |
| `PUT` | `/api/v1/devices/{id}/groups` | 그룹 배정 |
| `POST` | `/api/v1/devices/{id}/update` | 데몬 원격 업데이트 트리거 |
| `DELETE` | `/api/v1/devices/{id}` | 장비 삭제 |
| `GET/POST` | `/api/v1/groups` | 그룹 목록 / 생성 |
| `GET` | `/api/v1/logs` | 로그 조회 (기본 최근 90일, 총건수 미포함) |
| `GET` | `/api/v1/logs/count` | 로그 총건수 (목록과 동일 필터, 정확한 COUNT) |
| `GET` | `/api/v1/logs/export` | CSV 내보내기 |
| `GET` | `/api/v1/logs/export/xlsx` | XLSX 내보내기 |
| `GET` | `/api/v1/dashboard` | 대시보드 통계 |
| `GET` | `/api/v1/dashboard/users-hourly` | 사용자별 시간대 추이 |
| `GET` | `/api/v1/dashboard/service-health` | 서비스 영향도 (장비 상태 / 알림 / 추이) |
| `GET/POST` | `/api/v1/settings/notify` | 알림 채널 설정 |
| `GET/POST` | `/api/v1/settings/notify/mute` | 알림 음소거 |
| `GET` | `/api/v1/settings/storage` | DB 저장소 현황 (파티션별 크기·행수, default 잔존) |
| `GET/POST` | `/api/v1/users` | 고객 계정 목록 / 생성 (admin) |
| `PUT/DELETE` | `/api/v1/users/{id}` | 고객 계정 수정(비밀번호·customer·IP·활성) / 삭제 (admin) |

---

## 과거 데이터 일괄 전송 (`soltrace_bulk.py`)

데몬 설치 이전의 기존 로그나 압축 아카이브를 소급 전송할 때 사용한다.

```bash
# 압축 일별 로그만 복구 (glob 패턴)
/opt/soltrace-daemon/venv/bin/python3 /opt/soltrace-daemon/soltrace_bulk.py \
    --no-transfer \
    --extended-log "/usr/service/logs/proftpd/ExtendedAllLog.*.gz"

# 특정 기간 + 두 로그 모두
/opt/soltrace-daemon/venv/bin/python3 /opt/soltrace-daemon/soltrace_bulk.py \
    --date-from 2026-05-01 --date-to 2026-05-31

# 파싱 테스트 (전송 없음)
/opt/soltrace-daemon/venv/bin/python3 /opt/soltrace-daemon/soltrace_bulk.py \
    --no-transfer --dry-run \
    --extended-log "/usr/service/logs/proftpd/ExtendedAllLog.*.gz"
```

| 옵션 | 설명 |
|---|---|
| `--transfer-log PATH` | TransferLog 경로 또는 glob 패턴 |
| `--extended-log PATH` | ExtendedAllLog 경로 또는 glob 패턴 |
| `--date-from YYYY-MM-DD` | 시작 날짜 (포함) |
| `--date-to YYYY-MM-DD` | 종료 날짜 (포함) |
| `--batch-size N` | 배치 크기 (기본: 500) |
| `--dry-run` | 전송 없이 파싱 결과만 확인 |
| `--no-transfer` | TransferLog 무시 |
| `--no-extended` | ExtendedAllLog 무시 |

진행 상황은 50,000줄마다 로그로 출력되며, 파일 단위로 파싱 건수 / 날짜 제외 건수 / 파싱 오류 건수가 요약된다.

---

## DB 파티션 관리

`ftp_logs`는 `log_time` 기준 월별 파티션으로 분할된다. WAS 기동 시(및 `init.sql` 적용 시) **과거 36개월 ~ 향후 2개월** 파티션을 자동 생성한다. 과거 범위는 백업 보존 기간(`backup_db.sh` `RETENTION_MONTHS=36`)과 같아, 보존 기간 안의 과거 로그를 bulk import 해도 default 파티션으로 빠지지 않는다.

파티션별 크기·행수와 default 파티션 잔존 여부는 **설정 > DB 저장소** 에서 확인한다. default 에 행이 남아 있으면 WAS 기동 로그에도 경고가 남는다.

### `ftp_logs_default` 재배치

default 에 데이터가 있는 월은 파티션 생성이 건너뛰어지고 보존기간 DROP 대상에서도 빠진다. 아래 SQL로 월 파티션으로 이동한다.  
행 수에 따라 부하가 크므로 저시간대에 수동 실행 권장.

```bash
sudo -u postgres psql -d soltrace -f scripts/rebalance_default_partition.sql
```

---

## WAS 업데이트

```bash
# WAS 서버에서 실행
sudo bash scripts/update_rocky8.sh main
```

업데이트 시 `tune_pg_rocky8.sh` 가 함께 실행되어 PostgreSQL 설정이 서버 RAM 기준 권장값과 다르면 재적용하고 PostgreSQL 을 재시작한다(같으면 아무것도 하지 않음).

## PostgreSQL 튜닝

설치·업데이트 스크립트가 자동으로 적용하지만, 메모리를 증설했거나 수동 확인이 필요하면 직접 실행한다.

```bash
sudo bash scripts/tune_pg_rocky8.sh --dry-run   # 계산 결과만 확인
sudo bash scripts/tune_pg_rocky8.sh --restart   # 적용 + 변경 시 재시작
```

| 설정 | 산출 기준 |
|---|---|
| `shared_buffers` | RAM 25% (256MB ~ 8GB) |
| `effective_cache_size` | RAM 60% |
| `work_mem` | 16 / 32 / 64MB (RAM 4GB 미만 / 8GB 미만 / 이상) |
| `maintenance_work_mem` | 128MB ~ 1GB |
| `max_wal_size` | 1 / 2 / 4GB — bulk import 시 체크포인트 빈도 완화 |

## 테스트

인증·격리 로직 단위 테스트(DB 불필요):

```bash
cd was
python -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest tests -q
```

## 샘플(데모) 페이지

WAS·DB 없이 화면만 보여줄 때 쓴다. `samples/soltrace-demo.html` 을 브라우저로 바로 열면 된다
(Bootstrap/Chart.js 는 CDN 사용 → 인터넷 필요).

- 실제 `was/static` 의 화면·CSS·JS 를 그대로 인라인하고 **API 응답만** `js/demo-data.js` 의
  예시 데이터로 대체한다. 화면을 고친 뒤 아래 명령을 다시 실행하면 샘플도 같이 갱신된다.
- 로그인 없이 admin 화면으로 진입하며, 조회·차트·탭 이동은 동작하고 저장/삭제 등 변경 요청은 차단된다.

```bash
python3 scripts/build_demo.py              # samples/soltrace-demo.html 재생성
python3 scripts/build_demo.py -o /tmp/x.html
```

## 코드 배포

```bash
# 로컬 → GitLab + GitHub push + WAS 원격 업데이트
WAS_HOST=192.168.0.10 bash scripts/deploy_rocky8.sh main

# 환경변수 전체 지정 예시
WAS_HOST=192.168.0.10 WAS_USER=rocky WAS_KEY=~/.ssh/id_rsa WAS_REPO=~/soltrace \
    bash scripts/deploy_rocky8.sh main
```

`WAS_HOST` 미설정 시 git push만 하고 원격 업데이트는 건너뛴다.

---

## 서비스 관리

```bash
# WAS 서버
systemctl status soltrace-was
systemctl restart soltrace-was
journalctl -u soltrace-was -f

# FTP 서버 데몬
systemctl status soltrace-daemon
systemctl restart soltrace-daemon
journalctl -u soltrace-daemon -f
tail -f /var/log/soltrace-daemon.log
```

---

## 삭제

### FTP 서버 데몬 제거

```bash
systemctl stop soltrace-daemon
systemctl disable soltrace-daemon
rm -f /etc/systemd/system/soltrace-daemon.service
systemctl daemon-reload
rm -rf /opt/soltrace-daemon
rm -rf /var/lib/soltrace
rm -f /var/log/soltrace-daemon.log
userdel soltrace
setfacl -x u:soltrace /usr/service /usr/service/logs /usr/service/logs/proftpd 2>/dev/null || true
setfacl -R -x u:soltrace /usr/service/logs/proftpd 2>/dev/null || true
```

### WAS 서버 제거

```bash
systemctl stop soltrace-was
systemctl disable soltrace-was
rm -f /etc/systemd/system/soltrace-was.service
systemctl daemon-reload
rm -rf /opt/soltrace
sudo -u postgres psql -c "DROP DATABASE IF EXISTS soltrace;"
sudo -u postgres psql -c "DROP USER IF EXISTS soltrace;"
rm -f /etc/nginx/conf.d/soltrace.conf
systemctl reload nginx
```
