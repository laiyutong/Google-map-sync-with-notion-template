from typing import Any

from fastapi import FastAPI, Query
from notion_client.errors import APIResponseError

from app.schemas import SaveRequest
from app.services.articles import search_related_articles
from app.services.maps import resolve_place_from_url
from app.services.notion import create_notion_page, translate_notion_error
from app.services.reviews import build_review_summary_from_place
from app.utils.region import rating_to_stars

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
    """集中處理預覽與寫入共用的景點補充流程。"""
    expanded_url, place_name, place, region, day = await resolve_place_from_url(source_url)
    review_summary: dict[str, Any] | None = None
    related_articles: list[dict[str, str]] = []

    try:
        review_summary = await build_review_summary_from_place(place_name, place)
    except Exception as exc:
        print(f'⚠️ 評論摘要流程失敗：{exc}')

    try:
        related_articles = await search_related_articles(place_name)
    except Exception as exc:
        print(f'⚠️ 文章搜尋流程失敗：{exc}')

    return expanded_url, place, region, day, review_summary, related_articles


@app.post('/save-preview')
async def save_preview(req: SaveRequest) -> dict[str, Any]:
    source_url = req.url.strip()
    print(f'🔎 預覽：{source_url}')
    expanded_url, place, region, day, review_summary, related_articles = (
        await collect_enriched_place_data(source_url)
    )

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
    source_url = req.url.strip()
    print(f'📥 收到：{source_url}')
    _, place, region, _, review_summary, related_articles = await collect_enriched_place_data(source_url)

    try:
        page = create_notion_page(place, source_url, review_summary, related_articles)
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
def health() -> dict[str, str]:
    return {'status': 'ok'}
