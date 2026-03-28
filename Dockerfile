# 使用精簡版 Python 映像，縮小部署映像體積
FROM python:3.12-slim

# 設定 Python 與 pip 的執行環境
# PYTHONDONTWRITEBYTECODE: 不產生 .pyc
# PYTHONUNBUFFERED: 讓 log 即時輸出
# PIP_NO_CACHE_DIR: 安裝套件後不保留快取
# PORT: 提供部署平台預設連接埠
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

# 容器內的工作目錄
WORKDIR /app

# 先複製依賴清單，讓套件安裝層可以被快取
COPY requirements.txt .

# 安裝 Python 套件
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

# 複製專案原始碼到容器
COPY . .

# 建立非 root 使用者，避免應用程式用 root 身分執行
RUN useradd --create-home --shell /bin/bash appuser \
    && chown -R appuser:appuser /app

# 切換成一般使用者執行應用程式
USER appuser

# 宣告容器對外提供的連接埠
EXPOSE 8080

# 啟動 FastAPI 服務，並使用平台提供的 PORT
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
