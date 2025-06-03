import discord
from discord.ext import commands
from modules.config_manager import ConfigManager
from modules.discord_commands import setup_commands

# Załaduj konfigurację
config = ConfigManager('config.yaml')

TOKEN = config.get('discord', 'token')
if not TOKEN:
    raise ValueError("Brak tokena w pliku konfiguracyjnym!")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'Bot zalogowany jako: {bot.user} (ID: {bot.user.id})')
    # nie startujemy tu automatycznie main_loop

# Rejestracja wszystkich komend z modułu
setup_commands(bot, config)

if __name__ == "__main__":
    bot.run(TOKEN)
