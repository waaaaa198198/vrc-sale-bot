import discord
import asyncio
import os
import re
import json
import time
from typing import Optional
from urllib.parse import quote
from datetime import datetime, timedelta, timezone

import aiohttp
from bs4 import BeautifulSoup

# =========================
# Environment Variables
# =========================
DISCORD_TOKEN = os.getenv("TOKEN")
if not DISCORD_TOKEN:
    raise RuntimeError("TOKEN が未設定です")

# =========================
# Discord Channels
# =========================
CHANNEL_FREE = 1481284062789374013
CHANNEL_CHEAP = 1481283710660907242

# =========================
# Files
# =========================
STATE_FILE = "state.json"

# =========================
# Timezone
# =========================
JST = timezone(timedelta(hours=9))

# =========================
# Monitoring Settings
# =========================
CHECK_INTERVAL = 60 * 60              # 1時間ごと
REMINDER_AFTER = 22 * 60 * 60         # 22時間後
REMINDER_DELETE_AFTER = 2 * 60 * 60   # 2時間後に自動削除
REMINDER_POLL_INTERVAL = 60           # 1分ごとにリマインド確認

# =========================
# BOOTH Search
# =========================
SEARCH_KEYWORDS = [
    "VRChat",
    "VRC想定モデル",
    "VRChat可",
]

# =========================
# vrc-sale
# =========================
VRC_SALE_BASE = "https://vrc-sale.com/sales"

# =========================
# Filters
# =========================
NEGATIVE_WORDS = [
    # 素材系
    "素材", "テクスチャ", "texture", "matcap", "shader", "preset",
    "png", "jpg", "jpeg", "psd", "clip", "アイコン", "壁紙",
    "画像素材", "ブラシ", "フォント", "背景",

    # 音声・本系
    "配信", "同人誌", "漫画", "小説", "音声", "bgm", "効果音",

    # モーション系は完全除外
    "モーション", "motion", "animation", "アニメーション",
    "animator", "gesture", "fx", "ダンス", "振り付け", "ポーズ",

    # その他対象外になりやすいもの
    "live2d", "aviutl", "after effects", "動画素材",
]

CATEGORY_KEYWORDS = {
    "衣装": [
        "衣装", "outfit", "costume", "服", "dress", "wear", "clothes",
        "pants", "skirt", "shirt", "jacket", "hoodie", "onepiece"
    ],
    "髪型": [
        "髪", "ヘア", "hair", "hairstyle", "前髪", "後ろ髪", "ツインテ",
        "ポニテ", "ショートヘア", "ロングヘア"
    ],
    "アクセサリー": [
        "アクセサリー", "accessory", "アクセ", "装飾", "小物",
        "帽子", "帽", "眼鏡", "メガネ", "ピアス", "イヤリング",
        "ネックレス", "指輪", "リング", "尻尾", "しっぽ", "角", "羽",
        "チョーカー", "ブレスレット", "腕輪", "イヤーカフ", "カチューシャ"
    ],
}

COMMON_POSITIVE_WORDS = [
    "3d", "モデル", "model", "vrchat", "vrc", "unitypackage",
    "fbx", "vrm", "unity", "アバター", "avatar", "modularavatar",
    "ma対応", "vrc想定"
]

LIMITED_FREE_PATTERNS = [
    r"期間限定無料",
    r"期間限定",
    r"今だけ無料",
    r"無料配布",
    r"期間限定で無料",
    r"無料になりました",
    r"無料です",
    r"\d{1,2}/\d{1,2}\s*まで",
    r"\d{1,2}月\d{1,2}日\s*まで",
    r"\d{1,2}:\d{2}\s*まで",
    r"本日中",
    r"今日まで",
    r"今週末まで",
    r"先着\d+名限定",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

intents = discord.Intents.default()


def load_state():
    default_state = {
        "seen_urls": [],
        "reminders": [],
        "reminded_keys": [],
    }
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            for k, v in default_state.items():
                if k not in data:
                    data[k] = v
            return data
    except Exception:
        return default_state


def save_state(state):
    tmp = {
        "seen_urls": sorted(list(set(state.get("seen_urls", [])))),
        "reminders": state.get("reminders", []),
        "reminded_keys": sorted(list(set(state.get("reminded_keys", [])))),
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(tmp, f, ensure_ascii=False, indent=2)


def now_ts() -> int:
    return int(time.time())


def today_jst_str() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d")


def build_vrc_sale_url(date_str: Optional[str] = None) -> str:
    if not date_str:
        date_str = today_jst_str()
    return f"{VRC_SALE_BASE}/{date_str}"


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


def normalize_booth_url(url: str) -> str:
    url = normalize_url(url)
    m = re.search(r"(https://[^/]+/items/\d+)", url)
    if m:
        return m.group(1)
    return url


def parse_price(text: str) -> Optional[int]:
    patterns = [
        r"¥\s*([0-9][0-9,]*)",
        r"([0-9][0-9,]*)\s*JPY",
        r"([0-9][0-9,]*)\s*円",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return int(m.group(1).replace(",", ""))
    if re.search(r"\b無料\b", text):
        return 0
    return None


def should_exclude(text: str) -> bool:
    lower = text.lower()
    return any(word.lower() in lower for word in NEGATIVE_WORDS)


def detect_categories(text: str) -> list[str]:
    lower = text.lower()
    found = []
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(word.lower() in lower for word in keywords):
            found.append(category)
    return found


def looks_target_item(text: str, categories: list[str]) -> bool:
    lower = text.lower()

    if not categories:
        return False

    if any(word.lower() in lower for word in COMMON_POSITIVE_WORDS):
        return True

    return True


def looks_limited_free(text: str) -> bool:
    for pattern in LIMITED_FREE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def reminder_key(url: str) -> str:
    return f"reminder::{normalize_booth_url(url)}"


async def fetch_text(session: aiohttp.ClientSession, url: str, headers: dict | None = None) -> Optional[str]:
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=25)) as resp:
            resp.raise_for_status()
            return await resp.text()
    except Exception as e:
        print(f"fetch_text error {url}: {e}")
        return None


def extract_items_from_search_html(html: str):
    soup = BeautifulSoup(html, "html.parser")
    items = []

    for a in soup.select("a[href*='/items/']"):
        href = normalize_booth_url(a.get("href", ""))
        if not href:
            continue

        title = a.get("title") or a.get_text(" ", strip=True)
        title = re.sub(r"\s+", " ", title).strip()
        if not title:
            continue

        parent_text = a.parent.get_text(" ", strip=True) if a.parent else title
        combined = f"{title} {parent_text}"
        price = parse_price(combined)

        items.append({
            "title": title[:256],
            "url": href,
            "price": price,
            "text": combined,
            "source": "booth_search",
            "source_detail": "",
        })

    dedup = {}
    for item in items:
        if item["url"] not in dedup:
            dedup[item["url"]] = item
    return list(dedup.values())


async def get_product_page_info(session: aiohttp.ClientSession, url: str) -> dict:
    html = await fetch_text(session, url)
    if not html:
        return {
            "title": None,
            "image": None,
            "text": "",
            "price": None,
        }

    try:
        soup = BeautifulSoup(html, "html.parser")

        title = None
        og_title = soup.select_one('meta[property="og:title"]')
        if og_title and og_title.get("content"):
            title = og_title["content"].strip()

        image = None
        og = soup.select_one('meta[property="og:image"]')
        if og and og.get("content"):
            image = normalize_url(og["content"])
        if not image:
            tw = soup.select_one('meta[name="twitter:image"]')
            if tw and tw.get("content"):
                image = normalize_url(tw["content"])

        body_text = soup.get_text(" ", strip=True)
        body_text = re.sub(r"\s+", " ", body_text).strip()

        price = parse_price(body_text)

        return {
            "title": title[:256] if title else None,
            "image": image,
            "text": body_text,
            "price": price,
        }
    except Exception as e:
        print(f"get_product_page_info parse error {url}: {e}")
        return {
            "title": None,
            "image": None,
            "text": "",
            "price": None,
        }


def extract_items_from_vrc_sale_html(html: str, page_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    items = []

    booth_links = soup.select('a[href*="booth.pm"]')

    for a in booth_links:
        href = normalize_url(a.get("href", ""))
        if not href:
            continue

        parent = a.parent
        block_text = ""
        title = ""
        category_text = ""

        node = parent
        for _ in range(4):
            if node:
                block_text = node.get_text(" ", strip=True)
                node = node.parent

        h3 = None
        node = a.parent
        for _ in range(6):
            if not node:
                break
            h3 = node.find(["h3", "h2"])
            if h3:
                break
            node = node.parent

        if h3:
            title = re.sub(r"\s+", " ", h3.get_text(" ", strip=True)).strip()

        cat_match = re.search(r"(3D衣装|3D装飾品|3Dキャラクター|3Dテクスチャ|3Dツール・システム)", block_text)
        if cat_match:
            category_text = cat_match.group(1)

        price = parse_price(block_text)

        items.append({
            "title": (title or "vrc-sale item")[:256],
            "url": href,
            "price": price,
            "text": f"{title} {category_text} {block_text}".strip(),
            "source": "vrc-sale",
            "source_detail": page_url,
        })

    dedup = {}
    for item in items:
        norm = normalize_booth_url(item["url"])
        if "/items/" in norm:
            item["url"] = norm
            dedup[norm] = item

    return list(dedup.values())


async def enrich_and_filter_item(session: aiohttp.ClientSession, item: dict) -> Optional[dict]:
    url = item.get("url", "")
    if not url:
        return None

    page_info = await get_product_page_info(session, url)
    merged_text = " ".join([
        item.get("title", ""),
        item.get("text", ""),
        page_info.get("title") or "",
        page_info.get("text") or "",
    ]).strip()

    if should_exclude(merged_text):
        return None

    categories = detect_categories(merged_text)
    if not looks_target_item(merged_text, categories):
        return None

    price = item.get("price")
    if price is None:
        price = page_info.get("price")

    if price is None:
        return None

    if price > 1000:
        return None

    final_item = {
        "title": (page_info.get("title") or item.get("title") or "BOOTH item")[:256],
        "url": normalize_booth_url(url),
        "price": price,
        "text": merged_text,
        "categories": categories,
        "image": page_info.get("image"),
        "source": item.get("source", "unknown"),
        "source_detail": item.get("source_detail", ""),
        "limited_free": looks_limited_free(merged_text),
    }
    return final_item


class BoothMonitorBot(discord.Client):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.http_session: Optional[aiohttp.ClientSession] = None
        self.state = load_state()
        self.initialized = False
        self.monitor_task = None
        self.reminder_task = None

    async def setup_hook(self):
        self.http_session = aiohttp.ClientSession(headers=HEADERS)
        self.monitor_task = asyncio.create_task(self.monitor_loop())
        self.reminder_task = asyncio.create_task(self.reminder_loop())

    async def close(self):
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()
        await super().close()

    @property
    def seen_urls(self) -> set[str]:
        return set(self.state.get("seen_urls", []))

    def add_seen_url(self, url: str):
        s = self.seen_urls
        s.add(normalize_booth_url(url))
        self.state["seen_urls"] = list(s)

    @property
    def reminded_keys(self) -> set[str]:
        return set(self.state.get("reminded_keys", []))

    def add_reminded_key(self, key: str):
        s = self.reminded_keys
        s.add(key)
        self.state["reminded_keys"] = list(s)

    async def on_ready(self):
        print(f"BOT起動: {self.user}")
        if not self.initialized:
            self.initialized = True
            ch = self.get_channel(CHANNEL_FREE)
            if ch:
                await ch.send(
                    "✅ 監視BOTが起動しました\n"
                    "対象: BOOTH検索 + vrc-sale\n"
                    "カテゴリ: 衣装 / 髪型 / アクセサリー\n"
                    "チェック間隔: 1時間\n"
                    "期間限定無料: 22時間後リマインド / 約2時間で自動削除"
                )

    async def send_item(self, item: dict):
        price = item["price"]
        url = item["url"]
        categories = item.get("categories", [])
        image = item.get("image")
        category_text = " / ".join(categories) if categories else "未分類"

        source_text = item.get("source", "unknown")
        source_detail = item.get("source_detail", "")
        source_line = source_text
        if source_detail:
            source_line += f" ({source_detail})"

        limited_flag = "⏳ 期間限定無料の可能性あり\n" if item.get("limited_free") and price == 0 else ""

        if price == 0:
            embed = discord.Embed(
                title=item["title"],
                url=url,
                description=f"{limited_flag}🆓 無料アイテム\n📂 {category_text}",
                color=0x00CC99
            )
            ch = self.get_channel(CHANNEL_FREE)
        else:
            embed = discord.Embed(
                title=item["title"],
                url=url,
                description=f"💰 {price}円\n📂 {category_text}",
                color=0x3399FF
            )
            ch = self.get_channel(CHANNEL_CHEAP)

        embed.add_field(name="BOOTH", value=url, inline=False)
        embed.add_field(name="検出元", value=source_line, inline=False)

        if image:
            embed.set_image(url=image)

        embed.set_footer(text="BOOTH / vrc-sale monitor")

        if ch:
            await ch.send(embed=embed)

    async def send_reminder(self, rem: dict):
        ch = self.get_channel(CHANNEL_FREE)
        if not ch:
            return

        categories = rem.get("categories", [])
        category_text = " / ".join(categories) if categories else "未分類"

        embed = discord.Embed(
            title=f"⏰ 期間限定無料リマインド: {rem.get('title', 'BOOTH item')[:220]}",
            url=rem["url"],
            description=(
                "22時間前に検出した期間限定無料の可能性があるアイテムです。\n"
                "終了前か確認してください。"
            ),
            color=0xFFAA33,
        )
        embed.add_field(name="BOOTH", value=rem["url"], inline=False)
        embed.add_field(name="カテゴリ", value=category_text, inline=False)

        src = rem.get("source", "")
        src_detail = rem.get("source_detail", "")
        source_line = src if not src_detail else f"{src} ({src_detail})"
        if source_line:
            embed.add_field(name="検出元", value=source_line, inline=False)

        image = rem.get("image")
        if image:
            embed.set_image(url=image)

        embed.set_footer(text="This reminder will auto-delete in about 2 hours")

        await ch.send(embed=embed, delete_after=REMINDER_DELETE_AFTER)

    def queue_limited_free_reminder(self, item: dict):
        if item.get("price") != 0:
            return
        if not item.get("limited_free"):
            return

        key = reminder_key(item["url"])
        if key in self.reminded_keys:
            return

        due_at = now_ts() + REMINDER_AFTER
        reminder = {
            "key": key,
            "due_at": due_at,
            "url": item["url"],
            "title": item["title"],
            "categories": item.get("categories", []),
            "image": item.get("image"),
            "source": item.get("source", ""),
            "source_detail": item.get("source_detail", ""),
        }
        self.state.setdefault("reminders", []).append(reminder)
        self.add_reminded_key(key)

    async def fetch_booth_candidates(self) -> list[dict]:
        if not self.http_session:
            return []

        candidates = []
        for keyword in SEARCH_KEYWORDS:
            search_url = build_search_url(keyword)
            html = await fetch_text(self.http_session, search_url)
            if not html:
                continue
            items = extract_items_from_search_html(html)
            for item in items:
                item["source"] = "booth_search"
                item["source_detail"] = keyword
            candidates.extend(items)

        merged = {}
        for item in candidates:
            merged[item["url"]] = item
        return list(merged.values())

    async def fetch_vrc_sale_candidates(self) -> list[dict]:
        if not self.http_session:
            return []

        page_url = build_vrc_sale_url()
        html = await fetch_text(self.http_session, page_url)
        if not html:
            return []

        items = extract_items_from_vrc_sale_html(html, page_url)

        merged = {}
        for item in items:
            merged[item["url"]] = item
        return list(merged.values())

    async def initialize_seen_without_notifying(self):
        if not self.http_session:
            return

        print("初回既読化を開始")
        all_candidates = []
        all_candidates.extend(await self.fetch_booth_candidates())
        all_candidates.extend(await self.fetch_vrc_sale_candidates())

        dedup = {}
        for item in all_candidates:
            dedup[item["url"]] = item

        for item in dedup.values():
            try:
                enriched = await enrich_and_filter_item(self.http_session, item)
                if enriched:
                    self.add_seen_url(enriched["url"])
            except Exception as e:
                print(f"initialize item error {item.get('url')}: {e}")

        save_state(self.state)
        print(f"初回既読化完了: {len(self.state.get('seen_urls', []))}件")

    async def process_candidates(self, candidates: list[dict]):
        if not self.http_session:
            return

        for item in candidates:
            try:
                normalized = normalize_booth_url(item["url"])
                if normalized in self.seen_urls:
                    continue

                enriched = await enrich_and_filter_item(self.http_session, item)
                if not enriched:
                    continue

                if enriched["url"] in self.seen_urls:
                    continue

                self.add_seen_url(enriched["url"])
                await self.send_item(enriched)
                self.queue_limited_free_reminder(enriched)
                save_state(self.state)

            except Exception as e:
                print(f"process item error {item.get('url')}: {e}")

    async def monitor_once(self):
        booth_candidates = await self.fetch_booth_candidates()
        vrc_sale_candidates = await self.fetch_vrc_sale_candidates()

        merged = {}
        for item in booth_candidates + vrc_sale_candidates:
            merged[item["url"]] = item

        await self.process_candidates(list(merged.values()))

    async def monitor_loop(self):
        await self.wait_until_ready()

        try:
            await self.initialize_seen_without_notifying()
        except Exception as e:
            print(f"initialization error: {e}")

        while not self.is_closed():
            try:
                await self.monitor_once()
                save_state(self.state)
            except Exception as e:
                print(f"monitor loop error: {e}")

            await asyncio.sleep(CHECK_INTERVAL)

    async def reminder_loop(self):
        await self.wait_until_ready()

        while not self.is_closed():
            try:
                reminders = self.state.get("reminders", [])
                remaining = []
                current = now_ts()

                for rem in reminders:
                    if rem.get("due_at", 0) <= current:
                        try:
                            await self.send_reminder(rem)
                        except Exception as e:
                            print(f"send reminder error {rem.get('url')}: {e}")
                    else:
                        remaining.append(rem)

                self.state["reminders"] = remaining
                save_state(self.state)

            except Exception as e:
                print(f"reminder loop error: {e}")

            await asyncio.sleep(REMINDER_POLL_INTERVAL)


client = BoothMonitorBot(intents=intents)
client.run(DISCORD_TOKEN)