import discord
import asyncio
import os
import re
import json
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote

TOKEN = os.getenv("TOKEN")

CHANNEL_FREE = 1481284062789374013
CHANNEL_CHEAP = 1481283710660907242

SEEN_FILE = "seen.json"

SEARCH_KEYWORDS = [
    "VRChat",
    "VRC想定モデル",
    "VRChat可",
]

NEGATIVE_WORDS = [
    "素材", "テクスチャ", "texture", "matcap", "shader",
    "preset", "配信", "同人誌", "漫画", "音声", "BGM", "効果音"
]

MODEL_POSITIVE_WORDS = [
    "3D", "モデル", "アバター", "衣装", "アクセサリー",
    "髪", "ヘア", "body", "avatar", "outfit", "fbx", "unitypackage"
]

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

client = discord.Client(intents=discord.Intents.default())

try:
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        seen = set(json.load(f))
except Exception:
    seen = set()

def save_seen():
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen), f, ensure_ascii=False, indent=2)

def build_search_url(keyword: str) -> str:
    encoded = quote(keyword)
    return f"https://booth.pm/ja/search/{encoded}?sort=new"

def normalize_url(url: str) -> str:
    return url.split("?")[0].rstrip("/")

def parse_price(text: str):
    # ¥ 0 / 0 JPY / 500 JPY~ / ¥ 1,000 などに対応
    m = re.search(r"(?:¥|\bJPY\b)?\s*([0-9][0-9,]*)\s*(?:JPY)?\s*~?", text, re.IGNORECASE)
    if not m:
        return None
    return int(m.group(1).replace(",", ""))

def should_exclude(title: str) -> bool:
    lower = title.lower()
    return any(word.lower() in lower for word in NEGATIVE_WORDS)

def looks_model_related(title: str, meta_text: str) -> bool:
    joined = f"{title} {meta_text}".lower()
    return any(word.lower() in joined for word in MODEL_POSITIVE_WORDS)

def extract_items(html: str):
    soup = BeautifulSoup(html, "html.parser")
    items = []

    for a in soup.select("a[href*='/items/']"):
        href = a.get("href")
        if not href:
            continue

        title = a.get_text(" ", strip=True)
        if not title:
            continue

        card_text = a.parent.get_text(" ", strip=True) if a.parent else title
        price = parse_price(card_text)

        img = a.select_one("img")
        image_url = img.get("src") if img else None

        items.append({
            "title": title,
            "url": normalize_url(href if href.startswith("http") else f"https://booth.pm{href}"),
            "price": price,
            "image": image_url,
            "meta": card_text,
        })

    # URLで重複排除
    dedup = {}
    for item in items:
        dedup[item["url"]] = item
    return list(dedup.values())

async def send_item(item: dict):
    price = item["price"]
    title = item["title"]
    url = item["url"]
    image = item["image"]

    embed = discord.Embed(
        title=title,
        url=url,
        color=0x00CC99 if price == 0 else 0x3399FF
    )

    if price == 0:
        embed.description = "🆓 無料アイテム"
    else:
        embed.description = f"💰 {price}円"

    embed.add_field(name="BOOTH", value=url, inline=False)

    if image:
        embed.set_thumbnail(url=image)

    embed.set_footer(text="BOOTH model monitor")

    if price == 0:
        ch = client.get_channel(CHANNEL_FREE)
        if ch:
            await ch.send(embed=embed)
    elif 1 <= price <= 1000:
        ch = client.get_channel(CHANNEL_CHEAP)
        if ch:
            await ch.send(embed=embed)

async def check_booth():
    await client.wait_until_ready()

    while True:
        try:
            candidates = []

            for keyword in SEARCH_KEYWORDS:
                url = build_search_url(keyword)
                r = requests.get(url, headers=HEADERS, timeout=20)
                r.raise_for_status()
                candidates.extend(extract_items(r.text))

            # URLで再重複排除
            merged = {}
            for item in candidates:
                merged[item["url"]] = item

            for item in merged.values():
                title = item["title"]
                url = item["url"]
                price = item["price"]
                meta = item["meta"]

                if url in seen:
                    continue
                if price is None:
                    continue
                if should_exclude(title):
                    continue
                if not looks_model_related(title, meta):
                    continue

                seen.add(url)
                save_seen()

                if price == 0 or (1 <= price <= 1000):
                    await send_item(item)

        except Exception as e:
            print(f"error: {e}")

        await asyncio.sleep(300)

@client.event
async def on_ready():
    print("BOT起動")
    client.loop.create_task(check_booth())

client.run(TOKEN)
