# modules/main_loop.py

import asyncio
import sys
import os
import traceback
from pathlib import Path

from modules.config_manager import ConfigManager
from modules.arbitrage import load_csv, compute_surebets, format_for_discord

_loop_task = None
_scraper_procs = []

def start_loop(bot):
    """
    Uruchamia asynchroniczny task _scrape_and_post_loop.
    """
    global _loop_task
    if _loop_task is None:
        _loop_task = asyncio.create_task(_scrape_and_post_loop(bot))
        print("[MAIN_LOOP] Uruchomiono task _scrape_and_post_loop")
        return True
    return False

async def stop_loop():
    """
    Zatrzymuje task _scrape_and_post_loop i przerywa wszystkie procesy scrapujące.
    """
    global _loop_task, _scraper_procs

    # 1) anuluj główną pętlę
    if _loop_task:
        print("[MAIN_LOOP] Anuluję task _scrape_and_post_loop…")
        _loop_task.cancel()
        try:
            await _loop_task
        except asyncio.CancelledError:
            pass
        _loop_task = None
        print("[MAIN_LOOP] ✔️ Task _scrape_and_post_loop został zatrzymany")

    # 2) zabij procesy scrapujące
    if _scraper_procs:
        print(f"[MAIN_LOOP] 🛑 Zatrzymuję {_scraper_procs!r}")
        for proc in _scraper_procs:
            if proc.returncode is None:   # wciąż żywy
                proc.terminate()
        _scraper_procs.clear()
        print("[MAIN_LOOP] ✔️ Wszystkie procesy scrapujące zakończone")

    return True

async def _run_scraper(script_path, bot_arg=False, bot=None):
    """
    Wewnętrzne uruchomienie scraperów jako subprocess.
    Jeśli bot_arg=True, przekazuje bot.id jako argument (możesz dostosować, jeśli
    potrzebujesz instancji bot-a w scraperze).
    """
    cmd = [sys.executable, str(script_path)]
    if bot_arg and bot is not None:
        cmd.append(str(bot.user.id))

    # uruchom i pilnuj w globalnej liście
    proc = await asyncio.create_subprocess_exec(*cmd)
    _scraper_procs.append(proc)
    await proc.wait()
    # po zakończeniu usuń z listy
    _scraper_procs.remove(proc)

async def _scrape_and_post_loop(bot):
    """
    1) Co interwał uruchamia procesy: scraper_fortuna.py i scraper_sts.py
    2) Po zakończeniu obu procesów czeka 2 s, ładuje CSV i generuje surebety
    3) Wysyła na Discord nowe surebety
    4) Odkłada się na koniec interwału
    """
    await bot.wait_until_ready()
    print("[MAIN_LOOP] Bot jest ready, startuję loop")

    config       = ConfigManager("config.yaml")
    interval     = float(config.get('scraping', 'interval'))
    sts_csv      = config.get('scraping', 'paths', 'sts_csv')
    fortuna_csv  = config.get('scraping', 'paths', 'fortuna_csv')
    free_max     = float(config.get('thresholds', 'free_max'))
    premium_min  = float(config.get('thresholds', 'premium_min'))
    free_ch_id   = int(config.get('discord', 'channels', 'free'))
    premium_ch_id= int(config.get('discord', 'channels', 'premium', 'all'))

    # ścieżki do skryptów
    base = Path(__file__).parent
    fortuna_py = base / "scraper_fortuna.py"
    sts_py     = base / "scraper_sts.py"

    processed = set()

    while True:
        try:
            print("[MAIN_LOOP] ► Startuję oba scrapery jako subprocessy…")
            await asyncio.gather(
                _run_scraper(fortuna_py),
                _run_scraper(sts_py, bot_arg=True, bot=bot),
            )
            print("[MAIN_LOOP] ✔️ Procesy scrapujące zakończone.")

            print("[MAIN_LOOP] Czekam 2 sek. na zapis CSV…")
            await asyncio.sleep(2)

            try:
                d_sts     = load_csv(sts_csv)
                d_fortuna= load_csv(fortuna_csv)
            except Exception as e:
                print(f"[MAIN_LOOP-ERROR] błąd CSV: {e}")
                traceback.print_exc()
                d_sts, d_fortuna = {}, {}

            try:
                surebety = compute_surebets(d_sts, d_fortuna)
            except Exception as e:
                print(f"[MAIN_LOOP-ERROR] błąd compute_surebets: {e}")
                traceback.print_exc()
                surebety = []

            for sb in surebety:
                mid    = sb["match_id"]
                profit = sb.get("profit", 0.0)
                if mid in processed or profit <= 0:
                    continue

                if profit <= free_max:
                    ch_id, tag = free_ch_id, "[FREE]"
                elif profit >= premium_min:
                    ch_id, tag = premium_ch_id, "[PREMIUM]"
                else:
                    continue

                channel = bot.get_channel(ch_id)
                if channel:
                    txt = f"{tag} {format_for_discord(sb)}"
                    fut = asyncio.run_coroutine_threadsafe(channel.send(txt), bot.loop)
                    try:
                        fut.result(timeout=10)
                        print(f"[MAIN_LOOP] Wysłano {mid} ({profit:.2f}%)")
                    except Exception as e:
                        print(f"[MAIN_LOOP-ERROR] wysyłka: {e}")
                processed.add(mid)

            print(f"[MAIN_LOOP] Czekam {interval}s przed kolejnym cyklem…")
            await asyncio.sleep(interval)

        except asyncio.CancelledError:
            print("[MAIN_LOOP] Otrzymałem CancelledError, przerywam pętlę")
            break

        except Exception:
            print("[MAIN_LOOP-ERROR] nieprzewidziany błąd w pętli:")
            traceback.print_exc()
            await asyncio.sleep(10)

    print("[MAIN_LOOP] Pętla zakończona")
