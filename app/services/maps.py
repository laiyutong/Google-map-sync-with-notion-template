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
    """移除 Google Maps 分享連結中的追蹤參數，保留原始目的地。"""
    stripped_url = raw_url.strip()
    parsed = urlparse(stripped_url)

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
    """將 URL 中可能的地點名稱片段清理成可搜尋文字。"""
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


def extract_place_name(url: str) -> str | None:
    """從多種 Google Maps URL 型態提取店名。"""
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
    """從 Google Maps URL 內的 @lat,lng 片段提取座標。"""
    match = re.search(r'@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)', url)
    if not match:
        return None

    return float(match.group(1)), float(match.group(2))


async def resolve_place_from_url(url: str) -> tuple[str, str, dict[str, Any], str, str]:
    """解析分享網址並產出可預覽/寫入的景點資料。"""
    try:
        expanded_url = await expand_url(url)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=400, detail=f'無法展開分享網址：{exc}') from exc

    normalized_expanded_url = normalize_google_maps_share_url(expanded_url)
    if normalized_expanded_url != expanded_url:
        print(f'🧹 已清理展開後分享參數：{normalized_expanded_url}')

    expanded_url = normalized_expanded_url
    print(f'🔗 展開：{expanded_url}')

    place_name = extract_place_name(expanded_url)
    if not place_name:
        if extract_coordinates(expanded_url):
            place_name = 'Google Maps 地點'
        else:
            raise HTTPException(status_code=400, detail='無法從 URL 解析店名')

    print(f'🏪 店名：{place_name}')
    place = await get_place_details(place_name, expanded_url)
    print(f'📍 地址：{place.get("formatted_address")}')

    region = detect_region(place.get('formatted_address', ''))
    day = detect_day(region)
    return expanded_url, place_name, place, region, day


def unwrap_duckduckgo_url(raw_url: str) -> str:
    """將 DuckDuckGo 跳轉連結還原成原始文章網址。"""
    if raw_url.startswith('//'):
        raw_url = f'https:{raw_url}'

    parsed = urlparse(unescape(raw_url))
    if 'duckduckgo.com' not in parsed.netloc:
        return raw_url

    query = parse_qs(parsed.query)
    return query.get('uddg', [raw_url])[0]
