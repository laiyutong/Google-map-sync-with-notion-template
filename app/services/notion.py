"""處理 Notion 寫入的 service。

這支檔案主要負責：
1. 將景點資料轉成 Notion properties 與 block children。
2. 尋找可寫入的目標 data source / database。
3. 依不同欄位型別組裝 Notion API payload。
4. 建立資料列或子頁，並翻譯常見錯誤訊息。

它在整個流程中的位置大致是：
整理好的景點資料 -> Notion 頁面 / database row
"""

from typing import Any

from fastapi import HTTPException
from notion_client.errors import APIResponseError

from app.config import DB_ID, NOTION_TARGET_NAME, notion
from app.utils.region import detect_day, detect_region, rating_to_stars


def build_notion_children(
    place: dict[str, Any],
    source_url: str,
    region: str,
    review_summary: dict[str, Any] | None = None,
    related_articles: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """建立頁面內容區塊，供 database/page 兩種父層共用。

    這裡組出的是 Notion page 內文 blocks，
    包括評分、營業時間、評論摘要、相關文章與分類重點。
    """
    rating = place.get('rating')
    rating_text = f'{rating:.1f}' if isinstance(rating, int | float) else '未提供'
    hours_lines = place.get('opening_hours', {}).get('weekday_text', [])

    # 先放最基本的評分資訊。
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

    # 營業時間有資料時，逐行拆成條列。
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

    # 若有評論摘要，就補上摘要段落。
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

    # 若有補充文章，將標題做成可點擊連結。
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

    # 再將評論分類重點逐項列出，方便在 Notion 內快速掃讀。
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
    """在可存取的 Notion data source 中找出目標資料庫與欄位定義。

    尋找規則如下：
    1. 優先找名稱完全等於 `NOTION_TARGET_NAME` 的 data source。
    2. 若只有一個可用 data source，就直接使用它。
    3. 否則退回第一個找到的候選項。
    """
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
    """依照實際資料庫欄位型別建立 Notion properties。

    Notion 不同資料庫的欄位型別可能不同，
    所以這裡會根據實際 schema 決定要填 `title`、`select`、
    `multi_select`、`url` 或 `number` 等格式。
    """
    schema = schema or {}
    title_property_name = 'Name'

    # 找出真正的 title 欄位名稱，不強制一定叫做 Name。
    for property_name, metadata in schema.items():
        if metadata.get('type') == 'title':
            title_property_name = property_name
            break

    properties: dict[str, Any] = {
        title_property_name: {
            'title': [{'text': {'content': place.get('name', '未知店名')}}]
        }
    }

    def assign_coordinate_property(property_name: str, value: float | None) -> None:
        """依欄位型別寫入經緯度。"""
        if value is None:
            return

        property_schema = schema.get(property_name)
        if not property_schema:
            return

        property_type = property_schema.get('type')
        if property_type == 'number':
            properties[property_name] = {'number': value}
        elif property_type == 'rich_text':
            properties[property_name] = {'rich_text': [{'text': {'content': str(value)}}]}
        elif property_type == 'title':
            properties[property_name] = {'title': [{'text': {'content': str(value)}}]}

    # 將區域轉成對應行程天數，例如 Day1~Day4。
    day_name = detect_day(region)

    # 依資料庫欄位型別填入「分類」。
    category_schema = schema.get('分類')
    if category_schema:
        category_type = category_schema.get('type')
        if category_type == 'multi_select':
            properties['分類'] = {'multi_select': [{'name': region}]}
        elif category_type == 'select':
            properties['分類'] = {'select': {'name': region}}

    # 依資料庫欄位型別填入「日程」。
    schedule_schema = schema.get('日程')
    if schedule_schema:
        schedule_type = schedule_schema.get('type')
        if schedule_type == 'multi_select':
            properties['日程'] = {'multi_select': [{'name': day_name}]}
        elif schedule_type == 'select':
            properties['日程'] = {'select': {'name': day_name}}

    # 若資料庫有 Google Map 欄位，填入原始或補充後的 Google Maps 連結。
    google_map_url = place.get('google_maps_url') or source_url
    google_map_schema = schema.get('Google Map')
    if google_map_url and google_map_schema and google_map_schema.get('type') == 'url':
        properties['Google Map'] = {'url': google_map_url}

    # 將數字評分轉成星等文字，再寫進 Notion。
    rating_text = rating_to_stars(place.get('rating'))
    rating_schema = schema.get('評分')
    if rating_text and rating_schema:
        rating_type = rating_schema.get('type')
        if rating_type == 'multi_select':
            properties['評分'] = {'multi_select': [{'name': rating_text}]}
        elif rating_type == 'select':
            properties['評分'] = {'select': {'name': rating_text}}

    assign_coordinate_property('經度(lng)', place.get('longitude'))
    assign_coordinate_property('緯度(lat)', place.get('latitude'))

    return properties


def create_notion_page(
    place: dict[str, Any],
    source_url: str,
    review_summary: dict[str, Any] | None = None,
    related_articles: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """建立 Notion 頁面。

    寫入順序如下：
    1. 先建立共用的 page 內容 blocks。
    2. 嘗試找到指定的 data source / database。
    3. 若找到資料庫，建立 database row。
    4. 若 `DB_ID` 其實是 page，則退回建立子頁模式。
    """
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

    # 若找不到特定 data source，就直接用 `.env` 的 `DB_ID` 嘗試寫入。
    properties = build_database_properties(place, source_url, region)

    try:
        payload = {
            'parent': {'database_id': DB_ID},
            'properties': properties,
            'children': children,
        }

        return notion.pages.create(**payload)
    except APIResponseError as exc:
        # 如果 `DB_ID` 其實是 page 而不是 database，就改用子頁模式。
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
    """將 Notion API 錯誤轉成使用者看得懂的訊息。

    這樣 API 呼叫端就不需要直接面對 Notion SDK 原始錯誤文字。
    """
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
