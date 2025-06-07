import requests
import random
import time
from datetime import datetime, timedelta
import sys
import os
import csv
import warnings
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# Opcjonalnie ignorujemy ostrzeżenia przy parsowaniu dat bez roku
warnings.filterwarnings(
    "ignore",
    message=r"Parsing dates involving a day of month without a year specified is ambiguious"
)

# Upewnij się, że ścieżka do modułów jest poprawna:
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from modules.config_manager import ConfigManager
from modules.proxy_manager import ProxyManager

# ————————————
# Stałe konfiguracyjne
# ————————————
SCAN_LIMIT_BEFORE_PAUSE    = 12
PAUSE_TIME_RANGE           = (500, 700)      # 7–12 minut
SLEEP_BETWEEN_REQUESTS     = (11, 17)        # 11–17 s między meczami
MATCH_SKIP_TIME            = 2700            # 45 minut

BASE_URL                   = "https://www.sts.pl"
LOG_FILE                   = "bot_log_sts.txt"
OUT_CSV_FILE               = "sts_data.csv"

# Teraz definiujemy listę lig do skanowania:
LEAGUES_TO_SCAN = [
    #"https://www.sts.pl/zaklady-bukmacherskie/pilka-nozna/brazylia/1-liga/184/30863/86452",
    #"https://www.sts.pl/zaklady-bukmacherskie/pilka-nozna/argentyna/2-liga/184/31033/88200",
    "https://www.sts.pl/zaklady-bukmacherskie/pilka-nozna/miedzynarodowe/liga-narodow-uefa/184/30851/86422",
]

# ————————————
# Dozwolone rynki (market names) – analogicznie do Fortuny
# Wszystkie nazwy w małych literach
# ————————————

config = ConfigManager("config.yaml")
proxy_manager = ProxyManager(config)
scanned_matches = {}

# Mapowanie polskich dni tygodnia na indeks (0=poniedziałek, ..., 6=niedziela)
WEEKDAY_MAP = {
    "poniedziałek": 0,
    "wtorek":      1,
    "środa":       2,
    "czwartek":    3,
    "piątek":      4,
    "sobota":      5,
    "niedziela":   6
}

# ————————————
# Funkcja logująca
# ————————————
def log(message: str):
    """
    Logowanie z rotacją pliku (maks. 1000 ostatnich linii).
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > 1500:
            with open(LOG_FILE, "w", encoding="utf-8") as f2:
                f2.writelines(lines[-1000:])
    except Exception:
        pass

# ————————————
# Obcinanie i podmiana wierszy w CSV
# ————————————
def append_match_to_csv(match: dict, filename: str):
    """
    1) Jeśli plik istnieje: wczytuje wszystkie wiersze CSV, filtruje je
       - usuwa wiersze starsze niż 24 godziny (na podstawie pola 'datetime')
       - usuwa wiersze z identycznym 'match_id' (żeby podmienić wcześniejsze)
    2) Nadpisuje plik nagłówkiem + przefiltrowanymi wierszami.
    3) Dopisuje wiersze dla aktualnego meczu.
    Jeśli pliku jeszcze nie ma: tworzy go, wpisuje nagłówek i wiersze meczu.
    """
    dir_name = os.path.dirname(filename)
    if dir_name and not os.path.exists(dir_name):
        os.makedirs(dir_name, exist_ok=True)

    fieldnames = [
        "match_id", "match_name", "sport", "competition", "datetime",
        "market", "selection", "odds", "bookmaker"
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
                    try:
                        row_dt = datetime.strptime(row["datetime"], "%Y-%m-%dT%H:%M:%S")
                    except Exception:
                        continue
                    if row_dt < threshold_dt:
                        continue
                    if row["match_id"] == match["match_id"]:
                        continue
                    kept_rows.append(row)
        except Exception as e:
            log(f"[✖] Błąd przy czytaniu i filtrowaniu starego CSV: {e}")

    try:
        with open(filename, "w", encoding="utf-8", newline="") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for row in kept_rows:
                writer.writerow(row)
    except Exception as e:
        log(f"[✖] Błąd przy zapisie przefiltrowanych wierszy do CSV: {e}")

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
            dt_val      = match.get("datetime")
            if isinstance(dt_val, datetime):
                dt_str = dt_val.strftime("%Y-%m-%dT%H:%M:%S")
            else:
                dt_str = dt_val or ""

            for m in match.get("markets", []):
                market    = m.get("market", "")
                selection = m.get("selection", "")
                odds      = m.get("odds", "")
                row = {
                    "match_id":    match_id,
                    "match_name":  match_name,
                    "sport":       sport,
                    "competition": competition,
                    "datetime":    dt_str,
                    "market":      market,
                    "selection":   selection,
                    "odds":        odds,
                    "bookmaker":   "STS"
                }
                writer.writerow(row)

        log(f"[✔] Podmieniono/stworzono dane meczu {match.get('match_id')} w pliku CSV: {filename}")
    except Exception as e:
        log(f"[✖] Błąd zapisu nowych wierszy do CSV: {e}")

# ————————————
# Funkcje parsujące (działają poprawnie – nie ruszać)
# ————————————
def next_date_for_weekday(weekday_index: int) -> datetime.date:
    """
    Zwraca najbliższą przyszłą (lub dzisiejszą) datę odpowiadającą podanemu indeksowi dnia tygodnia.
    """
    today = datetime.now().date()
    today_index = today.weekday()
    days_ahead = (weekday_index - today_index) % 7
    return today + timedelta(days=days_ahead)

def parse_match_datetime(date_txt: str, time_txt: str):
    """
    Parsuje tekst daty i godziny ze strony STS i zwraca obiekt datetime.
    - date_txt może być: "Dzisiaj", "Jutro", "Poniedziałek,", "DD.MM.YYYY", "DD.MM" lub None.
    - time_txt: "HH:MM" lub None.
    """
    now = datetime.now()
    date_obj = None

    if not date_txt:
        date_obj = now.date()
    else:
        txt = date_txt.strip().lower().rstrip(',')
        if "dziś" in txt or "dzisiaj" in txt:
            date_obj = now.date()
        elif "jutro" in txt:
            date_obj = (now + timedelta(days=1)).date()
        elif txt in WEEKDAY_MAP:
            date_obj = next_date_for_weekday(WEEKDAY_MAP[txt])
        else:
            try:
                date_obj = datetime.strptime(date_txt.strip(), "%d.%m.%Y").date()
            except ValueError:
                try:
                    temp = datetime.strptime(date_txt.strip(), "%d.%m").date()
                    date_obj = temp.replace(year=now.year)
                    if date_obj < now.date():
                        date_obj = date_obj.replace(year=now.year + 1)
                except ValueError:
                    return None

    try:
        time_obj = datetime.strptime(time_txt, "%H:%M").time() if time_txt else datetime.min.time()
    except (ValueError, TypeError):
        time_obj = datetime.min.time()

    return datetime.combine(date_obj, time_obj)

def make_match_id(match_name: str, dt: datetime) -> str:
    """
    Generuje unikalne ID w postaci:
      dwie pierwsze litery drużyny domowej + dwie pierwsze litery drużyny gościa
      + DDMMHHMM (bez roku).
    """
    try:
        home, away = [x.strip() for x in match_name.split("-", 1)]
    except ValueError:
        home = match_name.strip()
        away = ""
    h = (home.replace(" ", "")[:2].upper() if len(home.replace(" ", "")) >= 2 else (home[:1].upper() + "_"))
    a = (away.replace(" ", "")[:2].upper() if len(away.replace(" ", "")) >= 2 else (away[:1].upper() + "_"))
    key_teams = h + a
    key_date = dt.strftime("%d%m%H%M")  # ddmmHHMM
    return f"{key_teams}{key_date}"

def get_match_links(league_url: str):
    """
    Scrapuje linki do poszczególnych meczów z podanej strony ligi STS.
    """
    links = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            kwargs = proxy_manager.get_request_kwargs()
            ua = kwargs.get("headers", {}).get("User-Agent", "")
            ctx_args = {}
            proxy = kwargs.get("proxies")
            if proxy:
                server = proxy if isinstance(proxy, str) else next(iter(proxy.values()), None)
                if server:
                    ctx_args["proxy"] = {"server": server}
                    log(f"PLAYWRIGHT używa proxy: {server}")

            context = browser.new_context(user_agent=ua, **ctx_args)
            log(f"PLAYWRIGHT używa User-Agent: {ua}")

            page = context.new_page()
            log(f"PLAYWRIGHT (liga): Ładowanie {league_url}")
            try:
                response = page.goto(league_url, timeout=8000)
                if response:
                    log(f"PLAYWRIGHT (liga): Status HTTP: {response.status}")
                page.wait_for_timeout(1100)
                for _ in range(5):
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(1200)
            except PlaywrightTimeoutError as e:
                log(f"PLAYWRIGHT WARN (liga): {e}")

            anchors = page.query_selector_all("bb-prematch-match-tile a")
            log(f"PLAYWRIGHT (liga): Znaleziono {len(anchors)} linków do meczów.")
            for a in anchors:
                href = a.get_attribute("href")
                if href and href.startswith("/kursy/"):
                    full_url = requests.compat.urljoin(BASE_URL, href)
                    links.append(full_url)
                    log(f"Dodano link: {full_url}")

            context.close()
            browser.close()
    except Exception as e:
        log(f"PLAYWRIGHT ERROR (liga): {e}")

    return links

def fetch_markets_with_playwright(match_url: str):
    """
    Otwiera stronę meczu i przewija CAŁĄ stronę stopniowo, aż placeholdery
    <bb-loading-match> zostaną zastąpione przez rzeczywiste
    <div.match-details-group__container>. Loguje przy każdej iteracji
    liczbę loaderów i liczbę wyrenderowanych kontenerów. Kończy, gdy liczba
    kontenerów w DOM-ie przestanie rosnąć.

    Dodatkowo: filtruje tylko te rynki, których nazwa w lower() znajduje się
    na liście ALLOWED_MARKETS.
    """
    markets = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            kwargs = proxy_manager.get_request_kwargs()
            ua = kwargs.get("headers", {}).get("User-Agent", "")
            ctx_args = {}
            proxy = kwargs.get("proxies")
            if proxy:
                server = proxy if isinstance(proxy, str) else next(iter(proxy.values()), None)
                if server:
                    ctx_args["proxy"] = {"server": server}
                    log(f"PLAYWRIGHT używa proxy: {server}")

            context = browser.new_context(user_agent=ua, **ctx_args)
            log(f"PLAYWRIGHT używa User-Agent: {ua}")

            page = context.new_page()
            log(f"PLAYWRIGHT (mecz): Ładowanie {match_url}")
            try:
                response = page.goto(match_url, timeout=4500)
                if response:
                    log(f"PLAYWRIGHT (mecz): Status HTTP: {response.status}")
                page.wait_for_selector(".shirts-container .detailed-scoreboard__container", timeout=4500)
            except PlaywrightTimeoutError:
                log("PLAYWRIGHT WARN (nagłówek): header nie załadował się w 10 s, kontynuuję.")

            # Dajemy chwilę na wstępne wczytanie (pierwsze grupy + placeholdery)
            page.wait_for_timeout(500)

            # Wstrzykiwanie własnego pola "wyszukaj" do DOM, aby symulować Ctrl+F
            page.evaluate("""
              if (!document.getElementById('playwrightSearch')) {
                const input = document.createElement('input');
                input.id = 'playwrightSearch';
                input.style.position = 'fixed';
                input.style.top = '10px';
                input.style.left = '10px';
                input.style.zIndex = '9999';
                input.style.padding = '4px';
                input.style.background = 'white';
                input.style.border = '1px solid #888';
                input.placeholder = 'Wyszukaj bb-loading-match...';
                document.body.appendChild(input);

                let lastIndex = 0;
                let matches = [];

                input.addEventListener('keydown', async (e) => {
                  if (e.key === 'Enter') {
                    const term = input.value.trim();
                    if (!term) {
                      matches = [];
                      lastIndex = 0;
                      return;
                    }
                    matches = Array.from(document.querySelectorAll(term));
                    lastIndex = 0;
                    if (matches.length > 0) {
                      matches[0].scrollIntoView({ block: 'center' });
                      matches[0].style.outline = '2px solid orange';
                    }
                  } else if (e.key === 'F3' || (e.key === 'ArrowDown' && e.ctrlKey)) {
                    if (matches.length > 0) {
                      matches[lastIndex].style.outline = '';
                      lastIndex = (lastIndex + 1) % matches.length;
                      matches[lastIndex].scrollIntoView({ block: 'center' });
                      matches[lastIndex].style.outline = '2px solid orange';
                    }
                  }
                });
              }
            """)

            # Zliczenie faktycznej liczby placeholderów <bb-loading-match>
            initial_loaders = len(page.query_selector_all("bb-loading-match"))
            log(f"PLAYWRIGHT DEBUG: Placeholderów do przewinięcia: {initial_loaders}")

            # Automatyczne użycie wstrzykniętego pola do przewinięcia każdego loadera
            page.fill('#playwrightSearch', 'bb-loading-match')
            page.press('#playwrightSearch', 'Enter')
            page.wait_for_timeout(300)

            # Przewinięcie w zależności od liczby placeholderów (+2 dla pewności)
            for _ in range(initial_loaders + 2):
                page.press('#playwrightSearch', 'F3')
                page.wait_for_timeout(200)

            # Teraz przechodzimy do normalnego scrollowania w dół
            prev_container_count = -1
            stable_loops = 0
            log("PLAYWRIGHT: Rozpoczynam stopniowe przewijanie, aż wszystkie loadery zostaną wymienione…")

            max_scrolls = initial_loaders + 5
            for step in range(max_scrolls):
                page.evaluate("window.scrollBy(0, window.innerHeight);")
                page.wait_for_timeout(250)

                loader_count = len(page.query_selector_all("bb-loading-match"))
                container_count = len(page.query_selector_all("div.match-details-group__container"))
                log(f"    [SCROLL] krok={step+1}, placeholderów={loader_count}, wyrenderowanych grup={container_count}")

                if container_count == prev_container_count and loader_count == 0:
                    stable_loops += 1
                else:
                    stable_loops = 0
                    prev_container_count = container_count

                if stable_loops >= 2 and loader_count == 0:
                    log(f"    [SCROLL] Liczba grup ustabilizowała się na {container_count}, przerywam.")
                    break

            page.wait_for_timeout(300)

            groups = page.query_selector_all("div.match-details-group__container")
            final_count = len(groups)
            log(f"PLAYWRIGHT (mecz): OSTATECZNIE znaleziono {final_count} grup rynków.")
            if final_count < 30:
                html0 = groups[0].inner_html() if groups else ""
                log(f"PLAYWRIGHT DEBUG: Tylko {final_count} grup. HTML pierwszej: {html0[:200]}…")

            markets = []
            for idx, grp in enumerate(groups, start=1):
                try:
                    title_el = grp.query_selector(".match-details-group__title div")
                    if not title_el:
                        continue
                    mkt_name = title_el.inner_text().strip().lower()

                    # *** Filtrujemy tylko ALLOWED_MARKETS ***
                    if not mkt_name :
                        continue

                    buttons = grp.query_selector_all("sds-odds-button")
                    if not buttons:
                        log(f"    WARN: Rynek '{mkt_name}' — brak <sds-odds-button>")
                        continue

                    count_in_group = 0
                    for btn in buttons:
                        label_el = btn.query_selector(".odds-button__label span")
                        odd_el   = btn.query_selector(".odds-button__odd-value")
                        sel_txt = label_el.inner_text().strip() if label_el else None
                        val_txt = odd_el.inner_text().strip() if odd_el else None

                        if sel_txt and val_txt and val_txt not in ("0", "0.0", "-", "–"):
                            markets.append({
                                "market":    mkt_name,
                                "selection": sel_txt.lower(),
                                "odds":      val_txt
                            })
                            count_in_group += 1

                    if count_in_group > 0 and (idx <= 5 or idx % 10 == 0):
                        log(f"    Grupa {idx}: '{mkt_name}' — dodano {count_in_group} kursów")
                except Exception as e:
                    log(f"    WARN (przy grupie {idx}): {e}")

            log(f"PLAYWRIGHT: Zebrano łącznie {len(markets)} kursów z {final_count} grup rynków.")

            context.close()
            browser.close()
            return markets

    except Exception as e:
        log(f"PLAYWRIGHT ERROR (mecz): {e}")
        return []

def parse_match_page(match_url: str):
    """
    – Scrapujemy nagłówek meczu (sport, liga, drużyny, data/godzina),
    – Następnie wywołujemy fetch_markets_with_playwright(), aby zebrać wszystkie kursy.
    Zwracany słownik ma teraz klucz 'match_id'.
    """
    result = {
        "match_name":  None,
        "sport":       None,
        "competition": None,
        "datetime":    None,
        "markets":     [],
        "match_id":    None,
    }

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            kwargs = proxy_manager.get_request_kwargs()
            ua = kwargs.get("headers", {}).get("User-Agent", "")
            ctx_args = {}
            proxy = kwargs.get("proxies")
            if proxy:
                server = proxy if isinstance(proxy, str) else next(iter(proxy.values()), None)
                if server:
                    ctx_args["proxy"] = {"server": server}
                    log(f"PLAYWRIGHT używa proxy: {server}")
            context = browser.new_context(user_agent=ua, **ctx_args)
            log(f"PLAYWRIGHT używa User-Agent: {ua}")

            page = context.new_page()
            log(f"PLAYWRIGHT (mecz nagłówek): Ładowanie strony: {match_url}")
            try:
                response = page.goto(match_url, timeout=3800)
                if response:
                    log(f"PLAYWRIGHT (nagłówek): Status HTTP: {response.status}")
                page.wait_for_timeout(900)
            except PlaywrightTimeoutError:
                log("PLAYWRIGHT WARN (nagłówek): header nie załadował się w 10 s.")

            left_el = page.query_selector(".team-container .detailed-scoreboard__bold-label span")
            if left_el:
                # === Brazylia ===
                labels = page.query_selector_all("div.breadcrumb-container__label")
                if len(labels) >= 2:
                    result["sport"]       = labels[0].inner_text().strip()
                    result["competition"] = labels[1].inner_text().strip()

                right_el = page.query_selector(".team-container.team-container--right .detailed-scoreboard__bold-label span")
                if right_el:
                    left  = left_el.inner_text().strip()
                    right = right_el.inner_text().strip()
                    result["match_name"] = f"{left} - {right}"
                else:
                    result["match_name"] = left_el.inner_text().strip()

                # Pełna data z <span> wewnątrz .detailed-scoreboard__sub-label
                date_span = page.query_selector(".detailed-scoreboard__sub-label span")
                date_txt  = date_span.inner_text().strip() if date_span else None

                time_el   = page.query_selector(".detailed-scoreboard__sub-label--highlight span")
                time_txt  = time_el.inner_text().strip() if time_el else None

                log(f"    [DEBUG BRAZYLIA] Surowe date_txt='{date_txt}', time_txt='{time_txt}'")
                result["datetime"] = parse_match_datetime(date_txt, time_txt)

            else:
                # === Argentyna (lub inna liga o podobnej strukturze) ===
                header_span = page.query_selector("div.team-names.detailed-scoreboard__bold-label span")
                if header_span:
                    result["match_name"] = header_span.inner_text().strip()

                labels = page.query_selector_all("div.breadcrumb-container__label")
                if len(labels) >= 2:
                    result["sport"]       = labels[0].inner_text().strip()
                    result["competition"] = labels[1].inner_text().strip()

                container = page.query_selector("div.detailed-scoreboard__sub-label")
                if container:
                    spans = container.query_selector_all("span")
                    if len(spans) >= 2:
                        day_txt  = spans[0].inner_text().strip()
                        time_txt = spans[-1].inner_text().strip()
                        log(f"    [DEBUG ARGENTYNA] day_txt='{day_txt}', time_txt='{time_txt}'")
                        result["datetime"] = parse_match_datetime(day_txt, time_txt)
                    else:
                        log("    [DEBUG ARGENTYNA] Nie znaleziono wystarczającej liczby <span> w detailed-scoreboard__sub-label")
                        result["datetime"] = None

            context.close()
            browser.close()
    except Exception as e:
        log(f"PLAYWRIGHT ERROR (nagłówek meczu): {e}")
        return None

    if result["match_name"] and result["datetime"]:
        result["match_id"] = make_match_id(result["match_name"], result["datetime"])

    result["markets"] = fetch_markets_with_playwright(match_url)
    return result

# ————————————
# Główna pętla “ciągłego skanowania”
# ————————————
def main():
    scan_count = 0

    log("START BOTA STS (ciągłe skanowanie + zapisywanie do CSV)")
    while True:
        for league_url in LEAGUES_TO_SCAN:
            log(f"INFO: Pobieram listę meczów z ligi: {league_url}")
            match_links = get_match_links(league_url)
            if not match_links:
                time.sleep(random.randint(*SLEEP_BETWEEN_REQUESTS))
                continue

            for link in match_links:
                details = parse_match_page(link)
                if not details or not details.get("match_id"):
                    time.sleep(random.randint(*SLEEP_BETWEEN_REQUESTS))
                    continue

                log_msg = f"INFO: [{details['match_id']}] {details['match_name']} | {details['sport']} | {details['competition']}"
                if details["datetime"]:
                    log_msg += f" | {details['datetime'].strftime('%d.%m.%Y %H:%M')}"
                else:
                    log_msg += " | data/godzina nieznana"
                log(log_msg)

                if details["markets"]:
                    for m in details["markets"]:
                        log(f"    - {m['market']} / {m['selection']} @ {m['odds']}")
                else:
                    log("    Brak rynków / kursów na stronie meczu")

                append_match_to_csv(details, OUT_CSV_FILE)

                scanned_matches[details["match_id"]] = datetime.now()
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

if __name__ == "__main__":
    main()
# Dodaj poniższą funkcję do pliku surebet-bot/modules/scraper_sts.py

def main_scrape(bot):
    print("[scraper_sts] ► START sts_main_scrape()", flush=True)
    """
    Jednorazowy przebieg skanowania lig z LEAGUES_TO_SCAN:
    - pobieranie linków do meczów z każdej ligi
    - dla każdego linku parsowanie strony meczu
    - zapisywanie wyników do CSV (append_match_to_csv)
    - pauzy między żądaniami i okresowa pauza po SCAN_LIMIT_BEFORE_PAUSE skanach
    - po zakończeniu pętli lig oczyszczanie stale trzymanych match_id, by móc je pobrać ponownie po MATCH_SKIP_TIME
    """
    scan_count = 0

    log("START BOTA STS (jednorazowe skanowanie + zapisywanie do CSV)")

    for league_url in LEAGUES_TO_SCAN:
        log(f"INFO: Pobieram listę meczów z ligi: {league_url}")
        match_links = get_match_links(league_url)
        if not match_links:
            # jeśli nie udało się pobrać żadnych linków, odczekaj chwilę i idź dalej
            time.sleep(random.randint(*SLEEP_BETWEEN_REQUESTS))
            continue

        for link in match_links:
            details = parse_match_page(link)
            if not details or not details.get("match_id"):
                log(f"[DEBUG] Brak danych z parse_match_page dla: {link}")
                time.sleep(random.randint(*SLEEP_BETWEEN_REQUESTS))
                continue

            # Log podstawowych informacji o meczu
            log_msg = (
                f"INFO: [{details['match_id']}] {details['match_name']} | "
                f"{details['sport']} | {details['competition']}"
            )
            if details["datetime"]:
                log_msg += f" | {details['datetime'].strftime('%d.%m.%Y %H:%M')}"
            else:
                log_msg += " | data/godzina nieznana"
            log(log_msg)

            # Jeśli są jakieś rynki/ kursy, pokaż je w logu
            if details.get("markets"):
                for m in details["markets"]:
                    log(f"    - {m['market']} / {m['selection']} @ {m['odds']}")
            else:
                log("    Brak rynków / kursów na stronie meczu")

            # Zapisz dane meczu do pliku CSV (podmiana starszych wierszy, usunięcie wpisów starszych niż 24h)
            append_match_to_csv(details, OUT_CSV_FILE)

            # Odnotuj, że ten match_id był zeskanowany w tej iteracji
            scanned_matches[details["match_id"]] = datetime.now()
            scan_count += 1

            # Po pewnej liczbie skanów zrób dłuższą pauzę
            if scan_count >= SCAN_LIMIT_BEFORE_PAUSE:
                pause = random.randint(*PAUSE_TIME_RANGE)
                log(f"PAUSE: Pauza na {pause}s po {SCAN_LIMIT_BEFORE_PAUSE} skanach")
                time.sleep(pause)
                scan_count = 0

            # Krótka pauza między kolejnymi meczami
            time.sleep(random.randint(*SLEEP_BETWEEN_REQUESTS))

        # Po zakończeniu przetwarzania jednej ligi, usuń z scanned_matches te match_id,
        # które były zeskanowane ponad MATCH_SKIP_TIME sekund temu,
        # aby w przyszłości mogły być pobrane ponownie
        now = datetime.now()
        for key, ts in list(scanned_matches.items()):
            if (now - ts).seconds > MATCH_SKIP_TIME:
                del scanned_matches[key]
