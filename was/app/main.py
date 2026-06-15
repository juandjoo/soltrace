from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.database import SessionLocal, engine
from app.models import Base
from app.routers import auth, dashboard, devices, groups, ingest, logs
from app import write_buffer as wb


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
    wb.init_buffer(SessionLocal, flush_interval=3, max_size=2000)
    yield
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

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", include_in_schema=False)
@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str = ""):
    if full_path.startswith("api/"):
        from fastapi import HTTPException
        raise HTTPException(status_code=404)
    return FileResponse("static/index.html")
