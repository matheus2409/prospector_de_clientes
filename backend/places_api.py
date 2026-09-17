"""
Motor de busca via Google Places API (New) — alternativa ao scraping por
Playwright em scraper.py, usada quando GOOGLE_PLACES_API_KEY está configurada
no .env.

Por que isso existe: o scraper.py depende de seletores CSS do Google Maps que
mudam com o tempo (veja o aviso no topo daquele arquivo) e de rodar um
Chromium de verdade. A Places API é uma API JSON oficial e estável — não
quebra por causa de uma classe CSS renomeada — mas cobra por consulta acima
do nível gratuito. Veja o README pra como conseguir a chave e uma estimativa
de custo.

IMPORTANTE — assim como o scraper.py, este arquivo não foi testado contra a
API ao vivo (o ambiente onde foi gerado não tem acesso à internet pública,
incluindo places.googleapis.com). O formato de request/response abaixo segue
a documentação oficial da Places API (New) — Text Search — mas vale testar
com uma busca pequena (max_results baixo) antes de confiar de olhos fechados.

Diferenças importantes em relação ao scraper.py:
- Não há "retomar" (resume) aqui: a Places API não trabalha com uma lista de
  links coletados pra revisitar depois, é uma busca paginada por token. Um
  job que falhar no meio simplesmente não oferece o botão "Retomar" (porque
  nenhum JobLink é criado pra ele) — rodar a mesma busca de novo do zero é
  barato o bastante pra não valer a pena replicar toda a lógica de resume.
- Não existe "bloqueio/captcha" (isso é uma coisa de scraping); os erros
  possíveis aqui são de rede, autenticação/cota da chave, ou resposta vazia.
"""
import asyncio
from typing import Awaitable, Callable, Optional

import httpx

from .config import settings

LogFn = Callable[[str], Awaitable[None]]
OnLeadFn = Callable[[dict], Awaitable[None]]
OnLinksCollectedFn = Callable[[list], Awaitable[list]]
OnLinkResultFn = Callable[[int, bool], Awaitable[None]]
ShouldCancelFn = Callable[[], bool]

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

# Só pede os campos que realmente usamos — a Places API cobra pelo SKU mais
# caro entre os campos pedidos (rating/userRatingCount jogam pro nível
# "Enterprise"); pedir mais do que isso só aumentaria o custo à toa.
FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.nationalPhoneNumber",
    "places.websiteUri",
    "places.types",
    "places.rating",
    "places.userRatingCount",
    "places.location",
    "nextPageToken",
])

PAGE_SIZE = 20
# A Places API pede uma pequena espera antes do nextPageToken de uma página
# "ativar" — sem isso a próxima chamada pode vir vazia mesmo tendo mais itens.
PAGE_TOKEN_DELAY = 2.0


def _place_to_lead(place: dict) -> Optional[dict]:
    place_id = place.get("id")
    if not place_id:
        return None  # sem id não dá pra montar um maps_url estável; pula esse item
    location = place.get("location") or {}
    types = place.get("types") or []
    return {
        "name": (place.get("displayName") or {}).get("text") or "(sem nome)",
        "category": types[0].replace("_", " ") if types else None,
        "address": place.get("formattedAddress"),
        "phone": place.get("nationalPhoneNumber"),
        "website": place.get("websiteUri"),
        "rating": place.get("rating"),
        "review_count": place.get("userRatingCount"),
        "maps_url": f"https://www.google.com/maps/place/?q=place_id:{place_id}",
        "latitude": location.get("latitude"),
        "longitude": location.get("longitude"),
    }


async def search_google_places(
    query: str,
    location: str,
    max_results: int,
    log: LogFn,
    on_lead: OnLeadFn,
    on_links_collected: OnLinksCollectedFn,
    on_link_result: OnLinkResultFn,
    should_cancel: ShouldCancelFn,
) -> str:
    """Mesma assinatura de scrape_google_maps() (pra ser intercambiável em
    jobs.py) — on_links_collected e on_link_result não são usados aqui (não
    existe conceito de 'link individual' na Places API), só ficam nos
    parâmetros pra manter as duas funções com a cara idêntica."""
    text_query = f"{query} em {location}" if location else query
    api_key = settings.GOOGLE_PLACES_API_KEY
    if not api_key:
        await log("GOOGLE_PLACES_API_KEY não configurada — não é possível usar a Places API.")
        return "failed"

    await log(f"Consultando a Google Places API por '{text_query}'...")
    collected = 0
    page_token: Optional[str] = None

    async with httpx.AsyncClient(timeout=20.0) as client:
        while collected < max_results:
            if should_cancel():
                await log("Cancelamento solicitado.")
                return "cancelled"

            body = {"textQuery": text_query, "pageSize": min(PAGE_SIZE, max_results - collected)}
            if page_token:
                body["pageToken"] = page_token

            try:
                resp = await client.post(
                    SEARCH_URL,
                    json=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Goog-Api-Key": api_key,
                        "X-Goog-FieldMask": FIELD_MASK,
                    },
                )
            except httpx.HTTPError as e:
                await log(f"Erro de rede ao chamar a Places API: {e}")
                return "partial" if collected else "failed"

            if resp.status_code == 403:
                await log(
                    "Places API recusou a chave (403). Confira se GOOGLE_PLACES_API_KEY "
                    "está correta, se a 'Places API (New)' está ativada no projeto do "
                    "Google Cloud, e se o faturamento está habilitado."
                )
                return "partial" if collected else "failed"
            if resp.status_code == 429:
                await log("Places API retornou 429 (cota excedida). Pausando a varredura.")
                return "partial" if collected else "failed"
            if resp.status_code != 200:
                await log(f"Places API retornou HTTP {resp.status_code}: {resp.text[:300]}")
                return "partial" if collected else "failed"

            data = resp.json()
            places = data.get("places", [])
            if not places:
                break  # sem mais resultados (ou busca sem nenhum resultado, se collected==0)

            for place in places:
                if collected >= max_results or should_cancel():
                    break
                lead = _place_to_lead(place)
                if lead is None:
                    continue
                await on_lead(lead)
                collected += 1

            await log(f"{collected} lead(s) coletado(s) até agora...")

            page_token = data.get("nextPageToken")
            if not page_token or collected >= max_results:
                break
            await asyncio.sleep(PAGE_TOKEN_DELAY)

    if collected == 0:
        await log("Nenhum resultado encontrado pra essa busca.")
    return "completed"
