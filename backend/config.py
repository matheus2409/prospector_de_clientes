"""
Configurações da aplicação, carregadas do arquivo .env na raiz do projeto.
Copie .env.example para .env e ajuste os valores antes de rodar.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _get(key: str, default: str = "") -> str:
    """Como os.getenv, mas trata uma variável PRESENTE e VAZIA ('') no .env
    como se não estivesse definida. Sem isso, uma linha tipo 'DB_PATH=' no
    .env (deixada em branco de propósito no .env.example) sobrescreve
    silenciosamente o valor padrão com string vazia — e uma string vazia
    como caminho de banco SQLite faz o SQLite abrir um banco privado e
    temporário por conexão, então tabelas "somem" entre uma request e outra."""
    val = os.getenv(key)
    return val if val not in (None, "") else default


def _bool(key: str, default: bool) -> bool:
    val = _get(key, "")
    if not val:
        return default
    return val.strip().lower() not in ("false", "0", "no")


class Settings:
    # Onde o banco SQLite fica salvo
    DB_PATH: str = _get("DB_PATH", str(BASE_DIR / "data" / "prospector.db"))

    # URL do webhook do n8n (deixe em branco pra desativar o envio automático)
    N8N_WEBHOOK_URL: str | None = _get("N8N_WEBHOOK_URL") or None

    # Caminho pro JSON da service account do Google (pra exportar pro Sheets)
    GOOGLE_SERVICE_ACCOUNT_FILE: str | None = _get("GOOGLE_SERVICE_ACCOUNT_FILE") or None

    # Chave opcional pra proteger os endpoints da API. Vazio = sem proteção (uso 100% local)
    API_KEY: str | None = _get("API_KEY") or None

    # Rodar o Chromium visível (false) ou headless (true)
    HEADLESS: bool = _bool("HEADLESS", True)

    # Atraso aleatório (segundos) entre ações do scraper, pra reduzir risco de bloqueio
    SCRAPE_DELAY_MIN: float = float(_get("SCRAPE_DELAY_MIN", "1.2"))
    SCRAPE_DELAY_MAX: float = float(_get("SCRAPE_DELAY_MAX", "3.0"))

    # Limite padrão de resultados por busca, se o usuário não especificar
    MAX_RESULTS_DEFAULT: int = int(_get("MAX_RESULTS_DEFAULT", "60"))
    MAX_RESULTS_HARD_CAP: int = int(_get("MAX_RESULTS_HARD_CAP", "200"))

    # Quantas tentativas extras por estabelecimento antes de desistir dele
    MAX_RETRIES_PER_LISTING: int = int(_get("MAX_RETRIES_PER_LISTING", "2"))

    # Quantas falhas seguidas (após esgotar as tentativas) pausam a varredura inteira
    CIRCUIT_BREAKER_THRESHOLD: int = int(_get("CIRCUIT_BREAKER_THRESHOLD", "5"))

    # Chave da Google Places API (New). Se preenchida, a varredura usa a API
    # oficial em vez de raspar o Google Maps com Playwright — mais confiável
    # (não depende de seletor CSS), mas tem custo por consulta acima do nível
    # gratuito do Google. Veja o README pra como obter a chave e os custos.
    GOOGLE_PLACES_API_KEY: str | None = _get("GOOGLE_PLACES_API_KEY") or None


settings = Settings()
