import subprocess
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware

from app.database import SessionLocal, engine
from app.models import Base
from app.routers import auth, dashboard, devices, groups, ingest, logs, settings, telcos
from app import write_buffer as wb
from app import service_monitor as sm


def _ensure_partitions(db):
    """당월 + 향후 2개월 파티션이 없으면 생성 (create_all 경로 대응)."""
    is_partitioned = db.execute(text(
        "SELECT COUNT(*) FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relname = 'ftp_logs' AND c.relkind = 'p' AND n.nspname = 'public'"
    )).scalar()
    if not is_partitioned:
        return

    now = datetime.now(timezone.utc)
    for offset in range(3):
        total = now.month - 1 + offset
        year, month = now.year + total // 12, total % 12 + 1
        next_total = total + 1
        ny, nm = now.year + next_total // 12, next_total % 12 + 1
        part = f"ftp_logs_{year:04d}_{month:02d}"
        db.execute(text(
            f"CREATE TABLE IF NOT EXISTS {part} PARTITION OF ftp_logs "
            f"FOR VALUES FROM ('{year:04d}-{month:02d}-01') TO ('{ny:04d}-{nm:02d}-01')"
        ))

    db.execute(text(
        "CREATE TABLE IF NOT EXISTS ftp_logs_default PARTITION OF ftp_logs DEFAULT"
    ))
    db.commit()


def _run_migrations(conn):
    """스키마 변경이 필요한 마이그레이션을 idempotent하게 실행한다."""
    # groups.auth → groups.application (rename)
    conn.execute(text("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='groups' AND column_name='auth')
             AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='groups' AND column_name='application')
          THEN ALTER TABLE groups RENAME COLUMN auth TO application; END IF;
        END $$
    """))
    # client_ip GIN trigram 인덱스 — ILIKE '%...%' 검색 성능
    conn.execute(text("""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_ftp_logs_client_ip_trgm')
          THEN CREATE INDEX idx_ftp_logs_client_ip_trgm ON ftp_logs USING gin (client_ip gin_trgm_ops); END IF;
        END $$
    """))
    # file_path GIN trigram 인덱스 — 파일명/경로 부분 검색 성능
    conn.execute(text("""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_ftp_logs_file_path_trgm')
          THEN CREATE INDEX idx_ftp_logs_file_path_trgm ON ftp_logs USING gin (file_path gin_trgm_ops); END IF;
        END $$
    """))
    # device_groups.group_id 인덱스 — PK가 (device_id, group_id) 순서라 group_id 단독 조회 불가
    conn.execute(text("""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_device_groups_group_id')
          THEN CREATE INDEX idx_device_groups_group_id ON device_groups (group_id); END IF;
        END $$
    """))
    # service_metrics (device_id, bucket) 복합 인덱스 — 장비별 시계열 쿼리
    conn.execute(text("""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_service_metrics_device_bucket')
          THEN CREATE INDEX idx_service_metrics_device_bucket ON service_metrics (device_id, bucket); END IF;
        END $$
    """))
    # service_alerts (device_id, created_at) 복합 인덱스 — 장비별 알림 조회
    conn.execute(text("""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_service_alerts_device_created')
          THEN CREATE INDEX idx_service_alerts_device_created ON service_alerts (device_id, created_at); END IF;
        END $$
    """))
    # service_alerts.notified 부분 인덱스 — 미발송 알림 polling
    conn.execute(text("""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_service_alerts_notified')
          THEN CREATE INDEX idx_service_alerts_notified ON service_alerts (notified) WHERE notified = FALSE; END IF;
        END $$
    """))
    # ftp_logs.row_hash 컬럼 — 재전송 중복 방지용 MD5 식별키
    conn.execute(text("""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                         WHERE table_name='ftp_logs' AND column_name='row_hash')
          THEN ALTER TABLE ftp_logs ADD COLUMN row_hash VARCHAR(32); END IF;
        END $$
    """))
    # 인덱스가 없을 때만 한 번 실행: 중복 제거 → 백필 → 유니크 인덱스 생성
    # 순서가 중요: 기존 중복 행을 먼저 제거해야 백필 후 인덱스 생성이 실패하지 않음
    conn.execute(text("""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_ftp_logs_dedup') THEN
            -- 1) 기존 중복 행 제거: 동일 내용이면 id 낮은 쪽(먼저 들어온 행) 유지
            DELETE FROM ftp_logs
            WHERE id IN (
              SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                  PARTITION BY
                    device_id,
                    to_char(log_time AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'),
                    coalesce(username, ''), action,
                    coalesce(file_path, ''), coalesce(file_size::text, '0'),
                    coalesce(session_id, ''), coalesce(client_ip, '')
                  ORDER BY id
                ) AS rn
                FROM ftp_logs
                WHERE log_time >= now() - interval '90 days'
              ) t
              WHERE rn > 1
            );
            -- 2) 백필: Python _row_hash()와 동일한 필드·순서로 MD5 계산
            UPDATE ftp_logs SET row_hash = md5(
              device_id::text || '|' ||
              to_char(log_time AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || '|' ||
              coalesce(username, '') || '|' || action || '|' ||
              coalesce(file_path, '') || '|' ||
              coalesce(file_size::text, '0') || '|' ||
              coalesce(session_id, '') || '|' ||
              coalesce(client_ip, '')
            ) WHERE row_hash IS NULL AND log_time >= now() - interval '90 days';
            -- 3) 유니크 인덱스 생성 — 파티션 키(log_time) 포함으로 파티셔닝 테이블 호환
            CREATE UNIQUE INDEX idx_ftp_logs_dedup ON ftp_logs (device_id, log_time, row_hash);
          END IF;
        END $$
    """))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # pg_trgm 확장을 create_all 전에 보장 — username GIN(gin_trgm_ops) 인덱스 생성 선행 조건
    with engine.begin() as conn:
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pg_trgm"'))
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        _run_migrations(conn)
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
        _ensure_partitions(db)
    wb.init_buffer(SessionLocal, flush_interval=3, max_size=2000)
    sm.init_monitor(SessionLocal)
    global _INDEX_HTML
    _INDEX_HTML = _load_index_html()
    yield
    sm.shutdown_monitor()
    wb.shutdown_buffer()


app = FastAPI(
    title="SolTrace - FTP Log Analyzer",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    _CSP = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "img-src 'self' data:; "
        "font-src 'self' https://cdn.jsdelivr.net data:; "
        "connect-src 'self';"
    )

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = self._CSP
        return response


app.add_middleware(SecurityHeadersMiddleware)

app.include_router(auth.router)
app.include_router(ingest.router)
app.include_router(devices.router)
app.include_router(groups.router)
app.include_router(logs.router)
app.include_router(dashboard.router)
app.include_router(settings.router)
app.include_router(telcos.router)

app.mount("/static", StaticFiles(directory="static"), name="static")


def _git_short_hash() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return "0"


_INDEX_HTML: str = ""


def _load_index_html() -> str:
    return Path("static/index.html").read_text(encoding="utf-8").replace("__VER__", _git_short_hash())


@app.get("/", include_in_schema=False)
@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str = ""):
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404)
    return HTMLResponse(
        content=_INDEX_HTML,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"},
    )
