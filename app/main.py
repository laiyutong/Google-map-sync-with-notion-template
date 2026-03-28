"""FastAPI 路由入口。

這支檔案負責 3 件事：
1. 定義 API 路由。
2. 串接各個 service，完成景點資料補充流程。
3. 回傳預覽結果，或將資料寫入 Notion。
"""

from typing import Any

from fastapi import FastAPI, Query
from notion_client.errors import APIResponseError

from app.schemas import SaveRequest
from app.services.articles import search_related_articles
from app.services.maps import resolve_place_from_url
from app.services.notion import create_notion_page, translate_notion_error
from app.services.reviews import build_review_summary_from_place
from app.utils.region import rating_to_stars

# 建立 FastAPI 應用程式實例，所有路由都會掛在這個物件上。
app = FastAPI()


def build_preview_payload(
    source_url: str,
    expanded_url: str,
    place: dict[str, Any],
    region: str,
    day: str,
    review_summary: dict[str, Any] | None = None,
    related_articles: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """建立給前端檢查用的預覽資料。

    這裡只負責整理輸出格式，不負責查資料或寫入 Notion。
    """
    # Google Places 回傳的營業時間會放在 opening_hours.weekday_text。
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
        'latitude': place.get('latitude'),
        'longitude': place.get('longitude'),
        'opening_hours': hours_lines,
        'google_maps_url': place.get('google_maps_url') or source_url,
        'data_source': place.get('data_source'),
        'review_count': (review_summary or {}).get('review_count', 0),
        'review_source': (review_summary or {}).get('review_source'),
        'review_error': (review_summary or {}).get('review_error'),
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
    """集中處理預覽與寫入共用的景點補充流程。

    主流程如下：
    1. 先從 Google Maps 分享網址解析出景點資料。
    2. 嘗試補上評論摘要。
    3. 嘗試補上相關文章。

    這支函式同時被 `/save-preview` 與 `/save` 共用，
    避免兩邊重複寫一樣的查詢流程。
    """
    # 先把分享網址轉成可用的景點資訊，例如名稱、地址、座標、地區、日程。
    expanded_url, place_name, place, region, day = await resolve_place_from_url(source_url)
    review_summary: dict[str, Any] | None = None
    related_articles: list[dict[str, str]] = []

    # 評論摘要屬於加值資訊，失敗時不應阻止主流程。
    try:
        review_summary = await build_review_summary_from_place(place_name, place)
    except Exception as exc:
        print(f'⚠️ 評論摘要流程失敗：{exc}')

    # 相關文章同樣屬於補充資訊，失敗時也不要讓 API 整體失敗。
    try:
        related_articles = await search_related_articles(place_name)
    except Exception as exc:
        print(f'⚠️ 文章搜尋流程失敗：{exc}')

    return expanded_url, place, region, day, review_summary, related_articles


@app.post('/save-preview')
async def save_preview(req: SaveRequest) -> dict[str, Any]:
    """只做解析與預覽，不會寫入 Notion。"""
    # 移除前後空白，避免使用者貼上網址時夾帶多餘空格。
    source_url = req.url.strip()
    print(f'🔎 預覽：{source_url}')

    # 共用景點補充流程：解析網址、補評論摘要、補相關文章。
    expanded_url, place, region, day, review_summary, related_articles = (
        await collect_enriched_place_data(source_url)
    )

    # 將內部資料整理成適合前端或呼叫端閱讀的預覽格式。
    return build_preview_payload(
        source_url,
        expanded_url,
        place,
        region,
        day,
        review_summary,
        related_articles,
    )


@app.post('/save')
async def save(req: SaveRequest) -> dict[str, Any]:
    """解析資料後，真正寫入 Notion。"""
    source_url = req.url.strip()
    print(f'📥 收到：{source_url}')

    # 寫入前仍需先經過同一套補資料流程，確保 Notion 拿到完整內容。
    _, place, region, _, review_summary, related_articles = await collect_enriched_place_data(source_url)

    try:
        # 實際建立 Notion page 或 database row。
        page = create_notion_page(place, source_url, review_summary, related_articles)
    except APIResponseError as exc:
        # 將 Notion SDK 錯誤轉成較適合 API 回傳的訊息。
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
    """讓手機或瀏覽器可直接用 query string 呼叫預覽 API。

    這支只是 GET 包裝版本，實際邏輯仍直接重用 `save_preview()`。
    """
    return await save_preview(SaveRequest(url=url))


@app.get('/save-link')
async def save_link(
    url: str = Query(..., min_length=1, description='Google Maps 分享網址'),
) -> dict[str, Any]:
    """讓手機或瀏覽器可直接用 query string 呼叫寫入 API。

    這支只是 GET 包裝版本，實際邏輯仍直接重用 `save()`。
    """
    return await save(SaveRequest(url=url))


@app.get('/')
def health() -> dict[str, str]:
    """健康檢查 API，確認服務是否正常啟動。"""
    return {'status': 'ok'}
