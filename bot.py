import discord
import requests
from bs4 import BeautifulSoup
import asyncio
import re
import json
import os
from datetime import datetime

TOKEN = os.getenv("TOKEN")

CHANNEL_FREE = 1481284062789374013
CHANNEL_CHEAP = 1481283710660907242

intents = discord.Intents.default()
client = discord.Client(intents=intents)

SEEN_FILE = "seen.json"

try:
    with open(SEEN_FILE, "r") as f:
        seen = set(json.load(f))
except:
    seen = set()

def save_seen():
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)

def get_today_url():
    today = datetime.now().strftime("%Y-%m-%d")
    return f"https://vrc-sale.com/sales/{today}"

async def check_sales():
    await client.wait_until_ready()

    while True:
        url = get_today_url()

        try:
            headers = {
                "User-Agent": "Mozilla/5.0"
            }
            r = requests.get(url, headers=headers, timeout=10)
        except:
            await asyncio.sleep(300)
            continue

        soup = BeautifulSoup(r.text, "html.parser")
        cards = soup.select("article")

        for card in cards:
            title_tag = card.select_one("h3")

            if not title_tag:
                continue

            name = title_tag.text.strip()

            if name in seen:
                continue

            text = card.text
            price = None

            if "無料" in text:
                price = 0
            else:
                m = re.search(r"￥(\d+)", text)
                if m:
                    price = int(m.group(1))

            if price is None:
                continue

            link_tag = card.select_one("a")
            booth_url = link_tag["href"] if link_tag else None

            img_tag = card.select_one("img")
            image = img_tag["src"] if img_tag else None

            seen.add(name)
            save_seen()

            embed = discord.Embed(
                title=name,
                url=booth_url,
                color=0x00ffcc
            )

            if price == 0:
                embed.description = "🆓 **無料アイテム**"
            else:
                embed.description = f"💰 **{price}円セール**"

            embed.add_field(
                name="セールページ",
                value=url,
                inline=False
            )

            if image:
                embed.set_thumbnail(url=image)

            embed.set_footer(text="VRC Sale Monitor")

            if price == 0:
                ch = client.get_channel(CHANNEL_FREE)
                await ch.send(embed=embed)

            elif price <= 1000:
                ch = client.get_channel(CHANNEL_CHEAP)
                await ch.send(embed=embed)

        await asyncio.sleep(300)

@client.event
async def on_ready():
    print("BOT起動")
    client.loop.create_task(check_sales())

client.run(TOKEN)
