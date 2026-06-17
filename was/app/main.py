from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

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

app.include_router(auth.router)
app.include_router(ingest.router)
app.include_router(devices.router)
app.include_router(groups.router)
app.include_router(logs.router)
app.include_router(dashboard.router)
app.include_router(settings.router)
app.include_router(telcos.router)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", include_in_schema=False)
@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str = ""):
    if full_path.startswith("api/"):
        from fastapi import HTTPException
        raise HTTPException(status_code=404)
    return FileResponse(
        "static/index.html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"},
    )
