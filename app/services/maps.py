"""處理 Google Maps 分享網址的 service。

這支檔案主要負責：
1. 清理與展開 Google Maps 分享連結。
2. 從 URL 解析出店名或座標。
3. 呼叫 `places.py` 取得完整景點資料。
4. 根據地址推測景點區域與行程天數。

它在整個專案中的位置，大致是：
原始分享網址 -> 可用的景點資訊
"""

import re
from html import unescape
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlparse, urlunparse

import httpx
from fastapi import HTTPException

from app.config import HTTP_TIMEOUT
from app.services.places import get_place_details
from app.utils.region import detect_day, detect_region


def normalize_google_maps_share_url(raw_url: str) -> str:
    """移除 Google Maps 分享連結中的追蹤參數，保留原始目的地。

    這裡主要是把像 `g_st` 這類追蹤用途的 query 參數移除，
    讓同一個地點網址更穩定，也方便後續解析。
    """
    stripped_url = raw_url.strip()
    parsed = urlparse(stripped_url)

    # 如果不是 Google Maps 網址，就不做額外處理。
    if 'maps.app.goo.gl' not in parsed.netloc and 'google.' not in parsed.netloc:
        return stripped_url

    filtered_query = [
        (key, value)
        for key, values in parse_qs(parsed.query, keep_blank_values=True).items()
        if key.lower() not in {'g_st'}
        for value in values
    ]
    normalized_query = urlencode(filtered_query, doseq=True)

    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            normalized_query,
            parsed.fragment,
        )
    )


def normalize_place_candidate(raw_value: str) -> str | None:
    """將 URL 中可能的地點名稱片段清理成可搜尋文字。

    目標是把 URL 裡拆出來的片段，整理成適合拿去查 Places API 的字串。
    例如：
    - URL encoded 字串要先還原
    - `+` 轉成空白
    - 純座標或另一個網址要排除
    """
    decoded_value = unquote(raw_value or '').replace('+', ' ').strip()
    if not decoded_value:
        return None

    compact_value = re.sub(r'\s+', ' ', decoded_value)
    if compact_value.startswith(('http://', 'https://')):
        return None

    if re.fullmatch(r'-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?', compact_value):
        return None

    return compact_value


async def expand_url(short_url: str) -> str:
    """展開 Google Maps 分享短網址。

    先用 HEAD 嘗試取得最終跳轉網址；
    如果失敗，再退回 GET，兼容不同站點的行為。
    """
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


def extract_place_name(url: str) -> str | None:
    """從多種 Google Maps URL 型態提取店名。

    Google Maps 的網址格式很多，所以這裡會依序嘗試：
    1. 從 path 中的 `/place/...` 或 `/search/...` 抓名稱。
    2. 從 query string 的 `q`、`query`、`destination` 抓名稱。
    3. 最後退回 path 最後一段。
    """
    parsed = urlparse(url)
    decoded_path = unquote(parsed.path)

    path_patterns = [
        r'/maps/place/([^/@?]+)',
        r'/place/([^/@?]+)',
        r'/maps/search/([^/@?]+)',
        r'/search/([^/@?]+)',
    ]
    for pattern in path_patterns:
        match = re.search(pattern, decoded_path)
        if not match:
            continue

        candidate = normalize_place_candidate(match.group(1))
        if candidate:
            return candidate

    query = parse_qs(parsed.query)
    for key in ('q', 'query', 'destination'):
        for raw_value in query.get(key, []):
            candidate = normalize_place_candidate(raw_value)
            if candidate:
                return candidate

    path_segments = [segment for segment in decoded_path.split('/') if segment]
    if path_segments:
        candidate = normalize_place_candidate(path_segments[-1])
        if candidate and candidate.lower() not in {'maps', 'place', 'search'}:
            return candidate

    return None


def extract_coordinates(url: str) -> tuple[float, float] | None:
    """從 Google Maps URL 內的 `@lat,lng` 片段提取座標。"""
    match = re.search(r'@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)', url)
    if not match:
        return None

    return float(match.group(1)), float(match.group(2))


async def resolve_place_from_url(url: str) -> tuple[str, str, dict[str, Any], str, str]:
    """解析分享網址並產出可預覽/寫入的景點資料。

    這是這支檔案最核心的函式，流程如下：
    1. 展開短網址。
    2. 清理網址中的追蹤參數。
    3. 從網址提取店名。
    4. 呼叫 Places service 取得完整景點資料。
    5. 根據地址判斷區域與對應天數。
    """
    try:
        expanded_url = await expand_url(url)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=400, detail=f'無法展開分享網址：{exc}') from exc

    normalized_expanded_url = normalize_google_maps_share_url(expanded_url)
    if normalized_expanded_url != expanded_url:
        print(f'🧹 已清理展開後分享參數：{normalized_expanded_url}')

    expanded_url = normalized_expanded_url
    print(f'🔗 展開：{expanded_url}')

    # 優先從網址直接解析店名；若失敗但有座標，就給一個通用名稱讓後續流程能繼續。
    place_name = extract_place_name(expanded_url)
    if not place_name:
        if extract_coordinates(expanded_url):
            place_name = 'Google Maps 地點'
        else:
            raise HTTPException(status_code=400, detail='無法從 URL 解析店名')

    print(f'🏪 店名：{place_name}')

    # 交給 Places service 補齊地址、評分、照片、評論等資訊。
    place = await get_place_details(place_name, expanded_url)
    print(f'📍 地址：{place.get("formatted_address")}')

    # 根據地址推測屬於濟州哪個區域，並轉成 Day1~Day4。
    region = detect_region(place.get('formatted_address', ''))
    day = detect_day(region)
    return expanded_url, place_name, place, region, day


def unwrap_duckduckgo_url(raw_url: str) -> str:
    """將 DuckDuckGo 跳轉連結還原成原始文章網址。

    DuckDuckGo 搜尋結果常會先跳到自己的 redirect URL，
    真正目標網址會放在 `uddg` 參數裡，這裡負責還原它。
    """
    if raw_url.startswith('//'):
        raw_url = f'https:{raw_url}'

    parsed = urlparse(unescape(raw_url))
    if 'duckduckgo.com' not in parsed.netloc:
        return raw_url

    query = parse_qs(parsed.query)
    return query.get('uddg', [raw_url])[0]
