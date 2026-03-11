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

SEEN_FILE = "seen.json"

SEARCH_KEYWORDS = [
    "VRChat",
    "VRC想定モデル",
    "VRChat可",
]

NEGATIVE_WORDS = [
    "素材", "テクスチャ", "texture", "matcap", "shader",
    "preset", "配信", "同人誌", "漫画", "音声", "BGM", "効果音",
    "背景", "ブラシ", "フォント", "小説"
]

MODEL_POSITIVE_WORDS = [
    "3d", "モデル", "アバター", "衣装", "アクセサリー",
    "髪", "ヘア", "body", "avatar", "outfit",
    "fbx", "unitypackage", "vrm", "3dキャラクター"
]

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

intents = discord.Intents.default()
client = discord.Client(intents=intents)

session = requests.Session()
session.headers.update(HEADERS)

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
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = "https://booth.pm" + url
    return url.split("?")[0].rstrip("/")

def parse_price(text: str):
    patterns = [
        r"¥\s*([0-9][0-9,]*)",
        r"([0-9][0-9,]*)\s*JPY",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return int(m.group(1).replace(",", ""))
    return None

def should_exclude(text: str) -> bool:
    lower = text.lower()
    return any(word.lower() in lower for word in NEGATIVE_WORDS)

def looks_model_related(text: str) -> bool:
    lower = text.lower()
    return any(word.lower() in lower for word in MODEL_POSITIVE_WORDS)

def get_product_page_image(url: str) -> str | None:
    try:
        r = session.get(url, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        og_image = soup.select_one('meta[property="og:image"]')
        if og_image and og_image.get("content"):
            return normalize_url(og_image["content"])

        twitter_image = soup.select_one('meta[name="twitter:image"]')
        if twitter_image and twitter_image.get("content"):
            return normalize_url(twitter_image["content"])

        img = soup.select_one("img")
        if img and img.get("src"):
            return normalize_url(img["src"])

    except Exception as e:
        print(f"image fetch error: {url} / {e}")

    return None

def extract_items(html: str):
    soup = BeautifulSoup(html, "html.parser")
    items = []

    for a in soup.select("a[href*='/items/']"):
        href = normalize_url(a.get("href", ""))
        if not href:
            continue

        title = a.get_text(" ", strip=True)
        if not title:
            continue

        parent_text = a.parent.get_text(" ", strip=True) if a.parent else title
        combined = f"{title} {parent_text}"
        price = parse_price(combined)

        items.append({
            "title": title,
            "url": href,
            "price": price,
            "image": None,
            "text": combined
        })

    dedup = {}
    for item in items:
        dedup[item["url"]] = item
    return list(dedup.values())

async def send_item(item):
    embed = discord.Embed(
        title=item["title"][:256],
        url=item["url"],
        description="🆓 無料アイテム",
        color=0x00CC99
    )

    embed.add_field(name="BOOTH", value=item["url"], inline=False)

    if item["image"]:
        embed.set_image(url=item["image"])

    embed.set_footer(text="BOOTH free model monitor")

    ch = client.get_channel(CHANNEL_FREE)
    if ch:
        await ch.send(embed=embed)

async def check_booth():
    await client.wait_until_ready()

    while True:
        try:
            candidates = []

            for keyword in SEARCH_KEYWORDS:
                search_url = build_search_url(keyword)
                r = session.get(search_url, timeout=20)
                r.raise_for_status()
                candidates.extend(extract_items(r.text))

            merged = {}
            for item in candidates:
                merged[item["url"]] = item

            for item in merged.values():
                url = item["url"]
                price = item["price"]
                text = item["text"]

                if not url or url in seen:
                    continue
                if price != 0:
                    continue
                if should_exclude(text):
                    continue
                if not looks_model_related(text):
                    continue

                item["image"] = get_product_page_image(url)

                seen.add(url)
                save_seen()
                await send_item(item)

        except Exception as e:
            print(f"error: {e}")

        await asyncio.sleep(300)

@client.event
async def on_ready():
    print("BOT起動")

    ch = client.get_channel(CHANNEL_FREE)
    if ch:
        await ch.send("✅ 無料モデル監視BOTが起動しました")

    client.loop.create_task(check_booth())

client.run(TOKEN)
