"""`app.schemas` 的匯出入口。

這個資料夾專門放 API 會用到的資料格式定義，
例如請求 body 的欄位結構。

有了這層匯出後，其他模組可以直接寫：
`from app.schemas import SaveRequest`

而不必寫成：
`from app.schemas.requests import SaveRequest`
"""

from .requests import SaveRequest

# `__all__` 用來明確宣告這個套件對外公開的名稱。
__all__ = ['SaveRequest']
