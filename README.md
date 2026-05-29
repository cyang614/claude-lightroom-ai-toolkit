# Claude Lightroom AI Toolkit

一個基於 Python Streamlit 的照片分析、Lightroom XMP 預設集、作品集評分與 Notion 匯出工具。

## 功能

- 上傳 `jpg`、`jpeg`、`png`、`cr3` 圖片。
- JPG/PNG/CR3 送出前會自動壓縮到 Claude 圖片限制以下，避免常見相機 JPG 超過 5MB 的錯誤。
- 後端將圖片轉成 Base64，呼叫 Anthropic Claude Messages API。
- 使用 Claude Tool Use / Function Calling，透過 `tool_choice` 強制回傳結構化 JSON；若模型/帳號不支援強制 tool choice，會自動 fallback 到 `auto` 並要求 Claude 必須呼叫 tool。
- 產生 Lightroom `.xmp` preset。
- 批次下載：
  - 全部 XMP ZIP
  - 作品集 CSV
  - 壓縮後照片 ZIP
- 可選匯出到 Notion，並可自動把原始大小照片上傳到 Google Drive，讓 Notion 頁面以 Google 公開連結預覽圖片。
- 預設 Claude model：`claude-sonnet-4-6`。
- 全新 Ubuntu 建置手冊：[`UBUNTU_SETUP_GUIDE.md`](UBUNTU_SETUP_GUIDE.md)。

## 三種工作模式

### A. 批次 Lightroom Preset 產生器

一次上傳多張照片，針對每張照片產生一個 `.xmp`，最後可下載整包 ZIP。

### B. 風格一致化工具

輸入參考風格描述，例如：

```text
暖色調、柔和對比、保留高光細節、陰影略微抬起、底片感但不過度褪色。
```

App 會把這段風格需求加入 Claude prompt，讓每張照片產生朝同一方向靠攏的 Lightroom 參數。

### C. 攝影作品集評分器

批次分析後產生作品集總表與 CSV，包含：

- 檔名
- 綜合評分
- 構圖分析
- 光影分析
- 色彩分析
- Lightroom 參數
- 原始大小與送 Claude 前壓縮後大小

## Notion 匯出

側邊欄可勾選「分析後匯出到 Notion」，需要：

1. Notion Integration Token
2. Notion Database ID
3. 將該 Notion database 分享給你的 integration

建議 Notion database 至少建立這些欄位：

| 欄位 | 型別 |
|---|---|
| Name | Title |
| Score | Number |
| Type | Select |
| Composition Analysis | Text / Rich text |
| Lighting Analysis | Text / Rich text |
| Color Analysis | Text / Rich text |
| Image URL | URL |

App 會建立一頁 Notion page，內容包含：

- Google Drive 原始照片預覽圖（若啟用 Drive 上傳）
- 構圖/光影/色彩分析，也會同步寫入 database properties
- Lightroom 參數 JSON
- 本機壓縮圖路徑
- 本機 XMP 路徑

### Google Drive 原始照片預覽設定

若希望 Notion 頁面能直接預覽原始大小圖片，請在側邊欄勾選「匯出 Notion 前，上傳原始照片到 Google Drive 供預覽」。

建議使用 **OAuth authorized_user token**，讓程式用你的個人 Google Drive quota 上傳。Service Account 上傳到一般 My Drive 會遇到 `Service Accounts do not have storage quota`；Service Account 只建議搭配 Google Shared Drive 使用。

#### 建議方式：OAuth user token

1. 到 Google Cloud Console 建立 OAuth 2.0 Client ID，Application type 選 `Desktop app`。
2. 啟用 Google Drive API。
3. 下載 OAuth client secret JSON。
4. 執行：

```bash
python3 create_google_drive_oauth_token.py /path/to/oauth_desktop_client_secret.json
```

5. 完成瀏覽器授權後，會產生：

```text
google_drive_oauth_token.json
```

6. Streamlit 側邊欄 `Google credentials JSON 路徑` 預設就是：

```text
~/workspace/claude-lightroom-ai-toolkit/google_drive_oauth_token.json
```

#### 可選方式：Service Account + Shared Drive

若你一定要使用 Service Account：

1. 目標資料夾需位於 Google Shared Drive，不是個人 My Drive。
2. 將 Shared Drive 或資料夾分享給 service account email。
3. 側邊欄填入 service account JSON 路徑。

App 上傳後會自動把檔案設為「知道連結即可讀取」，並把 `https://drive.google.com/uc?export=view&id=...` 寫入 Notion 的 `Image URL` 欄位與 image block。

也可以用環境變數指定 credentials JSON：

```bash
export GOOGLE_SERVICE_ACCOUNT_FILE="/path/to/google_credentials.json"
```

## 安裝

```bash
cd /home/rickyyang/workspace/claude-lightroom-ai-toolkit
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> CR3 支援依賴 `rawpy`。若你的系統無法安裝 rawpy，JPG/PNG 仍可正常使用。

## 設定 API Key / Notion Token

建議使用專案根目錄的 `.env` 檔案保存本機開發用設定。`.env` 已加入 `.gitignore`，不要提交到版本控制。

```bash
cp .env.example .env
```

然後編輯 `.env`：

```dotenv
ANTHROPIC_API_KEY=你的 Anthropic API Key
NOTION_TOKEN=你的 Notion Integration Token
NOTION_DATABASE_ID=你的 Notion Database ID
```

App 啟動時會自動讀取 `.env`，並預填 Streamlit 側邊欄的 Claude / Notion 欄位。

其他可選方式：

1. 直接設定環境變數：

```bash
export ANTHROPIC_API_KEY="你的 Anthropic API Key"
export NOTION_TOKEN="你的 Notion token"
export NOTION_DATABASE_ID="你的 database id"
```

2. 在 Streamlit 側邊欄手動輸入。

3. Streamlit secrets：

```toml
# .streamlit/secrets.toml
ANTHROPIC_API_KEY = "你的 Anthropic API Key"
```

## 執行

```bash
streamlit run app.py
```

## 測試核心邏輯

```bash
python3 -m unittest tests.test_photo_analyzer -v
```

## 檔案說明

- `app.py`：Streamlit 前端、批次處理、下載與 Notion 匯出流程。
- `photo_analyzer.py`：Claude Tool Use payload、圖片壓縮、回應解析、XMP/CSV/ZIP/Notion payload 產生。
- `tests/test_photo_analyzer.py`：核心邏輯測試。
- `requirements.txt`：Python 相依套件。
- `outputs/`：執行後自動產生，保存壓縮圖片、XMP 與 JSON 結果。
