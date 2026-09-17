"""
Motor de raspagem do Google Maps usando Playwright.

IMPORTANTE — leia isto antes de mexer:
O Google troca os nomes de classe do Maps periodicamente (são classes minificadas,
tipo "hfpxzc", "DUwDvf" etc.). Por isso todos os seletores ficam centralizados no
dicionário SELECTORS abaixo — se a raspagem parar de funcionar, é aqui que você
deve olhar primeiro (abra o DevTools no Maps, inspecione o elemento que parou
de ser encontrado, e atualize o seletor correspondente).

Esse arquivo não foi testado contra o Google Maps ao vivo (o ambiente onde foi
gerado não tem acesso à internet pública). A lógica e a API do Playwright estão
corretas, mas os seletores CSS específicos do Maps podem precisar de ajuste na
sua primeira execução real.

Camadas de robustez implementadas nesse arquivo:
- Retry com backoff por estabelecimento (MAX_RETRIES_PER_LISTING)
- Detecção de bloqueio/captcha (_is_blocked) -> para a varredura inteira
- Circuit breaker: N falhas seguidas -> pausa a varredura (status "partial")
- Resume: retoma uma varredura pausada/cancelada a partir dos links pendentes
  (persistidos como JobLink), sem precisar refazer a busca/scroll no Maps
"""
import asyncio
import random
import re
import urllib.parse
from typing import Awaitable, Callable, Optional

from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

from .config import settings

LogFn = Callable[[str], Awaitable[None]]
OnLeadFn = Callable[[dict], Awaitable[None]]
OnLinksCollectedFn = Callable[[list], Awaitable[list]]
OnLinkResultFn = Callable[[int, bool], Awaitable[None]]
ShouldCancelFn = Callable[[], bool]

SELECTORS = {
    "consent_accept": [
        "button:has-text('Aceitar tudo')",
        "button:has-text('Accept all')",
        "form[action*='consent'] button",
    ],
    "feed": "div[role='feed']",
    "result_link": "div[role='feed'] a[href*='/maps/place/']",
    "end_of_list": "text=/chegou ao fim da lista|You've reached the end of the list/i",
    "name": "h1.DUwDvf, h1[class*='fontHeadlineLarge'], h1",
    "category": "button.DkEaL",
    "rating": "div.F7nice span[aria-hidden='true']",
    "review_count": "div.F7nice span[aria-label*='avalia'], div.F7nice span[aria-label*='review']",
    "address_button": "button[data-item-id='address']",
    "phone_button": "button[data-item-id^='phone:tel:']",
    "website_link": "a[data-item-id='authority']",
    "blocked_iframe": "iframe[src*='recaptcha'], #captcha-form, form[action*='sorry']",
}


class BlockedError(Exception):
    """Levantada quando detectamos a tela de bloqueio/captcha do Google."""


class ExtractionFailed(Exception):
    """Levantada quando um estabelecimento não pôde ser extraído após todas as tentativas."""


def _rand_delay(min_s: Optional[float] = None, max_s: Optional[float] = None) -> float:
    lo = min_s if min_s is not None else settings.SCRAPE_DELAY_MIN
    hi = max_s if max_s is not None else settings.SCRAPE_DELAY_MAX
    return random.uniform(lo, hi)


def _extract_latlng_from_url(url: str):
    m = re.search(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", url)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", url)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None, None


async def _dismiss_consent(page: Page):
    for sel in SELECTORS["consent_accept"]:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=2000):
                await btn.click()
                await asyncio.sleep(1)
                return
        except Exception:
            continue


async def _is_blocked(page: Page) -> bool:
    """Detecta a tela de 'tráfego incomum'/captcha do Google. Fica restrito a
    sinais bem específicos (URL /sorry/, iframe de recaptcha, título da página)
    pra evitar falso positivo em cima de conteúdo legítimo de algum estabelecimento."""
    try:
        url = page.url or ""
        if "/sorry/" in url or "recaptcha" in url:
            return True
        if await page.locator(SELECTORS["blocked_iframe"]).count() > 0:
            return True
        title = (await page.title()) or ""
        title_lower = title.lower()
        if "unusual traffic" in title_lower or "tráfego incomum" in title_lower:
            return True
    except Exception:
        pass
    return False


async def _extract_text(page: Page, selector: str) -> Optional[str]:
    try:
        loc = page.locator(selector).first
        if await loc.count() == 0:
            return None
        txt = await loc.inner_text(timeout=3000)
        txt = txt.strip()
        return txt or None
    except Exception:
        return None


async def _collect_result_links(
    page: Page, max_results: int, log: LogFn, should_cancel: ShouldCancelFn
) -> list[str]:
    """Rola o painel de resultados e coleta os links únicos de cada estabelecimento."""
    seen: dict[str, None] = {}
    feed = page.locator(SELECTORS["feed"])

    try:
        await feed.wait_for(timeout=15000)
    except PWTimeout:
        await log(
            "Não encontrei a lista de resultados. Pode ser captcha/bloqueio do "
            "Google, zero resultados pra essa busca, ou o seletor 'feed' mudou "
            "(veja SELECTORS em scraper.py)."
        )
        return []

    stagnant_rounds = 0
    max_stagnant = 4

    while len(seen) < max_results and stagnant_rounds < max_stagnant:
        if should_cancel():
            break

        links = await page.locator(SELECTORS["result_link"]).all()
        for a in links:
            href = await a.get_attribute("href")
            if href and href not in seen:
                seen[href] = None

        before = len(seen)
        await feed.evaluate("el => el.scrollTop = el.scrollHeight")
        await asyncio.sleep(_rand_delay())

        links = await page.locator(SELECTORS["result_link"]).all()
        for a in links:
            href = await a.get_attribute("href")
            if href and href not in seen:
                seen[href] = None

        stagnant_rounds = stagnant_rounds + 1 if len(seen) == before else 0
        await log(f"Coletando resultados... {len(seen)} encontrados até agora")

        try:
            if await page.locator(SELECTORS["end_of_list"]).first.is_visible(timeout=500):
                break
        except Exception:
            pass

    return list(seen.keys())[:max_results]


async def _extract_place(page: Page, url: str) -> dict:
    data = {
        "name": None, "category": None, "address": None, "phone": None,
        "website": None, "rating": None, "review_count": None,
        "maps_url": url, "latitude": None, "longitude": None,
    }

    data["name"] = await _extract_text(page, SELECTORS["name"])
    data["category"] = await _extract_text(page, SELECTORS["category"])
    data["address"] = await _extract_text(page, SELECTORS["address_button"])
    data["phone"] = await _extract_text(page, SELECTORS["phone_button"])

    try:
        website_loc = page.locator(SELECTORS["website_link"]).first
        if await website_loc.count() > 0:
            data["website"] = await website_loc.get_attribute("href")
    except Exception:
        pass

    rating_raw = await _extract_text(page, SELECTORS["rating"])
    if rating_raw:
        try:
            data["rating"] = float(rating_raw.replace(",", "."))
        except ValueError:
            pass

    review_raw = await _extract_text(page, SELECTORS["review_count"])
    if review_raw:
        digits = re.sub(r"[^\d]", "", review_raw)
        if digits:
            data["review_count"] = int(digits)

    data["latitude"], data["longitude"] = _extract_latlng_from_url(page.url)
    return data


async def _extract_place_live(detail_page: Page, href: str) -> dict:
    """Wrapper de produção: navega até o link, checa bloqueio, e extrai os dados.
    Isolado da lógica de retry pra poder ser substituído por uma função falsa nos testes."""
    await detail_page.goto(href, timeout=20000)
    await asyncio.sleep(_rand_delay(0.8, 1.8))
    if await _is_blocked(detail_page):
        raise BlockedError("Bloqueio/captcha detectado pelo Google")
    return await _extract_place(detail_page, href)


async def _extract_with_retry(
    detail_page: Page,
    href: str,
    log: LogFn,
    index: int,
    total: int,
    max_retries: Optional[int] = None,
    extract_fn=None,
) -> dict:
    """Tenta extrair um estabelecimento, com retry + backoff crescente em caso
    de falha. Levanta BlockedError imediatamente (sem retry) se detectar
    bloqueio, ou ExtractionFailed se esgotar as tentativas."""
    extract_fn = extract_fn or _extract_place_live
    retries = settings.MAX_RETRIES_PER_LISTING if max_retries is None else max_retries

    last_error = "erro desconhecido"
    for attempt in range(0, retries + 1):
        try:
            data = await extract_fn(detail_page, href)
            if data and data.get("name"):
                return data
            last_error = "item sem nome reconhecível"
        except BlockedError:
            raise
        except Exception as e:
            last_error = str(e)

        if attempt < retries:
            wait = _rand_delay(1.5, 3.0) * (attempt + 1)
            await log(
                f"[{index}/{total}] tentativa {attempt + 1} falhou ({last_error}), "
                f"tentando de novo em {wait:.1f}s..."
            )
            await asyncio.sleep(wait)

    raise ExtractionFailed(last_error)


async def _process_links(
    detail_page: Page,
    links_with_ids: list,
    log: LogFn,
    on_lead: OnLeadFn,
    on_link_result: OnLinkResultFn,
    should_cancel: ShouldCancelFn,
    extract_fn=None,
) -> str:
    """Processa uma lista de (link_id, href), extraindo cada estabelecimento.
    Retorna o status final: 'completed' | 'partial' | 'blocked' | 'cancelled'."""
    consecutive_failures = 0
    total = len(links_with_ids)

    for i, (link_id, href) in enumerate(links_with_ids, start=1):
        if should_cancel():
            await log("Varredura cancelada pelo usuário.")
            return "cancelled"

        try:
            data = await _extract_with_retry(detail_page, href, log, i, total, extract_fn=extract_fn)
            await log(f"[{i}/{total}] {data['name']}")
            await on_lead(data)
            await on_link_result(link_id, True)
            consecutive_failures = 0
        except BlockedError:
            await log("Bloqueio/captcha detectado pelo Google — parando a varredura pra não piorar a situação.")
            await on_link_result(link_id, False)
            return "blocked"
        except ExtractionFailed as e:
            await log(f"[{i}/{total}] falhou após tentativas: {e}")
            await on_link_result(link_id, False)
            consecutive_failures += 1
            if consecutive_failures >= settings.CIRCUIT_BREAKER_THRESHOLD:
                restantes = total - i
                await log(
                    f"{consecutive_failures} falhas seguidas — pausando a varredura "
                    f"({restantes} item(ns) ainda pendente(s), dá pra retomar depois)."
                )
                return "partial"

        await asyncio.sleep(_rand_delay())

    return "completed"


async def scrape_google_maps(
    query: str,
    location: str,
    max_results: int,
    log: LogFn,
    on_lead: OnLeadFn,
    on_links_collected: OnLinksCollectedFn,
    on_link_result: OnLinkResultFn,
    should_cancel: ShouldCancelFn,
) -> str:
    """Fluxo completo pra uma busca nova: abre o Maps, coleta os links, persiste
    (via on_links_collected) e processa cada um. Retorna o status final."""
    search_term = f"{query} em {location}" if location else query
    search_url = f"https://www.google.com/maps/search/{urllib.parse.quote(search_term)}"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=settings.HEADLESS)
        context = await browser.new_context(
            locale="pt-BR",
            viewport={"width": 1366, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        try:
            await log(f"Abrindo Google Maps para '{search_term}'...")
            await page.goto(search_url, timeout=30000)
            await _dismiss_consent(page)
            await asyncio.sleep(_rand_delay())

            if await _is_blocked(page):
                await log(
                    "Bloqueio/captcha detectado logo ao abrir a busca. Espere um "
                    "tempo antes de tentar de novo, ou rode com HEADLESS=false pra "
                    "resolver manualmente."
                )
                return "blocked"

            links = await _collect_result_links(page, max_results, log, should_cancel)
            await log(f"{len(links)} resultados localizados. Extraindo detalhes de cada um...")

            if not links:
                return "completed"

            links_with_ids = await on_links_collected(links)

            detail_page = await context.new_page()
            return await _process_links(detail_page, links_with_ids, log, on_lead, on_link_result, should_cancel)
        finally:
            await context.close()
            await browser.close()


async def resume_scrape(
    links_with_ids: list,
    log: LogFn,
    on_lead: OnLeadFn,
    on_link_result: OnLinkResultFn,
    should_cancel: ShouldCancelFn,
) -> str:
    """Retoma uma varredura a partir dos links ainda pendentes (persistidos como
    JobLink), sem refazer a busca/scroll no Maps."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=settings.HEADLESS)
        context = await browser.new_context(
            locale="pt-BR",
            viewport={"width": 1366, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        try:
            detail_page = await context.new_page()
            await log(f"Retomando varredura: {len(links_with_ids)} item(ns) pendente(s).")
            return await _process_links(detail_page, links_with_ids, log, on_lead, on_link_result, should_cancel)
        finally:
            await context.close()
            await browser.close()
