import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from . import exporters
from .config import settings
from .database import get_session, init_db
from .jobs import (
    active_backend_name,
    cancel_job,
    get_queue,
    is_resumable,
    pending_count,
    recover_orphaned_jobs,
    resume_job,
    run_job,
    running_job,
    scheduler_loop,
)
from .models import LEAD_STATUSES, Job, Lead, Schedule

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    recover_orphaned_jobs()
    task = asyncio.create_task(scheduler_loop())
    yield
    task.cancel()


app = FastAPI(title="Prospector Maps", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


def check_api_key(x_api_key: Optional[str] = Header(default=None)):
    if settings.API_KEY and x_api_key != settings.API_KEY:
        raise HTTPException(status_code=401, detail="API key inválida ou ausente (header X-API-Key)")


class JobCreate(BaseModel):
    query: str = Field(min_length=1)
    location: str = Field(min_length=1)
    max_results: Optional[int] = Field(default=None, ge=1, le=settings.MAX_RESULTS_HARD_CAP)


class SheetsExportRequest(BaseModel):
    job_id: Optional[str] = None
    spreadsheet_id: Optional[str] = None
    only_no_website: bool = False
    dedupe: bool = False


@app.get("/", response_class=HTMLResponse)
def index():
    return (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/api/status")
def status():
    return {
        "n8n_webhook_configured": bool(settings.N8N_WEBHOOK_URL),
        "google_sheets_configured": bool(settings.GOOGLE_SERVICE_ACCOUNT_FILE),
        "api_key_protected": bool(settings.API_KEY),
        "headless": settings.HEADLESS,
        "max_results_hard_cap": settings.MAX_RESULTS_HARD_CAP,
        "active_backend": active_backend_name(),
        "lead_statuses": LEAD_STATUSES,
    }


@app.post("/api/jobs", dependencies=[Depends(check_api_key)])
def create_job(payload: JobCreate, background_tasks: BackgroundTasks, session: Session = Depends(get_session)):
    existing = running_job(session)
    if existing:
        raise HTTPException(
            409,
            f"Já existe uma varredura em andamento (\"{existing.query}\" em \"{existing.location}\"). "
            "Aguarde terminar ou cancele antes de iniciar outra.",
        )
    job = Job(
        query=payload.query.strip(),
        location=payload.location.strip(),
        max_results=payload.max_results or settings.MAX_RESULTS_DEFAULT,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    background_tasks.add_task(run_job, job.id, job.query, job.location, job.max_results)
    return {"job_id": job.id}


@app.get("/api/jobs", dependencies=[Depends(check_api_key)])
def list_jobs(session: Session = Depends(get_session)):
    return session.exec(select(Job).order_by(Job.created_at.desc())).all()


@app.get("/api/jobs/{job_id}", dependencies=[Depends(check_api_key)])
def get_job(job_id: str, session: Session = Depends(get_session)):
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job não encontrado")
    return job


@app.post("/api/jobs/{job_id}/cancel", dependencies=[Depends(check_api_key)])
def cancel(job_id: str):
    cancel_job(job_id)
    return {"ok": True}


@app.post("/api/jobs/{job_id}/resume", dependencies=[Depends(check_api_key)])
def resume(job_id: str, background_tasks: BackgroundTasks, session: Session = Depends(get_session)):
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job não encontrado")
    if not is_resumable(job_id):
        raise HTTPException(400, "Não há itens pendentes pra retomar nesse job")
    background_tasks.add_task(resume_job, job_id)
    return {"ok": True}


@app.get("/api/jobs/{job_id}/resumable", dependencies=[Depends(check_api_key)])
def resumable(job_id: str, session: Session = Depends(get_session)):
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job não encontrado")
    count = pending_count(job_id)
    return {"resumable": count > 0, "pending_count": count}


@app.get("/api/jobs/{job_id}/stream")
async def stream_job(job_id: str):
    async def event_gen():
        q = get_queue(job_id)
        while True:
            msg = await q.get()
            yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
            if msg.get("type") == "done":
                break

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/leads", dependencies=[Depends(check_api_key)])
def list_leads(
    job_id: Optional[str] = None,
    only_no_website: bool = False,
    status: Optional[str] = None,
    dedupe: bool = False,
    session: Session = Depends(get_session),
):
    stmt = select(Lead)
    if job_id:
        stmt = stmt.where(Lead.job_id == job_id)
    if status:
        stmt = stmt.where(Lead.status == status)
    stmt = stmt.order_by(Lead.scraped_at)
    leads = session.exec(stmt).all()
    if only_no_website:
        leads = [l for l in leads if not l.website]
    if not dedupe:
        return leads
    # Modo deduplicado: agrupa o mesmo estabelecimento visto em buscas
    # diferentes e devolve dicts (não objetos SQLModel) com duplicate_count.
    out = []
    for lead, count in exporters.dedupe_leads(leads):
        d = lead.model_dump(mode="json")
        d["duplicate_count"] = count
        out.append(d)
    return out


class LeadStatusUpdate(BaseModel):
    status: str


@app.patch("/api/leads/{lead_id}/status", dependencies=[Depends(check_api_key)])
def update_lead_status(lead_id: int, payload: LeadStatusUpdate, session: Session = Depends(get_session)):
    if payload.status not in LEAD_STATUSES:
        raise HTTPException(400, f"Status inválido. Use um de: {', '.join(LEAD_STATUSES)}")
    lead = session.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Lead não encontrado")
    lead.status = payload.status
    session.add(lead)
    session.commit()
    session.refresh(lead)
    return lead


@app.delete("/api/leads/{lead_id}", dependencies=[Depends(check_api_key)])
def delete_lead(lead_id: int, session: Session = Depends(get_session)):
    lead = session.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Lead não encontrado")
    session.delete(lead)
    session.commit()
    return {"ok": True}


class ScheduleCreate(BaseModel):
    query: str = Field(min_length=1)
    location: str = Field(min_length=1)
    max_results: Optional[int] = Field(default=None, ge=1, le=settings.MAX_RESULTS_HARD_CAP)
    frequency_days: int = Field(default=7, ge=1, le=90)


class ScheduleUpdate(BaseModel):
    active: Optional[bool] = None
    frequency_days: Optional[int] = Field(default=None, ge=1, le=90)


@app.post("/api/schedules", dependencies=[Depends(check_api_key)])
def create_schedule(payload: ScheduleCreate, session: Session = Depends(get_session)):
    now = datetime.now(timezone.utc)
    schedule = Schedule(
        query=payload.query.strip(),
        location=payload.location.strip(),
        max_results=payload.max_results or settings.MAX_RESULTS_DEFAULT,
        frequency_days=payload.frequency_days,
        next_run_at=now + timedelta(days=payload.frequency_days),
    )
    session.add(schedule)
    session.commit()
    session.refresh(schedule)
    return schedule


@app.get("/api/schedules", dependencies=[Depends(check_api_key)])
def list_schedules(session: Session = Depends(get_session)):
    return session.exec(select(Schedule).order_by(Schedule.created_at.desc())).all()


@app.patch("/api/schedules/{schedule_id}", dependencies=[Depends(check_api_key)])
def update_schedule(schedule_id: int, payload: ScheduleUpdate, session: Session = Depends(get_session)):
    schedule = session.get(Schedule, schedule_id)
    if not schedule:
        raise HTTPException(404, "Agendamento não encontrado")
    if payload.active is not None:
        schedule.active = payload.active
    if payload.frequency_days is not None:
        schedule.frequency_days = payload.frequency_days
    session.add(schedule)
    session.commit()
    session.refresh(schedule)
    return schedule


@app.delete("/api/schedules/{schedule_id}", dependencies=[Depends(check_api_key)])
def delete_schedule(schedule_id: int, session: Session = Depends(get_session)):
    schedule = session.get(Schedule, schedule_id)
    if not schedule:
        raise HTTPException(404, "Agendamento não encontrado")
    session.delete(schedule)
    session.commit()
    return {"ok": True}


@app.get("/api/export/csv", dependencies=[Depends(check_api_key)])
def export_csv(job_id: Optional[str] = None, only_no_website: bool = False, dedupe: bool = False, session: Session = Depends(get_session)):
    content = exporters.export_csv(session, job_id, only_no_website, dedupe)
    filename = exporters.export_filename(session, job_id, "csv", dedupe)
    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/export/xlsx", dependencies=[Depends(check_api_key)])
def export_xlsx(job_id: Optional[str] = None, only_no_website: bool = False, dedupe: bool = False, session: Session = Depends(get_session)):
    content = exporters.export_xlsx(session, job_id, only_no_website, dedupe)
    filename = exporters.export_filename(session, job_id, "xlsx", dedupe)
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/export/sheets", dependencies=[Depends(check_api_key)])
def export_sheets(payload: SheetsExportRequest, session: Session = Depends(get_session)):
    try:
        url = exporters.export_google_sheets(
            session, payload.job_id, payload.spreadsheet_id, payload.only_no_website, payload.dedupe
        )
        return {"url": url}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/webhook/resend/{job_id}", dependencies=[Depends(check_api_key)])
async def resend_webhook(job_id: str):
    from .webhook import send_job_webhook
    ok = await send_job_webhook(job_id)
    if not ok:
        raise HTTPException(400, "Falha ao enviar pro webhook (verifique N8N_WEBHOOK_URL no .env)")
    return {"ok": True}
