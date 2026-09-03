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
│               ├── apiguide.js   # API 가이드 (엔드포인트·파라미터·예시 표)
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
    ├── nginx_conf.sh                 # nginx.conf 렌더링 공통 로직 (도메인·인증서 경로 치환)
    ├── db_migrate.sh                 # init.sql 적용·소유권 이관 공통 로직 (lock_timeout + 재시도)
    ├── setup_ssl.sh                  # Let's Encrypt 인증서 발급 + 자동 갱신 등록
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

설치 중 **서비스 도메인**과 **Let's Encrypt 인증서 발급 여부**를 물어본다. 발급을 선택하면 certbot 으로
인증서를 받고 자동 갱신(certbot 타이머 + nginx reload 훅)까지 등록한다.

비대화형 설치는 환경변수로 지정한다(터미널이 없으면 인증서 발급은 기본적으로 건너뛴다 — 잘못된
도메인으로 시도하면 Let's Encrypt 발급 한도만 소모되기 때문):

```bash
sudo SOLTRACE_DOMAIN=soltrace.example.com \
     SOLTRACE_LE_EMAIL=admin@example.com \
     SOLTRACE_SETUP_SSL=yes \
     bash scripts/install_was_rocky8.sh
```

설치 완료 후 출력되는 `ADMIN_PASSWORD`를 보관한다.  
접속: `https://<도메인>`

#### HTTPS 인증서 (Let's Encrypt)

- 발급 전제: 도메인 A레코드가 이 서버 공인 IP를 가리키고, **80 포트가 외부에 열려 있을 것**(ACME 챌린지 경로).
- 인증서가 없는 동안에는 임시 **자체서명** 인증서로 nginx 가 뜬다(브라우저 경고). 인증서 발급 자체가
  80 포트를 서빙하는 nginx 를 필요로 하기 때문이며, 발급이 끝나면 자동으로 실제 인증서로 교체된다.
- 설치 때 건너뛰었거나 실패했다면 나중에 단독 실행:

```bash
sudo bash scripts/setup_ssl.sh soltrace.example.com admin@example.com
```

- 갱신 방식: `certonly --webroot`(nginx 플러그인 미사용 — 플러그인이 빠지면 갱신이 조용히 실패한다).
  certbot 타이머(없으면 `/etc/cron.d/soltrace-certbot`)가 갱신하고, deploy 훅이 nginx 를 reload 한다.
- 확인: `certbot certificates`, `certbot renew --dry-run`, 갱신 로그 `/var/log/soltrace/ssl_renew.log`
- 선택한 도메인은 `/etc/soltrace/domain` 에 저장되어 이후 업데이트(`update_rocky8.sh`, 웹 자가 업데이트)에도
  그대로 유지된다. 도메인을 바꾸려면 `setup_ssl.sh <새 도메인>` 을 다시 실행한다.

### FTP 서버 데몬

> **OS별 상세 설치 가이드**: [ftp-daemon/README.md](ftp-daemon/README.md)  
> CentOS 7 (EOL 저장소 교체 포함), Rocky Linux 8/9 모두 다룬다.

**Rocky Linux 8 / 9 — 자동 설치:**

```bash
curl -fsSL https://raw.githubusercontent.com/juandjoo/soltrace/main/ftp-daemon/install.sh | sudo bash
```

설치 중 **WAS 서버 주소**를 물어본다(엔터 = 기존/기본값 유지). `curl | sudo bash` 로 실행해도 터미널에서 직접 입력받는다.
비대화형(자동화) 설치는 환경변수로 지정한다:

```bash
curl -fsSL .../install.sh | sudo SOLTRACE_WAS_URL=https://soltrace.example.com bash
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

설치 후 WAS 주소 확인 / 변경(설치 시 입력값이 이미 반영되어 있음):

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
| `was_url` | — | WAS 주소 (`https://` 권장). 설치 시 입력받으며 `SOLTRACE_WAS_URL` 환경변수로도 지정 가능 |
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
| 서비스 영향도 | 전송 실패율 / 로그인 실패율 / CWD 실패 도넛 차트, 장비별 상태, 알림 테이블, CWD 실패 원인 분석 |
| 장비 관리 | 신규 장비 확인(Confirm), 그룹 배정, 비활성화, 삭제, 원격 업데이트 |
| 그룹 관리 | telco / service / other 유형으로 그룹 생성·수정·삭제 |
| 로그 조회 | 장비·그룹·사용자·IP·파일명·작업유형·기간 필터, 페이징, CSV / XLSX 내보내기 (기본 90일) |
| API 가이드 | 조회 전용 API 키 사용법 — 인증, 엔드포인트, 파라미터, 오류 코드, 복사용 curl/Python 예시 |
| 설정 | 알림 채널 (웹훅 / HMS), 이상 감지 임계값, 알림 음소거, 고객 계정, DB 저장소 현황 |

### 고객사 계정 (데이터 격리)

관리자(admin) 외에 고객사별 조회 전용 계정을 둘 수 있다. 설정 > 고객 계정에서 관리한다.

- **격리 기준**: 계정의 `customer` 값과 `groups.customer` 가 정확히 일치하는 그룹에 속한 장비의 데이터만 보인다. 대시보드·로그 조회·장비 목록·그룹 목록 모두 동일한 `device_scope` 로 제한된다.
- **권한**: 고객 계정은 조회만 가능하며 설정 메뉴와 모든 관리 API(`require_admin`)가 차단된다.
- **IP 제한**: 계정별 허용 IP/CIDR 목록을 둘 수 있다(비어 있으면 제한 없음). admin 은 기존의 전역 접속 허용 IP 를 따른다.
- **주의**: customer 문자열이 공백·대소문자까지 같아야 하므로, 계정 생성 화면의 자동완성(그룹의 customer 목록)을 사용한다.

### API 키 (조회 전용 토큰)

프로그램에서 데이터를 가져갈 때 쓰는 계정별 토큰이다. **설정 > 고객 계정 > API 키**에서 발급한다.

- **발급 대상**: 관리자(전체 조회) 또는 특정 고객 계정. 계정 권한을 그대로 물려받으므로,
  고객 계정 키로는 해당 `customer` 의 장비 데이터만 보인다(`device_scope` 동일 적용).
- **조회 전용**: `GET`/`HEAD` 만 허용한다. 다른 메서드는 키가 유효해도 403이다.
- **저장 방식**: 원본 키는 저장하지 않고 SHA-256 해시만 보관한다. 발급 직후 한 번만 표시되며
  분실하면 재발급해야 한다. 목록에는 앞부분(`slt_xxxxxxxx…`)만 나온다.
- **만료·폐기**: 만료일을 지정할 수 있고, 폐기(비활성)와 삭제를 지원한다.
  계정을 삭제하면 그 계정의 키도 함께 삭제된다.

웹 UI 상단 **API 가이드** 탭에 인증 방법, 조회 가능한 엔드포인트, 로그 필터 파라미터,
오류 코드와 복사용 예시(curl / Python)가 정리되어 있다. 예시의 주소는 접속한 주소로 자동 채워진다.

```bash
# X-API-Key 헤더 (권장)
curl -H "X-API-Key: slt_..." "https://<주소>/api/v1/logs?size=50"

# Authorization 헤더로도 받는다 (키가 slt_ 로 시작해 JWT 와 구분됨)
curl -H "Authorization: Bearer slt_..." "https://<주소>/api/v1/dashboard?days=7"
```

> 키는 비밀번호와 달리 고엔트로피 난수라 pbkdf2 같은 느린 해시가 필요 없다.
> 매 요청마다 검증하므로 단일 SHA-256을 쓴다. 마지막 사용 시각은 1분 간격으로만 기록한다.

### 로그 조회 총건수

목록과 총건수(`/logs/count`)를 분리해 병렬로 요청한다. 대량 구간에서 COUNT 가 오래 걸려도 첫 페이지는 즉시 표시되고, 총건수는 집계가 끝나면 채워진다. 같은 필터로 페이지만 바꿀 때는 재집계하지 않는다. 총건수는 상한이나 추정 없이 정확히 센다.

### 서비스 영향도 감지

FTP 서버 부하가 실제 서비스에 영향을 주는지를 로그에서 직접 판정한다.

- **지표**: 전송 실패율(upload/download), 실효 전송속도(Σsize/Σtime), 로그인 실패율(식별된 계정 한정), CWD 실패 급증
- **집계**: WAS가 5분 주기로 `ftp_logs`를 10분 버킷(`service_metrics`)으로 롤업
- **판정**: 장비별 최근 7일 **median+MAD** baseline 대비 이탈을 `service_alerts`에 적재
- **알림**: 웹 UI 노출 + (설정 시) 웹훅 / HMS 발송
- **데몬 상태 반영**: `degraded` / `disabled` / `error` 상태 장비는 서비스 알림 없어도 건강도 `warning`으로 표시

#### 전송 속도 판정 — 작은 파일 대량 전송 오탐 방지

작은 파일은 연결·인증 오버헤드가 전송시간의 대부분을 차지해 실효속도(Σsize/Σtime)가 낮게 나온다.
예를 들어 20KB 파일이 파일당 0.2초 걸리면 0.1MB/s로 계산되어, 평소 50MB/s이던 장비가
**소량 파일을 대량 업로드하는 것만으로 성능 저하로 오인**된다.

이를 막기 위해 전송 속도는 아래 조건을 만족하는 전송만으로 판정한다.

| 조건 | 이유 |
|---|---|
| `ALERT_LARGE_FILE_BYTES`(기본 4MB) 이상 | 고정 오버헤드 비중이 낮아 회선 상태를 반영 |
| 성공(`status=success`)한 전송 | 중단된 전송의 부분 바이트가 속도를 왜곡 |
| 대상 건수 ≥ 최소 대상 건수(기본 5) | 소표본 판정 방지 (미달 시 판정 자체를 건너뜀) |
| 평균 파일 크기가 평소의 1/4 ~ 4배 이내 | 크기 구성이 달라지면 비교가 성립하지 않음 → 보류 |

집계는 `service_metrics.xfers_big / bytes_big / secs_big` 컬럼에 롤업 시점에 저장된다.
`ALERT_LARGE_FILE_BYTES`는 롤업에 반영되는 값이라 `.env`에서만 바꾸며(재배포 필요),
변경하면 baseline(기본 7일)이 새 기준으로 다시 쌓일 때까지 판정이 보수적으로 동작한다.

#### 임계값 조정

판정 임계값은 **설정 > 알림 설정 > 이상 감지 임계값**에서 바꾸며, 저장하면 다음 판정 주기
(기본 5분)에 반영된다(WAS 재시작 불필요). `.env` 값은 기본값으로만 쓰이고, "기본값 복원"으로
언제든 되돌릴 수 있다.

| 항목 | 기본값 | 설명 |
|---|---|---|
| 이탈 배수 (`ALERT_MAD_K`) | 4.0 | 클수록 둔감 |
| 전송 속도 하락 비율 (`ALERT_THROUGHPUT_DROP`) | 0.5 | 평소 대비 50%↓ |
| 전송 실패율 하한 (`ALERT_FAIL_RATE_FLOOR`) | 0.05 | |
| 로그인 실패율 하한 (`ALERT_LOGIN_FAIL_RATE_FLOOR`) | 0.30 | |
| CWD 실패 하한 (`ALERT_CWD_FAIL_FLOOR`) | 20건 | |
| CWD 실패 제외 경로 | (없음) | 설정 페이지 전용(여러 줄) · 한 줄에 하나, `*` 와일드카드 |
| 최소 표본 (`ALERT_MIN_SAMPLES` 등) | 20 / 10 / 5 | 전송 / 로그인 / CWD |
| 전송 속도 최소 대상 건수 (`ALERT_MIN_LARGE_SAMPLES`) | 5 | 큰 파일 건수 |

#### CWD 실패의 원인 구분

`cwd_fail` 은 proftpd 의 `CWD` 명령에 **550**(경로 없음/권한 없음)이 돌아온 건이다. 침입성
탐색뿐 아니라 **홈·업로드 경로 설정 오류**(오타, 마운트 누락, 권한)로도 매 세션 같은 경로에서
대량 발생한다.

**존재 확인은 실패로 세지 않는다.** 업로드 클라이언트는 새 디렉토리에 `CWD` → 550 → `MKD` →
업로드 순으로 도는 경우가 많다. 이건 "폴더로 이동하다 실패"한 게 아니라 있는지 떠본 것이므로,
**CWD 실패 뒤 10분 안에 같은 경로가 생성(`mkdir`)됐으면 집계·추이·알림·분석 목록에서 모두 뺀다**
(`service_monitor.cwd_real_fail_sql()`, 부분 인덱스 `idx_ftp_logs_mkdir_path` 사용).
로그 조회에는 원본이 그대로 남는다. 그래서 "CWD 실패 급증" 알림에는 그 버킷에서 실패가 가장 많은 경로를 함께 적고,
한 경로에 90% 이상 몰려 있으면 설정 확인 문구를 덧붙인다.

원인이 밝혀져 더 볼 필요가 없는 경로는 **설정 > 알림 설정 > CWD 실패 제외 경로**에 넣으면
집계에서 빠져 알림이 멈춘다. 로그 조회에는 그대로 남으므로 감사 기록은 잃지 않는다.
(최근 2시간 버킷은 다음 판정 주기에 다시 집계되므로 방금 뜬 알림 구간에도 바로 반영된다.)

**CWD 실패 분석 화면** — 대시보드 "전송/로그인 실패 건수" 카드의 `CWD 실패 분석` 버튼(또는 도넛의
CWD 조각 클릭)으로 연다. 조회 기간의 cwd_fail 을 **경로별·사용자별 상위 집계**로 보여주므로
"몇 건인가"가 아니라 "어디에 몰려 있는가"로 판단할 수 있다.

- 목록에는 **진짜 이동 실패만** 올라온다. 요약 줄에 "원본 N건 중 존재 확인 M건, 제외 경로 K건 제외"로 근거를 밝힌다.
- 상위 3개 경로에 90% 이상 몰려 있으면 설정 오류일 가능성이 크다는 안내를 띄운다.
- 경로별로 사용자 수 · 클라이언트 IP 수 · 마지막 발생 시각을 함께 보여준다(소수 IP + 다수 경로면 탐색성).
- 각 행에서 그 경로만 **로그 조회**로 드릴다운하거나(관리자는) 그 자리에서 **제외/제외 해제**할 수 있다.
- API: `GET /api/v1/dashboard/cwd-fails` (기간·장비 파라미터는 service-health 와 동일, `limit` 기본 15)

롤업(`service_metrics.cwd_fails`) · 알림 · 대시보드 실패 건수 · 분석 화면이 **같은 조건**
(`service_monitor.cwd_real_fail_sql()` = 제외 경로 + 존재 확인 제외)을 쓴다. 대시보드 도넛은
남은 실패만 보여주고, 설정으로 숨긴 건수만 범례·툴팁에 "제외 N건"으로 적는다.

### 드릴다운 필터

대시보드 서비스 영향도 차트에서 항목 클릭 시 로그 조회 페이지로 이동하며 자동으로 필터가 적용된다.

| 클릭 항목 | 동작 |
|---|---|
| 전송 실패 | 로그 조회 — `action=upload/download` + `status=fail` (cwd_fail 제외) |
| 로그인 실패 | 로그 조회 — `action=login` + `status=fail` |
| CWD 실패 | **CWD 실패 분석** 모달 (경로별 집계) → 경로 행에서 `action=cwd_fail` + 해당 경로로 드릴다운 |

---

## 보안

- **XSS 방지**: 사용자 입력·DB 데이터를 innerHTML에 삽입 시 `esc()` 헬퍼로 이스케이프
- **보안 헤더**: `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, `Content-Security-Policy`, HSTS
- **인증**: JWT Bearer 토큰, 관리 API 전체에 `require_admin`, 조회 API는 `device_scope` 로 고객사 격리
- **문서 비공개**: `docs_url` / `redoc_url` / `openapi_url` 모두 비활성 (스키마로 엔드포인트가 노출되지 않도록)
- **알림**: SMTP 미지원, 웹훅 / HMS만 지원

### 접속 IP 판정 (허용목록 우회 방지)

접속 IP는 **`X-Real-IP` → `X-Forwarded-For`(첫 항목) → 소켓 peer** 순으로 판정한다.

`X-Forwarded-For`를 먼저 믿으면 안 된다. nginx가 이 헤더를 `$proxy_add_x_forwarded_for`로
넘기면 **클라이언트가 보낸 값 뒤에** 실제 IP가 덧붙으므로, 첫 항목이 공격자가 지정한 값이 된다.
헤더 한 줄로 관리자 전역 허용 IP와 고객사 계정별 허용 IP를 모두 통과할 수 있다.

방어는 두 겹이다.

1. `nginx.conf`가 모든 location에서 `X-Real-IP`와 `X-Forwarded-For`를 `$remote_addr`로 **덮어쓴다**
   (append하지 않는다).
2. 앱이 위조 불가능한 `X-Real-IP`를 우선 참조한다 — nginx 설정이 어긋나도 막힌다.

`was/tests/test_client_ip.py`가 이 동작을 회귀 테스트로 고정한다.
**nginx를 거치지 않고 8000 포트를 직접 노출하면 두 방어가 모두 무력화**되므로,
gunicorn은 `127.0.0.1`에만 바인딩된 상태를 유지한다.

### TLS / 프록시

- TLS 1.2·1.3만 허용, AEAD(GCM/ChaCha20) 암호군만 사용, 세션 티켓 비활성, OCSP 스테이플링
- HSTS `max-age=31536000; includeSubDomains`
  (`add_header`를 쓰는 location은 상위 헤더를 상속하지 않으므로 해당 블록에도 다시 명시)
- slowloris 방어: `client_header_timeout` / `client_body_timeout` / `send_timeout`, `limit_conn`
- 레이트리밋: 인증 10r/m, 일반 API 60r/m, 데몬 ingest 300r/m
- nginx는 Rocky 8 기본 스트림(1.14, 2018년)을 쓰지 않고 설치 시 최신 스트림으로 올린다.
  **1.25.1 이상으로 올릴 경우** `listen 443 ssl http2;` 를 `listen 443 ssl;` + `http2 on;` 으로 바꿔야 한다.

### 남은 조치 (운영 판단 필요)

- **데몬 등록 API는 의도적으로 개방**: `/api/v1/ingest/register` 는 어느 IP에서든 호출할 수 있으나,
  등록된 장비는 `pending` 상태로만 남고 **관리자가 확인(Confirm)해야** 로그 수집이 시작된다.
  승인 절차가 통제 지점이므로 IP 제한은 두지 않는다. 필요 시 `nginx.conf` 의
  `location /api/v1/ingest/` 에 주석으로 둔 `allow` / `deny` 를 활성화하면 된다.
- **로그인 실패 잠금 없음**: nginx 레이트리밋(분당 10회)만으로 막고 있어 분산 IP 공격에는 약하고
  실패 이력이 남지 않는다.
- **CSP `unsafe-inline`**: 인라인 스크립트를 걷어내면 XSS 방어가 한 겹 늘어난다
  (토큰을 localStorage에 두므로 XSS 시 탈취 가능).

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
| `GET/PUT` | `/api/v1/settings/alerts` | 이상 감지 임계값 조회 / 저장 |
| `POST` | `/api/v1/settings/alerts/reset` | 임계값 기본값 복원 |
| `GET/POST` | `/api/v1/users` | 고객 계정 목록 / 생성 (admin) |
| `PUT/DELETE` | `/api/v1/users/{id}` | 고객 계정 수정(비밀번호·customer·IP·활성) / 삭제 (admin) |
| `GET/POST` | `/api/v1/api-keys` | API 키 목록 / 발급 (admin, 발급 응답에만 평문 키) |
| `PUT` | `/api/v1/api-keys/{id}/status` | 키 폐기 / 재활성 (admin) |
| `DELETE` | `/api/v1/api-keys/{id}` | 키 삭제 (admin) |

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

### 배포 순서 (update_rocky8.sh · soltrace-selfupdate.sh 공통)

```
git reset --hard → pip install → init.sql(스키마) → nginx → [app/static 복사 → WAS 재시작] → 헬스체크
```

**스키마 적용은 락을 오래 잡지 않게** 한다(`scripts/db_migrate.sh` 를 세 배포 경로가 공유).
`ALTER TABLE` 은 `ACCESS EXCLUSIVE` 락이 필요해서, 라이브 ingest 가 `ftp_logs` 를 잡고 있으면
DDL 이 락 큐에서 대기하고 **그 뒤로 신규 쿼리가 전부 막힌다** → 커넥션 풀 고갈 →
하트비트까지 500. 2026-09-03 에 스키마 단계가 30분을 끌어 그동안 서비스가 죽었다.

- `lock_timeout=5s` 로 빨리 포기하고 파일 전체를 최대 3회 재시도한다(init.sql 은 멱등).
- 소유권 이관은 **소유자가 실제로 다른 객체에만** 건다(정상 상태에서는 0건 → DDL 없음).
  예전에는 매 배포마다 전 테이블에 `ALTER TABLE ... OWNER TO` 를 걸어 그 자체가 락 폭풍이었다.
- `ftp_logs` 의 큰 인덱스는 `CREATE INDEX ... ON ONLY`(부모, 스캔 없음) + 파티션별
  `CREATE INDEX CONCURRENTLY` + `ATTACH PARTITION` 으로 만든다 — 쓰기를 막지 않는다.
  모든 파티션이 붙으면 부모 인덱스가 자동으로 valid 가 되고, 이후 새 파티션은 자동 상속된다.

**새 코드 복사는 재시작 직전에** 한다. 복사 시점부터 재시작까지는 "디스크는 새 코드 / 프로세스는
옛 코드"인 어긋난 상태이고, unit 의 `--max-requests 2000` 으로 워커가 그 사이 재활용되면
**새 코드가 옛 스키마·옛 의존성 위에서 기동해 500** 이 난다. 예전에는 복사가 맨 앞이라 이 창이
수 분이었다. 자가 업데이트는 복사 이후 실패해도 `trap` 으로 재시작을 반드시 수행하고,
`/usr/local/sbin/soltrace-selfupdate` 를 저장소 최신본으로 갱신한다(배포 스크립트 수정이
웹 업데이트에 반영되지 않던 구멍).

> 앱 로그는 저널이 아니라 파일이다 — 500 조사는 `/var/log/soltrace/error.log`(트레이스백),
> `/var/log/soltrace/access.log`(상태코드), 배포는 `/var/log/soltrace/selfupdate.log`.

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
