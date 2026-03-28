import re
from typing import Any

import httpx

from app.config import GOOGLE_KEY, GOOGLE_PLACES_FIELD_MASK, HTTP_TIMEOUT


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
            'latitude': place.get('location', {}).get('latitude'),
            'longitude': place.get('location', {}).get('longitude'),
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

    from app.services.maps import extract_coordinates

    coordinates = extract_coordinates(expanded_url)
    if not coordinates:
        return {
            'name': place_name,
            'formatted_address': '',
            'latitude': None,
            'longitude': None,
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
        'latitude': lat,
        'longitude': lng,
        'opening_hours': {},
        'photo_url': None,
        'photo_name': None,
        'photo_content_type': None,
        'rating': None,
        'google_maps_url': expanded_url,
        'data_source': 'fallback_reverse_geocode',
        'reviews': [],
    }
