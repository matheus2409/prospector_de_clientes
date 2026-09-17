import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlmodel import SQLModel, Field

from .database import UTCDateTime


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Job(SQLModel, table=True):
    """Uma varredura (busca) disparada pelo usuário."""

    id: str = Field(default_factory=new_id, primary_key=True)
    query: str                      # nicho/categoria buscado, ex: "barbearia"
    location: str                   # localização, ex: "São José do Rio Preto, SP"
    max_results: int = 60
    status: str = "pending"         # pending | running | completed | failed | cancelled
    total_found: int = 0
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
    finished_at: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)


LEAD_STATUSES = ["novo", "contatado", "interessado", "sem_interesse", "fechado"]


class Lead(SQLModel, table=True):
    """Um lead (estabelecimento) extraído do Google Maps."""

    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: str = Field(index=True, foreign_key="job.id")
    name: str
    category: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    rating: Optional[float] = None
    review_count: Optional[int] = None
    maps_url: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    status: str = Field(default="novo", index=True)  # novo | contatado | interessado | sem_interesse | fechado
    scraped_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)


class Schedule(SQLModel, table=True):
    """Uma busca recorrente: repete a mesma varredura a cada N dias, sozinha,
    enquanto o servidor local estiver rodando."""

    id: Optional[int] = Field(default=None, primary_key=True)
    query: str
    location: str
    max_results: int = 60
    frequency_days: int = 7
    active: bool = Field(default=True)
    last_run_at: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)
    last_job_id: Optional[str] = None
    next_run_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
    created_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)


class JobLink(SQLModel, table=True):
    """Cada link de estabelecimento coletado na varredura de um job, com seu
    status individual. Permite retomar um job interrompido (cancelado, pausado
    pelo circuit breaker, ou bloqueado) processando só os itens 'pending'."""

    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: str = Field(index=True, foreign_key="job.id")
    href: str
    order_index: int
    status: str = "pending"  # pending | done | failed
