import discord
import asyncio
import os
import feedparser

TOKEN = os.getenv("TOKEN")

CHANNEL_FREE = 1481284062789374013
CHANNEL_CHEAP = 1481283710660907242

RSS_URL = "https://vrc-sale.com/feed"

intents = discord.Intents.default()
client = discord.Client(intents=intents)

seen = set()

async def check_rss():
    await client.wait_until_ready()

    while True:
        feed = feedparser.parse(RSS_URL)

        for entry in feed.entries:
            title = entry.title
            link = entry.link

            if title in seen:
                continue

            seen.add(title)

            if "無料" in title:
                ch = client.get_channel(CHANNEL_FREE)
                await ch.send(f"🆓 無料アイテム\n{title}\n{link}")

            else:
                import re
                m = re.search(r"(\d+)円", title)
                if m:
                    price = int(m.group(1))
                    if price <= 1000:
                        ch = client.get_channel(CHANNEL_CHEAP)
                        await ch.send(f"💰 {price}円セール\n{title}\n{link}")

        await asyncio.sleep(300)

@client.event
async def on_ready():
    print("BOT起動")
    client.loop.create_task(check_rss())

client.run(TOKEN)
