import requests
from bs4 import BeautifulSoup
import random
import time
from datetime import datetime
import sys
import os
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from modules.config_manager import ConfigManager
from modules.proxy_manager import ProxyManager

# Stałe konfiguracyjne
SCAN_LIMIT_BEFORE_PAUSE = 12
PAUSE_TIME_RANGE = (600, 900)  # 10-15 minut
SLEEP_BETWEEN_REQUESTS = (12, 25)
MATCH_SKIP_TIME = 2700  # 45 minut

BASE_URL = "https://www.efortuna.pl"
LOG_FILE = "bot_log_fortu.txt"

# Rynki do porównania
ALLOWED_MARKETS = [
    "obie drużyny strzelą gola",
    "obie drużyny strzelą gola w 1.połowie",
    "1x2",
    "wynik meczu",
    "podwójna szansa",
    "spotkanie bez remisu",
    "powyżej",
    "poniżej",
]

# Inicjalizacja konfiguracji i proxy
config = ConfigManager("config.yaml")
proxy_manager = ProxyManager(config)
# Cache zeskanowanych meczów
typedict = {}
scanned_matches = {}

# Funkcja logująca z rotacją pliku
def log(message: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > 1500:
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.writelines(lines[-1000:])
    except Exception:
        pass

# Pobranie i parsowanie strony przez requests
def fetch_and_parse(url: str):
    kwargs = proxy_manager.get_request_kwargs()
    log(f"Pobieram URL: {url}")
    log(f"Proxy: {kwargs.get('proxies', 'brak')}")
    log(f"User-Agent: {kwargs.get('headers', {}).get('User-Agent', 'brak')}")
    try:
        response = requests.get(url, timeout=15, **kwargs)
        log(f"HTTP Status: {response.status_code} ({response.reason})")
        response.raise_for_status()
        return BeautifulSoup(response.text, "html.parser")
    except Exception as e:
        log(f"ERROR fetch_and_parse: {e}")
        return None

# Pobranie linków do meczów z listy ligi
def get_match_links(league_url: str):
    soup = fetch_and_parse(league_url)
    if not soup:
        return []
    links = []
    for a in soup.select('a.event-link'):
        href = a.get('href')
        if href:
            full_url = requests.compat.urljoin(BASE_URL, href)
            links.append(full_url)
            log(f"Dodany link do meczu: {full_url}")
    return links

# Parsowanie rynków na stronie meczu używając Playwright
def fetch_markets_with_playwright(match_url: str):
    markets = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            kwargs = proxy_manager.get_request_kwargs()
            ua = kwargs.get('headers', {}).get('User-Agent', '')
            ctx_args = {}
            proxy = kwargs.get('proxies')
            if proxy:
                server = proxy if isinstance(proxy, str) else next(iter(proxy.values()), None)
                if server:
                    ctx_args['proxy'] = {'server': server}
                    log(f"PLAYWRIGHT używa proxy: {server}")
            ctx = browser.new_context(user_agent=ua, **ctx_args)
            page = ctx.new_page()

            log(f"PLAYWRIGHT: Ładowanie strony: {match_url}")
            try:
                page.goto(match_url, timeout=30000)
                page.wait_for_selector('.market-container, .market', timeout=16000)
                page.wait_for_timeout(2000)
            except PlaywrightTimeoutError as e:
                log(f"PLAYWRIGHT: Timeout lub błąd ładowania rynków: {e}")

            containers = page.query_selector_all('.market-container, .market')
            log(f"PLAYWRIGHT: Znaleziono {len(containers)} kontenerów rynków.")
            for idx, cont in enumerate(containers, start=1):
                try:
                    name_el = cont.query_selector('h3 a')
                    mkt = name_el.inner_text().strip().lower() if name_el else None
                    if not mkt or mkt not in ALLOWED_MARKETS:
                        continue
                    buttons = cont.query_selector_all('a.odds-button')
                    if not buttons:
                        log(f"PLAYWRIGHT WARN: Rynek '{mkt}' bez kursów.")
                        continue
                    for btn in buttons:
                        sel = btn.query_selector('span.odds-name')
                        val = btn.query_selector('span.odds-value')
                        sel_txt = sel.inner_text().strip() if sel else None
                        val_txt = val.inner_text().strip() if val else None
                        if sel_txt and val_txt and val_txt not in ('0','0.0'):
                            markets.append({'market': mkt, 'selection': sel_txt, 'odds': val_txt})
                except Exception as e:
                    log(f"PLAYWRIGHT WARN: Błąd parsowania marketu {idx}: {e}")
            ctx.close()
            browser.close()
    except Exception as e:
        log(f"PLAYWRIGHT ERROR: {e}")
    return markets

# Parsowanie szczegółów meczu wraz z datą, godziną i drużynami
def parse_match_page(match_url: str):
    soup = fetch_and_parse(match_url)
    if not soup:
        return None
    sec = soup.select_one('section.event-detail')
    sport = sec['data-sport'] if sec and sec.has_attr('data-sport') else None
    comp = sec['data-competition'] if sec and sec.has_attr('data-competition') else None
    dt_el = soup.select_one('span.event-datetime')
    match_date, match_time = None, None
    if dt_el:
        txt = dt_el.get_text(strip=True)
        parts = txt.split()
        if len(parts) >= 2:
            match_date, match_time = parts[0], parts[1]
    team_el = soup.select_one('h1.breadcrumbed-title span.event-name')
    match_name = team_el.get_text(strip=True) if team_el else None
    if not match_name:
        return None
    return {
        'match_name': match_name,
        'sport': sport,
        'competition': comp,
        'date': match_date,
        'time': match_time,
        'markets': fetch_markets_with_playwright(match_url)
    }

# Główna pętla bota
def main():
    scan_count = 0
    while True:
        for league_url in LEAGUES_TO_SCAN:
            log(f"INFO: Pobieram listę meczów z ligi: {league_url}")
            match_links = get_match_links(league_url)
            if not match_links:
                time.sleep(random.randint(*SLEEP_BETWEEN_REQUESTS))
                continue
            for link in match_links:
                match_id = link.rstrip('/').split('/')[-1]
                if match_id in scanned_matches and (datetime.now() - scanned_matches[match_id]).seconds < MATCH_SKIP_TIME:
                    continue
                details = parse_match_page(link)
                if not details:
                    continue
                log(f"INFO: {details['match_name']} | {details['sport']} | {details['competition']} | {details['date']} {details['time']}")
                if details['markets']:
                    for m in details['markets']:
                        log(f"    - {m['market']} / {m['selection']} @ {m['odds']}")
                else:
                    log("    Brak rynków / kursów na stronie meczu")
                scanned_matches[match_id] = datetime.now()
                scan_count += 1
                if scan_count >= SCAN_LIMIT_BEFORE_PAUSE:
                    pause = random.randint(*PAUSE_TIME_RANGE)
                    log(f"PAUSE: Pauza na {pause}s po {SCAN_LIMIT_BEFORE_PAUSE} skanach")
                    time.sleep(pause)
                    scan_count = 0
                time.sleep(random.randint(*SLEEP_BETWEEN_REQUESTS))
        now = datetime.now()
        for key, ts in list(scanned_matches.items()):
            if (now - ts).seconds > MATCH_SKIP_TIME:
                del scanned_matches[key]

if __name__ == '__main__':
    LEAGUES_TO_SCAN = [
        "https://www.efortuna.pl/zaklady-bukmacherskie/pilka-nozna/1-turcja",
        "https://www.efortuna.pl/zaklady-bukmacherskie/pilka-nozna/1-algieria",
    ]
    log("START BOTA")
    main()
