# SolTrace - FTP Log Analyzer

proftpd 로그를 수집·분석하는 시스템.  
FTP 장비의 데몬이 로그를 파싱해 WAS API로 전송하고, 웹 대시보드에서 조회한다.

---

## 아키텍처

```
FTP 서버 (proftpd)
  └─ soltrace_daemon.py  ──(HTTP API)──▶  WAS (FastAPI + Gunicorn)
  └─ soltrace_bulk.py    ──(일괄전송)──▶    └─ Nginx (80)
                                             └─ PostgreSQL
```

| 컴포넌트 | 스펙 |
|---|---|
| WAS | FastAPI + Gunicorn 2worker (4core / 2GB 최적화) |
| DB | PostgreSQL 16 (shared_buffers=256MB, max_connections=50) |
| Proxy | Nginx 1.27 (레이트리밋, gzip) |
| 데몬 | Python 3, 폴링 5초 간격, 전송 실패 시 로컬 버퍼 |

---

## 디렉토리 구조

```
soltrace/
├── docker-compose.yml
├── nginx/
│   └── nginx.conf
├── postgres/
│   └── init.sql
├── was/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── main.py
│       ├── config.py
│       ├── database.py
│       ├── models.py
│       ├── schemas.py
│       ├── deps.py
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
    ├── deploy.sh              # 로컬 → GitLab + GitHub push
    ├── update.sh              # EC2 pull + 빌드 + 재시작
    ├── install_was.sh         # WAS 최초 설치
    └── install_daemon.sh      # 데몬 최초 설치
```

---

## 설치

### WAS 서버 (EC2 Amazon Linux 2023)

```bash
git clone https://gitlab.solbox.com/ts-group/soltrace.git
cd soltrace
cp .env.example .env
vi .env                         # DB_PASSWORD, ADMIN_PASSWORD, SECRET_KEY 설정
sudo bash scripts/install_was.sh
```

접속: `http://<EC2_IP>` → 초기 비밀번호: `.env`의 `ADMIN_PASSWORD`

### FTP 서버 데몬

```bash
git clone https://gitlab.solbox.com/ts-group/soltrace.git
sudo bash scripts/install_daemon.sh
```

설치 후 설정 확인:
```bash
vi /opt/soltrace-daemon/config.ini
systemctl restart soltrace-daemon
journalctl -u soltrace-daemon -f
```

---

## 환경 변수 (.env)

| 변수 | 설명 | 기본값 |
|---|---|---|
| `DB_PASSWORD` | PostgreSQL 비밀번호 | `soltracepass` |
| `ADMIN_PASSWORD` | 웹 UI 로그인 비밀번호 | `Admin1234!` |
| `SECRET_KEY` | JWT 서명 키 (32자 이상) | 설치 시 자동 생성 |
| `LISTEN_PORT` | Nginx 리슨 포트 | `80` |

---

## 데몬 설정 (config.ini)

| 항목 | 설명 | 기본값 |
|---|---|---|
| `was_url` | WAS 주소 | `http://soltrace.mbone.net` |
| `transfer_log` | proftpd TransferLog 경로 | `/usr/service/logs/proftpd/TransferLog` |
| `extended_log` | proftpd ExtendedAllLog 경로 | `/usr/service/logs/proftpd/ExtendedAllLog` |
| `poll_interval` | 로그 파일 폴링 주기 (초) | `5` |
| `batch_size` | 한 번에 전송할 최대 건수 | `100` |
| `heartbeat_interval` | 하트비트 주기 (초) | `30` |

---

## 웹 UI 기능

| 메뉴 | 기능 |
|---|---|
| 대시보드 | 기간별 업로드/다운로드 추이, 작업 유형 분포, 상위 사용자/장비 |
| 장비 관리 | 신규 장비 확인(Confirm), 그룹 배정, 비활성화 |
| 그룹 관리 | telco / service / other 유형으로 그룹 생성·수정·삭제 |
| 로그 조회 | 장비·사용자·작업유형·기간 필터, 페이징, CSV 내보내기 |

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
| `GET` | `/api/docs` | Swagger UI |

---

## 과거 데이터 일괄 전송

```bash
# 전체 이력 전송
python3 /opt/soltrace-daemon/soltrace_bulk.py

# 특정 기간만 전송
python3 soltrace_bulk.py --date-from 2026-06-01 --date-to 2026-06-30

# 전송 없이 파싱 테스트
python3 soltrace_bulk.py --date-from 2026-06-01 --dry-run
```

---

## 배포

```bash
# 로컬 → GitLab + GitHub 동시 push (실패해도 다음 진행)
bash scripts/deploy.sh

# 특정 브랜치
bash scripts/deploy.sh develop
```

## EC2 업데이트

```bash
# EC2 WAS 서버에서 실행
bash scripts/update.sh

# 특정 브랜치
bash scripts/update.sh develop
```

---

## 로그 파싱 대상

| 파일 | 파싱 항목 |
|---|---|
| `TransferLog` | 업로드(i), 다운로드(o), 삭제(d) |
| `ExtendedAllLog` | 로그인(230/PASS), 로그아웃(QUIT), 이름변경(RNTO), 폴더생성(MKD), 폴더삭제(RMD) |
