"""`app.utils` 的匯出入口。

這支檔案的目的不是放邏輯，而是統一整理 `utils/` 底下
希望對外提供的工具函式。

有了這層匯出後，其他模組可以直接寫：
`from app.utils import detect_region`

而不一定要寫成：
`from app.utils.region import detect_region`
"""

from .region import detect_day, detect_region, normalize_address_text, rating_to_stars

# `__all__` 用來明確宣告：
# 當外部使用 `from app.utils import *` 時，哪些名稱會被匯出。
# 也順便表達這幾個函式是目前 `utils` 模組的公開介面。
__all__ = ['detect_day', 'detect_region', 'normalize_address_text', 'rating_to_stars']
