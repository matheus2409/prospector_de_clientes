import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict

from sqlmodel import Session, select

from .config import settings
from .database import engine
from .models import Job, Lead, JobLink, Schedule
from . import places_api
from . import scraper as scraper_module
from .webhook import send_job_webhook

_queues: Dict[str, asyncio.Queue] = {}
_cancel_flags: Dict[str, bool] = {}


def active_backend_name() -> str:
    """Qual motor de busca está ativo, pra exibir na UI e nos logs."""
    return "places_api" if settings.GOOGLE_PLACES_API_KEY else "scraper"


def _search_fn():
    """scrape_google_maps (Playwright) e search_google_places (Places API)
    têm exatamente a mesma assinatura de parâmetros — por isso dá pra trocar
    qual delas usar sem o resto de jobs.py precisar saber a diferença."""
    if settings.GOOGLE_PLACES_API_KEY:
        return places_api.search_google_places
    return scraper_module.scrape_google_maps


def get_queue(job_id: str) -> asyncio.Queue:
    if job_id not in _queues:
        _queues[job_id] = asyncio.Queue()
    return _queues[job_id]


def reset_queue(job_id: str) -> asyncio.Queue:
    """Esvazia a fila do job_id SEM trocar o objeto Queue.

    Bug corrigido aqui: a versão antiga fazia `_queues[job_id] = asyncio.Queue()`,
    criando um objeto NOVO. Se o frontend já tivesse conectado no /stream (e portanto
    já capturado uma referência à fila antiga) bem no instante entre o job começar
    e essa troca acontecer, ele ficava escutando pra sempre numa fila que ninguém
    mais alimentava — o log/tabela travava sem nenhum erro visível, mesmo com o job
    rodando normalmente no backend. Mantendo o MESMO objeto (só esvaziando) e sempre
    lendo `_queues[job_id]` "ao vivo" via get_queue()/_push(), qualquer consumidor
    conectado antes ou depois do reset enxerga as mesmas mensagens."""
    q = get_queue(job_id)
    while not q.empty():
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            break
    return q


def cancel_job(job_id: str):
    _cancel_flags[job_id] = True


def is_resumable(job_id: str) -> bool:
    with Session(engine) as session:
        pending = session.exec(
            select(JobLink).where(JobLink.job_id == job_id, JobLink.status == "pending")
        ).all()
        return len(pending) > 0


def pending_count(job_id: str) -> int:
    with Session(engine) as session:
        pending = session.exec(
            select(JobLink).where(JobLink.job_id == job_id, JobLink.status == "pending")
        ).all()
        return len(pending)


def running_job(session: Session) -> Job | None:
    """Retorna o job com status 'running', se houver algum (usado pra impedir
    duas varreduras simultâneas — rodar dois Chromiums ao mesmo tempo pela mesma
    máquina/IP só aumenta o risco de bloqueio do Google à toa)."""
    return session.exec(select(Job).where(Job.status == "running")).first()


def recover_orphaned_jobs():
    """Roda uma vez na subida do servidor. Se o processo foi encerrado no meio
    de uma varredura (Ctrl+C, queda de energia, fechar a janela do run.bat), o
    job correspondente fica preso com status 'running' pra sempre no banco —
    ninguém nunca chama _finalize() pra ele. Isso trava a UI (o botão "Iniciar
    Varredura" global fica bloqueado achando que ainda há algo rodando) e o
    deixa sem um jeito de ser retomado. Aqui, todo job 'running' encontrado ao
    iniciar é rebaixado pra 'partial': os JobLink que ainda estiverem 'pending'
    continuam retomáveis normalmente pelo botão "Retomar"."""
    with Session(engine) as session:
        orphaned = session.exec(select(Job).where(Job.status == "running")).all()
        for job in orphaned:
            job.status = "partial"
            job.finished_at = datetime.now(timezone.utc)
            session.add(job)
        if orphaned:
            session.commit()
        return len(orphaned)


async def _run_due_schedules_once():
    """Verifica se alguma busca agendada está na hora de rodar. Só dispara
    uma por vez, e só se não houver nenhuma varredura em andamento — a mesma
    regra de "uma por vez" que vale pro resto do app (evita brigar por
    recursos/IP com uma busca manual que o usuário tenha acabado de iniciar)."""
    now = datetime.now(timezone.utc)
    job_id = job_query = job_location = None
    job_max_results = None
    with Session(engine) as session:
        if running_job(session):
            return
        due = session.exec(
            select(Schedule).where(Schedule.active == True, Schedule.next_run_at <= now)  # noqa: E712
        ).first()
        if not due:
            return
        job = Job(query=due.query, location=due.location, max_results=due.max_results)
        session.add(job)
        due.last_run_at = now
        due.last_job_id = job.id
        due.next_run_at = now + timedelta(days=max(due.frequency_days, 1))
        session.add(due)
        session.commit()
        # Extrai os valores AGORA, ainda dentro do "with" — depois do commit()
        # o objeto "job" fica "expirado" (comportamento padrão do SQLAlchemy) e
        # ler um atributo dele DEPOIS que a sessão fechar levantaria
        # DetachedInstanceError.
        job_id, job_query, job_location, job_max_results = job.id, job.query, job.location, job.max_results
    asyncio.create_task(run_job(job_id, job_query, job_location, job_max_results))


async def scheduler_loop():
    """Roda em background desde a subida do servidor, verificando a cada
    minuto se alguma busca agendada está vencida. Fica preso num loop
    infinito de propósito — é uma asyncio task de fundo, não uma função que
    deve retornar."""
    while True:
        try:
            await _run_due_schedules_once()
        except Exception:
            pass
        await asyncio.sleep(60)


async def _push(job_id: str, payload: dict):
    await get_queue(job_id).put(payload)


def _make_callbacks(job_id: str):
    async def log(msg: str):
        await _push(job_id, {"type": "log", "message": msg})

    async def on_lead(data: dict):
        with Session(engine) as session:
            lead = Lead(job_id=job_id, **data)
            session.add(lead)
            session.commit()
            session.refresh(lead)
            # Serializa o lead JÁ SALVO (com id, job_id e scraped_at incluídos),
            # não o dict cru vindo do scraper — assim o evento SSE "lead" tem o
            # mesmo formato que /api/leads devolve, e o id existe desde o primeiro
            # instante em que o lead aparece na tela (necessário pra poder excluir
            # um lead ao vivo, sem precisar recarregar a página primeiro).
            payload = lead.model_dump(mode="json")
        await _push(job_id, {"type": "lead", "lead": payload})

    async def on_links_collected(hrefs):
        with Session(engine) as session:
            links = [
                JobLink(job_id=job_id, href=h, order_index=i, status="pending")
                for i, h in enumerate(hrefs)
            ]
            session.add_all(links)
            session.commit()
            for l in links:
                session.refresh(l)
            return [(l.id, l.href) for l in links]

    async def on_link_result(link_id: int, success: bool):
        with Session(engine) as session:
            jl = session.get(JobLink, link_id)
            if jl:
                jl.status = "done" if success else "failed"
                session.add(jl)
                session.commit()

    def should_cancel() -> bool:
        return _cancel_flags.get(job_id, False)

    return log, on_lead, on_links_collected, on_link_result, should_cancel


async def _finalize(job_id: str, status: str):
    total = 0
    with Session(engine) as session:
        job = session.get(Job, job_id)
        job.status = status
        job.finished_at = datetime.now(timezone.utc)
        total = len(session.exec(select(Lead).where(Lead.job_id == job_id)).all())
        job.total_found = total
        session.add(job)
        session.commit()

    await _push(job_id, {"type": "status", "status": status, "total_found": total})
    await _push(job_id, {"type": "done"})

    if status == "completed":
        await send_job_webhook(job_id)


async def run_job(job_id: str, query: str, location: str, max_results: int):
    _cancel_flags[job_id] = False
    reset_queue(job_id)
    log, on_lead, on_links_collected, on_link_result, should_cancel = _make_callbacks(job_id)

    with Session(engine) as session:
        job = session.get(Job, job_id)
        job.status = "running"
        session.add(job)
        session.commit()

    status = "failed"
    try:
        search_fn = _search_fn()
        status = await search_fn(
            query=query,
            location=location,
            max_results=max_results,
            log=log,
            on_lead=on_lead,
            on_links_collected=on_links_collected,
            on_link_result=on_link_result,
            should_cancel=should_cancel,
        )
    except Exception as e:
        await _push(job_id, {"type": "log", "message": f"Erro fatal na varredura: {e}"})
        status = "failed"

    await _finalize(job_id, status)


async def resume_job(job_id: str):
    _cancel_flags[job_id] = False
    reset_queue(job_id)
    log, on_lead, _on_links_collected, on_link_result, should_cancel = _make_callbacks(job_id)

    with Session(engine) as session:
        job = session.get(Job, job_id)
        if not job:
            return
        pending = session.exec(
            select(JobLink)
            .where(JobLink.job_id == job_id, JobLink.status == "pending")
            .order_by(JobLink.order_index)
        ).all()
        links_with_ids = [(l.id, l.href) for l in pending]
        job.status = "running"
        session.add(job)
        session.commit()

    if not links_with_ids:
        await log("Nada pendente pra retomar — todos os itens já foram processados.")
        await _finalize(job_id, "completed")
        return

    status = "failed"
    try:
        status = await scraper_module.resume_scrape(
            links_with_ids=links_with_ids,
            log=log,
            on_lead=on_lead,
            on_link_result=on_link_result,
            should_cancel=should_cancel,
        )
    except Exception as e:
        await _push(job_id, {"type": "log", "message": f"Erro fatal ao retomar: {e}"})
        status = "failed"

    await _finalize(job_id, status)
