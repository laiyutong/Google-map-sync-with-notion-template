"""API 請求資料格式定義。

這支檔案主要用來描述 FastAPI 接收到的 request body 應該長什麼樣子。
目前專案的核心輸入很單純：只需要一個 Google Maps 分享網址。
"""

from pydantic import BaseModel


class SaveRequest(BaseModel):
    """寫入或預覽流程的請求格式。

    目前只需要提供：
    - `url`: 使用者貼上的 Google Maps 分享連結

    FastAPI 會搭配 Pydantic 自動驗證這個欄位是否存在，
    並將傳入的 JSON 轉成 Python 物件。
    """

    # 使用者傳入的 Google Maps 分享網址。
    url: str
