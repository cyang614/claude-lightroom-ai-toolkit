# Notion 新手版面設計：AI 攝影分析工作台

這份文件假設你是 Notion 新手，目標是建立一個清楚、好用、可讓 Streamlit app 匯入分析結果的攝影工作台。

---

## 你最後會得到什麼？

建議建立一個 Notion 頁面，名稱叫：

```text
📷 AI 攝影分析工作台
```

裡面放三個區塊：

1. **快速入口**：放操作說明與常用連結。
2. **照片分析資料庫**：Streamlit app 匯入的主要資料庫。
3. **常用風格 Preset 筆記**：記錄你喜歡的風格描述，給 B 模式使用。

---

## 第 1 步：建立主頁面

在 Notion 左側按 **New page**，頁面名稱填：

```text
📷 AI 攝影分析工作台
```

頁面內容可以先貼上這段：

```markdown
# 📷 AI 攝影分析工作台

## 今日工作流
1. 將照片匯入 Streamlit app
2. 選擇批次 Preset / 風格一致化 / 作品集評分
3. 下載 XMP ZIP、CSV、壓縮圖 ZIP
4. 將分析結果匯入本頁資料庫
5. 在 Lightroom 匯入 XMP 預設集

## 常用風格描述
- 暖色底片感：暖色調、柔和對比、保留高光細節、陰影略微抬起、底片感但不過度褪色。
- 街拍紀實：中高對比、保留暗部層次、色彩自然、略加清晰度與質感。
- 清新旅拍：明亮曝光、低對比、自然膚色、天空高光不爆、綠色不要過飽和。
```

---

## 第 2 步：建立「照片分析」資料庫

在主頁面輸入 `/database`，選：

```text
Table - Inline
```

資料庫名稱：

```text
照片分析
```

---

## 第 3 步：建立資料庫欄位

請照下面建立欄位。最重要的是前三個，因為目前 app 匯出 Notion 時會使用它們：

| 欄位名稱 | Notion 型別 | 用途 | 必要 |
|---|---|---|---|
| Name | Title | 照片檔名 | 必要 |
| Score | Number | Claude 給的綜合評分 | 必要 |
| Type | Select | 固定為 Photo Analysis | 必要 |
| Shoot Date | Date | 拍攝日期，可手動補 | 建議 |
| Status | Select | 篩選狀態 | 建議 |
| Genre | Multi-select | 類型，如街拍/旅拍/人像 | 建議 |
| Mood | Multi-select | 氛圍，如暖色/冷色/底片感 | 建議 |
| Lightroom Ready | Checkbox | 是否已匯入 Lightroom | 建議 |
| XMP Path | Text | 本機 XMP 路徑 | 可選 |
| Compressed Image Path | Text | 本機壓縮圖路徑 | 可選 |
| Notes | Text | 你的人工備註 | 可選 |

### Status 建議選項

建立 `Status` 欄位時，建議加這些選項：

- Inbox
- Keep
- Edit
- Publish
- Reject

### Genre 建議選項

- Street
- Travel
- Portrait
- Food
- Landscape
- Family
- Product

### Mood 建議選項

- Warm
- Cool
- Film Look
- High Contrast
- Soft
- Clean
- Moody

---

## 第 4 步：設計幾個好用視圖

Notion database 上方可以新增不同 View。建議建立：

### 1. All Photos

型態：Table

用途：看全部資料。

排序：

```text
Score descending
```

也就是高分照片排前面。

---

### 2. Best Shots

型態：Gallery 或 Table

Filter：

```text
Score >= 85
```

用途：快速看到值得保留或發表的照片。

---

### 3. To Edit

型態：Table

Filter：

```text
Status is Edit
```

用途：整理還需要進 Lightroom 修的照片。

---

### 4. Ready to Publish

型態：Gallery 或 Board

Filter：

```text
Status is Publish
```

用途：整理準備發 IG、部落格、作品集的照片。

---

### 5. Rejected / Low Score

型態：Table

Filter：

```text
Score < 65
```

用途：檢討構圖、光影、色彩問題。

---

## 第 5 步：設定 Notion Integration

如果你只是手動看資料，可以先跳過這步。

如果要讓 Streamlit app 自動匯出到 Notion：

1. 打開：<https://www.notion.so/my-integrations>
2. 按 **New integration**
3. 名稱可填：

```text
Claude Lightroom Toolkit
```

4. 複製 Integration Token。
5. 回到你的 Notion「照片分析」資料庫。
6. 右上角按 `...`
7. 找到 **Connections** 或 **Connect to**
8. 選擇剛剛建立的 integration。

---

## 第 6 步：找到 Database ID

打開「照片分析」資料庫頁面，網址通常長得像：

```text
https://www.notion.so/你的workspace/xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx?v=yyyyyyyy
```

`xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` 那一長串就是 Database ID。

可以直接複製整串 32 個字元，或含 dash 的 UUID 都可以。

在 Streamlit 側邊欄填：

- Notion Integration Token
- Notion Database ID

---

## 目前 app 匯入 Notion 的限制

目前 Notion API 匯出會建立 page，內容包含：

- 檔名
- Score
- Type
- 構圖分析
- 光影分析
- 色彩分析
- Lightroom 參數 JSON
- 本機壓縮圖路徑
- 本機 XMP 路徑

但有一個重要限制：

> Notion API 不能直接把你電腦裡的圖片或 XMP 檔案當附件上傳。

所以目前匯入的是：

```text
outputs/xxx_compressed.jpg
outputs/xxx.xmp
```

這些是本機路徑，不是可公開瀏覽的網址。

如果你希望 Notion 裡直接看到圖片縮圖或下載 XMP，下一步需要增加雲端檔案上傳，例如：

- Google Drive
- Cloudinary
- S3
- Dropbox

---

## 建議的日常使用方式

### 篩片流程

1. 用 app 批次分析照片。
2. 匯出到 Notion。
3. 在 `All Photos` 按 Score 排序。
4. 85 分以上標成 `Keep` 或 `Publish`。
5. 65 分以下標成 `Reject`。
6. 中間分數標成 `Edit`，回 Lightroom 微調。

### 風格一致化流程

1. 先在 Notion 的「常用風格描述」挑一段風格。
2. 貼到 app 的 B 模式。
3. 批次上傳同一場拍攝的照片。
4. 下載 XMP ZIP。
5. 匯入 Lightroom 後套用到整組照片。
6. 回 Notion 標記哪些照片最終可發表。

---

## 最小可用版欄位

如果你不想一次設定太多，最小只要這三欄：

| 欄位名稱 | 型別 |
|---|---|
| Name | Title |
| Score | Number |
| Type | Select |

這樣 app 就能先匯入。其他欄位以後再慢慢加。
