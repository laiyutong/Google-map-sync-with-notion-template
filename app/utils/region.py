import re

from app.config import REGION_DAY_MAP


def normalize_address_text(address: str) -> str:
    """將地址正規化，方便比對英文與中韓文地名。"""
    lowered = address.lower()
    return re.sub(r'[\s,./()-]+', '', lowered)


def detect_region(address: str) -> str:
    """依地址判斷濟州島區域。"""
    if not address:
        return '濟州市'

    east = [
        '성산읍', '표선면', '남원읍', '구좌읍', '조천읍',
        '성산', '표선', '남원', '城山', '表善', '南元', '旧左', '朝天',
        'seongsaneup', 'seongsan', 'pyoseonmyeon', 'pyoseon',
        'namwoneup', 'namwon', 'gujwaeup', 'gujwa', 'jocheoneup', 'jocheon',
    ]
    west = [
        '한림읍', '한경면', '애월읍', '대정읍', '안덕면',
        '한림', '한경', '애월', '대정', '안덕', '翰林', '翰京', '涯月', '大静', '安德',
        'hallimeup', 'hallim', 'hangyeongmyeon', 'hangyeong',
        'aewoleup', 'aewol', 'daejeongeup', 'daejeong', 'andeokmyeon', 'andeok',
    ]
    normalized_address = normalize_address_text(address)

    for kw in east:
        if normalize_address_text(kw) in normalized_address:
            return '東部'
    for kw in west:
        if normalize_address_text(kw) in normalized_address:
            return '西部'
    if (
        '서귀포시' in address
        or '서귀포' in address
        or '西歸浦' in address
        or '西归浦' in address
        or 'seogwiposi' in normalized_address
        or 'seogwipo' in normalized_address
    ):
        return '西歸浦'
    if (
        '제주시' in address
        or '濟州市' in address
        or '济州市' in address
        or 'jejusi' in normalized_address
        or 'jejucity' in normalized_address
    ):
        return '濟州市'

    return '濟州市'


def detect_day(region: str) -> str:
    """依景點區域對應到 Notion 行程欄位。"""
    return REGION_DAY_MAP.get(region, 'Day1')


def rating_to_stars(rating: float | None) -> str | None:
    """將 Google 評分轉成 Notion 的星等選項。"""
    if rating is None:
        return None

    if rating >= 4.5:
        return '⭐⭐⭐⭐⭐'
    if rating >= 3.5:
        return '⭐⭐⭐⭐'
    if rating >= 2.5:
        return '⭐⭐⭐'
    if rating >= 1.5:
        return '⭐⭐'
    return '⭐'
