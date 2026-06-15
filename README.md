# SolTrace - FTP Log Analyzer

proftpd 로그를 수집·분석하는 시스템.  
FTP 장비의 데몬이 로그를 파싱해 WAS API로 전송하고, 웹 대시보드에서 조회한다.

---

## 아키텍처

```
FTP 서버 (proftpd, Rocky Linux 8)
  └─ soltrace_daemon.py  ──(HTTP API)──▶  WAS 서버 (Rocky Linux 8)
  └─ soltrace_bulk.py    ──(일괄전송)──▶    └─ Nginx (80)
                                             └─ FastAPI + Gunicorn
                                             └─ PostgreSQL 16
```

| 컴포넌트 | 스펙 |
|---|---|
| WAS | FastAPI + Gunicorn 2worker, systemd 서비스 |
| DB | PostgreSQL 16 (shared_buffers=256MB, max_connections=50) |
| Proxy | Nginx (레이트리밋, gzip), systemd 서비스 |
| 데몬 | Python 3.11, 폴링 10초 간격, 전송 실패 시 로컬 버퍼 |

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
│       ├── main.py
│       ├── config.py
│       ├── database.py
│       ├── models.py
│       ├── schemas.py
│       ├── deps.py
│       ├── write_buffer.py
│       ├── routers/
│       │   ├── auth.py        # 로그인 (JWT)
│       │   ├── ingest.py      # 데몬 수신 API
│       │   ├── devices.py     # 장비 관리
│       │   ├── groups.py      # 그룹 관리
│       │   ├── logs.py        # 로그 조회 + CSV
│       │   └── dashboard.py   # 통계 / 차트
│       └── static/
│           └── index.html     # SPA (Bootstrap5 + Chart.js)
├── ftp-daemon/
│   ├── soltrace_daemon.py     # 실시간 로그 감시 데몬
│   ├── soltrace_bulk.py       # 과거 데이터 일괄 전송
│   ├── config.ini.example
│   ├── requirements.txt
│   └── soltrace-daemon.service
└── scripts/
    ├── deploy_rocky8.sh           # 로컬 → GitLab + GitHub push + WAS 원격 업데이트
    ├── install_was_rocky8.sh      # WAS 최초 설치 (Rocky Linux 8)
    ├── update_rocky8.sh           # WAS pull + 재배포 (Rocky Linux 8)
    ├── install_daemon_rocky8.sh   # 데몬 최초 설치 (Rocky Linux 8)
    ├── create_partitions.sh       # ftp_logs 월별 파티션 자동 생성 (cron)
    └── backup_db.sh               # DB 증분 백업, 최대 3년치 보관 (cron)
```

---

## 설치

### WAS 서버 (Rocky Linux 8)

PostgreSQL 16, Python 3.11, nginx를 직접 설치하고 systemd 서비스로 등록한다.

```bash
git clone https://gitlab.solbox.com/ts-group/soltrace.git
cd soltrace
sudo bash scripts/install_was_rocky8.sh
```

설치 완료 후 출력되는 `ADMIN_PASSWORD`를 보관한다.  
접속: `http://<WAS_IP>`

### FTP 서버 데몬 (Rocky Linux 8)

```bash
git clone https://gitlab.solbox.com/ts-group/soltrace.git
sudo bash scripts/install_daemon_rocky8.sh
```

설치 후 WAS 주소 등 설정 확인:

```bash
vi /opt/soltrace-daemon/config.ini
systemctl restart soltrace-daemon
journalctl -u soltrace-daemon -f
```

---

## 환경 변수 (/opt/soltrace/.env)

WAS 서버의 `/opt/soltrace/.env`에 저장되며 설치 시 자동 생성된다.

| 변수 | 설명 |
|---|---|
| `DATABASE_URL` | PostgreSQL 접속 URL |
| `SECRET_KEY` | JWT 서명 키 (설치 시 자동 생성) |
| `ADMIN_PASSWORD` | 웹 UI 로그인 비밀번호 (설치 시 자동 생성) |

---

## 데몬 설정 (config.ini)

| 항목 | 설명 | 기본값 |
|---|---|---|
| `was_url` | WAS 주소 | `http://soltrace.mbone.net` |
| `transfer_log` | proftpd TransferLog 경로 | `/usr/service/logs/proftpd/TransferLog` |
| `extended_log` | proftpd ExtendedAllLog 경로 | `/usr/service/logs/proftpd/ExtendedAllLog` |
| `poll_interval` | 로그 파일 폴링 주기 (초) | `10` |
| `batch_size` | 한 번에 전송할 최대 건수 | `200` |
| `heartbeat_interval` | 하트비트 주기 (초) | `60` |
| `max_buffer_lines` | 전송 실패 시 로컬 버퍼 최대 줄 수 | `50000` |

---

## 웹 UI 기능

| 메뉴 | 기능 |
|---|---|
| 대시보드 | 기간별 업로드/다운로드 추이, 작업 유형 분포, 상위 사용자/장비 |
| 서비스 영향도 | FTP 서버 부하로 인한 서비스 품질 저하 감지 (장비별 상태/추이/알림) |
| 장비 관리 | 신규 장비 확인(Confirm), 그룹 배정, 비활성화 |
| 그룹 관리 | telco / service / other 유형으로 그룹 생성·수정·삭제 |
| 로그 조회 | 장비·사용자·작업유형·기간 필터, 페이징, CSV 내보내기 |

### 서비스 영향도 감지

FTP 서버 부하가 실제 서비스에 영향을 주는지를 로그에서 직접 판정한다.

- **지표**: 전송 실패율, 실효 전송속도(Σsize/Σtransfer_time), 로그인 실패율(식별된 계정 한정 — 익명/빈 계정 스캔은 제외)
- **판정**: WAS가 5분 주기로 ftp_logs를 10분 버킷(`service_metrics`)으로 롤업하고, 장비별 최근 7일 **median+MAD** baseline 대비 이탈을 `service_alerts`에 적재
- **알림**: 웹 UI에 항상 노출 + (설정 시) 메일/웹훅 발송 — `.env`의 `SMTP_*`, `ALERT_WEBHOOK_URL` 참조
- 임계값은 `.env`로 조정 (`ALERT_MAD_K`, `ALERT_FAIL_RATE_FLOOR`, `ALERT_THROUGHPUT_DROP` 등)

> 로그인 실패 감지는 데몬이 proftpd `ExtendedAllLog`의 인증 실패(PASS 530 등) 라인을 파싱하므로, 해당 로그에 실패 항목이 기록되도록 설정돼 있어야 한다.

---

## API

| 메서드 | 경로 | 설명 |
|---|---|---|
| `POST` | `/api/v1/auth/login` | 로그인 (JWT 발급) |
| `POST` | `/api/v1/ingest/register` | 장비 등록 (데몬) |
| `POST` | `/api/v1/ingest/heartbeat` | 하트비트 (데몬) |
| `POST` | `/api/v1/ingest/logs` | 로그 배치 수신 (데몬) |
| `GET` | `/api/v1/devices` | 장비 목록 |
| `PUT` | `/api/v1/devices/{id}/status` | 장비 상태 변경 |
| `PUT` | `/api/v1/devices/{id}/groups` | 그룹 배정 |
| `GET` | `/api/v1/groups` | 그룹 목록 |
| `POST` | `/api/v1/groups` | 그룹 생성 |
| `GET` | `/api/v1/logs` | 로그 조회 |
| `GET` | `/api/v1/logs/export` | CSV 내보내기 |
| `GET` | `/api/v1/dashboard` | 대시보드 통계 |
| `GET` | `/api/v1/dashboard/service-health` | 서비스 영향도 (장비 상태/알림/추이) |
| `GET` | `/api/docs` | Swagger UI |

---

## 과거 데이터 일괄 전송

```bash
# 전체 이력 전송
/opt/soltrace-daemon/venv/bin/python3 /opt/soltrace-daemon/soltrace_bulk.py

# 특정 기간만 전송
/opt/soltrace-daemon/venv/bin/python3 /opt/soltrace-daemon/soltrace_bulk.py \
    --date-from 2026-06-01 --date-to 2026-06-30

# 전송 없이 파싱 테스트
/opt/soltrace-daemon/venv/bin/python3 /opt/soltrace-daemon/soltrace_bulk.py \
    --date-from 2026-06-01 --dry-run
```

---

## WAS 업데이트

```bash
# WAS 서버에서 실행 (main 브랜치)
sudo bash scripts/update_rocky8.sh

# 특정 브랜치
sudo bash scripts/update_rocky8.sh develop
```

## 코드 배포

```bash
# 로컬 → GitLab + GitHub push + WAS 원격 업데이트 (Rocky Linux 8)
WAS_HOST=192.168.0.10 bash scripts/deploy_rocky8.sh

# 특정 브랜치
WAS_HOST=192.168.0.10 bash scripts/deploy_rocky8.sh develop

# 환경변수 전체 지정 예시
WAS_HOST=192.168.0.10 WAS_USER=rocky WAS_KEY=~/.ssh/id_rsa WAS_REPO=~/soltrace \
    bash scripts/deploy_rocky8.sh
```

WAS_HOST 미설정 시 git push만 하고 원격 업데이트는 건너뛴다.

---

## 로그 파싱 대상

| 파일 | 파싱 항목 |
|---|---|
| `TransferLog` | 업로드(i), 다운로드(o), 삭제(d) |
| `ExtendedAllLog` | 로그인 성공(230/PASS), 로그인 실패(PASS 530 등, 식별된 계정 한정), 로그아웃(QUIT), 이름변경(RNTO), 폴더생성(MKD), 폴더삭제(RMD) |

---

## 서비스 관리

```bash
# WAS 서버
systemctl status soltrace-was
systemctl restart soltrace-was
journalctl -u soltrace-was -f

# FTP 서버
systemctl status soltrace-daemon
systemctl restart soltrace-daemon
journalctl -u soltrace-daemon -f
```
