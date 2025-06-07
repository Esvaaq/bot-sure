import requests
from bs4 import BeautifulSoup
import random
import time
from datetime import datetime, timedelta
import sys
import os
import csv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# Upewnij się, że ścieżka do modułów jest poprawna:
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from modules.config_manager import ConfigManager
from modules.proxy_manager import ProxyManager

# ———————————— 
# DODANE IMPORTY do obliczania surebetów i wysyłki Discord
# ————————————
from modules.arbitrage import load_csv, compute_surebets, format_for_discord

# ———————————— 
# Stałe konfiguracyjne 
# ————————————
SCAN_LIMIT_BEFORE_PAUSE = 12
PAUSE_TIME_RANGE       = (600, 900)   # 10–15 minut
SLEEP_BETWEEN_REQUESTS = (20, 40)     # 12–25 s między meczami
MATCH_SKIP_TIME        = 2700         # 45 minut

BASE_URL       = "https://www.efortuna.pl"
LOG_FILE       = "bot_log_fortu.txt"
OUT_CSV_FILE   = "fortuna_data.csv"

LEAGUES_TO_SCAN = [
    #"https://www.efortuna.pl/zaklady-bukmacherskie/pilka-nozna/1-brazylia",
    #"https://www.efortuna.pl/zaklady-bukmacherskie/pilka-nozna/2-argentyna",
    #"https://www.efortuna.pl/zaklady-bukmacherskie/pilka-nozna/3-dania",
    "https://www.efortuna.pl/zaklady-bukmacherskie/pilka-nozna",
]

config         = ConfigManager("config.yaml")
proxy_manager  = ProxyManager(config)
scanned_matches = {}

# ———————————— 
# Pobranie z config.yaml progów i ID kanałów Discord
# ————————————
FREE_MAX      = float(config.get('thresholds', 'free_max'))
PREMIUM_MIN   = float(config.get('thresholds', 'premium_min'))
FREE_CH_ID    = int(config.get('discord', 'channels', 'free'))
PREMIUM_CH_ID = int(config.get('discord', 'channels', 'premium', 'all'))


# ———————————— 
# Funkcja logująca 
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

# ———————————— 
# Parsowanie daty i czasu 
# ————————————
def parse_match_datetime(date_txt: str, time_txt: str):
    now = datetime.now()
    date_obj = None

    if not date_txt:
        date_obj = now.date()
    else:
        txt = date_txt.strip().rstrip('.')
        try:
            if txt.count('.') == 2:
                date_obj = datetime.strptime(txt, "%d.%m.%Y").date()
            else:
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

# ———————————— 
# Generowanie match_id 
# ———————————— 
def make_match_id(match_name: str, dt: datetime) -> str:
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
    key_date  = dt.strftime("%d%m%H%M")
    return f"{key_teams}{key_date}"

# ———————————— 
# Pobranie i parsowanie strony przez requests 
# ———————————— 
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

# ———————————— 
# Pobranie linków do meczów z listy ligi 
# ———————————— 
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

# ———————————— 
# Parsowanie rynków na stronie meczu przez Playwright 
# ———————————— 
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
                    log(f"[DEBUG-MKT] surowa nazwa rynku: '{mkt}'")
                    if not mkt:
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
                            markets.append({
                                'market_raw': mkt,
                                'selection':  sel_txt,
                                'odds':       val_txt
                            })
                except Exception as e:
                    log(f"PLAYWRIGHT WARN: Błąd parsowania marketu {idx}: {e}")
            ctx.close()
            browser.close()
    except Exception as e:
        log(f"PLAYWRIGHT ERROR: {e}")
    return markets

# ———————————— 
# Grupowanie surowych wpisów w strukturę rynków 
# ———————————— 
def group_markets(markets_raw):
    grouped = {}
    for entry in markets_raw:
        mkt = entry.get('market_raw')
        sel = entry.get('selection')
        odds_str = entry.get('odds')
        try:
            odds_val = float(odds_str.replace(',', '.'))
        except Exception:
            continue
        if not mkt:
            continue

        market_name = mkt.strip().upper()
        if market_name not in grouped:
            grouped[market_name] = []

        grouped[market_name].append({
            "outcome":  sel,
            "odds":     odds_val,
            "bookmaker": "Fortuna"
        })

    result = []
    for mkt_name, sels in grouped.items():
        result.append({
            "market_name": mkt_name,
            "selections":  sels
        })
    return result

# ———————————— 
# Parsowanie pojedynczej strony meczu 
# ———————————— 
def parse_match_page(match_url: str):
    soup = fetch_and_parse(match_url)
    if not soup:
        return None

    sec = soup.select_one('section.event-detail')
    sport = sec['data-sport'] if sec and sec.has_attr('data-sport') else None
    comp  = sec['data-competition'] if sec and sec.has_attr('data-competition') else None

    dt_el = soup.select_one('span.event-datetime')
    match_date, match_time = None, None
    if dt_el:
        txt = dt_el.get_text(strip=True)
        parts = txt.split()
        if len(parts) >= 2:
            match_date, match_time = parts[0], parts[1]

    team_el    = soup.select_one('h1.breadcrumbed-title span.event-name')
    match_name = team_el.get_text(strip=True) if team_el else None
    if not match_name:
        return None

    dt_obj = None
    if match_date and match_time:
        dt_obj = parse_match_datetime(match_date, match_time)
        if not dt_obj:
            log(f"[WARN] NieParsDaty: '{match_date}' '{match_time}' dla {match_name}")

    if not dt_obj:
        log(f"[INFO] Brak match_id (nie można sparsować daty) dla: {match_name}")
        return None

    match_id = make_match_id(match_name, dt_obj)

    markets_raw = fetch_markets_with_playwright(match_url)
    if not markets_raw:
        log(f"[INFO] Brak rynków/kursów dla: {match_name} ({match_id})")
        markets = []
    else:
        markets = group_markets(markets_raw)

    return {
        "match_id":    match_id,
        "match_name":  match_name,
        "sport":       sport,
        "competition": comp,
        "datetime":    dt_obj.strftime("%Y-%m-%dT%H:%M:%S"),
        "markets":     markets
    }

# ———————————— 
# Dopisywanie jednego meczu do CSV, usuwanie wierszy starszych niż 1 dzień 
# oraz podmiana wpisów o tym samym match_id 
# ———————————— 
def append_match_to_csv(match: dict, filename: str):
    """
    1) Wczytuje wszystkie wiersze (jeśli plik istnieje).
    2) Filtruje: 
       - usuwa wiersze starsze niż 1 dzień (na podstawie pola 'datetime'),
       - usuwa wiersze z tym samym match_id, co nowy mecz (żeby podmienić).
    3) Nadpisuje plik tylko z wierszami, które przetrwały filtr (plus nagłówek).
    4) Dopisuje nowe wiersze odpowiadające temu meczowi.
    Jeśli plik nie istnieje: tworzy go i zapisuje nagłówek + wiersze meczu.
    """
    dir_name = os.path.dirname(filename)
    if dir_name and not os.path.exists(dir_name):
        os.makedirs(dir_name, exist_ok=True)

    fieldnames = [
        "match_id", "match_name", "sport", "competition", "datetime",
        "market_name", "outcome", "odds", "bookmaker"
    ]

    file_exists = os.path.isfile(filename)

    kept_rows = []
    if file_exists:
        try:
            threshold_dt = datetime.now() - timedelta(days=1)
            with open(filename, "r", encoding="utf-8", newline="") as csvfile:
                reader = csv.DictReader(csvfile, fieldnames=fieldnames)
                next(reader)  # pomijamy nagłówek
                for row in reader:
                    # Parsujemy datetime w wierszu
                    try:
                        row_dt = datetime.strptime(row["datetime"], "%Y-%m-%dT%H:%M:%S")
                    except Exception:
                        continue  # jeśli nie parsuje się poprawnie, pomijamy
                    # Jeśli wiersz jest starszy niż 1 dzień => pomijamy
                    if row_dt < threshold_dt:
                        continue
                    # Jeśli match_id jest taki sam jak nowego meczu => pomijamy (podmiana)
                    if row["match_id"] == match["match_id"]:
                        continue
                    # W przeciwnym razie zostawiamy wiersz
                    kept_rows.append(row)
        except Exception as e:
            log(f"[✖] Błąd przy czytaniu i filtrowaniu starego CSV: {e}")
            # Jeśli coś nie wyjdzie, traktujemy jakby pliku nie było – nie wczytujemy nic

    # Nadpisujemy plik wyłącznie wierszami, które przeszły filtr (jeśli są) oraz nagłówkiem
    try:
        with open(filename, "w", encoding="utf-8", newline="") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for row in kept_rows:
                writer.writerow(row)
    except Exception as e:
        log(f"[✖] Błąd przy zapisie przefiltrowanych wierszy: {e}")
        # Jeśli nie można nadpisać, to dopisanie następnie nowych wierszy może powieść się w trybie 'a'

    # Teraz dopisujemy dane nowego meczu
    try:
        mode = "a" if file_exists else "w"
        with open(filename, mode, encoding="utf-8", newline="") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()

            match_id    = match.get("match_id", "")
            match_name  = match.get("match_name", "")
            sport       = match.get("sport", "")
            competition = match.get("competition", "")
            dt          = match.get("datetime", "")

            for market in match.get("markets", []):
                market_name = market.get("market_name", "")
                for sel in market.get("selections", []):
                    row = {
                        "match_id":    match_id,
                        "match_name":  match_name,
                        "sport":       sport,
                        "competition": competition,
                        "datetime":    dt,
                        "market_name": market_name,
                        "outcome":     sel.get("outcome", ""),
                        "odds":        sel.get("odds", ""),
                        "bookmaker":   sel.get("bookmaker", "")
                    }
                    writer.writerow(row)

        log(f"[✔] Podmieniono/stworzono dane meczu {match_id} w pliku CSV: {filename}")
    except Exception as e:
        log(f"[✖] Błąd zapisu nowych wierszy do CSV: {e}")



# ———————————— 
# Główna funkcja scrapująca:
#   - w trybie solo: scrapuje i zapisuje CSV
#   - gdy podano `bot`: dodatkowo po każdym meczu wczytuje oba CSV i wysyła surebety na Discord
# ———————————— 
def main_scrape(bot=None):
    scan_count = 0
    log("START BOTA FORTUNA")

    for league_url in LEAGUES_TO_SCAN:
        log(f"INFO: Pobieram listę meczów z ligi: {league_url}")
        match_links = get_match_links(league_url)
        if not match_links:
            time.sleep(random.randint(*SLEEP_BETWEEN_REQUESTS))
            continue

        for link in match_links:
            log(f"[DEBUG-PAGE] Próbuję sparsować link: {link}")
            details = parse_match_page(link)
            if not details:
                log(f"[DEBUG-PAGE] parse_match_page zwróciło None dla: {link}")
                time.sleep(random.randint(*SLEEP_BETWEEN_REQUESTS))
                continue

            # 1) Dopisanie meczu do CSV
            append_match_to_csv(details, OUT_CSV_FILE)

            # Logowanie szczegółów (details['datetime'] to string w ISO)
            dt_str = details['datetime'] or "data nieznana"
            log(f"INFO: [{details['match_id']}] {details['match_name']} | {details['sport']} | {details['competition']} | {dt_str}")
            for m in details['markets']:
                log(f"    - {m['market_name']} → {len(m['selections'])} selekcji:")
                for sel in m['selections']:
                    log(f"         • {sel['outcome']} @ {sel['odds']}")

            # 2) Gdy podano obiekt bot, natychmiast wczytaj oba CSV i policz surebety
            if bot is not None:
                try:
                    sts_data     = load_csv(config.get('scraping', 'paths', 'sts_csv'))
                    fortuna_data = load_csv(OUT_CSV_FILE)
                    surebets     = compute_surebets(sts_data, fortuna_data)
                except Exception as e:
                    log(f"[FORTUNA-SCRAPER] Błąd load_csv/compute_surebets: {e}")
                    surebets = []

                for sb in surebets:
                    profit = sb.get("profit", 0.0)
                    if profit <= FREE_MAX:
                        channel_id = FREE_CH_ID
                        tag = "[FREE]"
                    elif profit >= PREMIUM_MIN:
                        channel_id = PREMIUM_CH_ID
                        tag = "[PREMIUM]"
                    else:
                        continue

                    content = f"{tag} {format_for_discord(sb)}"
                    channel = bot.get_channel(channel_id)
                    if channel:
                        try:
                            import asyncio
                            fut = asyncio.run_coroutine_threadsafe(channel.send(content), bot.loop)
                            fut.result(timeout=10)
                            log(f"[FORTUNA-SCRAPER] Wysłano surebet na kanał {channel_id}")
                        except Exception as ex:
                            log(f"[FORTUNA-SCRAPER] Błąd wysyłania wiadomości: {ex}")
                    else:
                        log(f"[FORTUNA-SCRAPER] Nie znalazłem kanału o ID {channel_id}")

            # 3) Pauza i zarządzanie limitami
            scan_count += 1
            if scan_count >= SCAN_LIMIT_BEFORE_PAUSE:
                pause = random.randint(*PAUSE_TIME_RANGE)
                log(f"PAUSE: Pauza na {pause}s po {SCAN_LIMIT_BEFORE_PAUSE} skanach")
                time.sleep(pause)
                scan_count = 0

            time.sleep(random.randint(*SLEEP_BETWEEN_REQUESTS))

        # Usuwanie starych z kluczy (MATCH_SKIP_TIME)
        now = datetime.now()
        for key, ts in list(scanned_matches.items()):
            if (now - ts).seconds > MATCH_SKIP_TIME:
                del scanned_matches[key]


# ———————————— 
# Punkt wejścia w trybie solo 
# ———————————— 
if __name__ == '__main__':
    log("URUCHAMIANIE FORTUNA SCRAPERA W TRYBIE SOLO (bez Discorda)")
    main_scrape(bot=None)
