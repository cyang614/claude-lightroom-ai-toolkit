# 全新 Ubuntu 環境建置手冊：Claude Lightroom AI Toolkit

這份手冊適合你在一台全新的 Ubuntu / WSL Ubuntu / Linux VM 上，從零開始把這個 Streamlit 網頁 app 建置到可以運行。

專案目錄名稱：

```text
claude-lightroom-ai-toolkit
```

主要功能：

- 批次照片分析
- 自動壓縮超過 Claude API 限制的圖片
- Claude Tool Use 結構化回傳
- 產生 Lightroom `.xmp` preset
- 作品集 CSV 評分表
- 壓縮後照片與 XMP ZIP 下載
- 可選 Notion 匯出

---

## 0. 你需要準備的東西

### 必要

1. 一台 Ubuntu 環境
2. Anthropic API Key
3. 專案資料夾 `claude-lightroom-ai-toolkit`

### 可選

如果你要匯出到 Notion，還需要：

1. Notion Integration Token
2. Notion Database ID
3. 已把 Notion database 分享給 integration

---

## 1. 更新 Ubuntu 套件

先更新系統套件清單：

```bash
sudo apt update
sudo apt upgrade -y
```

---

## 2. 安裝必要系統工具

```bash
sudo apt install -y \
  python3 \
  python3-venv \
  python3-pip \
  build-essential \
  libjpeg-dev \
  zlib1g-dev \
  libtiff-dev \
  libraw-dev \
  curl
```

說明：

| 套件 | 用途 |
|---|---|
| `python3` | 執行 Python app |
| `python3-venv` | 建立虛擬環境 |
| `python3-pip` | 安裝 Python 套件 |
| `build-essential` | 編譯部分 Python 套件可能需要 |
| `libjpeg-dev` / `zlib1g-dev` / `libtiff-dev` | Pillow 圖片處理依賴 |
| `libraw-dev` | RAW / CR3 相關依賴 |
| `curl` | 測試 API 或下載工具 |

---

## 3. 放置專案資料夾

如果你已經有這個資料夾，請把它放到例如：

```text
~/workspace/claude-lightroom-ai-toolkit
```

如果還沒有 `workspace`：

```bash
mkdir -p ~/workspace
```

進入專案：

```bash
cd ~/workspace/claude-lightroom-ai-toolkit
```

確認裡面至少有這些檔案：

```bash
ls
```

應該看到類似：

```text
app.py
photo_analyzer.py
requirements.txt
README.md
NOTION_LAYOUT.md
notion_database_template.json
tests/
```

---

## 4. 建立 Python 虛擬環境

在專案目錄內執行：

```bash
python3 -m venv .venv
```

啟用虛擬環境：

```bash
source .venv/bin/activate
```

啟用成功後，終端機前面通常會出現：

```text
(.venv)
```

確認 Python 位置：

```bash
which python
```

應該類似：

```text
/home/你的使用者/workspace/claude-lightroom-ai-toolkit/.venv/bin/python
```

---

## 5. 安裝 Python 套件

確認已啟用 `.venv` 後：

```bash
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
```

如果安裝 `rawpy` 失敗，但你暫時不需要 CR3，可以先移除 `requirements.txt` 裡的：

```text
rawpy>=0.19
```

然後重新執行：

```bash
pip install -r requirements.txt
```

JPG / PNG 功能仍可正常使用。

---

## 6. 設定 Anthropic API Key

### 方法 A：用環境變數，推薦開發測試用

```bash
export ANTHROPIC_API_KEY="你的 Anthropic API Key"
```

確認是否有設定：

```bash
echo $ANTHROPIC_API_KEY
```

如果有輸出一串 key，代表成功。

> 注意：這種方式只對目前這個 terminal session 有效。關掉終端機後需要重設。

---

### 方法 B：寫進 shell 設定，推薦常用環境

如果你用 bash：

```bash
nano ~/.bashrc
```

在檔案最後加入：

```bash
export ANTHROPIC_API_KEY="你的 Anthropic API Key"
```

儲存後執行：

```bash
source ~/.bashrc
```

---

### 方法 C：Streamlit secrets

建立 `.streamlit` 目錄：

```bash
mkdir -p .streamlit
```

建立 secrets 檔案：

```bash
nano .streamlit/secrets.toml
```

內容：

```toml
ANTHROPIC_API_KEY = "你的 Anthropic API Key"
```

這種方式適合 Streamlit app 使用。

---

## 7. 執行測試

先確認核心邏輯正常：

```bash
python3 -m unittest tests.test_photo_analyzer -v
```

成功時會看到類似：

```text
Ran 13 tests

OK
```

如果看到：

```text
OK (skipped=1)
```

也可以接受，通常代表目前環境某個圖片壓縮整合測試因缺少 Pillow 或測試條件而略過。

再檢查 Python 語法：

```bash
python3 -m py_compile app.py photo_analyzer.py
```

沒有輸出通常代表成功。

---

## 8. 啟動 Streamlit 網頁 app

在專案目錄內，並且已啟用 `.venv`：

```bash
streamlit run app.py
```

成功後終端機會顯示類似：

```text
Local URL: http://localhost:8501
Network URL: http://你的區網IP:8501
```

用瀏覽器打開：

```text
http://localhost:8501
```

---

## 9. 如果是在遠端 Ubuntu Server 上執行

如果你在遠端主機、雲端 VM、或沒有桌面的 Ubuntu server 上跑，可以指定監聽位址：

```bash
streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

然後用瀏覽器打開：

```text
http://你的伺服器IP:8501
```

### 防火牆開 port

如果連不上，可能需要開 8501 port：

```bash
sudo ufw allow 8501/tcp
sudo ufw status
```

> 如果你的伺服器在雲端平台，還要到雲端平台後台開 Security Group / Firewall。

---

## 10. 使用 app 的基本流程

1. 打開 Streamlit 網頁。
2. 在側邊欄確認 Claude model：

```text
claude-sonnet-4-6
```

3. 輸入 Anthropic API Key，或使用環境變數 / secrets。
4. 選擇工作模式：
   - A. 批次 Lightroom Preset 產生器
   - B. 風格一致化工具
   - C. 攝影作品集評分器
5. 上傳一張或多張照片。
6. 按「開始批次分析」。
7. 下載：
   - 全部 XMP ZIP
   - 作品集 CSV
   - 壓縮後照片 ZIP

---

## 11. Notion 新手設定

如果你是 Notion 新手，請先看專案內這份文件：

```text
NOTION_LAYOUT.md
```

它會教你：

- 建立 Notion 主頁
- 建立「照片分析」資料庫
- 設定欄位
- 建立不同視圖
- 連接 Notion Integration
- 找 Database ID

### 最小可用 Notion 欄位

你的 Notion database 至少要有：

| 欄位 | 型別 |
|---|---|
| Name | Title |
| Score | Number |
| Type | Select |

### Notion Integration 步驟簡述

1. 到 <https://www.notion.so/my-integrations>
2. 建立 New integration
3. 複製 token
4. 回到你的 Notion database
5. 右上角 `...`
6. 選 `Connect to` 或 `Connections`
7. 加入你的 integration
8. 將 token 和 database id 填到 Streamlit 側邊欄

---

## 12. 重要限制：Notion 不能直接吃本機檔案

目前 app 會把壓縮後照片與 XMP 存到：

```text
outputs/
```

例如：

```text
outputs/IMG_0001_compressed.jpg
outputs/IMG_0001.xmp
outputs/IMG_0001.json
```

匯出到 Notion 時，目前寫入的是「本機路徑」，不是公開網址。

也就是 Notion 會看到：

```text
outputs/IMG_0001_compressed.jpg
outputs/IMG_0001.xmp
```

如果你希望 Notion 直接顯示圖片或下載 XMP，需要額外接雲端儲存，例如：

- Google Drive
- Dropbox
- Cloudinary
- S3
- 自架靜態檔案服務

---

## 13. 常見問題

### Q1. `python3: command not found`

安裝 Python：

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
```

---

### Q2. `No module named streamlit`

通常是沒有啟用虛擬環境，或沒有安裝套件。

請重新執行：

```bash
cd ~/workspace/claude-lightroom-ai-toolkit
source .venv/bin/activate
pip install -r requirements.txt
```

---

### Q3. `No module named PIL`

安裝 Pillow：

```bash
source .venv/bin/activate
pip install pillow
```

或重裝 requirements：

```bash
pip install -r requirements.txt
```

---

### Q4. CR3 無法讀取

CR3 需要 `rawpy` 與系統 RAW 相關依賴。

先裝系統套件：

```bash
sudo apt install -y libraw-dev
```

再裝 Python 套件：

```bash
source .venv/bin/activate
pip install rawpy pillow
```

如果仍失敗，可以先把 CR3 轉成 JPG 再上傳。

---

### Q5. Claude API 回傳圖片太大

app 會自動壓縮 JPG / PNG / CR3 到 Claude API 限制以下。

如果仍失敗，請先手動縮圖，例如長邊 3000px 以下後再上傳。

---

### Q6. `Anthropic API 401` 或 authentication error

代表 API key 有問題。

檢查：

```bash
echo $ANTHROPIC_API_KEY
```

或重新在 Streamlit 側邊欄輸入正確 key。

---

### Q7. Notion 匯出失敗 401

通常是 token 錯誤。

請重新複製 Notion Integration Token。

---

### Q8. Notion 匯出失敗 404

通常是 database 沒有分享給 integration，或 database id 錯誤。

請確認：

1. Notion database 右上角 `...`
2. `Connections` / `Connect to`
3. 加入你的 integration
4. database id 複製正確

---

## 14. 建議的專案目錄結構

```text
claude-lightroom-ai-toolkit/
├── app.py
├── photo_analyzer.py
├── requirements.txt
├── README.md
├── UBUNTU_SETUP_GUIDE.md
├── NOTION_LAYOUT.md
├── notion_database_template.json
├── tests/
│   └── test_photo_analyzer.py
└── outputs/
    ├── xxx_compressed.jpg
    ├── xxx.xmp
    └── xxx.json
```

---

## 15. 一鍵啟動流程摘要

如果你已經安裝過系統套件，之後每次啟動只需要：

```bash
cd ~/workspace/claude-lightroom-ai-toolkit
source .venv/bin/activate
streamlit run app.py
```

如果在遠端 server：

```bash
cd ~/workspace/claude-lightroom-ai-toolkit
source .venv/bin/activate
streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```
