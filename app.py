import io
import json
import os
import zipfile
from pathlib import Path

import streamlit as st

from photo_analyzer import (
    DEFAULT_CLAUDE_MODEL,
    build_portfolio_csv,
    build_xmp_zip,
    call_claude_api,
    export_result_to_notion,
    extract_tool_json,
    generate_xmp,
    get_api_key,
    load_dotenv_file,
    prepare_image_for_claude,
    upload_file_to_notion,
)

OUTPUT_DIR = Path("outputs")
load_dotenv_file()
DEFAULT_NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
DEFAULT_NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")

st.set_page_config(
    page_title="Claude Lightroom AI Toolkit",
    page_icon="📷",
    layout="wide",
)

st.markdown(
    """
<style>
:root { color-scheme: light; }
.block-container { padding-top: 2rem; max-width: 1220px; }
.hero {
    padding: 2rem;
    border-radius: 28px;
    background: linear-gradient(135deg, #111827 0%, #273449 50%, #7c3aed 100%);
    color: white;
    box-shadow: 0 24px 60px rgba(17,24,39,.25);
    margin-bottom: 1.5rem;
}
.hero h1 { margin: 0 0 .35rem 0; font-size: 2.4rem; letter-spacing: -0.04em; }
.hero p { margin: 0; opacity: .9; font-size: 1.05rem; }
.card {
    border: 1px solid rgba(148,163,184,.28);
    background: rgba(255,255,255,.92);
    border-radius: 22px;
    padding: 1.25rem 1.35rem;
    box-shadow: 0 14px 38px rgba(15,23,42,.08);
    min-height: 150px;
    margin-bottom: 1rem;
}
.card h3 { margin: 0 0 .65rem 0; font-size: 1.1rem; color: #111827; }
.card p { color: #334155; line-height: 1.75; margin: 0; }
.score-card {
    text-align: center;
    border-radius: 26px;
    padding: 1.4rem;
    background: radial-gradient(circle at top left, #fde68a, #fb7185 45%, #7c3aed);
    color: white;
    box-shadow: 0 20px 50px rgba(124,58,237,.25);
}
.score { font-size: 3.4rem; line-height: 1; font-weight: 800; letter-spacing: -0.06em; }
.score-label { opacity: .9; margin-top: .35rem; }
.param-pill {
    display: flex;
    justify-content: space-between;
    gap: .75rem;
    padding: .7rem .9rem;
    border: 1px solid rgba(148,163,184,.28);
    border-radius: 999px;
    margin: .35rem 0;
    background: #f8fafc;
}
.param-pill span:first-child { color: #475569; }
.param-pill span:last-child { color: #111827; font-weight: 700; }
.small-note { color: #64748b; font-size: .92rem; }
</style>
""",
    unsafe_allow_html=True,
)


def render_card(title: str, body: str) -> None:
    st.markdown(
        f"""
<div class="card">
  <h3>{title}</h3>
  <p>{body}</p>
</div>
""",
        unsafe_allow_html=True,
    )


def render_params(params: dict) -> None:
    for key, value in params.items():
        st.markdown(
            f'<div class="param-pill"><span>{key}</span><span>{value}</span></div>',
            unsafe_allow_html=True,
        )


def safe_stem(filename: str) -> str:
    return Path(filename).stem.replace(" ", "_") or "photo"


def compressed_images_zip(results: list[dict]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for result in results:
            data = result.get("compressed_image_bytes")
            path = result.get("compressed_image_path")
            if data and path:
                archive.writestr(Path(path).name, data)
    return buffer.getvalue()


def analyze_uploaded_file(uploaded_file, api_key: str, model: str, style_reference: str | None) -> dict:
    original_bytes = uploaded_file.getvalue()
    prepared_bytes, media_type = prepare_image_for_claude(original_bytes, uploaded_file.name)
    raw_response = call_claude_api(
        prepared_bytes,
        media_type,
        api_key=api_key,
        model=model,
        style_reference=style_reference,
    )
    analysis = extract_tool_json(raw_response)
    xmp = generate_xmp(analysis, preset_name=f"Claude - {safe_stem(uploaded_file.name)}")

    OUTPUT_DIR.mkdir(exist_ok=True)
    stem = safe_stem(uploaded_file.name)
    compressed_path = OUTPUT_DIR / f"{stem}_compressed.jpg"
    xmp_path = OUTPUT_DIR / f"{stem}.xmp"
    json_path = OUTPUT_DIR / f"{stem}.json"
    compressed_path.write_bytes(prepared_bytes)
    xmp_path.write_text(xmp, encoding="utf-8")
    json_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "filename": uploaded_file.name,
        "analysis": analysis,
        "xmp": xmp,
        "original_image_bytes": original_bytes,
        "compressed_image_bytes": prepared_bytes,
        "compressed_media_type": media_type,
        "compressed_image_path": str(compressed_path),
        "xmp_path": str(xmp_path),
        "json_path": str(json_path),
        "original_size_mb": len(original_bytes) / 1024 / 1024,
        "compressed_size_mb": len(prepared_bytes) / 1024 / 1024,
    }


st.markdown(
    """
<div class="hero">
  <h1>📷 Claude Lightroom AI Toolkit</h1>
  <p>批次 XMP、風格一致化、作品集評分，以及可選 Notion 匯出。</p>
</div>
""",
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header("基本設定")
    model = st.text_input("Claude model", value=DEFAULT_CLAUDE_MODEL)
    api_key = st.text_input("Anthropic API Key", value=get_api_key() or "", type="password")
    mode = st.radio(
        "工作模式",
        [
            "A. 批次 Lightroom Preset 產生器",
            "B. 風格一致化工具",
            "C. 攝影作品集評分器",
        ],
    )
    st.divider()
    st.header("Notion 匯出（可選）")
    notion_enabled = st.checkbox("分析後逐張匯出到 Notion", value=True)
    notion_token = st.text_input("Notion Integration Token", value=DEFAULT_NOTION_TOKEN, type="password", help="需先在 Notion 將資料庫分享給 integration。")
    notion_database_id = st.text_input("Notion Database ID", value=DEFAULT_NOTION_DATABASE_ID)
    notion_file_upload_enabled = st.checkbox("匯出 Notion 前，直接上傳壓縮圖到 Notion", value=True)
    st.caption("Notion 會為每張照片建立獨立頁面；頁面會寫入照片名稱、Score、Status、Tags、XMP code block、構圖/光影/色彩分析，並可用 Notion File Upload 建立預覽圖。")

style_reference = None
if mode.startswith("B"):
    st.subheader("參考風格")
    style_reference = st.text_area(
        "描述希望統一的風格，或貼上參考照片的調色特徵",
        value="暖色調、柔和對比、保留高光細節、陰影略微抬起、底片感但不過度褪色。",
        height=120,
    )
else:
    st.info("若要統一風格，請在側邊欄切換到 B 模式。A/C 模式會以每張照片自身最佳化為主。")

uploaded_files = st.file_uploader(
    "上傳一張或多張圖片",
    type=["jpg", "jpeg", "png", "cr3"],
    accept_multiple_files=True,
    help="JPG/PNG/CR3 會在後端自動壓縮到 Claude API 圖片限制以下。",
)

if not uploaded_files:
    st.info("請先上傳照片。A/B/C 三種模式都支援多張批次處理。")
    st.stop()

preview_cols = st.columns(min(3, len(uploaded_files)))
for idx, uploaded in enumerate(uploaded_files[:3]):
    ext = uploaded.name.rsplit(".", 1)[-1].lower() if "." in uploaded.name else ""
    with preview_cols[idx % len(preview_cols)]:
        if ext in {"jpg", "jpeg", "png"}:
            st.image(uploaded.getvalue(), caption=uploaded.name, use_container_width=True)
        else:
            st.info(f"{uploaded.name}\nCR3 RAW 將轉成 JPEG 預覽分析。")
if len(uploaded_files) > 3:
    st.caption(f"另有 {len(uploaded_files) - 3} 張照片待批次處理。")

if not api_key:
    st.warning("請在側邊欄輸入 Anthropic API Key，或設定 ANTHROPIC_API_KEY。")
    st.stop()

if st.button("開始批次分析", type="primary", use_container_width=True):
    progress = st.progress(0)
    status = st.empty()
    results: list[dict] = []
    errors: list[str] = []

    for index, uploaded in enumerate(uploaded_files, start=1):
        status.write(f"正在分析 {index}/{len(uploaded_files)}：{uploaded.name}")
        try:
            result = analyze_uploaded_file(uploaded, api_key, model, style_reference if mode.startswith("B") else None)
            if notion_enabled and notion_file_upload_enabled and notion_token:
                compressed_filename = Path(result["compressed_image_path"]).name
                status.write(f"正在上傳壓縮後照片到 Notion：{compressed_filename}")
                notion_file = upload_file_to_notion(
                    notion_token,
                    result["compressed_image_bytes"],
                    compressed_filename,
                    content_type=result.get("compressed_media_type", "image/jpeg"),
                )
                result["notion_file_upload_id"] = notion_file["id"]
                result["notion_file_upload_filename"] = notion_file.get("filename") or compressed_filename
                result["notion_file_upload_source"] = "compressed"
                result["notion_xmp_embed_method"] = "code_block"
            if notion_enabled and notion_token and notion_database_id:
                notion_page = export_result_to_notion(notion_token, notion_database_id, result)
                result["notion_page_id"] = notion_page.get("id")
            results.append(result)
        except Exception as exc:
            errors.append(f"{uploaded.name}: {exc}")
        progress.progress(index / len(uploaded_files))

    st.session_state["analysis_results"] = results
    st.session_state["analysis_errors"] = errors
    status.write("批次分析完成。")

results = st.session_state.get("analysis_results", [])
errors = st.session_state.get("analysis_errors", [])

if errors:
    st.error("部分照片分析失敗：")
    for error in errors:
        st.write(f"- {error}")

if not results:
    st.stop()

st.success(f"完成 {len(results)} 張照片分析。")

summary_rows = []
for result in results:
    analysis = result["analysis"]
    summary_rows.append(
        {
            "photo_title": analysis.get("photo_title", ""),
            "filename": result["filename"],
            "composition_score": analysis.get("composition_score"),
            "lighting_score": analysis.get("lighting_score"),
            "color_score": analysis.get("color_score"),
            "technical_score": analysis.get("technical_score"),
            "overall_score": analysis.get("overall_score"),
            "status": analysis.get("selection_status"),
            "tags": ", ".join(str(tag) for tag in analysis.get("photo_tags", []) if str(tag).strip()),
            "original_MB": round(result.get("original_size_mb", 0), 2),
            "sent_to_claude_MB": round(result.get("compressed_size_mb", 0), 2),
            "xmp_path": result.get("xmp_path"),
            "notion_page_id": result.get("notion_page_id"),
            "notion_file_upload_id": result.get("notion_file_upload_id"),
            "notion_xmp_embed_method": result.get("notion_xmp_embed_method"),
            "compressed_image_path": result.get("compressed_image_path"),
        }
    )
st.subheader("作品集評分總表")
st.dataframe(summary_rows, use_container_width=True)

xmp_zip = build_xmp_zip(results)
csv_text = build_portfolio_csv(results)
image_zip = compressed_images_zip(results)

col_a, col_b, col_c = st.columns(3)
with col_a:
    st.download_button("下載全部 XMP ZIP", xmp_zip, file_name="lightroom_presets.zip", mime="application/zip", use_container_width=True)
with col_b:
    st.download_button("下載作品集 CSV", csv_text.encode("utf-8-sig"), file_name="portfolio_scores.csv", mime="text/csv", use_container_width=True)
with col_c:
    st.download_button("下載壓縮後照片 ZIP", image_zip, file_name="compressed_images.zip", mime="application/zip", use_container_width=True)

st.subheader("逐張分析")
for result in results:
    analysis = result["analysis"]
    photo_title = analysis.get("photo_title") or result["filename"]
    tags = ", ".join(str(tag) for tag in analysis.get("photo_tags", []) if str(tag).strip())
    with st.expander(f"{photo_title}｜{result['filename']}｜分數 {analysis.get('overall_score', '—')}/100", expanded=len(results) == 1):
        score_col, meta_col = st.columns([1, 2])
        with score_col:
            st.markdown(
                f"""
<div class="score-card">
  <div class="score">{analysis.get('overall_score', '—')}</div>
  <div class="score-label">綜合評分 / 100</div>
</div>
""",
                unsafe_allow_html=True,
            )
        with meta_col:
            st.write(f"Claude 命名：**{photo_title}**")
            st.write(f"篩選狀態：**{analysis.get('selection_status', '—')}**")
            st.write(f"標籤：{tags or '—'}")
            st.write(f"原始檔名：`{result['filename']}`")
            st.write(f"原始大小：{result.get('original_size_mb', 0):.2f} MB")
            st.write(f"送 Claude 大小：{result.get('compressed_size_mb', 0):.2f} MB")
            st.write(f"壓縮圖：`{result.get('compressed_image_path')}`")
            st.write(f"XMP：`{result.get('xmp_path')}`")
            if result.get("notion_page_id"):
                st.caption(f"Notion 頁面：已建立 {result.get('notion_page_id')}")
            if result.get("notion_file_upload_id"):
                st.caption(f"Notion 預覽圖：已直接上傳 {result.get('notion_file_upload_filename')}")
            if result.get("notion_xmp_embed_method") == "code_block":
                st.caption("Notion XMP：已寫入頁面 XML code block")

        st.markdown("#### 評分明細")
        score_cols = st.columns(4)
        score_items = [
            ("構圖", analysis.get("composition_score", "—"), 35),
            ("光影", analysis.get("lighting_score", "—"), 35),
            ("色彩", analysis.get("color_score", "—"), 20),
            ("技術品質", analysis.get("technical_score", "—"), 10),
        ]
        for col, (label, value, maximum) in zip(score_cols, score_items):
            with col:
                st.metric(label, f"{value}/{maximum}")

        col1, col2, col3 = st.columns(3)
        with col1:
            render_card("構圖分析", analysis.get("composition_analysis", "—"))
        with col2:
            render_card("光影分析", analysis.get("lighting_analysis", "—"))
        with col3:
            render_card("色彩分析", analysis.get("color_analysis", "—"))

        st.markdown("#### Lightroom 參數")
        render_params(analysis.get("lightroom_parameters", {}))
        st.download_button(
            f"下載 {safe_stem(result['filename'])}.xmp",
            result["xmp"].encode("utf-8"),
            file_name=f"{safe_stem(result['filename'])}.xmp",
            mime="application/octet-stream",
            key=f"xmp-{result['filename']}",
        )
        with st.expander("JSON / XMP 原始內容"):
            st.json(analysis)
            st.code(result["xmp"], language="xml")
