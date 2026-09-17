from datetime import timezone
from pathlib import Path

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator
from sqlmodel import SQLModel, create_engine, Session

from .config import settings

Path(settings.DB_PATH).parent.mkdir(parents=True, exist_ok=True)


class UTCDateTime(TypeDecorator):
    """SQLite não tem um tipo de data com fuso horário nativo: um datetime
    "consciente" (timezone-aware, ex: datetime.now(timezone.utc)) salvo nele
    volta "ingênuo" (sem tzinfo) na leitura. Isso quebra de duas formas:
    1) comparações em Python entre uma data do banco e datetime.now(timezone.utc)
       levantam TypeError ("can't compare offset-naive and offset-aware");
    2) ao virar JSON pro frontend, uma data sem fuso é interpretada pelo
       JavaScript como hora LOCAL do navegador, não UTC — fazendo qualquer
       data/hora mostrada na tela (varreduras anteriores, próxima execução de
       um agendamento) aparecer com a hora errada, deslocada pelo fuso do
       usuário.
    Esse tipo resolve isso guardando sempre em UTC "ingênuo" no banco (mesmo
    formato que já era usado antes — compatível com bancos já existentes) e
    reanexando tzinfo=UTC toda vez que o valor volta pro Python."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


engine = create_engine(
    f"sqlite:///{settings.DB_PATH}",
    connect_args={"check_same_thread": False},
)


def init_db():
    from . import models  # noqa: F401  (garante que os modelos sejam registrados)
    SQLModel.metadata.create_all(engine)
    _ensure_schema_upgrades()


def _ensure_schema_upgrades():
    """create_all() só cria tabelas que ainda não existem — não adiciona
    colunas novas a uma tabela já existente. Então quem já tinha um
    data/prospector.db de antes desta versão precisa ganhar a coluna
    'status' do Lead manualmente aqui, uma vez, na subida do servidor."""
    with engine.connect() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(lead)").fetchall()}
        if cols and "status" not in cols:
            conn.exec_driver_sql("ALTER TABLE lead ADD COLUMN status VARCHAR DEFAULT 'novo'")
            conn.commit()


def get_session():
    with Session(engine) as session:
        yield session
