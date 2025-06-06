import asyncio
import traceback
import os, sys


from modules.arbitrage import load_csv, compute_surebets, format_for_discord
from modules.config_manager import ConfigManager

# Globalna zmienna, w której przechowamy referencję do uruchomionego Task‐as
_loop_task = None

def start_loop(bot):
    """
    Uruchamia asynchroniczną pętlę, która co X sekund:
      1. Wczytuje sts_data.csv i fortuna_data.csv
      2. Wywołuje compute_surebets(sts, fortune)
      3. Rozdziela wyniki na Free / Premium (wg progów z config)
      4. Wysyła każdą wiadomość na właściwy kanał Discord
    """
    global _loop_task
    if _loop_task is None:
        _loop_task = asyncio.create_task(_scrape_and_post_loop(bot))

def stop_loop():
    """
    Zatrzymuje pętlę, jeśli jest uruchomiona.
    """
    global _loop_task
    if _loop_task:
        _loop_task.cancel()
        _loop_task = None

async def _scrape_and_post_loop(bot):
    """
    Właściwa implementacja pętli (uruchamiana w tle).
    """
    # Poczekaj, aż bot będzie w pełni zalogowany i gotowy
    await bot.wait_until_ready()

    # Wczytaj ustawienia z config.yaml
    config = ConfigManager("config.yaml")
    interval = config.get('scraping', 'interval')
    # Ścieżki do CSV
    sts_path     = config.get('scraping', 'paths', 'sts_csv')
    fortuna_path = config.get('scraping', 'paths', 'fortuna_csv')
    # Progi procentowe
    free_max     = float(config.get('thresholds', 'free_max'))
    premium_min  = float(config.get('thresholds', 'premium_min'))
    # ID kanałów Discord
    free_ch_id   = int(config.get('discord', 'channels', 'free'))
    premium_ch_id= int(config.get('discord', 'channels', 'premium', 'all'))

    while True:
        try:
            # 1) Poczekaj 'interval' sekund przed kolejnym sprawdzeniem
            await asyncio.sleep(interval)

            # 2) Wczytaj dane z CSV
            sts_data     = load_csv(sts_path)
            fortuna_data = load_csv(fortuna_path)

            # 3) Oblicz surebety
            surebets = compute_surebets(sts_data, fortuna_data)

            # 4) Dla każdego surebetu decyduj, gdzie wysłać
            for sb in surebets:
                profit = sb.get("profit", 0.0)
                # Wybierz kanał wg progu
                if profit <= free_max:
                    channel_id = free_ch_id
                    tag = "[FREE]"
                elif profit >= premium_min:
                    channel_id = premium_ch_id
                    tag = "[PREMIUM]"
                else:
                    # Jeśli mieści się pomiędzy free_max a premium_min, nie wysyłaj
                    continue

                channel = bot.get_channel(channel_id)
                if channel:
                    content = f"{tag} {format_for_discord(sb)}"
                    try:
                        await channel.send(content)
                    except Exception as e:
                        # Jeśli np. brak uprawnień lub błąd podczas wysyłania
                        print(f"⚠️ Błąd przy wysyłaniu na Discord: {e}")
                else:
                    print(f"⚠️ Nie można znaleźć kanału o ID {channel_id}.")

        except asyncio.CancelledError:
            # Ktoś wywołał stop_loop() → przerwij tę pętlę
            break
        except Exception:
            # Wszelkie inne błędy (np. plik CSV nie istnieje, błąd w compute_surebets itp.)
            traceback.print_exc()
            # Poczekaj krótko, żeby nie spamować błędami
            await asyncio.sleep(10)

    print("[MAIN_LOOP] Pętla została zatrzymana.") 
