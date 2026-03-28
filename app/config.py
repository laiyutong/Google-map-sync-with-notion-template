"""專案的共用設定中心。

這支檔案負責：
1. 載入 `.env` 環境變數。
2. 集中管理外部服務需要的金鑰與端點。
3. 定義多個模組共用的常數。
4. 建立可重複使用的 Notion client。
"""

import os

import httpx
from dotenv import load_dotenv
from notion_client import Client

# 讀取專案根目錄下的 `.env`，讓後續 `os.getenv()` / `os.environ[]` 能取得設定值。
load_dotenv()

# Google Places API 金鑰。
# 供 `app/services/places.py` 呼叫 Google Places API 時使用。
GOOGLE_KEY = os.getenv('GOOGLE_PLACES_API_KEY', '')

# Notion 的目標資料庫或頁面 ID。
# 這個值是必要設定，所以使用 `os.environ[]`；若沒設定會直接拋錯，提醒開發者補上。
DB_ID = os.environ['NOTION_DATABASE_ID']

# 當 Notion workspace 中有多個 data source 時，用這個名稱指定預設目標。
NOTION_TARGET_NAME = os.getenv('NOTION_TARGET_NAME', '行程安排')

# 所有 `httpx` 請求共用的 timeout，避免外部 API 卡太久。
HTTP_TIMEOUT = httpx.Timeout(15.0)

# Azure OpenAI 摘要服務的端點與金鑰。
# 若未設定，評論摘要流程會退回 fallback 邏輯，而不會讓整體流程中斷。
AZURE_OPENAI_ENDPOINT = os.getenv('AZURE_OPENAI_ENDPOINT', '')
AZURE_OPENAI_API_KEY = os.getenv('AZURE_OPENAI_API_KEY', '')

# 指定 Google Places API 回傳欄位，避免抓太多不必要資料。
# 這樣可以減少回應內容，也讓程式更明確知道自己需要哪些欄位。
GOOGLE_PLACES_FIELD_MASK = (
    'places.id,'
    'places.displayName,'
    'places.formattedAddress,'
    'places.location,'
    'places.rating,'
    'places.googleMapsUri,'
    'places.regularOpeningHours.weekdayDescriptions,'
    'places.reviews'
)

# 將濟州不同區域對應到行程天數。
# 供 `app/utils/region.py` 的 `detect_day()` 使用。
REGION_DAY_MAP = {
    '濟州市': 'Day1',
    '西部': 'Day2',
    '西歸浦': 'Day3',
    '牛島': 'Day5',
    '東部': 'Day4',
}

# 評論摘要固定要輸出的分類。
# 供 `app/services/reviews.py` 組 prompt、整理 fallback 與標準化輸出使用。
REVIEW_SUMMARY_CATEGORIES = [
    '餐點',
    '服務',
    '環境',
    '排隊',
    '停車',
    '價格',
    '適合族群',
    '雷點',
]

# 最多拿多少筆評論做摘要，避免請求過大或模型輸入太長。
REVIEW_LIMIT = 50

# 最多回傳幾篇相關文章。
ARTICLE_LIMIT = 5

# 建立全域共用的 Notion client。
# 其他模組可直接 `from app.config import notion` 使用，不必重複初始化。
notion = Client(auth=os.environ['NOTION_TOKEN'])
