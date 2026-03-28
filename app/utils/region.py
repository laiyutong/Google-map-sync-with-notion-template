"""與濟州地區判斷有關的工具函式。

這支檔案主要處理 3 件事：
1. 將地址文字正規化，方便做字串比對。
2. 根據地址推測景點位於濟州哪個區域。
3. 將區域或評分轉成更適合 Notion 使用的格式。
"""

import re

from app.config import REGION_DAY_MAP


def normalize_address_text(address: str) -> str:
    """將地址正規化，方便比對英文與中韓文地名。

    例如把空白、逗號、括號、斜線等符號移除，
    讓 `Seogwipo-si`、`Seogwipo si`、`Seogwipo/si`
    這類寫法更容易被統一比對。
    """
    lowered = address.lower()
    return re.sub(r'[\s,./()-]+', '', lowered)


def detect_region(address: str) -> str:
    """依地址判斷濟州島區域。

    判斷順序大致如下：
    1. 先看是否命中牛島關鍵字。
    2. 再看是否命中東部關鍵字。
    3. 再看是否命中西部關鍵字。
    4. 再看是否屬於西歸浦。
    5. 最後判斷是否屬於濟州市。

    如果完全判斷不出來，預設回傳 `濟州市`，
    避免主流程因為地區未知而中斷。
    """
    # 沒地址時直接給預設值，讓後續流程仍可繼續。
    if not address:
        return '濟州市'

    # 牛島常見行政區、道路名與多語系關鍵字。
    # 需要優先判斷，避免先被較大的東部分類吃掉。
    udo = [
        '우도면', '우도', '牛島', '牛岛',
        'udomyeon', 'udobonggil', 'udori', 'udodo', 'udo',
    ]

    # 東部常見行政區與多語系關鍵字。
    # 這裡同時收韓文、中文與英文拼音，提升匹配成功率。
    east = [
        '성산읍', '표선면', '남원읍', '구좌읍', '조천읍',
        '성산', '표선', '남원', '城山', '表善', '南元', '旧左', '朝天', '朝天邑', '조함해안로',
        'seongsaneup', 'seongsan', 'pyoseonmyeon', 'pyoseon',
        'namwoneup', 'namwon', 'gujwaeup', 'gujwa',
        'jocheoneup', 'jocheon-eup', 'jocheon', 'johamhaeanro', 'johamhaean-ro',
    ]

    # 西部常見行政區與多語系關鍵字。
    west = [
        '한림읍', '한경면', '애월읍', '대정읍', '안덕면',
        '한림', '한경', '애월', '대정', '안덕', '翰林', '翰京', '涯月', '大静', '安德',
        'hallimeup', 'hallim', 'hangyeongmyeon', 'hangyeong',
        'aewoleup', 'aewol', 'daejeongeup', 'daejeong', 'andeokmyeon', 'andeok',
    ]

    # 先把整段地址標準化，後面所有比對都盡量用同一格式進行。
    normalized_address = normalize_address_text(address)

    # 先判斷牛島。像 `Udo-myeon`、`Udobong-gil` 都應獨立歸為牛島。
    for kw in udo:
        if normalize_address_text(kw) in normalized_address:
            return '牛島'

    # 先判斷東部。只要命中任何一個關鍵字，就直接回傳。
    for kw in east:
        if normalize_address_text(kw) in normalized_address:
            return '東部'

    # 再判斷西部。
    for kw in west:
        if normalize_address_text(kw) in normalized_address:
            return '西部'

    # 接著判斷是否屬於西歸浦市。
    # 這裡部分比對直接使用原始地址，部分使用正規化後的地址。
    if (
        '서귀포시' in address
        or '서귀포' in address
        or '西歸浦' in address
        or '西归浦' in address
        or 'seogwiposi' in normalized_address
        or 'seogwipo' in normalized_address
    ):
        return '西歸浦'

    # 最後檢查是否明確屬於濟州市。
    if (
        '제주시' in address
        or '濟州市' in address
        or '济州市' in address
        or 'jejusi' in normalized_address
        or 'jejucity' in normalized_address
    ):
        return '濟州市'

    # 如果都沒命中，仍回傳預設值，避免主流程沒有區域可用。
    return '濟州市'


def detect_day(region: str) -> str:
    """依景點區域對應到 Notion 行程欄位。

    實際對應規則放在 `app.config.REGION_DAY_MAP`，
    這裡只負責查表並提供預設值。
    """
    return REGION_DAY_MAP.get(region, 'Day1')


def rating_to_stars(rating: float | None) -> str | None:
    """將 Google 評分轉成 Notion 的星等選項。

    例如：
    - 4.6 -> ⭐⭐⭐⭐⭐
    - 3.8 -> ⭐⭐⭐⭐
    - 2.9 -> ⭐⭐⭐

    若沒有評分，回傳 `None`，表示不要顯示星等。
    """
    if rating is None:
        return None

    # 這裡使用區間判斷，把 5 分制數值轉成較容易閱讀的星星文字。
    if rating >= 4.5:
        return '⭐⭐⭐⭐⭐'
    if rating >= 3.5:
        return '⭐⭐⭐⭐'
    if rating >= 2.5:
        return '⭐⭐⭐'
    if rating >= 1.5:
        return '⭐⭐'
    return '⭐'
