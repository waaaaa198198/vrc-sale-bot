import asyncio
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

import aiohttp
from bs4 import BeautifulSoup

# =========================
# Environment Variables
# =========================
DISCORD_TOKEN = os.getenv("TOKEN")
if not DISCORD_TOKEN:
    raise RuntimeError("TOKEN が未設定です")

CHANNEL_FREE = int(os.getenv("CHANNEL_FREE", "1481284062789374013"))
CHANNEL_B = int(os.getenv("CHANNEL_B", "1481283710660907242"))

STATE_FILE = "state.json"

JST = timezone(timedelta(hours=9))

REMINDER_AFTER = 22 * 60 * 60
REMINDER_DELETE_AFTER = 2 * 60 * 60

SEARCH_KEYWORDS = [
    "VRChat",
    "VRC想定モデル",
    "VRChat可",
]

VRC_SALE_BASE = "https://vrc-sale.com/sales"

NEGATIVE_WORDS = [
    # 素材系
    "素材", "テクスチャ", "texture", "matcap", "shader", "preset",
    "png", "jpg", "jpeg", "psd", "clip", "アイコン", "壁紙",
    "画像素材", "ブラシ", "フォント", "背景",

    # 音声・本系
    "配信", "同人誌", "漫画", "小説", "音声", "bgm", "効果音",
    "mp3", "wav",

    # モーション系は除外（ポーズは除外しない）
    "モーション", "motion", "animation", "アニメーション",
    "animator", "gesture", "fx", "ダンス", "振り付け",

    # 二次創作グッズ系
    "二次創作", "fanart", "ファンアート", "アクリル", "アクキー",
    "アクリルキーホルダー", "缶バッジ", "ステッカー", "ポスター",
    "タペストリー", "キーホルダー", "ぬいぐるみ", "グッズ",
    "抱き枕", "抱き枕カバー", "クリアファイル", "同人グッズ",

    # ワールドアイテム系
    "world item", "worlditem", "ワールド", "ワールド用", "ワールド向け",
    "ワールド専用", "ワールド配置", "ワールド設置",
    "u#don", "udon", "udonsharp", "pickup", "pick up",
    "interact", "ギミック", "オブジェクト", "cluster",

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
        "ポニテ", "ポニーテール", "ショートヘア", "ロングヘア"
    ],
    "アクセサリー": [
        "アクセサリー", "accessory", "アクセ", "装飾", "小物",
        "帽子", "帽", "眼鏡", "メガネ", "ピアス", "イヤリング",
        "ネックレス", "指輪", "リング", "尻尾", "しっぽ", "角", "羽",
        "チョーカー", "ブレスレット", "腕輪", "イヤーカフ", "カチューシャ",
        "バンスクリップ", "ヘアクリップ"
    ],
    "ポーズ": [
        "ポーズ", "pose", "撮影ポーズ", "立ちポーズ",
        "座りポーズ", "ポージング"
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

SALE_HINT_PATTERNS = [
    r"セール",
    r"sale",
    r"割引",
    r"値引",
    r"off",
    r"discount",
    r"期間限定価格",
    r"発売記念",
    r"記念価格",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


def extract_item_id(url: str) -> Optional[str]:
    if not url:
        return None
    m = re.search(r"/items/(\d+)", url)
    if m:
        return m.group(1)
    return None


def canonical_item_key(url: str) -> str:
    item_id = extract_item_id(url)
    if item_id:
        return f"booth-item:{item_id}"
    return normalize_url(url)


def load_state():
    default_state = {
        "seen_keys": [],
        "seen_urls": [],
        "reminders": [],
        "reminded_keys": [],
        "initialized_once": False,
    }
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = default_state.copy()

    for k, v in default_state.items():
        if k not in data:
            data[k] = v

    seen_keys = set(data.get("seen_keys", []))
    for url in data.get("seen_urls", []):
        seen_keys.add(canonical_item_key(url))
    data["seen_keys"] = list(seen_keys)

    return data


def save_state(state):
    tmp = {
        "seen_keys": sorted(list(set(state.get("seen_keys", [])))),
        "seen_urls": [],
        "reminders": state.get("reminders", []),
        "reminded_keys": sorted(list(set(state.get("reminded_keys", [])))),
        "initialized_once": state.get("initialized_once", False),
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


def parse_discount_percent(text: str) -> int:
    if not text:
        return 0

    best = 0

    patterns = [
        r"([0-9]{1,3})\s*%",
        r"([0-9]{1,3})\s*％",
        r"([0-9]{1,3})\s*percent",
        r"([0-9]{1,3})\s*off",
        r"([0-9]{1,3})\s*OFF",
        r"([0-9]{1,3})\s*割引",
        r"([0-9]{1,3})\s*引き",
        r"([0-9]{1,3})\s*オフ",
    ]

    for pattern in patterns:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            try:
                value = int(m.group(1))
                if 0 < value <= 100:
                    best = max(best, value)
            except Exception:
                pass

    yen_prices = re.findall(r'([0-9][0-9,]*)\s*円', text)
    if len(yen_prices) >= 2:
        try:
            nums = [int(x.replace(",", "")) for x in yen_prices[:6]]
            high = max(nums)
            low = min(nums)
            if high > 0 and low < high:
                inferred = int(round((high - low) / high * 100))
                if 0 < inferred <= 100:
                    best = max(best, inferred)
        except Exception:
            pass

    return best


def looks_sale_text(text: str) -> bool:
    for pattern in SALE_HINT_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


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
    if any(word.lower() in lower for word in COMMON_POSITIVE_WORDS):
        return True
    return True


def looks_limited_free(text: str) -> bool:
    for pattern in LIMITED_FREE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def reminder_key(url: str) -> str:
    return f"reminder::{canonical_item_key(url)}"


async def fetch_text(session: aiohttp.ClientSession, url: str) -> Optional[str]:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=25)) as resp:
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
            "key": canonical_item_key(href),
            "title": title[:256],
            "url": href,
            "price": price,
            "text": combined,
            "source": "booth_search",
            "source_detail": "",
            "discount_percent": parse_discount_percent(combined),
            "is_sale_hint": looks_sale_text(combined),
        })

    dedup = {}
    for item in items:
        dedup[item["key"]] = item
    return list(dedup.values())


def extract_items_from_vrc_sale_html(html: str, page_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    dedup = {}

    all_urls = re.findall(r'https://[A-Za-z0-9_.-]+\.booth\.pm/items/\d+', html)

    for a in soup.select('a[href*="booth.pm"]'):
        href = a.get("href", "")
        if href and "booth.pm" in href and "/items/" in href:
            all_urls.append(href)

    normalized_urls = []
    seen_url_set = set()
    for raw_url in all_urls:
        norm = normalize_booth_url(raw_url)
        if not norm or "/items/" not in norm:
            continue
        if norm in seen_url_set:
            continue
        seen_url_set.add(norm)
        normalized_urls.append(norm)

    for href in normalized_urls:
        key = canonical_item_key(href)
        context_texts = []

        for a in soup.select('a[href*="booth.pm"]'):
            raw = a.get("href", "")
            if not raw:
                continue
            if normalize_booth_url(raw) != href:
                continue

            node = a
            for _ in range(6):
                if not node:
                    break
                try:
                    text = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
                    if text:
                        context_texts.append(text)
                except Exception:
                    pass
                node = node.parent

        combined = " ".join(context_texts)
        combined = re.sub(r"\s+", " ", combined).strip()

        title = "vrc-sale item"
        for a in soup.select('a[href*="booth.pm"]'):
            raw = a.get("href", "")
            if not raw:
                continue
            if normalize_booth_url(raw) != href:
                continue

            node = a
            for _ in range(8):
                if not node:
                    break
                h = node.find(["h1", "h2", "h3", "h4"])
                if h:
                    t = re.sub(r"\s+", " ", h.get_text(" ", strip=True)).strip()
                    if t:
                        title = t
                        break
                node = node.parent

            if title != "vrc-sale item":
                break

        dedup[key] = {
            "key": key,
            "title": title[:256],
            "url": href,
            "price": parse_price(combined),
            "text": combined,
            "source": "vrc-sale",
            "source_detail": page_url,
            "discount_percent": parse_discount_percent(combined),
            "is_sale_hint": True,
        }

    return list(dedup.values())


async def get_product_page_info(session: aiohttp.ClientSession, url: str) -> dict:
    html = await fetch_text(session, url)
    if not html:
        return {
            "title": None,
            "image": None,
            "text": "",
            "price": None,
            "discount_percent": 0,
            "is_sale_hint": False,
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
        discount_percent = parse_discount_percent(body_text)
        is_sale_hint = looks_sale_text(body_text)

        return {
            "title": title[:256] if title else None,
            "image": image,
            "text": body_text,
            "price": price,
            "discount_percent": discount_percent,
            "is_sale_hint": is_sale_hint,
        }
    except Exception as e:
        print(f"get_product_page_info parse error {url}: {e}")
        return {
            "title": None,
            "image": None,
            "text": "",
            "price": None,
            "discount_percent": 0,
            "is_sale_hint": False,
        }


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

    strong_exclude_patterns = [
        r"ワールド(専用|向け|用)",
        r"world( item|用|専用)?",
        r"u#don",
        r"\budon\b",
        r"アクリル",
        r"缶バッジ",
        r"キーホルダー",
        r"ステッカー",
        r"ぬいぐるみ",
    ]
    for pattern in strong_exclude_patterns:
        if re.search(pattern, merged_text, re.IGNORECASE):
            print(f"FILTER strong_exclude: {normalize_booth_url(url)}")
            return None

    if should_exclude(merged_text):
        print(f"FILTER negative_words: {normalize_booth_url(url)}")
        return None

    categories = detect_categories(merged_text)
    if not categories:
        categories = ["未分類"]

    if not looks_target_item(merged_text, categories):
        print(f"FILTER looks_target_item: {normalize_booth_url(url)}")
        return None

    price = item.get("price")
    if price is None:
        price = page_info.get("price")

    if price is None:
        print(f"FILTER price_none: {normalize_booth_url(url)}")
        return None

    discount_percent = max(
        item.get("discount_percent", 0) or 0,
        page_info.get("discount_percent", 0) or 0,
    )

    is_sale = (
        bool(item.get("is_sale_hint"))
        or bool(page_info.get("is_sale_hint"))
        or looks_sale_text(merged_text)
        or discount_percent > 0
        or item.get("source") == "vrc-sale"
    )

    if "ポーズ" in categories and len(categories) == 1 and price != 0:
        print(f"FILTER pose_paid: {normalize_booth_url(url)}")
        return None

    if price == 0:
        route = "free"
    else:
        qualifies_b = (
            price <= 1000
            or (price > 1000 and discount_percent >= 50)
            or (price <= 1000 and is_sale)
        )
        if not qualifies_b:
            print(
                "FILTER b_condition:",
                {
                    "url": normalize_booth_url(url),
                    "price": price,
                    "discount_percent": discount_percent,
                    "is_sale": is_sale,
                }
            )
            return None
        route = "b"

    print(
        "DEBUG enrich:",
        {
            "title": (page_info.get("title") or item.get("title") or "")[:60],
            "price": price,
            "categories": categories,
            "discount_percent": discount_percent,
            "is_sale": is_sale,
            "route": route,
            "url": normalize_booth_url(url),
        }
    )

    return {
        "key": item.get("key") or canonical_item_key(url),
        "title": (page_info.get("title") or item.get("title") or "BOOTH item")[:256],
        "url": normalize_booth_url(url),
        "price": price,
        "categories": categories,
        "image": page_info.get("image"),
        "source": item.get("source", "unknown"),
        "source_detail": item.get("source_detail", ""),
        "limited_free": looks_limited_free(merged_text),
        "discount_percent": discount_percent,
        "is_sale": is_sale,
        "route": route,
    }


async def discord_api(session: aiohttp.ClientSession, method: str, path: str, **kwargs):
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bot {DISCORD_TOKEN}"
    headers["Content-Type"] = "application/json"
    url = f"https://discord.com/api/v10{path}"
    async with session.request(method, url, headers=headers, **kwargs) as resp:
        text = await resp.text()
        if resp.status >= 400:
            raise RuntimeError(f"Discord API error {resp.status}: {text}")
        if text:
            return json.loads(text)
        return None


def build_embed(item: dict) -> dict:
    category_text = " / ".join(item.get("categories", [])) if item.get("categories") else "未分類"

    source_text = item.get("source", "unknown")
    source_detail = item.get("source_detail", "")
    source_line = source_text if not source_detail else f"{source_text} ({source_detail})"

    sale_line = ""
    if item.get("is_sale"):
        sale_line += "🏷 セール対象\n"
    if item.get("discount_percent", 0) > 0:
        sale_line += f"📉 値引き率: {item['discount_percent']}%\n"

    limited_flag = ""
    if item.get("limited_free") and item.get("price") == 0:
        limited_flag = "⏳ 期間限定無料の可能性あり\n"

    if item.get("route") == "free":
        description = f"{limited_flag}{sale_line}🆓 無料アイテム\n📂 {category_text}"
        color = 0x00CC99
    else:
        description = f"{sale_line}💰 {item['price']}円\n📂 {category_text}"
        color = 0x3399FF

    embed = {
        "title": item["title"][:256],
        "url": item["url"],
        "description": description[:4096],
        "color": color,
        "fields": [
            {"name": "BOOTH", "value": item["url"], "inline": False},
            {"name": "検出元", "value": source_line[:1024], "inline": False},
        ],
        "footer": {"text": "BOOTH / vrc-sale monitor"},
    }

    if item.get("image"):
        embed["image"] = {"url": item["image"]}

    return embed


async def send_item(session: aiohttp.ClientSession, item: dict):
    channel_id = CHANNEL_FREE if item.get("route") == "free" else CHANNEL_B
    payload = {"embeds": [build_embed(item)]}
    await discord_api(session, "POST", f"/channels/{channel_id}/messages", data=json.dumps(payload))


async def send_reminder(session: aiohttp.ClientSession, rem: dict):
    category_text = " / ".join(rem.get("categories", [])) if rem.get("categories") else "未分類"

    source_text = rem.get("source", "")
    source_detail = rem.get("source_detail", "")
    source_line = source_text if not source_detail else f"{source_text} ({source_detail})"

    embed = {
        "title": f"⏰ 期間限定無料リマインド: {rem.get('title', 'BOOTH item')[:220]}",
        "url": rem["url"],
        "description": "22時間前に検出した期間限定無料の可能性があるアイテムです。\n終了前か確認してください。",
        "color": 0xFFAA33,
        "fields": [
            {"name": "BOOTH", "value": rem["url"], "inline": False},
            {"name": "カテゴリ", "value": category_text[:1024], "inline": False},
            {"name": "検出元", "value": source_line[:1024], "inline": False},
        ],
        "footer": {"text": "This reminder will auto-delete in about 2 hours"},
    }

    if rem.get("image"):
        embed["image"] = {"url": rem["image"]}

    payload = {"embeds": [embed]}
    message = await discord_api(session, "POST", f"/channels/{CHANNEL_FREE}/messages", data=json.dumps(payload))

    if message and "id" in message:
        rem["delete_at"] = now_ts() + REMINDER_DELETE_AFTER
        rem["message_id"] = message["id"]


async def delete_message(session: aiohttp.ClientSession, channel_id: int, message_id: str):
    await discord_api(session, "DELETE", f"/channels/{channel_id}/messages/{message_id}")


async def fetch_booth_candidates(session: aiohttp.ClientSession) -> list[dict]:
    candidates = []
    for keyword in SEARCH_KEYWORDS:
        search_url = build_search_url(keyword)
        html = await fetch_text(session, search_url)
        if not html:
            continue
        items = extract_items_from_search_html(html)
        for item in items:
            item["source"] = "booth_search"
            item["source_detail"] = keyword
        candidates.extend(items)

    merged = {}
    for item in candidates:
        merged[item["key"]] = item
    return list(merged.values())


async def fetch_vrc_sale_candidates(session: aiohttp.ClientSession) -> list[dict]:
    pages = [
        build_vrc_sale_url(),
        build_vrc_sale_url((datetime.now(JST) - timedelta(days=1)).strftime("%Y-%m-%d")),
    ]

    merged = {}

    for page_url in pages:
        html = await fetch_text(session, page_url)
        if not html:
            continue

        items = extract_items_from_vrc_sale_html(html, page_url)
        for item in items:
            merged[item["key"]] = item

    return list(merged.values())


def queue_limited_free_reminder(state: dict, item: dict):
    if item.get("price") != 0:
        return
    if not item.get("limited_free"):
        return

    key = reminder_key(item["url"])
    reminded = set(state.get("reminded_keys", []))
    if key in reminded:
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
    state.setdefault("reminders", []).append(reminder)
    reminded.add(key)
    state["reminded_keys"] = list(reminded)


async def initialize_seen_without_notifying(session: aiohttp.ClientSession, state: dict):
    all_candidates = []
    all_candidates.extend(await fetch_booth_candidates(session))
    all_candidates.extend(await fetch_vrc_sale_candidates(session))

    dedup = {}
    for item in all_candidates:
        key = item["key"]
        if key not in dedup or item.get("source") == "vrc-sale":
            dedup[key] = item

    seen = set(state.get("seen_keys", []))
    added = 0

    for item in dedup.values():
        try:
            enriched = await enrich_and_filter_item(session, item)
            if enriched:
                seen.add(enriched["key"])
                added += 1
        except Exception as e:
            print(f"initialize item error {item.get('url')}: {e}")

    state["seen_keys"] = list(seen)
    state["initialized_once"] = True
    print(f"DEBUG initialize_seen added={added}")


async def process_reminders(session: aiohttp.ClientSession, state: dict):
    reminders = state.get("reminders", [])
    remaining = []
    current = now_ts()

    for rem in reminders:
        try:
            if rem.get("due_at", 0) <= current and not rem.get("message_id"):
                await send_reminder(session, rem)
                remaining.append(rem)
            elif rem.get("message_id") and rem.get("delete_at", 0) <= current:
                await delete_message(session, CHANNEL_FREE, rem["message_id"])
            else:
                remaining.append(rem)
        except Exception as e:
            print(f"reminder error {rem.get('url')}: {e}")
            remaining.append(rem)

    state["reminders"] = remaining
    print(f"DEBUG reminders_remaining={len(remaining)}")


async def process_candidates(session: aiohttp.ClientSession, state: dict):
    booth_candidates = await fetch_booth_candidates(session)
    vrc_sale_candidates = await fetch_vrc_sale_candidates(session)

    print(f"DEBUG booth_candidates={len(booth_candidates)}")
    print(f"DEBUG vrc_sale_candidates={len(vrc_sale_candidates)}")

    merged = {}
    for item in booth_candidates:
        merged[item["key"]] = item
    for item in vrc_sale_candidates:
        merged[item["key"]] = item

    print(f"DEBUG merged_candidates={len(merged)}")

    seen = set(state.get("seen_keys", []))
    posted = 0

    for item in merged.values():
        try:
            if item["key"] in seen:
                print(f"SKIP seen: {item['url']}")
                continue

            enriched = await enrich_and_filter_item(session, item)
            if not enriched:
                print(f"SKIP filtered: {item['url']}")
                continue

            if enriched["key"] in seen:
                print(f"SKIP seen after enrich: {enriched['url']}")
                continue

            print(
                "POST:",
                {
                    "title": enriched["title"][:60],
                    "price": enriched["price"],
                    "route": enriched["route"],
                    "discount_percent": enriched["discount_percent"],
                    "is_sale": enriched["is_sale"],
                    "url": enriched["url"],
                }
            )

            await send_item(session, enriched)
            queue_limited_free_reminder(state, enriched)
            seen.add(enriched["key"])
            posted += 1

        except Exception as e:
            print(f"process item error {item.get('url')}: {e}")

    state["seen_keys"] = list(seen)
    print(f"DEBUG posted={posted}")


async def main():
    state = load_state()

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        if not state.get("initialized_once", False):
            print("初回既読化を開始")
            await initialize_seen_without_notifying(session, state)
            print("初回既読化完了")
        else:
            await process_reminders(session, state)
            await process_candidates(session, state)

    save_state(state)
    print("処理完了")


if __name__ == "__main__":
    asyncio.run(main())
