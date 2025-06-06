# modules/main_loop.py

import asyncio
import traceback

from modules.arbitrage import load_csv, compute_surebets, format_for_discord
from modules.config_manager import ConfigManager

# Importujemy Twoje scrapery
from modules.scraper_fortuna import main_scrape as fortuna_main_scrape
from modules.scraper_sts import main_scrape as sts_main_scrape

_loop_task = None

def start_loop(bot):
    """
    Uruchamia asynchroniczny task _scrape_and_post_loop.
    """
    global _loop_task
    if _loop_task is None:
        _loop_task = asyncio.create_task(_scrape_and_post_loop(bot))
        print("[MAIN_LOOP] Uruchomiono task _scrape_and_post_loop")

def stop_loop():
    """
    Zatrzymuje task _scrape_and_post_loop.
    """
    global _loop_task
    if _loop_task:
        _loop_task.cancel()
        _loop_task = None
        print("[MAIN_LOOP] Zatrzymano task _scrape_and_post_loop")

async def _scrape_and_post_loop(bot):
    print("[MAIN_LOOP] Wejście do _scrape_and_post_loop(), czekam na bot ready...")
    await bot.wait_until_ready()
    print("[MAIN_LOOP] Bot jest ready, rozpoczynam pętlę")

    config = ConfigManager("config.yaml")
    interval      = float(config.get('scraping', 'interval'))
    sts_path      = config.get('scraping', 'paths', 'sts_csv')
    fortuna_path  = config.get('scraping', 'paths', 'fortuna_csv')
    free_max      = float(config.get('thresholds', 'free_max'))
    premium_min   = float(config.get('thresholds', 'premium_min'))
    free_ch_id    = int(config.get('discord', 'channels', 'free'))
    premium_ch_id = int(config.get('discord', 'channels', 'premium', 'all'))

    while True:
        try:
            # 1) Czekamy zadeklarowany interwał
            print(f"[MAIN_LOOP] Czekam {interval} sekund...")
            await asyncio.sleep(interval)

            # 2) Najpierw odpalenie scrapów w tle (nie blokujemy event-loopa)
            print("[MAIN_LOOP] Uruchamiam scraping Fortuny i STS…")
            # Aby to zrobić w tle, przerzucamy wywołanie do wątku
            await asyncio.to_thread(fortuna_main_scrape)
            await asyncio.to_thread(sts_main_scrape)
            print("[MAIN_LOOP] Scrapowanie zakończone, czekam 2 sekundy na zapis CSV…")
            await asyncio.sleep(2)

            # 3) Teraz wczytujemy świeże pliki CSV
            print(f"[MAIN_LOOP] Wczytuję CSV:\n  sts: {sts_path}\n  fortuna: {fortuna_path}")
            sts_data = load_csv(sts_path)
            fortuna_data = load_csv(fortuna_path)
            print(f"[MAIN_LOOP] Wczytane CSV: sts={len(sts_data)} wpisów, fortuna={len(fortuna_data)} wpisów")

            # 4) Obliczamy surebety
            surebets = compute_surebets(sts_data, fortuna_data)
            print(f"[MAIN_LOOP] compute_surebets zwróciło {len(surebets)} surebet(ów)")

            # 5) Wysyłamy ewentualne wyniki na Discord
            for sb in surebets:
                profit = sb.get("profit", 0.0)
                if profit <= free_max:
                    channel_id = free_ch_id
                    tag = "[FREE]"
                elif profit >= premium_min:
                    channel_id = premium_ch_id
                    tag = "[PREMIUM]"
                else:
                    continue  # mieści się pomiędzy free_max i premium_min → pomijamy

                print(f"[MAIN_LOOP] Wysyłam surebet: {sb['match_name']} (profit {profit}%) tag={tag}")
                channel = bot.get_channel(channel_id)
                if channel:
                    content = f"{tag} {format_for_discord(sb)}"
                    try:
                        await channel.send(content)
                        print(f"[MAIN_LOOP] Wysłano wiadomość na kanał ID {channel_id}")
                    except Exception as e:
                        print(f"[MAIN_LOOP] ⚠️ Błąd przy wysyłaniu: {e}")
                else:
                    print(f"[MAIN_LOOP] ⚠️ Nie mogę znaleźć kanału o ID {channel_id}")

        except asyncio.CancelledError:
            print("[MAIN_LOOP] Otrzymałem CancelledError – przerywam pętlę")
            break

        except Exception:
            traceback.print_exc()
            print("[MAIN_LOOP] Błąd w pętli – spróbuję ponownie za 10 sek.")
            await asyncio.sleep(10)

    print("[MAIN_LOOP] Pętla _scrape_and_post_loop zakończona")
