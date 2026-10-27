from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import admin, allocations, auth, calendar, dashboard, feedback, masters, meta, requests
from app.bootstrap import bootstrap_demo_data
from app.core.config import settings
from app.core.database import Base, SessionLocal, engine
from app.core.middleware import SecurityHeadersMiddleware
from app.core.schema_upgrade import apply_local_schema_upgrades
from app.jobs.auto_deallocate import run_auto_deallocation

scheduler = None


def scheduled_auto_deallocate():
    db = SessionLocal()
    try:
        run_auto_deallocation(db)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    apply_local_schema_upgrades(engine)
    bootstrap_demo_data()
    global scheduler
    if settings.enable_internal_scheduler:
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
        except ImportError as exc:
            raise RuntimeError("APScheduler is required when ENABLE_INTERNAL_SCHEDULER=true") from exc
        scheduler = AsyncIOScheduler()
        scheduler.add_job(scheduled_auto_deallocate, "cron", hour=settings.auto_deallocate_hour, minute=0, id="auto-deallocate", replace_existing=True)
        scheduler.start()
    yield
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

prefix = settings.api_prefix
app.include_router(auth.router, prefix=prefix)
app.include_router(meta.router, prefix=prefix)
app.include_router(masters.router, prefix=prefix)
app.include_router(dashboard.router, prefix=prefix)
app.include_router(allocations.router, prefix=prefix)
app.include_router(calendar.router, prefix=prefix)
app.include_router(requests.router, prefix=prefix)
app.include_router(feedback.router, prefix=prefix)
app.include_router(admin.router, prefix=prefix)


@app.get("/health")
def health():
    return {"status": "ok"}
