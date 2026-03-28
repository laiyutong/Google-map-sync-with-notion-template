"""評論摘要 service。

這支檔案主要負責：
1. 清理 Google Places 回來的評論資料。
2. 呼叫 Azure OpenAI 產出繁體中文摘要。
3. 在模型失敗、未設定或沒有評論時提供 fallback。
4. 將最終摘要整理成固定格式，方便 API 與 Notion 使用。

它在整個流程中的位置大致是：
原始評論 -> 可讀的繁體中文摘要
"""

import json
import re
from typing import Any

import httpx

from app.config import (
    AZURE_OPENAI_API_KEY,
    AZURE_OPENAI_ENDPOINT,
    REVIEW_LIMIT,
    REVIEW_SUMMARY_CATEGORIES,
)


def parse_json_response(content: str) -> dict[str, Any]:
    """解析模型輸出的 JSON 內容。

    有些模型雖然被要求回傳 JSON，仍可能包在 ```json code fence 裡，
    所以這裡會先把外層 markdown 標記移除再做解析。
    """
    cleaned = content.strip()
    if cleaned.startswith('```'):
        cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
        cleaned = re.sub(r'\s*```$', '', cleaned)

    return json.loads(cleaned)


def build_empty_review_summary(message: str) -> dict[str, Any]:
    """建立沒有可用評論時的預設摘要結構。"""
    return {
        'overall_summary': [message],
        'category_highlights': {
            category: '幾乎未提及'
            for category in REVIEW_SUMMARY_CATEGORIES
        },
    }


def attach_review_metadata(
    summary: dict[str, Any],
    *,
    review_source: str,
    review_error: str | None = None,
) -> dict[str, Any]:
    """補上評論摘要來源與錯誤資訊，方便除錯。"""
    summary['review_source'] = review_source
    summary['review_error'] = review_error
    return summary


def sanitize_reviews_for_summary(reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """整理評論內容，避免模型收到空白或過長資料。

    這裡會：
    - 移除空白評論
    - 壓縮多餘空白
    - 限制單篇評論長度，避免模型輸入過長
    """
    sanitized_reviews: list[dict[str, Any]] = []
    for review in reviews:
        text = re.sub(r'\s+', ' ', str(review.get('text', '') or '')).strip()
        if not text:
            continue

        sanitized_reviews.append(
            {
                'stars': review.get('stars'),
                'text': text[:1500],
            }
        )

    return sanitized_reviews


def normalize_review_summary(summary: dict[str, Any], default_message: str) -> dict[str, Any]:
    """將模型或 fallback 產出的摘要整理成固定格式。

    不論資料來自模型還是 fallback，最終都會被整理成：
    - `overall_summary`: 最多 5 句摘要
    - `category_highlights`: 固定分類的摘要字典
    """
    overall_summary_raw = summary.get('overall_summary', [])
    if isinstance(overall_summary_raw, list):
        overall_summary = [
            re.sub(r'\s+', ' ', str(item)).strip()
            for item in overall_summary_raw
            if str(item).strip()
        ]
    elif isinstance(overall_summary_raw, str) and overall_summary_raw.strip():
        overall_summary = [re.sub(r'\s+', ' ', overall_summary_raw).strip()]
    else:
        overall_summary = []

    if not overall_summary:
        overall_summary = [default_message]

    category_highlights_raw = summary.get('category_highlights', {})
    if not isinstance(category_highlights_raw, dict):
        category_highlights_raw = {}

    category_highlights = {
        category: re.sub(
            r'\s+',
            ' ',
            str(category_highlights_raw.get(category, '幾乎未提及')),
        ).strip() or '幾乎未提及'
        for category in REVIEW_SUMMARY_CATEGORIES
    }

    return {
        'overall_summary': overall_summary[:5],
        'category_highlights': category_highlights,
    }


def extract_azure_message_content(data: dict[str, Any]) -> str:
    """兼容 Azure OpenAI 不同 content 結構。

    Azure OpenAI 的 `message.content` 可能是：
    - 單純字串
    - 多段 list 結構

    這裡的目的是把它統一抽成一段文字，再交給 JSON parser。
    """
    choices = data.get('choices')
    if not isinstance(choices, list) or not choices:
        raise KeyError('Azure OpenAI choices is missing')

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise TypeError('Azure OpenAI choice format is invalid')

    message = first_choice.get('message')
    if not isinstance(message, dict):
        raise KeyError('Azure OpenAI message is missing')

    message_content = message.get('content')
    if isinstance(message_content, str):
        return message_content

    if isinstance(message_content, list):
        text_parts: list[str] = []
        for item in message_content:
            if not isinstance(item, dict):
                continue
            if item.get('type') != 'text':
                continue

            text_value = item.get('text')
            if isinstance(text_value, str):
                text_parts.append(text_value)
            elif isinstance(text_value, dict):
                nested_text = text_value.get('value')
                if isinstance(nested_text, str):
                    text_parts.append(nested_text)
        if text_parts:
            return ''.join(text_parts)

    raise KeyError('Azure OpenAI content format is unsupported')


def build_review_summary_fallback(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    """當模型不可用時，提供最基本的評論摘要。"""
    if not reviews:
        return build_empty_review_summary('目前抓不到可分析的評論。')

    return {
        'overall_summary': ['已成功抓取評論，但 Azure OpenAI 摘要暫時不可用。'],
        'category_highlights': {
            category: '需人工補充'
            for category in REVIEW_SUMMARY_CATEGORIES
        },
    }


async def summarize_reviews_with_azure(
    place_name: str,
    reviews: list[dict[str, Any]],
) -> dict[str, Any]:
    """使用 Azure OpenAI 將評論整理成繁中摘要。

    這支函式會先處理 3 種情況：
    1. 沒有可用評論。
    2. Azure OpenAI 未設定。
    3. Azure OpenAI HTTP 或格式錯誤。

    只有在都正常時，才會真正採用模型輸出的摘要。
    """
    sanitized_reviews = sanitize_reviews_for_summary(reviews)
    if not sanitized_reviews:
        return build_empty_review_summary('目前抓不到可分析的評論。')

    # 若沒有設定 Azure OpenAI，就直接走 fallback，避免主流程失敗。
    if not AZURE_OPENAI_ENDPOINT or not AZURE_OPENAI_API_KEY:
        fallback_summary = build_review_summary_fallback(sanitized_reviews)
        return attach_review_metadata(
            normalize_review_summary(
                fallback_summary,
                '已成功抓取評論，但 Azure OpenAI 摘要暫時不可用。',
            ),
            review_source='azure_fallback_not_configured',
            review_error='未設定 AZURE_OPENAI_ENDPOINT 或 AZURE_OPENAI_API_KEY',
        )

    # 明確要求模型輸出 JSON，方便後續程式穩定解析。
    payload = {
        'messages': [
            {
                'role': 'system',
                'content': (
                    '你是旅遊評論分析助手。請根據提供的 Google Maps 評論，用繁體中文輸出 JSON。'
                    '請客觀整理，不要杜撰未提及內容。若資訊不足，請寫「少量提及」或「幾乎未提及」。'
                    '請務必回傳 overall_summary 與 category_highlights 兩個欄位。'
                ),
            },
            {
                'role': 'user',
                'content': json.dumps(
                    {
                        'place_name': place_name,
                        'review_count': len(sanitized_reviews),
                        'required_format': {
                            'overall_summary': ['3到5句繁中摘要'],
                            'category_highlights': {category: 'string' for category in REVIEW_SUMMARY_CATEGORIES},
                        },
                        'reviews': sanitized_reviews,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        'temperature': 0.2,
        'response_format': {'type': 'json_object'},
    }

    # 摘要模型可能較慢，因此這裡使用較長的 timeout。
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
            fallback_summary = build_review_summary_fallback(sanitized_reviews)
            return attach_review_metadata(
                normalize_review_summary(
                    fallback_summary,
                    '已成功抓取評論，但 Azure OpenAI 摘要暫時不可用。',
                ),
                review_source='azure_fallback_http_error',
                review_error=str(exc),
            )

    # 嘗試把模型輸出還原成乾淨的 JSON 物件。
    try:
        data = response.json()
        message = extract_azure_message_content(data)
        summary = parse_json_response(message)
    except (KeyError, TypeError, IndexError, ValueError, json.JSONDecodeError) as exc:
        print(f'⚠️ Azure OpenAI 回傳格式異常：{exc}')
        fallback_summary = build_review_summary_fallback(sanitized_reviews)
        return attach_review_metadata(
            normalize_review_summary(
                fallback_summary,
                '已成功抓取評論，但 Azure OpenAI 摘要暫時不可用。',
            ),
            review_source='azure_fallback_parse_error',
            review_error=str(exc),
        )
    except Exception as exc:
        print(f'⚠️ Azure OpenAI 摘要處理失敗：{exc}')
        fallback_summary = build_review_summary_fallback(sanitized_reviews)
        return attach_review_metadata(
            normalize_review_summary(
                fallback_summary,
                '已成功抓取評論，但 Azure OpenAI 摘要暫時不可用。',
            ),
            review_source='azure_fallback_unknown_error',
            review_error=str(exc),
        )

    return attach_review_metadata(
        normalize_review_summary(summary, '已成功抓取評論，但摘要內容不足。'),
        review_source='azure_openai',
    )


async def build_review_summary(place_name: str, reviews: list[dict[str, Any]]) -> dict[str, Any]:
    """使用 Places API reviews 與 Azure OpenAI 產出摘要。

    這是一般情況下的主要入口，會先限制評論數量，
    再呼叫 Azure 摘要流程，最後補上 review_count 等額外資訊。
    """
    sanitized_reviews = sanitize_reviews_for_summary(reviews)[:REVIEW_LIMIT]
    if not sanitized_reviews:
        summary = attach_review_metadata(
            build_empty_review_summary('目前抓不到可分析的評論。'),
            review_source='no_reviews_available',
            review_error='Places API 沒有可用評論',
        )
        summary['review_count'] = 0
        return summary

    summary = await summarize_reviews_with_azure(place_name, sanitized_reviews)
    summary['review_count'] = len(sanitized_reviews)
    summary['review_source'] = f'google_places_reviews/{summary.get("review_source")}'
    summary['review_error'] = None
    return summary


async def build_review_summary_from_place(
    place_name: str,
    place: dict[str, Any],
) -> dict[str, Any]:
    """固定使用 Places API reviews 整理評論摘要。

    這支是給其他模組呼叫的方便入口：
    直接傳入整個 `place` 物件，它會自己取出 `reviews` 欄位。

    如果內部流程有非預期例外，也會盡量回傳 fallback 摘要，
    避免整體 API 因評論摘要失敗而中斷。
    """
    try:
        return await build_review_summary(place_name, place.get('reviews', []))
    except Exception as exc:
        print(f'⚠️ 評論摘要非預期錯誤：{exc}')
        fallback_reviews = sanitize_reviews_for_summary(place.get('reviews', []))
        if fallback_reviews:
            summary = attach_review_metadata(
                normalize_review_summary(
                    build_review_summary_fallback(fallback_reviews),
                    '已成功抓取評論，但 Azure OpenAI 摘要暫時不可用。',
                ),
                review_source='google_places_reviews/fallback_after_error',
                review_error=str(exc),
            )
            summary['review_count'] = len(fallback_reviews)
            return summary

        summary = attach_review_metadata(
            build_empty_review_summary('目前抓不到可分析的評論。'),
            review_source='no_reviews_available',
            review_error='Places API 沒有可用評論',
        )
        summary['review_count'] = 0
        return summary
