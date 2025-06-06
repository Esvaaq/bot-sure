# modules/discord_commands.py

from discord.ext import commands
from modules.main_loop import start_loop, stop_loop

def setup_commands(bot, config):
    @bot.command(name='ping')
    async def ping(ctx):
        await ctx.send('Pong!')

    @bot.command(name='shelp')
    async def shellp(ctx):
        help_text = (
            "__**Komendy Surebet-Bot:**__\n"
            "`!ping` — test połączenia\n"
            "`!shelp` — ta wiadomość pomocy\n"
            "`!showconfig` — pokaż obecną konfigurację\n"
            "`!setlimit free <value>` — ustaw limit free-max (%)\n"
            "`!setlimit premium <value>` — ustaw limit premium-min (%)\n"
            "`!setchannel free <channel_id>` — zmień kanał Free\n"
            "`!setchannel premium_all <channel_id>` — zmień kanał Premium ALL\n"
            "`!setinterval <seconds>` — zmień interwał scrapowania\n"
            "`!post <surebet_data>` — ręcznie wyślij surebet na odpowiedni kanał\n"
            "`!start` — włącz pętlę scrapowania\n"
            "`!stop` — wyłącz pętlę scrapowania\n"
        )
        await ctx.send(help_text)

    @bot.command(name='showconfig')
    @commands.has_permissions(administrator=True)
    async def show_config(ctx):
        free    = config.get('thresholds', 'free_max')
        premium = config.get('thresholds', 'premium_min')
        free_ch = config.get('discord', 'channels', 'free')
        prem_ch = config.get('discord', 'channels', 'premium', 'all')
        interval = config.get('scraping', 'interval')
        msg = (
            f"**Aktualna konfiguracja:**\n"
            f"- Free do: {free}% (kanał ID `{free_ch}`)\n"
            f"- Premium od: {premium}% (kanał ALL ID `{prem_ch}`)\n"
            f"- Interwał scrapowania: {interval} s\n"
        )
        await ctx.send(msg)

    @bot.command(name='setlimit')
    @commands.has_permissions(administrator=True)
    async def set_limit(ctx, which: str, value: float):
        if which == 'free':
            config.set(value, 'thresholds', 'free_max')
        elif which == 'premium':
            config.set(value, 'thresholds', 'premium_min')
        else:
            return await ctx.send("Użyj: `!setlimit free <liczba>` lub `!setlimit premium <liczba>`")
        await ctx.send(f"Limit `{which}` ustawiony na {value}%")

    @bot.command(name='setchannel')
    @commands.has_permissions(administrator=True)
    async def set_channel(ctx, which: str, channel_id: int):
        if which == 'free':
            config.set(channel_id, 'discord', 'channels', 'free')
        elif which == 'premium_all':
            config.set(channel_id, 'discord', 'channels', 'premium', 'all')
        else:
            return await ctx.send("Użyj: `free` lub `premium_all`")
        await ctx.send(f"Kanał `{which}` ustawiony na ID `{channel_id}`")

    @bot.command(name='setinterval')
    @commands.has_permissions(administrator=True)
    async def set_interval(ctx, seconds: int):
        config.set(seconds, 'scraping', 'interval')
        await ctx.send(f"Interwał scrapowania ustawiony na {seconds} sekund")

    @bot.command(name='post')
    @commands.has_permissions(administrator=True)
    async def post_surebet(ctx, *, surebet_data: str):
        """
        !post Mecz A vs B | Etoto_U90.5@1.85 Etoto_O90.5@1.80 | value:5.0%
        """
        try:
            value = float(surebet_data.rstrip('%').split('value:')[-1])
        except:
            return await ctx.send("Format: `... value:5.0%`")

        free_max    = config.get('thresholds', 'free_max')
        premium_min = config.get('thresholds', 'premium_min')

        if value <= free_max:
            ch_id, tag = config.get('discord','channels','free'), 'FREE'
        elif value >= premium_min:
            ch_id, tag = config.get('discord','channels','premium','all'), 'PREMIUM'
        else:
            return await ctx.send("Surebet poza progami, nie wysyłam.")

        channel = bot.get_channel(ch_id)
        if channel:
            await channel.send(f"📈 [{tag}] {surebet_data}")
            await ctx.send(f"Surebet wysłany na kanał `{tag}`.")
        else:
            await ctx.send(f"Nie mogę znaleźć kanału `{tag}`.")

    @bot.command(name='start')
    @commands.has_permissions(administrator=True)
    async def start_scraper(ctx):
        print("[COMMAND] Odebrano !start – wywołuję start_loop(bot)")
        start_loop(bot)
        await ctx.send("Pętla scrapowania została uruchomiona.")


    @bot.command(name='stop')
    @commands.has_permissions(administrator=True)
    async def stop_scraper(ctx):
        stop_loop()
        await ctx.send("Pętla scrapowania została zatrzymana.")
