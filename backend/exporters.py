import csv
import io
import re
import unicodedata
from datetime import datetime, timezone
from typing import Optional

from openpyxl import Workbook
from sqlmodel import Session, select

from .config import settings
from .models import Job, Lead

FIELDS = ["name", "category", "phone", "website", "address", "rating", "review_count", "maps_url"]
HEADERS = ["Nome", "Categoria", "Telefone", "Website", "Endereço", "Avaliação", "Nº Avaliações", "Link Maps"]
DEDUPE_HEADER_EXTRA = "Visto em (buscas)"


def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "leads"


def export_filename(session: Session, job_id: Optional[str], ext: str, dedupe: bool = False) -> str:
    """Nome de arquivo pra exportação: <nicho>_<localização>_<data>.<ext> quando
    dá pra identificar a busca, ou leads-todos_<data>.<ext> / leads_<data>.<ext>
    pra exportação agregada (sem job_id — ex: todos os leads de todas as
    varreduras, com ou sem deduplicação)."""
    date_part = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if job_id:
        job = session.get(Job, job_id)
        if job:
            return f"{_slugify(job.query)}_{_slugify(job.location)}_{date_part}.{ext}"
    prefix = "leads-todos-dedup" if dedupe else "leads"
    return f"{prefix}_{date_part}.{ext}"


def _place_key(lead: Lead) -> str:
    """Chave de deduplicação entre buscas diferentes: tenta extrair um
    identificador estável do Google embutido na URL do Maps (o hexadecimal de
    CID, ou um place_id explícito); se não achar, cai pra nome+endereço
    normalizados (sem acento, minúsculo, espaços colapsados). Comparar a URL
    inteira não é confiável pra leads raspados via scraper.py, que às vezes
    carregam parâmetros de sessão que variam entre uma busca e outra mesmo
    sendo o mesmíssimo estabelecimento."""
    if lead.maps_url:
        m = re.search(r"0x[0-9a-f]+:0x[0-9a-f]+", lead.maps_url, re.IGNORECASE)
        if m:
            return f"cid:{m.group(0).lower()}"
        m = re.search(r"place_id:([\w-]+)", lead.maps_url)
        if m:
            return f"pid:{m.group(1)}"

    def norm(s):
        s = unicodedata.normalize("NFKD", (s or "").lower()).encode("ascii", "ignore").decode("ascii")
        return re.sub(r"\s+", " ", s).strip()

    return f"na:{norm(lead.name)}|{norm(lead.address)}"


def dedupe_leads(leads: list) -> list:
    """Agrupa leads que representam o mesmo estabelecimento (visto em buscas
    diferentes) e devolve um representante por grupo: o mais completo (mais
    campos preenchidos) e, em empate, o mais recente. Retorna pares
    (lead, quantas vezes apareceu no total)."""
    groups: dict[str, list] = {}
    for l in leads:
        groups.setdefault(_place_key(l), []).append(l)

    def completeness(l: Lead) -> int:
        return sum(1 for f in ("phone", "website", "rating", "category") if getattr(l, f))

    pairs = []
    for group in groups.values():
        best = max(group, key=lambda l: (completeness(l), l.scraped_at))
        pairs.append((best, len(group)))
    pairs.sort(key=lambda p: p[0].scraped_at)
    return pairs


def _get_leads(
    session: Session,
    job_id: Optional[str] = None,
    only_no_website: bool = False,
    dedupe: bool = False,
) -> list:
    """Retorna uma lista de pares (lead, quantas_vezes_visto). Sem dedupe,
    quantas_vezes_visto é sempre 1 — assim o resto do código (exportações)
    não precisa de dois caminhos diferentes."""
    stmt = select(Lead)
    if job_id:
        stmt = stmt.where(Lead.job_id == job_id)
    stmt = stmt.order_by(Lead.scraped_at)
    leads = session.exec(stmt).all()
    if only_no_website:
        leads = [l for l in leads if not l.website]
    if dedupe:
        return dedupe_leads(leads)
    return [(l, 1) for l in leads]


def _row(lead: Lead, count: int, dedupe: bool) -> list:
    row = [getattr(lead, f) if getattr(lead, f) is not None else "" for f in FIELDS]
    if dedupe:
        row.append(count)
    return row


def export_csv(session: Session, job_id: Optional[str] = None, only_no_website: bool = False, dedupe: bool = False) -> str:
    pairs = _get_leads(session, job_id, only_no_website, dedupe)
    output = io.StringIO()
    # BOM UTF-8: sem isso, o Excel no Windows abre o CSV assumindo ANSI/CP1252
    # e nomes/endereços com acento (ex: "São José do Rio Preto") viram mojibake.
    # Com o BOM, o Excel detecta UTF-8 corretamente ao dar duplo clique no arquivo.
    output.write("\ufeff")
    writer = csv.writer(output)
    writer.writerow(HEADERS + ([DEDUPE_HEADER_EXTRA] if dedupe else []))
    for lead, count in pairs:
        writer.writerow(_row(lead, count, dedupe))
    return output.getvalue()


def export_xlsx(session: Session, job_id: Optional[str] = None, only_no_website: bool = False, dedupe: bool = False) -> bytes:
    pairs = _get_leads(session, job_id, only_no_website, dedupe)
    wb = Workbook()
    ws = wb.active
    ws.title = "Leads"
    ws.append(HEADERS + ([DEDUPE_HEADER_EXTRA] if dedupe else []))
    for lead, count in pairs:
        ws.append(_row(lead, count, dedupe))

    for col in ws.columns:
        values = [str(c.value) for c in col if c.value not in (None, "")]
        width = max((len(v) for v in values), default=10)
        ws.column_dimensions[col[0].column_letter].width = min(width + 2, 50)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_google_sheets(
    session: Session,
    job_id: Optional[str],
    spreadsheet_id: Optional[str],
    only_no_website: bool = False,
    dedupe: bool = False,
) -> str:
    """Envia os leads pra uma planilha do Google Sheets via service account.
    Se spreadsheet_id não for passado, cria uma planilha nova.
    Requer GOOGLE_SERVICE_ACCOUNT_FILE configurado no .env (veja README).
    """
    import gspread
    from google.oauth2.service_account import Credentials

    if not settings.GOOGLE_SERVICE_ACCOUNT_FILE:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_FILE não configurado no .env. "
            "Veja o README para os passos de configuração do Google Sheets."
        )

    creds = Credentials.from_service_account_file(
        settings.GOOGLE_SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    gc = gspread.authorize(creds)

    if spreadsheet_id:
        sh = gc.open_by_key(spreadsheet_id)
    else:
        sh = gc.create("Leads - Prospecção Maps")

    ws = sh.sheet1
    pairs = _get_leads(session, job_id, only_no_website, dedupe)
    rows = [HEADERS + ([DEDUPE_HEADER_EXTRA] if dedupe else [])]
    rows += [_row(lead, count, dedupe) for lead, count in pairs]
    ws.clear()
    ws.update(rows)

    return sh.url
