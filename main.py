"""專案最外層的 ASGI 啟動入口。

讓 `uvicorn main:app`、`Procfile` 或部署平台可以直接載入 `app`。
真正的 FastAPI 路由與主要邏輯都放在 `app/main.py`。
"""

from app.main import app
