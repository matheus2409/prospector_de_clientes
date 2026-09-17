import httpx
from sqlmodel import Session, select

from .database import engine
from .models import Job, Lead
from .config import settings


def _lead_payload(l: Lead) -> dict:
    return {
        "name": l.name,
        "category": l.category,
        "phone": l.phone,
        "website": l.website,
        "address": l.address,
        "rating": l.rating,
        "review_count": l.review_count,
        "maps_url": l.maps_url,
        "status": l.status,
    }


async def send_job_webhook(job_id: str) -> bool:
    """Envia os leads de um job pro webhook do n8n configurado no .env.
    Retorna True se enviou (ou se não há webhook configurado), False se falhou.
    """
    if not settings.N8N_WEBHOOK_URL:
        return True

    with Session(engine) as session:
        job = session.get(Job, job_id)
        if not job:
            return False
        leads = session.exec(select(Lead).where(Lead.job_id == job_id)).all()
        payload = {
            "job_id": job_id,
            "query": job.query,
            "location": job.location,
            "total_found": len(leads),
            "leads": [_lead_payload(l) for l in leads],
        }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(settings.N8N_WEBHOOK_URL, json=payload)
            return resp.status_code < 400
    except Exception:
        return False
