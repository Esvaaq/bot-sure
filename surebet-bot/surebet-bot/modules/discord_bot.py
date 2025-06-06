# discord_bot.py

import logging
import os

import discord
from discord.ext import commands

from modules.config_manager import ConfigManager
from modules.discord_commands import setup_commands

# ————————————
# Ustawienia logowania
# ————————————
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# Wczytujemy konfigurację (ścieżkę do tokena możemy również trzymać w config.yaml lub w ENV)
config = ConfigManager("config.yaml")

# Pobierz token z pliku config.yaml lub ze zmiennej środowiskowej
DISCORD_TOKEN = config.get("discord", "token") or os.getenv("DISCORD_TOKEN", "")

if not DISCORD_TOKEN:
    logging.error("Nie ustawiono tokena Discorda w config.yaml ani DISCORD_TOKEN.")
    exit(1)

# Intencje – wystarczy default (nie potrzebujemy czytać treści cudzych wiadomości)
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# Podłączamy komendy (zdefiniowane w modules/discord_commands.py)
setup_commands(bot, config)

@bot.event
async def on_ready():
    logging.info(f"Zalogowano jako: {bot.user} (ID: {bot.user.id})")

if __name__ == "__main__":
    logging.info("Uruchamiam bota Discord...")
    bot.run(DISCORD_TOKEN)
