import logging
from discord.ext import tasks
from modules.scraper_etoto import get_surebets
from modules.config_manager import ConfigManager

# Konfiguracja loggera - logi pojawią się w konsoli
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

config = ConfigManager('config.yaml')

# Pobieramy i rzutujemy interwał na int lub float (np. 300 sekund)
interval = float(config.get('scraping', 'interval'))

@tasks.loop(seconds=interval)
async def main_loop(bot):
    surebets = get_surebets()
    free_max    = float(config.get('thresholds', 'free_max'))
    premium_min = float(config.get('thresholds', 'premium_min'))

    for sb in surebets:
        value = sb.get('value', 0) or 0
        msg = f"📈 {sb['match']} | {sb['odds']} | value:{value}%"

        # Logujemy surebety do konsoli (lub pliku, jeśli skonfigurujesz loggera)
        logging.info(msg)

        # Sprawdzamy wartość surebetu, wysyłamy tylko jeśli wartość jest w progach
        if value <= free_max:
            ch_id = int(config.get('discord', 'channels', 'free'))
        elif value >= premium_min:
            ch_id = int(config.get('discord', 'channels', 'premium', 'all'))
        else:
            continue

        channel = bot.get_channel(ch_id)
        if channel:
            await channel.send(msg)
        else:
            logging.warning(f"Nie znaleziono kanału Discord o ID: {ch_id}")

def start_loop(bot):
    if not main_loop.is_running():
        main_loop.start(bot)

def stop_loop():
    if main_loop.is_running():
        main_loop.stop()
