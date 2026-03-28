# maps-to-notion

將 Google Maps 分享連結轉成可寫入 Notion 的景點資料。

此專案提供一個 `FastAPI` API，會自動完成以下流程：

1. 展開 Google Maps 短網址。
2. 解析景點名稱與座標。
3. 優先用 Google Places API 補齊地址、評分、照片與評論。
4. 必要時用 OpenStreetMap 反查地址。
5. 使用 Google Places API reviews 作為評論來源。
6. 使用 Azure OpenAI 將評論整理成繁體中文摘要。
7. 搜尋相關介紹文章。
8. 將資料寫入 Notion Database 或指定頁面底下。

## 功能特色

- 支援 `Google Maps` 分享網址解析
- 支援 `Notion` Database 與 Page 兩種寫入模式
- 自動依地址判斷區域與行程天數
- 支援 Google Places 圖片與評分同步
- 支援 Places API 評論摘要整理
- 支援文章搜尋補充參考資料
- 提供預覽 API，寫入前可先檢查資料

## Tech Stack

- Python 3.10+
- FastAPI
- Uvicorn
- httpx
- notion-client
- python-dotenv

## 專案結構

```text
.
├── .dockerignore
├── main.py
├── Dockerfile
├── requirements.txt
├── Procfile
├── .env
└── .env.example
```

## 環境變數

請先複製範例檔：

```bash
cp .env.example .env
```

必要變數：

- `GOOGLE_PLACES_API_KEY`: Google Places API 金鑰
- `NOTION_TOKEN`: Notion Integration Token
- `NOTION_DATABASE_ID`: Notion Database ID 或 Page ID

可選變數：

- `NOTION_TARGET_NAME`: 指定要寫入的 Notion data source 名稱，預設為 `行程安排`
- `PORT`: 本機或部署時使用的連接埠，預設 `3000`
- `AZURE_OPENAI_ENDPOINT`: Azure OpenAI Chat Completions Endpoint
- `AZURE_OPENAI_API_KEY`: Azure OpenAI API Key

## 本機啟動

建議先建立虛擬環境：

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 3000
```

啟動後可用以下網址確認服務狀態：

```bash
curl http://127.0.0.1:3000/
```

預期回傳：

```json
{"status":"ok"}
```

## API

### `GET /`

健康檢查。

### `POST /save-preview`

先解析 Google Maps 連結並回傳預覽資料，不寫入 Notion。

請求範例：

```bash
curl -X POST http://127.0.0.1:3000/save-preview \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://maps.app.goo.gl/your-share-link"
  }'
```

### `POST /save`

解析 Google Maps 連結後，直接寫入 Notion。

請求範例：

```bash
curl -X POST http://127.0.0.1:3000/save \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://maps.app.goo.gl/your-share-link"
  }'
```

### `GET /save-preview-link`

讓手機、捷徑或瀏覽器可直接用 query string 呼叫預覽 API。

請求範例：

```bash
curl "http://127.0.0.1:3000/save-preview-link?url=https%3A%2F%2Fmaps.app.goo.gl%2Fyour-share-link"
```

### `GET /save-link`

讓手機、捷徑或瀏覽器可直接用 query string 觸發寫入 Notion。

請求範例：

```bash
curl "http://127.0.0.1:3000/save-link?url=https%3A%2F%2Fmaps.app.goo.gl%2Fyour-share-link"
```

## Notion 寫入邏輯

程式會優先搜尋可存取的 Notion data source：

- 若找到名稱符合 `NOTION_TARGET_NAME` 的 data source，會直接寫入對應資料庫
- 若找不到但只找到一個可用 data source，會自動使用
- 若 `NOTION_DATABASE_ID` 是 Database，會建立資料列
- 若 `NOTION_DATABASE_ID` 是 Page，會改為建立子頁

目前會嘗試對應下列欄位：

- `Name` 或資料庫中的 title 欄位
- `分類`
- `日程`
- `Google Map`
- `評分`
- `經度(lng)`
- `緯度(lat)`

## Railway 部署

專案已包含 `Dockerfile`，Railway 可直接使用容器部署。

### 1. 建立專案

將此專案推到 GitHub 後，到 Railway 建立新專案並選擇該 repository：

`https://railway.com/new`

### 2. 設定環境變數

在 Railway 專案的 Variables 設定以下值：

- `GOOGLE_PLACES_API_KEY`
- `NOTION_TOKEN`
- `NOTION_DATABASE_ID`
- `NOTION_TARGET_NAME`
- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_API_KEY`

### 3. 部署

Railway 偵測到 `Dockerfile` 後會自動建置並啟動服務。

部署成功後，可先用以下網址確認：

```bash
curl https://your-app.up.railway.app/
```

預期回傳：

```json
{"status":"ok"}
```

## 手機與電腦使用方式

### 電腦

桌面端呼叫方式不變，只要把 `http://127.0.0.1:3000` 改成 Railway 提供的網域：

```bash
curl -X POST https://your-app.up.railway.app/save \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://maps.app.goo.gl/your-share-link"
  }'
```

### iPhone

最方便的方式是用「捷徑」接 Google Maps 分享連結，將收到的網址做 URL Encode 後，直接打開。

後端會自動清理 iPhone Google Maps 分享常見的追蹤參數，例如 `?g_st=ic`，也會兼容部分不是 `/maps/place/...` 的 Google Maps 展開格式，因此捷徑不需要額外做字串替換：

```text
https://your-app.up.railway.app/save-link?url=<URL_ENCODED_GOOGLE_MAPS_LINK>
```

例如：

```text
https://your-app.up.railway.app/save-link?url=https%3A%2F%2Fmaps.app.goo.gl%2Fabc123
```

如果你只想先看預覽，不直接寫入 Notion，可改用：

```text
https://your-app.up.railway.app/save-preview-link?url=https%3A%2F%2Fmaps.app.goo.gl%2Fabc123
```

#### iPhone 快捷指令設定

如果你希望送出後留在捷徑內顯示結果、不跳到 Safari，可依下列流程設定：

1. 新增捷徑，點畫面下面中間 `i`，開啟「在分享表單中顯示」。
2. 在「接受的內容」只勾選 `URL`。
3. 加入 `從輸入取得 URL` 動作，接收 Google Maps 分享連結。
4. 加入 `URL 編碼` 動作，輸入選上一步的 `URL`。
5. 可選：加入 `取代文字` 動作，使用常規表示式將 `\?g_st=.*` 取代成空字串。
6. 加入 `文字` 動作，內容填入：

```text
https://your-app.up.railway.app/save-link?url=[更新的文字]
```

如果你不需要第 5 步，這裡也可以直接接 `[URL 編碼文字]`。

7. 加入 `取得網址內容` 動作，輸入選上一步的 `文字`。
8. 加入 `顯示` 或 `快速查看` 動作，顯示上一步回傳內容。

完成後，在 Google Maps 按 `分享`，選擇這個捷徑即可直接看到 API 回傳結果。

如果你想先檢查資料、不直接寫入 Notion，將第 6 步改成：

```text
https://your-app.up.railway.app/save-preview-link?url=[更新的文字]
```

![Railway 部署成功畫面](./images/railway-deploy-success.png)

## 疑難排解

### 如何快速判斷 Places API 是否正常

先看 `/save-preview` 回傳裡的 `data_source`：

- `google_places_new`
  - 代表 Google Places API 有成功查到地點資料。
  - 如果這時 `review_count = 0`，問題通常比較偏向 `reviews` 欄位沒有回來，或本機與 Railway 的 Places API 回應內容不同。

- `fallback_reverse_geocode`
  - 代表 Google Places API 沒有成功查到完整地點資料，程式已退回座標反查地址。
  - 這種情況通常優先檢查 Railway 的 `GOOGLE_PLACES_API_KEY`、Places API 是否已啟用、Billing 是否正常，以及是否有 API restrictions 導致雲端環境無法使用。

### 如何快速判斷評論摘要卡在哪一層

再看 `review_source` 與 `review_error`：

- `google_places_reviews/azure_openai`
  - 代表 Places API reviews 與 Azure OpenAI 摘要都成功。

- `google_places_reviews/azure_fallback_http_error`
  - 代表 Places API reviews 有拿到，但 Railway 呼叫 Azure OpenAI 時發生 HTTP 錯誤。
  - 請檢查 `AZURE_OPENAI_ENDPOINT`、`AZURE_OPENAI_API_KEY`、deployment 名稱與 `api-version` 是否正確。

- `no_reviews_available`
  - 代表 Places API 沒有提供可用評論，因此沒有內容可送進 Azure OpenAI。

## 注意事項

- 評論摘要目前固定使用 Google Places API reviews，不再依賴瀏覽器自動化
- 若未設定 Azure OpenAI，系統會退回基本評論摘要
- `NOTION_TOKEN` 對應的 integration 必須已分享到目標 Notion 頁面或資料庫

## 後續建議

- 補上 `pytest` 測試，覆蓋 URL 解析、區域判斷與 Notion payload 組裝
- 將 `main.py` 拆分成 `services`、`clients`、`schemas`，降低單檔維護成本
