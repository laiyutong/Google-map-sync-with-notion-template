import re
from html import unescape
from urllib.parse import quote

import httpx

from app.config import ARTICLE_LIMIT, HTTP_TIMEOUT
from app.services.maps import unwrap_duckduckgo_url


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
