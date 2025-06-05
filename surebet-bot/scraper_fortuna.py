import requests
from bs4 import BeautifulSoup
import random
import time
from datetime import datetime, timedelta
import sys
import os
import json
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# Upewnij się, że ścieżka do modułów jest poprawna:
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from modules.config_manager import ConfigManager
from modules.proxy_manager import ProxyManager

# ————————————
# Stałe konfiguracyjne
# ————————————
SCAN_LIMIT_BEFORE_PAUSE = 12
PAUSE_TIME_RANGE       = (600, 900)  # 10–15 minut
SLEEP_BETWEEN_REQUESTS = (12, 25)    # 12–25 s między meczami
MATCH_SKIP_TIME        = 2700        # 45 minut

BASE_URL = "https://www.efortuna.pl"
LOG_FILE = "bot_log_fortu.txt"

# Ta lista musi istnieć przed blokiem __main__
LEAGUES_TO_SCAN = [
    "https://www.efortuna.pl/zaklady-bukmacherskie/pilka-nozna/1-brazylia",
    "https://www.efortuna.pl/zaklady-bukmacherskie/pilka-nozna/1-algieria",
]

# Rynki do porównania (można odkomentować, jeśli chcesz filtrować)
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

config         = ConfigManager("config.yaml")
proxy_manager  = ProxyManager(config)
scanned_matches = {}

# ————————————
# Pomocnicze funkcje
# ————————————
def log(message: str):
    """Logowanie z rotacją pliku (maks. 1000 ostatnich linii)."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > 1500:
            with open(LOG_FILE, "w", encoding="utf-8") as f2:
                f2.writelines(lines[-1000:])
    except Exception:
        pass

def parse_match_datetime(date_txt: str, time_txt: str):
    """
    Parsuje tekst daty i godziny na obiekt datetime.
    - date_txt: "DD.MM." lub "DD.MM.YYYY"
    - time_txt: "HH:MM"
    """
    now = datetime.now()
    date_obj = None

    if not date_txt:
        date_obj = now.date()
    else:
        txt = date_txt.strip().rstrip('.')
        try:
            if txt.count('.') == 2:
                # Format "DD.MM.YYYY"
                date_obj = datetime.strptime(txt, "%d.%m.%Y").date()
            else:
                # Format "DD.MM"
                temp = datetime.strptime(txt, "%d.%m").date()
                date_obj = temp.replace(year=now.year)
                if date_obj < now.date():
                    date_obj = date_obj.replace(year=now.year + 1)
        except ValueError:
            return None

    try:
        time_obj = datetime.strptime(time_txt.strip(), "%H:%M").time() if time_txt else datetime.min.time()
    except (ValueError, TypeError):
        time_obj = datetime.min.time()

    return datetime.combine(date_obj, time_obj)

def make_match_id(match_name: str, dt: datetime) -> str:
    """
    Generuje unikalne ID:
      – dwie pierwsze litery drużyny domowej (bez spacji)
      – dwie pierwsze litery drużyny gościa (bez spacji)
      – ddmmHHMM (bez roku).
    """
    try:
        home, away = [x.strip() for x in match_name.split("-", 1)]
    except ValueError:
        home = match_name.strip()
        away = ""
    h = home.replace(" ", "")
    a = away.replace(" ", "")
    h_key = (h[:2].upper() if len(h) >= 2 else (h[:1].upper() + "_"))
    a_key = (a[:2].upper() if len(a) >= 2 else (a[:1].upper() + "_"))
    key_teams = h_key + a_key
    key_date  = dt.strftime("%d%m%H%M")  # ddmmHHMM
    return f"{key_teams}{key_date}"

def fetch_and_parse(url: str):
    """Pobranie i parsowanie strony przez requests."""
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

def get_match_links(league_url: str):
    """Pobranie linków do meczów z listy ligi (Fortuna)."""
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

def fetch_markets_with_playwright(match_url: str):
    """Parsowanie rynków na stronie meczu przy użyciu Playwright."""
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
                        if sel_txt and val_txt and val_txt not in ('0', '0.0'):
                            markets.append({'market': mkt, 'selection': sel_txt, 'odds': val_txt})
                except Exception as e:
                    log(f"PLAYWRIGHT WARN: Błąd parsowania marketu {idx}: {e}")
            ctx.close()
            browser.close()
    except Exception as e:
        log(f"PLAYWRIGHT ERROR: {e}")
    return markets

def parse_match_page(match_url: str):
    """
    Parsowanie szczegółów meczu wraz z:
      - match_id (generowany, jeśli da się sparsować datę)
      - match_name
      - sport, competition
      - datetime
      - markets
    """
    soup = fetch_and_parse(match_url)
    if not soup:
        return None

    sec = soup.select_one('section.event-detail')
    sport = sec['data-sport'] if sec and sec.has_attr('data-sport') else None
    comp  = sec['data-competition'] if sec and sec.has_attr('data-competition') else None

    # Wyciągamy datę i godzinę (np. "05.06." oraz "01:00")
    dt_el = soup.select_one('span.event-datetime')
    match_date, match_time = None, None
    if dt_el:
        txt = dt_el.get_text(strip=True)
        parts = txt.split()
        if len(parts) >= 2:
            match_date, match_time = parts[0], parts[1]

    # Wyciągamy nazwę meczu
    team_el = soup.select_one('h1.breadcrumbed-title span.event-name')
    match_name = team_el.get_text(strip=True) if team_el else None
    if not match_name:
        return None

    # Parsujemy datetime
    dt_obj = None
    if match_date and match_time:
        dt_obj = parse_match_datetime(match_date, match_time)
        if not dt_obj:
            log(f"[WARN] Nie udało się sparsować daty/godziny: '{match_date}' '{match_time}' dla linku {match_url}")

    # Generujemy match_id tylko jeśli mamy poprawny datetime
    match_id = make_match_id(match_name, dt_obj) if (match_name and dt_obj) else None
    if match_id is None:
        log(f"[INFO] Brak match_id (może nie udało się sparsować daty) dla: {match_name}")

    return {
        'match_id':    match_id,
        'match_name':  match_name,
        'sport':       sport,
        'competition': comp,
        'datetime':    dt_obj,
        'markets':     fetch_markets_with_playwright(match_url)
    }

# ————————————
# 1) Zbieramy wszystkie mecze z każdej ligi i zapisujemy do JSON-a
# ————————————
collected = {}  # { key: { 'match_name', 'sport', 'competition', 'datetime', 'markets' } }

for league_url in LEAGUES_TO_SCAN:
    match_links = get_match_links(league_url)
    for link in match_links:
        details = parse_match_page(link)
        if not details:
            time.sleep(random.randint(*SLEEP_BETWEEN_REQUESTS))
            continue

        # Jeżeli nie ma match_id, używamy nazwy jako klucza
        key = details['match_id'] or details['match_name']
        dt_str = details['datetime'].isoformat() if details['datetime'] else None

        collected[key] = {
            'match_name':  details['match_name'],
            'sport':       details['sport'],
            'competition': details['competition'],
            'datetime':    dt_str,
            'markets':     details['markets']
        }
        # Mały odstęp, żeby nie obciążyć serwera
        time.sleep(random.randint(*SLEEP_BETWEEN_REQUESTS))

out_file = "fortuna_data.json"
with open(out_file, "w", encoding="utf-8") as f:
    json.dump(collected, f, ensure_ascii=False, indent=2)
print(f"Zapisano {out_file} (znaleziono {len(collected)} wydarzeń)")

# ————————————
# 2) Potem wchodzimy w tryb ciągłego skanowania (logowanie w konsoli)
# ————————————
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
                match_id_key = link.rstrip('/').split('/')[-1]
                if match_id_key in scanned_matches and (datetime.now() - scanned_matches[match_id_key]).seconds < MATCH_SKIP_TIME:
                    continue

                details = parse_match_page(link)
                if not details:
                    continue

                # Logujemy z match_id (jeśli jest) lub nazwą
                display_key = details['match_id'] or details['match_name']
                if details['datetime']:
                    dt_str = details['datetime'].strftime("%d.%m.%Y %H:%M")
                    log(f"INFO: [{display_key}] {details['match_name']} | {details['sport']} | {details['competition']} | {dt_str}")
                else:
                    log(f"INFO: [{display_key}] {details['match_name']} | {details['sport']} | {details['competition']} | data/godzina nieznana")

                if details['markets']:
                    for m in details['markets']:
                        log(f"    - {m['market']} / {m['selection']} @ {m['odds']}")
                else:
                    log("    Brak rynków / kursów na stronie meczu")

                scanned_matches[match_id_key] = datetime.now()
                scan_count += 1
                if scan_count >= SCAN_LIMIT_BEFORE_PAUSE:
                    pause = random.randint(*PAUSE_TIME_RANGE)
                    log(f"PAUSE: Pauza na {pause}s po {SCAN_LIMIT_BEFORE_PAUSE} skanach")
                    time.sleep(pause)
                    scan_count = 0

                time.sleep(random.randint(*SLEEP_BETWEEN_REQUESTS))

        # Czyścimy stare wpisy w scanned_matches
        now = datetime.now()
        for key, ts in list(scanned_matches.items()):
            if (now - ts).seconds > MATCH_SKIP_TIME:
                del scanned_matches[key]

if __name__ == '__main__':
    log("START BOTA FORTUNA")
    main()
