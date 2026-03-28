"""搜尋景點相關文章的 service。

這支檔案主要負責：
1. 用景點名稱組出搜尋關鍵字。
2. 向 DuckDuckGo 發送搜尋請求。
3. 從 HTML 搜尋結果中擷取文章標題與網址。
4. 去除重複與無效結果。

它在整個流程中的位置大致是：
景點名稱 -> 補充閱讀文章列表
"""

import re
from html import unescape
from urllib.parse import quote

import httpx

from app.config import ARTICLE_LIMIT, HTTP_TIMEOUT
from app.services.maps import unwrap_duckduckgo_url


async def search_related_articles(place_name: str, limit: int = ARTICLE_LIMIT) -> list[dict[str, str]]:
    """用 DuckDuckGo 搜尋「店名 + 介紹」並抓前幾篇文章。

    這個功能屬於加值資訊，所以如果搜尋失敗，直接回傳空列表，
    不會讓主流程中斷。
    """
    # 將搜尋字串進行 URL encode，避免空白或特殊字元破壞查詢網址。
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

    # DuckDuckGo HTML 版搜尋結果會把標題放在 result__a 連結裡。
    matches = re.finditer(
        r'<a[^>]+class=\"result__a\"[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>',
        response.text,
        re.S,
    )
    articles: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    for match in matches:
        # DuckDuckGo 可能回傳自己的跳轉網址，這裡先還原成原始文章網址。
        url = unwrap_duckduckgo_url(match.group(1))
        title = re.sub(r'<.*?>', '', unescape(match.group(2))).strip()
        if not title or not url or url in seen_urls:
            continue
        if not url.startswith('http'):
            continue

        seen_urls.add(url)
        articles.append({'title': title, 'url': url})

        # 只保留前幾筆，避免結果過多。
        if len(articles) >= limit:
            break

    return articles
