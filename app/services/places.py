"""向地圖服務查詢景點資料的 service。

這支檔案主要負責：
1. 呼叫 Google Places API 取得景點資訊。
2. 將 Places API 回傳內容整理成專案內部的統一格式。
3. 當 Google Places 資料不足時，使用座標做 fallback。

它在整個流程中的位置大致是：
店名 / 座標 -> 完整景點資料
"""

from typing import Any

import httpx

from app.config import GOOGLE_KEY, GOOGLE_PLACES_FIELD_MASK, HTTP_TIMEOUT


def normalize_places_reviews(places_reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """將 Places API reviews 轉成統一結構。

    後續評論摘要流程只需要星等與文字內容，
    所以這裡會先把 Google Places 原始欄位整理成精簡格式。
    """
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
    """優先使用 Places API (New) 取得完整景點資訊。

    這支函式會：
    1. 用店名搜尋 Google Places。
    2. 取第一筆最可能的景點結果。
    3. 組成專案統一使用的景點資料格式。
    """
    # 如果沒有設定 Google API Key，就直接跳過 Google Places 查詢。
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

        # 搜尋結果可能為空，這時交由後續 fallback 處理。
        places = search_data.get('places', [])
        if not places:
            return None

        place = places[0]
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
            'rating': place.get('rating'),
            'google_maps_url': place.get('googleMapsUri'),
            'reviews': normalize_places_reviews(place.get('reviews', [])),
        }


async def reverse_geocode(lat: float, lng: float) -> str:
    """用座標向 OpenStreetMap 反查地址，避免被 Google Places API 綁死。

    當 Places API 無法提供可用地址時，至少還能依座標反查出基本地址，
    讓後續區域判斷與 Notion 寫入仍有資料可用。
    """
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
    """取得可寫入 Notion 的景點資料。

    fallback 順序如下：
    1. 先試 Google Places API。
    2. 若失敗，從展開後網址中抓座標。
    3. 若有座標，使用 OpenStreetMap 反查地址。
    4. 若連座標都沒有，就只保留最基本的店名與原始網址。
    """
    google_result = await try_google_places_lookup(place_name)
    if google_result:
        google_result['data_source'] = 'google_places_new'
        return google_result

    # 避免循環匯入：只有在 fallback 路徑需要時才延後匯入。
    from app.services.maps import extract_coordinates

    coordinates = extract_coordinates(expanded_url)
    if not coordinates:
        # 沒有座標時，只能回傳最基本的資料，讓主流程不要整個失敗。
        return {
            'name': place_name,
            'formatted_address': '',
            'latitude': None,
            'longitude': None,
            'opening_hours': {},
            'rating': None,
            'google_maps_url': expanded_url,
            'data_source': 'fallback_url_only',
            'reviews': [],
        }

    lat, lng = coordinates
    address = await reverse_geocode(lat, lng)

    # 至少保留座標與反查地址，供後續 Notion 寫入與區域判斷使用。
    return {
        'name': place_name,
        'formatted_address': address,
        'latitude': lat,
        'longitude': lng,
        'opening_hours': {},
        'rating': None,
        'google_maps_url': expanded_url,
        'data_source': 'fallback_reverse_geocode',
        'reviews': [],
    }
