import json
import os
import re
from html import unescape
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from notion_client import Client
from notion_client.errors import APIResponseError
from pydantic import BaseModel

load_dotenv()
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")

app = FastAPI()
notion = Client(auth=os.environ["NOTION_TOKEN"])

GOOGLE_KEY = os.getenv("GOOGLE_PLACES_API_KEY", "")
DB_ID = os.environ["NOTION_DATABASE_ID"]
NOTION_TARGET_NAME = os.getenv("NOTION_TARGET_NAME", "行程安排")
HTTP_TIMEOUT = httpx.Timeout(15.0)
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "")
GOOGLE_PLACES_FIELD_MASK = (
    'places.id,'
    'places.displayName,'
    'places.formattedAddress,'
    'places.rating,'
    'places.googleMapsUri,'
    'places.regularOpeningHours.weekdayDescriptions,'
    'places.photos.name,'
    'places.reviews'
)
REGION_DAY_MAP = {
    '濟州市': 'Day1',
    '西部': 'Day2',
    '西歸浦': 'Day3',
    '東部': 'Day4',
}
REVIEW_LIMIT = 50
ARTICLE_LIMIT = 5


class SaveRequest(BaseModel):
    url: str


def extract_star_rating(star_label: str) -> int | None:
    """從 Google Maps 的星等字串解析數字。"""
    match = re.search(r'(\d)', star_label or '')
    if not match:
        return None

    return int(match.group(1))


def parse_json_response(content: str) -> dict[str, Any]:
    """解析模型輸出的 JSON 內容。"""
    cleaned = content.strip()
    if cleaned.startswith('```'):
        cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
        cleaned = re.sub(r'\s*```$', '', cleaned)

    return json.loads(cleaned)


def unwrap_duckduckgo_url(raw_url: str) -> str:
    """將 DuckDuckGo 跳轉連結還原成原始文章網址。"""
    if raw_url.startswith('//'):
        raw_url = f'https:{raw_url}'

    parsed = urlparse(unescape(raw_url))
    if 'duckduckgo.com' not in parsed.netloc:
        return raw_url

    query = parse_qs(parsed.query)
    return query.get('uddg', [raw_url])[0]


# 展開短網址
async def expand_url(short_url: str) -> str:
    """展開 Google Maps 分享短網址。"""
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=HTTP_TIMEOUT,
    ) as client:
        try:
            res = await client.head(short_url)
            res.raise_for_status()
            return str(res.url)
        except httpx.HTTPError:
            res = await client.get(short_url)
            res.raise_for_status()
            return str(res.url)


# 從展開的 URL 解析店名
def extract_place_name(url: str) -> str | None:
    """從 Google Maps 路徑提取店名。"""
    decoded = unquote(httpx.URL(url).path)
    match = re.search(r'/maps/place/([^/@?]+)', decoded)
    if match:
        return match.group(1).replace('+', ' ')
    return None


def extract_coordinates(url: str) -> tuple[float, float] | None:
    """從 Google Maps URL 內的 @lat,lng 片段提取座標。"""
    match = re.search(r'@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)', url)
    if not match:
        return None

    return float(match.group(1)), float(match.group(2))


# 依地址判斷濟州島區域
def detect_region(address: str) -> str:
    if not address:
        return '濟州市'

    east = [
        '성산읍', '표선면', '남원읍', '구좌읍', '조천읍',
        '성산', '표선', '남원', '城山', '表善', '南元', '旧左', '朝天',
    ]
    west = [
        '한림읍', '한경면', '애월읍', '대정읍', '안덕면',
        '한림', '한경', '애월', '대정', '안덕', '翰林', '翰京', '涯月', '大静', '安德',
    ]

    for kw in east:
        if kw in address:
            return '東部'
    for kw in west:
        if kw in address:
            return '西部'
    if (
        '서귀포시' in address
        or '서귀포' in address
        or '西歸浦' in address
        or '西归浦' in address
    ):
        return '西歸浦'

    return '濟州市'


def detect_day(region: str) -> str:
    """依景點區域對應到 Notion 行程欄位。"""
    return REGION_DAY_MAP.get(region, 'Day1')


def rating_to_stars(rating: float | None) -> str | None:
    """將 Google 評分轉成 Notion 的星等選項。"""
    if rating is None:
        return None

    if rating >= 4.5:
        return '⭐⭐⭐⭐⭐'
    if rating >= 3.5:
        return '⭐⭐⭐⭐'
    if rating >= 2.5:
        return '⭐⭐⭐'
    if rating >= 1.5:
        return '⭐⭐'
    return '⭐'


async def get_google_place_photo_url(photo_name: str) -> str | None:
    """將新版 Places Photo 資源名稱轉成可給 Notion 使用的圖片網址。"""
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        try:
            res = await client.get(
                f'https://places.googleapis.com/v1/{photo_name}/media',
                params={
                    'maxWidthPx': 1200,
                    'skipHttpRedirect': 'true',
                    'key': GOOGLE_KEY,
                },
            )
            res.raise_for_status()
        except httpx.HTTPError as exc:
            print(f'⚠️ Google Places 照片取得失敗：{exc}')
            return None

    return res.json().get('photoUri')


async def build_notion_photo_asset(photo_url: str) -> dict[str, str] | None:
    """挑出 Notion 可預覽的圖片連結，僅接受原始檔名為 jpg/png。"""
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=HTTP_TIMEOUT,
    ) as client:
        try:
            res = await client.get(photo_url)
            res.raise_for_status()
        except httpx.HTTPError as exc:
            print(f'⚠️ Notion 圖片檢查失敗：{exc}')
            return None

    content_type = (res.headers.get('content-type') or '').split(';', maxsplit=1)[0].strip().lower()
    if content_type not in {'image/jpeg', 'image/jpg', 'image/png'}:
        print(f'⚠️ Notion 不接受的圖片格式：{content_type}')
        return None

    content_disposition = res.headers.get('content-disposition', '')
    filename_match = re.search(r'filename=\"?([^\";]+)\"?', content_disposition, re.IGNORECASE)
    if not filename_match:
        print('⚠️ 找不到圖片原始檔名，略過寫入 Notion 照片欄位')
        return None

    original_filename = filename_match.group(1).strip()
    lower_filename = original_filename.lower()
    if not (lower_filename.endswith('.jpg') or lower_filename.endswith('.jpeg') or lower_filename.endswith('.png')):
        print(f'⚠️ 原始圖片檔名不是 jpg/png：{original_filename}')
        return None

    return {
        'url': str(res.url),
        'name': original_filename,
        'content_type': content_type,
    }


def normalize_places_reviews(places_reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """將 Places API reviews 轉成統一結構。"""
    normalized: list[dict[str, Any]] = []
    for review in places_reviews:
        text = (
            review.get('originalText', {}).get('text')
            or review.get('text', {}).get('text')
            or ''
        ).strip()
        if not text:
            continue

        normalized.append(
            {
                'stars': review.get('rating'),
                'text': text,
            }
        )

    return normalized


async def try_google_places_lookup(place_name: str) -> dict[str, Any] | None:
    """優先使用 Places API (New) 取得完整景點資訊。"""
    if not GOOGLE_KEY:
        return None

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        try:
            search_res = await client.post(
                'https://places.googleapis.com/v1/places:searchText',
                headers={
                    'X-Goog-Api-Key': GOOGLE_KEY,
                    'X-Goog-FieldMask': GOOGLE_PLACES_FIELD_MASK,
                },
                json={
                    'textQuery': f'{place_name} 제주',
                    'languageCode': 'zh-TW',
                },
            )
            search_res.raise_for_status()
            search_data = search_res.json()
        except httpx.HTTPStatusError as exc:
            error_message = exc.response.text
            try:
                error_message = exc.response.json().get('error', {}).get('message', error_message)
            except ValueError:
                pass
            print(f'⚠️ Google Places(New) 查詢失敗：{error_message}')
            return None
        except httpx.HTTPError as exc:
            print(f'⚠️ Google Places 查詢失敗：{exc}')
            return None

        places = search_data.get('places', [])
        if not places:
            return None

        place = places[0]
        photo_name = ((place.get('photos') or [{}])[0]).get('name')
        photo_asset: dict[str, str] | None = None
        if photo_name:
            google_photo_url = await get_google_place_photo_url(photo_name)
            if google_photo_url:
                photo_asset = await build_notion_photo_asset(google_photo_url)

        return {
            'name': place.get('displayName', {}).get('text', place_name),
            'formatted_address': place.get('formattedAddress', ''),
            'opening_hours': {
                'weekday_text': (
                    place.get('regularOpeningHours', {}).get('weekdayDescriptions', [])
                )
            },
            'photo_url': photo_asset['url'] if photo_asset else None,
            'photo_name': photo_asset['name'] if photo_asset else None,
            'photo_content_type': photo_asset['content_type'] if photo_asset else None,
            'rating': place.get('rating'),
            'google_maps_url': place.get('googleMapsUri'),
            'reviews': normalize_places_reviews(place.get('reviews', [])),
        }


async def reverse_geocode(lat: float, lng: float) -> str:
    """用座標向 OpenStreetMap 反查地址，避免被 Google Places API 綁死。"""
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        try:
            res = await client.get(
                'https://nominatim.openstreetmap.org/reverse',
                params={
                    'format': 'jsonv2',
                    'lat': lat,
                    'lon': lng,
                    'accept-language': 'zh-TW',
                },
                headers={'User-Agent': 'maps-to-notion/1.0'},
            )
            res.raise_for_status()
        except httpx.HTTPError as exc:
            print(f'⚠️ 反向地理編碼失敗：{exc}')
            return ''

    data = res.json()
    return data.get('display_name', '')


async def get_place_details(place_name: str, expanded_url: str) -> dict[str, Any]:
    """取得可寫入 Notion 的景點資料。"""
    google_result = await try_google_places_lookup(place_name)
    if google_result:
        google_result['data_source'] = 'google_places_new'
        return google_result

    coordinates = extract_coordinates(expanded_url)
    if not coordinates:
        return {
            'name': place_name,
            'formatted_address': '',
            'opening_hours': {},
            'photo_url': None,
            'photo_name': None,
            'photo_content_type': None,
            'rating': None,
            'google_maps_url': expanded_url,
            'data_source': 'fallback_url_only',
            'reviews': [],
        }

    lat, lng = coordinates
    address = await reverse_geocode(lat, lng)

    return {
        'name': place_name,
        'formatted_address': address,
        'opening_hours': {},
        'photo_url': None,
        'photo_name': None,
        'photo_content_type': None,
        'rating': None,
        'google_maps_url': expanded_url,
        'data_source': 'fallback_reverse_geocode',
        'reviews': [],
    }


async def click_google_maps_button(page: Any, keywords: list[str]) -> bool:
    """依按鈕文字或 aria-label 關鍵字點擊 Google Maps 按鈕。"""
    buttons = page.locator('button')
    count = await buttons.count()
    for index in range(min(count, 80)):
        button = buttons.nth(index)
        try:
            aria_label = (await button.get_attribute('aria-label') or '').strip()
            text = (await button.inner_text() or '').strip()
        except Exception:
            continue

        combined = f'{aria_label} {text}'.lower()
        if any(keyword in combined for keyword in keywords):
            try:
                await button.click()
                return True
            except Exception:
                continue

    return False


async def expand_visible_review_cards(review_feed: Any) -> None:
    """展開目前畫面內可見的完整評論。"""
    buttons = review_feed.locator('button')
    count = await buttons.count()
    for index in range(min(count, 50)):
        button = buttons.nth(index)
        try:
            aria_label = (await button.get_attribute('aria-label') or '').lower()
            text = (await button.inner_text() or '').lower()
        except Exception:
            continue

        if any(keyword in f'{aria_label} {text}' for keyword in ['更多', 'more', '자세히']):
            try:
                await button.click()
            except Exception:
                continue


async def collect_reviews_from_feed(review_feed: Any) -> list[dict[str, Any]]:
    """從 Google Maps 評論清單提取星等與原文。"""
    cards = review_feed.locator('[data-review-id]')
    count = await cards.count()
    reviews: list[dict[str, Any]] = []

    for index in range(count):
        card = cards.nth(index)
        try:
            review_id = await card.get_attribute('data-review-id')
        except Exception:
            review_id = None

        star_label = ''
        for selector in ['span[aria-label*="stars"]', 'span[aria-label*="顆星"]', 'span[aria-label*="별점"]']:
            locator = card.locator(selector).first
            if await locator.count():
                star_label = (await locator.get_attribute('aria-label') or '').strip()
                if star_label:
                    break

        text = ''
        for selector in ['span.wiI7pd', 'span.MyEned', 'div.MyEned', 'div.wiI7pd']:
            locator = card.locator(selector).first
            if await locator.count():
                text = (await locator.inner_text() or '').strip()
                if text:
                    break

        if not text:
            continue

        reviews.append(
            {
                'review_id': review_id,
                'stars': extract_star_rating(star_label),
                'text': text,
            }
        )

    return reviews


async def scrape_google_maps_reviews(map_url: str, limit: int = REVIEW_LIMIT) -> list[dict[str, Any]]:
    """使用 Playwright 抓取 Google Maps 最新評論。"""
    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except ModuleNotFoundError as exc:
        raise HTTPException(status_code=500, detail='目前執行環境尚未安裝 Playwright') from exc

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(locale='zh-TW')
        page = await context.new_page()

        try:
            await page.goto(map_url, wait_until='domcontentloaded', timeout=60000)
            await page.wait_for_timeout(3000)

            opened_reviews = await click_google_maps_button(page, ['則評論', 'reviews', '리뷰'])
            if not opened_reviews:
                raise HTTPException(status_code=502, detail='無法開啟 Google Maps 評論視窗')

            review_feed = page.locator('div[role="feed"]').first
            await review_feed.wait_for(timeout=15000)

            sort_clicked = await click_google_maps_button(page, ['排序評論', 'sort reviews', '리뷰 정렬'])
            if sort_clicked:
                await page.wait_for_timeout(1000)
                await click_google_maps_button(page, ['最新', 'newest', '최신'])
                await page.wait_for_timeout(2000)

            unique_reviews: dict[str, dict[str, Any]] = {}
            stable_rounds = 0
            last_count = 0

            while len(unique_reviews) < limit and stable_rounds < 8:
                await expand_visible_review_cards(review_feed)
                for review in await collect_reviews_from_feed(review_feed):
                    review_key = review.get('review_id') or review['text']
                    unique_reviews[review_key] = review

                if len(unique_reviews) == last_count:
                    stable_rounds += 1
                else:
                    stable_rounds = 0
                    last_count = len(unique_reviews)

                await review_feed.evaluate('(node) => { node.scrollBy(0, node.scrollHeight); }')
                await page.wait_for_timeout(1800)

            ordered_reviews = list(unique_reviews.values())[:limit]
            return [
                {'stars': review.get('stars'), 'text': review['text']}
                for review in ordered_reviews
            ]
        except PlaywrightTimeoutError as exc:
            raise HTTPException(status_code=504, detail='抓取 Google Maps 評論逾時') from exc
        finally:
            await context.close()
            await browser.close()


def build_review_summary_fallback(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    """當模型不可用時，提供最基本的評論摘要。"""
    return {
        'overall_summary': ['已成功抓取評論，但 Azure OpenAI 摘要暫時不可用。'],
        'category_highlights': {
            '餐點': '需人工補充',
            '服務': '需人工補充',
            '環境': '需人工補充',
            '排隊': '需人工補充',
            '停車': '需人工補充',
            '價格': '需人工補充',
            '適合族群': '需人工補充',
            '雷點': '需人工補充',
        },
    }


async def summarize_reviews_with_azure(
    place_name: str,
    reviews: list[dict[str, Any]],
) -> dict[str, Any]:
    """使用 Azure OpenAI 將評論整理成繁中摘要。"""
    if not reviews:
        return {
            'overall_summary': ['目前抓不到可分析的評論。'],
            'category_highlights': {
                '餐點': '幾乎未提及',
                '服務': '幾乎未提及',
                '環境': '幾乎未提及',
                '排隊': '幾乎未提及',
                '停車': '幾乎未提及',
                '價格': '幾乎未提及',
                '適合族群': '幾乎未提及',
                '雷點': '幾乎未提及',
            },
        }

    if not AZURE_OPENAI_ENDPOINT or not AZURE_OPENAI_API_KEY:
        return build_review_summary_fallback(reviews)

    payload = {
        'messages': [
            {
                'role': 'system',
                'content': (
                    '你是旅遊評論分析助手。請根據提供的 Google Maps 評論，用繁體中文輸出 JSON。'
                    '請客觀整理，不要杜撰未提及內容。若資訊不足，請寫「少量提及」或「幾乎未提及」。'
                ),
            },
            {
                'role': 'user',
                'content': json.dumps(
                    {
                        'place_name': place_name,
                        'review_count': len(reviews),
                        'required_format': {
                            'overall_summary': ['3到5句繁中摘要'],
                            'category_highlights': {
                                '餐點': 'string',
                                '服務': 'string',
                                '環境': 'string',
                                '排隊': 'string',
                                '停車': 'string',
                                '價格': 'string',
                                '適合族群': 'string',
                                '雷點': 'string',
                            },
                        },
                        'reviews': reviews,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        'temperature': 0.2,
        'response_format': {'type': 'json_object'},
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        try:
            response = await client.post(
                AZURE_OPENAI_ENDPOINT,
                headers={
                    'api-key': AZURE_OPENAI_API_KEY,
                    'Content-Type': 'application/json',
                },
                json=payload,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            print(f'⚠️ Azure OpenAI 摘要失敗：{exc}')
            return build_review_summary_fallback(reviews)

    data = response.json()
    message = data['choices'][0]['message']['content']
    try:
        summary = parse_json_response(message)
    except (KeyError, json.JSONDecodeError) as exc:
        print(f'⚠️ Azure OpenAI 回傳格式異常：{exc}')
        return build_review_summary_fallback(reviews)

    return summary


async def build_review_summary(place_name: str, map_url: str) -> dict[str, Any]:
    """整合評論抓取與摘要流程。"""
    reviews = await scrape_google_maps_reviews(map_url, REVIEW_LIMIT)
    summary = await summarize_reviews_with_azure(place_name, reviews)
    summary['review_count'] = len(reviews)
    return summary


async def build_review_summary_with_fallback(
    place_name: str,
    map_url: str,
    place: dict[str, Any],
) -> dict[str, Any]:
    """優先抓 Google Maps 最新評論，失敗時退回 Places API reviews。"""
    try:
        return await build_review_summary(place_name, map_url)
    except HTTPException as exc:
        fallback_reviews = place.get('reviews', [])
        if not fallback_reviews:
            raise exc

        print(f'⚠️ 改用 Places API reviews fallback：{exc.detail}')
        summary = await summarize_reviews_with_azure(place_name, fallback_reviews)
        summary['review_count'] = len(fallback_reviews)
        summary['review_source'] = 'places_api_fallback'
        return summary


async def search_related_articles(place_name: str, limit: int = ARTICLE_LIMIT) -> list[dict[str, str]]:
    """用 DuckDuckGo 搜尋「店名 + 介紹」並抓前三篇文章。"""
    query = quote(f'{place_name} 介紹')
    search_url = f'https://html.duckduckgo.com/html/?q={query}'

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        try:
            response = await client.get(
                search_url,
                headers={'User-Agent': 'Mozilla/5.0'},
                follow_redirects=True,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            print(f'⚠️ 文章搜尋失敗：{exc}')
            return []

    matches = re.finditer(
        r'<a[^>]+class=\"result__a\"[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>',
        response.text,
        re.S,
    )
    articles: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    for match in matches:
        url = unwrap_duckduckgo_url(match.group(1))
        title = re.sub(r'<.*?>', '', unescape(match.group(2))).strip()
        if not title or not url or url in seen_urls:
            continue
        if not url.startswith('http'):
            continue

        seen_urls.add(url)
        articles.append({'title': title, 'url': url})
        if len(articles) >= limit:
            break

    return articles


def build_notion_children(
    place: dict[str, Any],
    source_url: str,
    region: str,
    review_summary: dict[str, Any] | None = None,
    related_articles: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """建立頁面內容區塊，供 database/page 兩種父層共用。"""
    rating = place.get('rating')
    rating_text = f'{rating:.1f}' if isinstance(rating, int | float) else '未提供'
    hours_lines = place.get('opening_hours', {}).get('weekday_text', [])

    children = [
        {
            'object': 'block',
            'type': 'paragraph',
            'paragraph': {
                'rich_text': [
                    {'text': {'content': '評分：'}, 'annotations': {'bold': True}},
                    {'text': {'content': rating_text}},
                ]
            },
        },
    ]

    if hours_lines:
        children.append(
            {
                'object': 'block',
                'type': 'paragraph',
                'paragraph': {
                    'rich_text': [
                        {'text': {'content': '營業時間：'}, 'annotations': {'bold': True}},
                    ]
                },
            }
        )
        for line in hours_lines:
            children.append(
                {
                    'object': 'block',
                    'type': 'bulleted_list_item',
                    'bulleted_list_item': {
                        'rich_text': [
                            {'text': {'content': line}},
                        ]
                    },
                }
            )
    else:
        children.append(
            {
                'object': 'block',
                'type': 'paragraph',
                'paragraph': {
                    'rich_text': [
                        {'text': {'content': '營業時間：'}, 'annotations': {'bold': True}},
                        {'text': {'content': '未提供'}},
                    ]
                },
            }
        )

    if review_summary:
        children.append(
            {
                'object': 'block',
                'type': 'paragraph',
                'paragraph': {
                    'rich_text': [
                        {'text': {'content': '評論摘要'}, 'annotations': {'bold': True}},
                    ]
                },
            }
        )
        for line in review_summary.get('overall_summary', []):
            children.append(
                {
                    'object': 'block',
                    'type': 'bulleted_list_item',
                    'bulleted_list_item': {
                        'rich_text': [
                            {'text': {'content': line}},
                        ]
                    },
                }
            )

    if related_articles:
        children.append(
            {
                'object': 'block',
                'type': 'paragraph',
                'paragraph': {
                    'rich_text': [
                        {'text': {'content': '相關文章'}, 'annotations': {'bold': True}},
                    ]
                },
            }
        )
        for article in related_articles:
            children.append(
                {
                    'object': 'block',
                    'type': 'bulleted_list_item',
                    'bulleted_list_item': {
                        'rich_text': [
                            {
                                'text': {
                                    'content': article['title'],
                                    'link': {'url': article['url']},
                                }
                            },
                        ]
                    },
                }
            )

    if review_summary:
        children.append(
            {
                'object': 'block',
                'type': 'paragraph',
                'paragraph': {
                    'rich_text': [
                        {'text': {'content': '分類重點'}, 'annotations': {'bold': True}},
                    ]
                },
            }
        )
        for category in ['餐點', '服務', '環境', '排隊', '停車', '價格', '適合族群', '雷點']:
            children.append(
                {
                    'object': 'block',
                    'type': 'bulleted_list_item',
                    'bulleted_list_item': {
                        'rich_text': [
                            {'text': {'content': f'{category}：'}, 'annotations': {'bold': True}},
                            {
                                'text': {
                                    'content': review_summary.get('category_highlights', {}).get(
                                        category,
                                        '幾乎未提及',
                                    )
                                }
                            },
                        ]
                    },
                }
            )

    return children


def find_target_data_source() -> tuple[str, dict[str, Any]] | None:
    """在可存取的 Notion data source 中找出目標資料庫與欄位定義。"""
    try:
        search_result = notion.search(
            filter={'property': 'object', 'value': 'data_source'}
        )
    except APIResponseError as exc:
        print(f'⚠️ Notion 搜尋 data source 失敗：{exc}')
        return None

    matched_item: tuple[str, str] | None = None
    fallback_items: list[tuple[str, str]] = []

    for item in search_result.get('results', []):
        title = ''.join(text.get('plain_text', '') for text in item.get('title', []))
        parent = item.get('parent', {})
        database_id = parent.get('database_id') if parent.get('type') == 'database_id' else None
        if not database_id:
            continue

        fallback_items.append((database_id, item['id']))
        if title == NOTION_TARGET_NAME:
            data_source = notion.data_sources.retrieve(data_source_id=item['id'])
            return database_id, data_source.get('properties', {})

        if matched_item is None:
            matched_item = (database_id, item['id'])

    if len(fallback_items) == 1:
        database_id, data_source_id = fallback_items[0]
        data_source = notion.data_sources.retrieve(data_source_id=data_source_id)
        return database_id, data_source.get('properties', {})

    if matched_item is None:
        return None

    database_id, data_source_id = matched_item
    data_source = notion.data_sources.retrieve(data_source_id=data_source_id)
    return database_id, data_source.get('properties', {})


def build_database_properties(
    place: dict[str, Any],
    source_url: str,
    region: str,
    schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """依照實際資料庫欄位型別建立 Notion properties。"""
    schema = schema or {}
    title_property_name = 'Name'

    for property_name, metadata in schema.items():
        if metadata.get('type') == 'title':
            title_property_name = property_name
            break

    properties: dict[str, Any] = {
        title_property_name: {
            'title': [{'text': {'content': place.get('name', '未知店名')}}]
        }
    }

    day_name = detect_day(region)

    category_schema = schema.get('分類')
    if category_schema:
        category_type = category_schema.get('type')
        if category_type == 'multi_select':
            properties['分類'] = {'multi_select': [{'name': region}]}
        elif category_type == 'select':
            properties['分類'] = {'select': {'name': region}}

    schedule_schema = schema.get('日程')
    if schedule_schema:
        schedule_type = schedule_schema.get('type')
        if schedule_type == 'multi_select':
            properties['日程'] = {'multi_select': [{'name': day_name}]}
        elif schedule_type == 'select':
            properties['日程'] = {'select': {'name': day_name}}

    google_map_url = place.get('google_maps_url') or source_url
    google_map_schema = schema.get('Google Map')
    if google_map_url and google_map_schema and google_map_schema.get('type') == 'url':
        properties['Google Map'] = {'url': google_map_url}

    rating_text = rating_to_stars(place.get('rating'))
    rating_schema = schema.get('評分')
    if rating_text and rating_schema:
        rating_type = rating_schema.get('type')
        if rating_type == 'multi_select':
            properties['評分'] = {'multi_select': [{'name': rating_text}]}
        elif rating_type == 'select':
            properties['評分'] = {'select': {'name': rating_text}}

    return properties


# 建立 Notion 頁面
def create_notion_page(
    place: dict[str, Any],
    source_url: str,
    review_summary: dict[str, Any] | None = None,
    related_articles: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    region = detect_region(place.get('formatted_address', ''))
    children = build_notion_children(place, source_url, region, review_summary, related_articles)
    target_data_source = find_target_data_source()
    if target_data_source:
        target_database_id, target_schema = target_data_source
        properties = build_database_properties(place, source_url, region, target_schema)
        print(f'ℹ️ 改寫入資料庫：{target_database_id}')
        payload: dict[str, Any] = {
            'parent': {'database_id': target_database_id},
            'properties': properties,
            'children': children,
        }

        return notion.pages.create(
            **payload,
        )

    properties = build_database_properties(place, source_url, region)

    try:
        payload = {
            'parent': {'database_id': DB_ID},
            'properties': properties,
            'children': children,
        }

        return notion.pages.create(**payload)
    except APIResponseError as exc:
        if 'is a page, not a database' not in str(exc):
            raise

        payload = {
            'parent': {'page_id': DB_ID},
            'properties': {
                'title': [
                    {'text': {'content': place.get('name', '未知店名')}}
                ]
            },
            'children': children,
        }

        return notion.pages.create(**payload)


def translate_notion_error(exc: APIResponseError) -> HTTPException:
    """將 Notion API 錯誤轉成使用者看得懂的訊息。"""
    error_text = str(exc)

    if 'Could not find page with ID' in error_text:
        return HTTPException(
            status_code=400,
            detail='找不到 Notion 目標頁面，請確認該頁面已分享給 integration「maps-sync」。',
        )

    if 'is a page, not a database' in error_text:
        return HTTPException(
            status_code=400,
            detail='NOTION_DATABASE_ID 目前是一個頁面 ID，已自動改用子頁模式；若仍失敗，請確認頁面是否有分享給 integration。',
        )

    return HTTPException(
        status_code=500,
        detail=f'Notion API 錯誤：{error_text}',
    )


async def resolve_place_from_url(url: str) -> tuple[str, str, dict[str, Any], str, str]:
    """解析分享網址並產出可預覽/寫入的景點資料。"""
    try:
        expanded_url = await expand_url(url)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=400, detail=f'無法展開分享網址：{exc}') from exc

    print(f'🔗 展開：{expanded_url}')

    place_name = extract_place_name(expanded_url)
    if not place_name:
        raise HTTPException(status_code=400, detail='無法從 URL 解析店名')

    print(f'🏪 店名：{place_name}')
    place = await get_place_details(place_name, expanded_url)
    print(f'📍 地址：{place.get("formatted_address")}')

    region = detect_region(place.get('formatted_address', ''))
    day = detect_day(region)
    return expanded_url, place_name, place, region, day


def build_preview_payload(
    source_url: str,
    expanded_url: str,
    place: dict[str, Any],
    region: str,
    day: str,
    review_summary: dict[str, Any] | None = None,
    related_articles: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """建立給前端檢查用的預覽資料。"""
    hours_lines = place.get('opening_hours', {}).get('weekday_text', [])

    return {
        'success': True,
        'source_url': source_url,
        'expanded_url': expanded_url,
        'name': place.get('name'),
        'region': region,
        'day': day,
        'address': place.get('formatted_address'),
        'rating': place.get('rating'),
        'rating_stars': rating_to_stars(place.get('rating')),
        'photo_url': place.get('photo_url'),
        'photo_name': place.get('photo_name'),
        'photo_content_type': place.get('photo_content_type'),
        'opening_hours': hours_lines,
        'google_maps_url': place.get('google_maps_url') or source_url,
        'data_source': place.get('data_source'),
        'review_count': (review_summary or {}).get('review_count', 0),
        'review_summary': (
            {
                'overall_summary': (review_summary or {}).get('overall_summary', []),
                'category_highlights': (review_summary or {}).get('category_highlights', {}),
            }
            if review_summary
            else None
        ),
        'related_articles': related_articles or [],
    }


async def collect_enriched_place_data(
    source_url: str,
) -> tuple[
    str,
    dict[str, Any],
    str,
    str,
    dict[str, Any] | None,
    list[dict[str, str]],
]:
    """集中處理預覽與寫入共用的景點補充流程。"""
    expanded_url, place_name, place, region, day = await resolve_place_from_url(source_url)
    map_url = place.get('google_maps_url') or expanded_url
    review_summary: dict[str, Any] | None = None
    related_articles: list[dict[str, str]] = []

    try:
        review_summary = await build_review_summary_with_fallback(place_name, map_url, place)
    except HTTPException as exc:
        print(f'⚠️ 評論抓取失敗：{exc.detail}')
    except Exception as exc:
        print(f'⚠️ 評論流程失敗：{exc}')

    try:
        related_articles = await search_related_articles(place_name)
    except Exception as exc:
        print(f'⚠️ 文章搜尋流程失敗：{exc}')

    return expanded_url, place, region, day, review_summary, related_articles


# API Endpoint
@app.post('/save-preview')
async def save_preview(req: SaveRequest) -> dict[str, Any]:
    print(f'🔎 預覽：{req.url}')
    expanded_url, place, region, day, review_summary, related_articles = (
        await collect_enriched_place_data(req.url)
    )

    return build_preview_payload(
        req.url,
        expanded_url,
        place,
        region,
        day,
        review_summary,
        related_articles,
    )


@app.post('/save')
async def save(req: SaveRequest) -> dict[str, Any]:
    print(f'📥 收到：{req.url}')
    _, place, region, _, review_summary, related_articles = await collect_enriched_place_data(req.url)

    try:
        page = create_notion_page(place, req.url, review_summary, related_articles)
    except APIResponseError as exc:
        raise translate_notion_error(exc) from exc

    print(f'✅ Notion 頁面：{page["id"]}')

    return {
        'success': True,
        'name': place.get('name'),
        'region': region,
        'review_summary_available': review_summary is not None,
        'review_count': (review_summary or {}).get('review_count', 0),
    }


@app.get('/save-preview-link')
async def save_preview_link(
    url: str = Query(..., min_length=1, description='Google Maps 分享網址'),
) -> dict[str, Any]:
    """讓手機或瀏覽器可直接用 query string 呼叫預覽 API。"""
    return await save_preview(SaveRequest(url=url))


@app.get('/save-link')
async def save_link(
    url: str = Query(..., min_length=1, description='Google Maps 分享網址'),
) -> dict[str, Any]:
    """讓手機或瀏覽器可直接用 query string 呼叫寫入 API。"""
    return await save(SaveRequest(url=url))


@app.get('/')
def health():
    return {'status': 'ok'}