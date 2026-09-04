import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware

from app.database import SessionLocal, engine
from app.gitinfo import git
from app.migrations import run_migrations
from app.models import Base
from app.security import ensure_admin_user
from app.routers import (
    api_keys, auth, dashboard, devices, groups, ingest, logs, settings, telcos, users,
)
from app import retention
from app import write_buffer as wb
from app import service_monitor as sm

log = logging.getLogger("soltrace.main")

# ftp_logs 월별 파티션 자동 생성 범위. 과거 범위는 보존 기간(app/retention.py)을 따른다
# — 보존 기간 안의 과거 로그(bulk import)가 default 파티션으로 빠지지 않게.
# init.sql / create_partitions.sh 의 초기 범위와 함께 볼 것.
PARTITION_FUTURE_MONTHS = 2


def _ensure_partitions(db):
    """과거 보존기간 + 당월 + 향후 PARTITION_FUTURE_MONTHS 파티션이 없으면 생성."""
    past_months = retention.get_retention_months(db)
    is_partitioned = db.execute(text(
        "SELECT COUNT(*) FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relname = 'ftp_logs' AND c.relkind = 'p' AND n.nspname = 'public'"
    )).scalar()
    if not is_partitioned:
        return

    now = datetime.now(timezone.utc)
    for offset in range(-past_months, PARTITION_FUTURE_MONTHS + 1):
        total = now.month - 1 + offset
        year, month = now.year + total // 12, total % 12 + 1
        next_total = total + 1
        ny, nm = now.year + next_total // 12, next_total % 12 + 1
        part = f"ftp_logs_{year:04d}_{month:02d}"
        start = f"{year:04d}-{month:02d}-01"
        end   = f"{ny:04d}-{nm:02d}-01"
        # ftp_logs_default에 해당 월 데이터가 있으면 파티션 생성 불가 (CheckViolation)
        # → 데이터가 없는 월만 파티션 생성, 있는 월은 rebalance_default_partition.sql 실행 후 적용
        has_data = db.execute(text(
            "SELECT EXISTS(SELECT 1 FROM ftp_logs_default "
            "WHERE log_time >= :s AND log_time < :e LIMIT 1)"
        ), {"s": start, "e": end}).scalar()
        if not has_data:
            db.execute(text(
                f"CREATE TABLE IF NOT EXISTS {part} PARTITION OF ftp_logs "
                f"FOR VALUES FROM ('{start}') TO ('{end}')"
            ))

    db.execute(text(
        "CREATE TABLE IF NOT EXISTS ftp_logs_default PARTITION OF ftp_logs DEFAULT"
    ))
    db.commit()

    # default 파티션에 행이 남아 있으면 해당 월 파티션이 생성되지 못하고(위 has_data 스킵)
    # 백업 스크립트의 보존기간 DROP 대상에서도 빠진다 → 기동 시 경고로 가시화.
    # 정확한 COUNT 대신 통계 추정치(reltuples)를 써서 대량일 때 기동을 늦추지 않는다.
    est = db.execute(text(
        "SELECT reltuples::bigint FROM pg_class WHERE relname = 'ftp_logs_default'"
    )).scalar() or 0
    if est > 0:
        log.warning(
            "ftp_logs_default 에 약 %s 행이 남아 있습니다. "
            "설정 > DB 저장소에서 확인 후 scripts/rebalance_default_partition.sql 로 재배치하세요.",
            f"{est:,}",
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # pg_trgm 확장을 create_all 전에 보장 — username GIN(gin_trgm_ops) 인덱스 생성 선행 조건
    with engine.begin() as conn:
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pg_trgm"'))
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        run_migrations(conn)
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
        # app_config 로만 있던 부트스트랩 관리자를 users 로 이관 (최초 1회)
        ensure_admin_user(db)
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
    # 스키마/문서 비공개 — openapi_url 을 끄지 않으면 docs_url 을 꺼도 /openapi.json 이
    # 인증 없이 열려 전체 엔드포인트·파라미터가 노출된다.
    openapi_url=None,
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
app.include_router(users.router)
app.include_router(api_keys.router)

app.mount("/static", StaticFiles(directory="static"), name="static")


_INDEX_HTML: str = ""


def _load_index_html() -> str:
    # __VER__ → 커밋 해시: 정적 JS/CSS 캐시 버스팅
    ver = git("rev-parse", "--short", "HEAD") or "0"
    return Path("static/index.html").read_text(encoding="utf-8").replace("__VER__", ver)


@app.get("/", include_in_schema=False)
@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str = ""):
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404)
    return HTMLResponse(
        content=_INDEX_HTML,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"},
    )
