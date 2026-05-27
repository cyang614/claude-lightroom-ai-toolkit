"""Core logic for the Claude-powered Lightroom preset Streamlit app."""

from __future__ import annotations

import base64
import csv
from collections import Counter
from datetime import datetime
import html
import io
import json
import os
import posixpath
import re
import zipfile
from pathlib import Path
from typing import Any

import requests

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
NOTION_API_VERSION = "2026-03-11"
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-6"
CLAUDE_IMAGE_MAX_BYTES = 5 * 1024 * 1024
CLAUDE_IMAGE_TARGET_BYTES = int(4.5 * 1024 * 1024)
LIGHTROOM_TOOL_NAME = "return_photo_analysis_and_lightroom_preset"
SUPPORTED_IMAGE_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "cr3": "image/jpeg",  # CR3 is converted to JPEG preview bytes before calling Claude.
}


class AnthropicAPIError(RuntimeError):
    """Readable wrapper for Anthropic API error responses."""


LIGHTROOM_SCHEMA_PROPERTIES: dict[str, dict[str, Any]] = {
    "Exposure": {"type": "number", "minimum": -5, "maximum": 5, "description": "Lightroom Exposure adjustment in stops."},
    "Contrast": {"type": "integer", "minimum": -100, "maximum": 100},
    "Highlights": {"type": "integer", "minimum": -100, "maximum": 100},
    "Shadows": {"type": "integer", "minimum": -100, "maximum": 100},
    "Whites": {"type": "integer", "minimum": -100, "maximum": 100},
    "Blacks": {"type": "integer", "minimum": -100, "maximum": 100},
    "Temp": {"type": "integer", "minimum": 2000, "maximum": 50000, "description": "Color temperature in Kelvin."},
    "Tint": {"type": "integer", "minimum": -150, "maximum": 150},
    "Texture": {"type": "integer", "minimum": -100, "maximum": 100},
    "Clarity": {"type": "integer", "minimum": -100, "maximum": 100},
    "Dehaze": {"type": "integer", "minimum": -100, "maximum": 100},
    "Vibrance": {"type": "integer", "minimum": -100, "maximum": 100},
    "Saturation": {"type": "integer", "minimum": -100, "maximum": 100},
    "Sharpening": {"type": "integer", "minimum": 0, "maximum": 150},
    "NoiseReduction": {"type": "integer", "minimum": 0, "maximum": 100},
    "LuminanceSmoothing": {"type": "integer", "minimum": 0, "maximum": 100},
    "ColorNoiseReduction": {"type": "integer", "minimum": 0, "maximum": 100},
    "PostCropVignetteAmount": {"type": "integer", "minimum": -100, "maximum": 100},
}

# Mapping from UI/Claude-friendly names to Lightroom Camera Raw Settings XMP attributes.
XMP_PARAM_MAP = {
    "Exposure": ("Exposure2012", "float_signed"),
    "Contrast": ("Contrast2012", "int"),
    "Highlights": ("Highlights2012", "int"),
    "Shadows": ("Shadow2012", "int"),
    "Whites": ("Whites2012", "int"),
    "Blacks": ("Blacks2012", "int"),
    "Temp": ("Temperature", "int"),
    "Tint": ("Tint", "int"),
    "Texture": ("Texture", "int"),
    "Clarity": ("Clarity2012", "int"),
    "Dehaze": ("Dehaze", "int"),
    "Vibrance": ("Vibrance", "int"),
    "Saturation": ("Saturation", "int"),
    "Sharpening": ("Sharpness", "int"),
    "NoiseReduction": ("LuminanceSmoothing", "int"),
    "LuminanceSmoothing": ("LuminanceSmoothing", "int"),
    "ColorNoiseReduction": ("ColorNoiseReduction", "int"),
    "PostCropVignetteAmount": ("PostCropVignetteAmount", "int"),
}


def get_api_key() -> str | None:
    """Return Anthropic API key from environment or Streamlit secrets when available."""
    if os.getenv("ANTHROPIC_API_KEY"):
        return os.getenv("ANTHROPIC_API_KEY")
    try:
        import streamlit as st  # type: ignore

        return st.secrets.get("ANTHROPIC_API_KEY")
    except Exception:
        return None


def convert_cr3_to_jpeg(image_bytes: bytes) -> bytes:
    """Convert Canon CR3 RAW bytes to JPEG bytes using rawpy + Pillow."""
    try:
        import rawpy  # type: ignore
        from PIL import Image  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on optional native wheels
        raise RuntimeError("CR3 支援需要安裝 rawpy 與 Pillow：pip install rawpy pillow") from exc

    try:
        with rawpy.imread(io.BytesIO(image_bytes)) as raw:
            rgb = raw.postprocess(use_camera_wb=True, no_auto_bright=False, output_bps=8)
        image = Image.fromarray(rgb)
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=92)
        return output.getvalue()
    except Exception as exc:  # pragma: no cover - depends on RAW decoder/files
        raise RuntimeError(f"CR3 轉 JPEG 失敗：{exc}") from exc


def compress_image_for_claude(
    image_bytes: bytes,
    media_type: str,
    max_bytes: int = CLAUDE_IMAGE_TARGET_BYTES,
) -> tuple[bytes, str]:
    """Compress JPEG/PNG bytes to fit Claude's image upload limit."""
    if len(image_bytes) <= max_bytes:
        return image_bytes, media_type

    try:
        from PIL import Image, ImageOps  # type: ignore
    except Exception as exc:  # pragma: no cover - dependency should be installed via requirements
        raise RuntimeError("圖片超過 Claude 5MB 限制，且缺少 Pillow 無法自動壓縮：pip install pillow") from exc

    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image = ImageOps.exif_transpose(image)
            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            elif image.mode == "L":
                image = image.convert("RGB")

            working = image.copy()
    except Exception as exc:
        raise RuntimeError(f"圖片超過 Claude 5MB 限制，且無法重新編碼壓縮：{exc}") from exc

    # Claude accepts JPEG and PNG; for analysis purposes JPEG is usually the
    # best trade-off when shrinking camera photos under the API size cap.
    for longest_edge in (4096, 3200, 2560, 2048, 1600, 1280, 1024, 768):
        candidate = working.copy()
        candidate.thumbnail((longest_edge, longest_edge))
        for quality in (90, 82, 74, 66, 58, 50, 42, 34):
            output = io.BytesIO()
            candidate.save(output, format="JPEG", quality=quality, optimize=True, progressive=True)
            compressed = output.getvalue()
            if len(compressed) <= max_bytes:
                return compressed, "image/jpeg"

    raise RuntimeError("圖片壓縮後仍超過 Claude 5MB 限制；請先手動縮小解析度後再上傳。")


def prepare_image_for_claude(image_bytes: bytes, filename: str) -> tuple[bytes, str]:
    """Return Claude-compatible image bytes and media type for an uploaded file."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in SUPPORTED_IMAGE_TYPES:
        raise ValueError("僅支援 jpg、jpeg、png、cr3 檔案")
    if ext == "cr3":
        image_bytes = convert_cr3_to_jpeg(image_bytes)
        return compress_image_for_claude(image_bytes, "image/jpeg")
    return compress_image_for_claude(image_bytes, SUPPORTED_IMAGE_TYPES[ext])


def build_lightroom_tool_schema() -> dict[str, Any]:
    """Build the Claude Tool Use schema that forces structured analysis output."""
    return {
        "name": LIGHTROOM_TOOL_NAME,
        "description": "Return objective photo critique and a complete Lightroom preset recommendation as structured JSON.",
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "photo_title",
                "photo_tags",
                "selection_status",
                "composition_analysis",
                "lighting_analysis",
                "color_analysis",
                "overall_score",
                "lightroom_parameters",
            ],
            "properties": {
                "photo_title": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 80,
                    "description": "A concise, descriptive photo title in Traditional Chinese for the Notion Name field.",
                },
                "photo_tags": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 8,
                    "items": {"type": "string", "minLength": 1, "maxLength": 24},
                    "description": "Practical Traditional Chinese tags for Notion multi-select filtering, such as subject, genre, mood, color style, and editing direction.",
                },
                "selection_status": {
                    "type": "string",
                    "enum": ["精選", "可修", "淘汰"],
                    "description": "Portfolio triage status. Use 精選 for strong keepers, 可修 for usable photos needing work, and 淘汰 for weak rejects.",
                },
                "composition_analysis": {"type": "string", "description": "Objective composition critique in Traditional Chinese."},
                "lighting_analysis": {"type": "string", "description": "Objective lighting and dynamic range critique in Traditional Chinese."},
                "color_analysis": {"type": "string", "description": "Objective color, white balance, and palette critique in Traditional Chinese."},
                "overall_score": {"type": "integer", "minimum": 0, "maximum": 100, "description": "Overall photographic quality score."},
                "lightroom_parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(LIGHTROOM_SCHEMA_PROPERTIES.keys()),
                    "properties": LIGHTROOM_SCHEMA_PROPERTIES,
                },
            },
        },
    }


def build_claude_request(
    image_bytes: bytes,
    media_type: str,
    model: str = DEFAULT_CLAUDE_MODEL,
    force_tool_choice: bool = True,
    style_reference: str | None = None,
) -> dict[str, Any]:
    """Create an Anthropic Messages API payload using Tool Use and base64 image content."""
    encoded = base64.b64encode(image_bytes).decode("ascii")
    instruction_text = (
        "請先根據照片內容為照片命名，產生一個適合放入 Notion Name 欄位的繁體中文短標題；"
        "再產生 3-8 個適合 Notion multi-select 篩選的繁體中文標籤，包含主體、類型、情緒、色調或修圖方向；"
        "接著判斷作品集篩選狀態：精選代表可保留或發表，可修代表有潛力但需後製或裁切，淘汰代表不建議保留；"
        "再分析這張照片的構圖、光影與色彩，給出 0-100 綜合評分，"
        "並產生可直接轉成 Lightroom XMP preset 的完整調整參數。"
    )
    if style_reference:
        instruction_text += (
            "\n\n參考風格需求：請讓本張照片的 Lightroom 參數朝以下風格一致化，"
            "但仍保留照片本身曝光與白平衡的合理性："
            f"\n{style_reference.strip()}"
        )
    if not force_tool_choice:
        instruction_text += (
            " You must call the return_photo_analysis_and_lightroom_preset tool exactly once; "
            "do not answer in plain text."
        )
    return {
        "model": model,
        "max_tokens": 1800,
        "temperature": 0.2,
        "tools": [build_lightroom_tool_schema()],
        "tool_choice": {"type": "tool", "name": LIGHTROOM_TOOL_NAME} if force_tool_choice else {"type": "auto"},
        "system": (
            "You are a professional photo editor and Lightroom preset designer. "
            "Analyze the uploaded image objectively. Return Traditional Chinese prose in the analysis fields. "
            "Use the forced tool only; do not answer with free-form JSON outside the tool."
        ),
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": encoded,
                        },
                    },
                    {
                        "type": "text",
                        "text": instruction_text,
                    },
                ],
            }
        ],
    }


def _anthropic_error_message(response: requests.Response) -> str:
    """Return a readable Anthropic error message while preserving response details."""
    try:
        body = response.json()
    except ValueError:
        body = response.text
    if isinstance(body, dict):
        error = body.get("error", {})
        message = error.get("message") if isinstance(error, dict) else None
        error_type = error.get("type") if isinstance(error, dict) else None
        if message:
            prefix = f"Anthropic API {response.status_code}"
            if error_type:
                prefix += f" ({error_type})"
            return f"{prefix}: {message}"
    return f"Anthropic API {response.status_code}: {body}"


def _post_claude_payload(payload: dict[str, Any], api_key: str) -> requests.Response:
    return requests.post(
        ANTHROPIC_API_URL,
        headers={
            "x-api-key": api_key.strip(),
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json=payload,
        timeout=120,
    )


def call_claude_api(
    image_bytes: bytes,
    media_type: str,
    api_key: str,
    model: str = DEFAULT_CLAUDE_MODEL,
    style_reference: str | None = None,
) -> dict[str, Any]:
    """Call Anthropic Claude Messages API and return the raw JSON response."""
    payload = build_claude_request(image_bytes, media_type, model=model, force_tool_choice=True, style_reference=style_reference)
    response = _post_claude_payload(payload, api_key)

    if response.status_code == 400 and "Method Not Allowed" in response.text:
        # Some Claude model/account combinations reject forced tool_choice for
        # multimodal requests. Keep Tool Use enabled, but fall back to auto
        # tool_choice plus an explicit instruction to call the tool.
        fallback_payload = build_claude_request(image_bytes, media_type, model=model, force_tool_choice=False, style_reference=style_reference)
        response = _post_claude_payload(fallback_payload, api_key)

    if response.status_code >= 400:
        raise AnthropicAPIError(_anthropic_error_message(response))
    return response.json()


def extract_tool_json(response_json: dict[str, Any]) -> dict[str, Any]:
    """Extract the forced tool_use input from an Anthropic Messages API response."""
    for block in response_json.get("content", []):
        if block.get("type") == "tool_use" and block.get("name") == LIGHTROOM_TOOL_NAME:
            tool_input = block.get("input")
            if isinstance(tool_input, dict):
                return tool_input
            if isinstance(tool_input, str):
                return json.loads(tool_input)
    raise ValueError("Claude 回應中找不到必要的 tool_use 結構化資料。")


def _format_xmp_value(value: Any, value_type: str) -> str:
    if value_type == "float_signed":
        number = float(value)
        return f"{number:+.2f}"
    if value_type == "int":
        return str(int(round(float(value))))
    return str(value)


def generate_xmp(analysis: dict[str, Any], preset_name: str = "Claude Lightroom Preset") -> str:
    """Generate a Lightroom-compatible .xmp preset from Claude analysis JSON."""
    params = analysis.get("lightroom_parameters", {}) or {}
    escaped_name = html.escape(preset_name, quote=True)

    attr_lines = [
        f'   crs:Name="{escaped_name}"',
        '   crs:PresetType="Normal"',
        '   crs:Cluster="Claude AI Presets"',
        '   crs:UUID=""',
        '   crs:SupportsAmount="False"',
        '   crs:SupportsColor="True"',
        '   crs:SupportsMonochrome="True"',
        '   crs:SupportsHighDynamicRange="True"',
        '   crs:CameraProfile="Adobe Color"',
        '   crs:ProcessVersion="15.4"',
    ]

    emitted_xmp_names: set[str] = set()
    for param_name, (xmp_name, value_type) in XMP_PARAM_MAP.items():
        if xmp_name in emitted_xmp_names:
            continue
        if param_name in params and params[param_name] is not None:
            attr_lines.append(f'   crs:{xmp_name}="{html.escape(_format_xmp_value(params[param_name], value_type), quote=True)}"')
            emitted_xmp_names.add(xmp_name)

    attr_blob = "\n".join(attr_lines)
    return f'''<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Claude Photo Analyzer">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
   xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
{attr_blob}>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>
'''


def _safe_stem(filename: str) -> str:
    basename = posixpath.basename(filename).rsplit(".", 1)[0] or "preset"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", basename).strip("._")
    return safe or "preset"


def build_xmp_zip(batch_results: list[dict[str, Any]]) -> bytes:
    """Return a zip archive containing one .xmp file per successful batch result."""
    buffer = io.BytesIO()
    used_names: set[str] = set()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, result in enumerate(batch_results, start=1):
            xmp = result.get("xmp")
            if not xmp:
                continue
            base_name = _safe_stem(str(result.get("filename") or f"preset_{index}"))
            archive_name = f"{base_name}.xmp"
            suffix = 2
            while archive_name in used_names:
                archive_name = f"{base_name}_{suffix}.xmp"
                suffix += 1
            used_names.add(archive_name)
            archive.writestr(archive_name, xmp.encode("utf-8") if isinstance(xmp, str) else xmp)
    return buffer.getvalue()


def build_portfolio_csv(batch_results: list[dict[str, Any]]) -> str:
    """Build a CSV portfolio report from batch analysis results."""
    fieldnames = [
        "photo_title",
        "selection_status",
        "photo_tags",
        "filename",
        "overall_score",
        "composition_analysis",
        "lighting_analysis",
        "color_analysis",
        *LIGHTROOM_SCHEMA_PROPERTIES.keys(),
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for result in batch_results:
        analysis = result.get("analysis", {}) or {}
        params = analysis.get("lightroom_parameters", {}) or {}
        row = {
            "photo_title": analysis.get("photo_title", ""),
            "selection_status": analysis.get("selection_status", _selection_status_from_score(analysis.get("overall_score"))),
            "photo_tags": ", ".join(str(tag) for tag in analysis.get("photo_tags", []) if str(tag).strip()),
            "filename": result.get("filename", ""),
            "overall_score": analysis.get("overall_score", ""),
            "composition_analysis": analysis.get("composition_analysis", ""),
            "lighting_analysis": analysis.get("lighting_analysis", ""),
            "color_analysis": analysis.get("color_analysis", ""),
        }
        for param in LIGHTROOM_SCHEMA_PROPERTIES:
            row[param] = params.get(param, "")
        writer.writerow(row)
    return output.getvalue()


def _notion_rich_text(content: Any) -> dict[str, Any]:
    return {"type": "text", "text": {"content": str(content)[:2000]}}


def _notion_rich_text_segments(content: Any) -> list[dict[str, Any]]:
    text = str(content)
    if not text:
        return [_notion_rich_text("")]
    return [_notion_rich_text(text[index : index + 2000]) for index in range(0, len(text), 2000)]


def _selection_status_from_score(score: Any) -> str:
    if isinstance(score, (int, float)):
        if score >= 80:
            return "精選"
        if score >= 60:
            return "可修"
    return "淘汰"


def _notion_multi_select_names(values: Any, limit: int = 8) -> list[dict[str, str]]:
    if not isinstance(values, list):
        return []
    names: list[dict[str, str]] = []
    seen: set[str] = set()
    for value in values:
        name = str(value).strip()
        if not name or name in seen:
            continue
        names.append({"name": name[:100]})
        seen.add(name)
        if len(names) >= limit:
            break
    return names


def _notion_file_upload_object(file_upload_id: Any) -> dict[str, Any]:
    return {
        "type": "file_upload",
        "file_upload": {"id": str(file_upload_id)},
    }


def _notion_headers(notion_token: str, content_type: str | None = "application/json") -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {notion_token.strip()}",
        "Notion-Version": NOTION_API_VERSION,
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _notion_error_message(response: requests.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = response.text
    if isinstance(body, dict):
        message = body.get("message")
        code = body.get("code")
        if message:
            prefix = f"Notion API {response.status_code}"
            if code:
                prefix += f" ({code})"
            return f"{prefix}: {message}"
    return f"Notion API {response.status_code}: {body}"


def upload_file_to_notion(
    notion_token: str,
    file_bytes: bytes,
    filename: str,
    content_type: str = "image/jpeg",
) -> dict[str, Any]:
    """Upload local bytes to Notion-managed storage and return the File Upload object."""
    create_response = requests.post(
        "https://api.notion.com/v1/file_uploads",
        headers=_notion_headers(notion_token),
        json={
            "mode": "single_part",
            "filename": filename,
            "content_type": content_type,
        },
        timeout=60,
    )
    if create_response.status_code >= 400:
        raise RuntimeError(f"Notion file upload 建立失敗: {_notion_error_message(create_response)}")

    file_upload = create_response.json()
    file_upload_id = file_upload["id"]
    upload_url = file_upload.get("upload_url") or f"https://api.notion.com/v1/file_uploads/{file_upload_id}/send"
    send_response = requests.post(
        upload_url,
        headers=_notion_headers(notion_token, content_type=None),
        files={"file": (filename, io.BytesIO(file_bytes), content_type)},
        timeout=120,
    )
    if send_response.status_code >= 400:
        raise RuntimeError(f"Notion file upload 傳送失敗: {_notion_error_message(send_response)}")

    uploaded = send_response.json()
    if uploaded.get("status") != "uploaded":
        raise RuntimeError(f"Notion file upload 狀態不是 uploaded: {uploaded.get('status')}")
    return uploaded


def _find_google_credentials_json(credentials_path: str) -> Path:
    """Expand a credentials path; if it is a directory, pick the first JSON file inside."""
    credentials_path_obj = Path(str(credentials_path)).expanduser()
    if credentials_path_obj.is_dir():
        json_candidates = sorted(credentials_path_obj.glob("*.json"))
        if not json_candidates:
            raise RuntimeError(f"Google credentials 路徑是資料夾，但找不到 .json 檔案：{credentials_path_obj}")
        credentials_path_obj = json_candidates[0]
    return credentials_path_obj


def load_google_drive_credentials(credentials_path: str):
    """Load Google Drive credentials from service-account or authorized-user JSON."""
    credentials_path_obj = _find_google_credentials_json(credentials_path)
    try:
        credential_info = json.loads(credentials_path_obj.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"無法讀取 Google credentials JSON：{credentials_path_obj}") from exc

    credential_type = credential_info.get("type")
    scopes = ["https://www.googleapis.com/auth/drive"]
    if credential_type == "service_account":
        from google.oauth2 import service_account  # type: ignore

        return service_account.Credentials.from_service_account_file(str(credentials_path_obj), scopes=scopes), "service_account"
    is_oauth_authorized_user = credential_type == "authorized_user" or all(
        credential_info.get(key) for key in ("refresh_token", "client_id", "client_secret")
    )
    if is_oauth_authorized_user:
        from google.oauth2.credentials import Credentials  # type: ignore

        return Credentials.from_authorized_user_file(str(credentials_path_obj), scopes=scopes), "authorized_user"
    raise RuntimeError(
        "不支援的 Google credentials JSON。請提供 service_account JSON，"
        "或由 OAuth 產生的 authorized_user token JSON。"
    )


def google_drive_direct_image_url(file_id: str) -> str:
    """Return a Google Drive image URL suitable for Notion external image blocks."""
    return f"https://drive.google.com/uc?export=view&id={file_id}"


def upload_image_to_google_drive(
    image_bytes: bytes,
    filename: str,
    folder_id: str | None = None,
    credentials_file: str | None = None,
    make_public: bool = True,
) -> dict[str, str]:
    """Upload image bytes to Google Drive and return public links.

    Uses a Google service-account JSON file. Share the target Drive folder with
    the service account email, then pass the folder id in the Streamlit sidebar.
    """
    try:
        from google.auth.transport.requests import Request  # type: ignore
        from googleapiclient.discovery import build  # type: ignore
        from googleapiclient.errors import HttpError  # type: ignore
        from googleapiclient.http import MediaIoBaseUpload  # type: ignore
    except Exception as exc:  # pragma: no cover - optional deployment dependency
        raise RuntimeError("Google Drive 上傳需要安裝 google-api-python-client 與 google-auth。請執行 pip install -r requirements.txt") from exc

    credentials_path = credentials_file or os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not credentials_path:
        raise RuntimeError("請提供 Google credentials JSON 路徑，或設定 GOOGLE_SERVICE_ACCOUNT_FILE。建議使用 OAuth authorized_user token，避免 Service Account quota 限制。")

    credentials, credential_type = load_google_drive_credentials(str(credentials_path))
    if getattr(credentials, "expired", False) and getattr(credentials, "refresh_token", None):
        credentials.refresh(Request())

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    mime_type = SUPPORTED_IMAGE_TYPES.get(ext, "image/jpeg")
    service = build("drive", "v3", credentials=credentials)
    metadata: dict[str, Any] = {"name": filename}
    if folder_id:
        metadata["parents"] = [folder_id]
    media = MediaIoBaseUpload(io.BytesIO(image_bytes), mimetype=mime_type, resumable=False)
    try:
        uploaded = service.files().create(
            body=metadata,
            media_body=media,
            fields="id,name,webViewLink,webContentLink,thumbnailLink",
            supportsAllDrives=True,
        ).execute()
    except HttpError as exc:
        error_text = str(exc)
        if "Service Accounts do not have storage quota" in error_text or "storageQuotaExceeded" in error_text:
            if credential_type == "service_account":
                raise RuntimeError(
                    "Google Drive 上傳失敗：Service Account 沒有 My Drive 儲存空間 quota。"
                    "請改用 OAuth authorized_user token JSON 上傳到你的個人 Drive，"
                    "或將目標資料夾移到 Google Shared Drive 後再使用 service account。"
                ) from exc
        raise
    file_id = uploaded["id"]
    if make_public:
        service.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
            fields="id",
            supportsAllDrives=True,
        ).execute()
    direct_url = google_drive_direct_image_url(file_id)
    thumbnail_url = uploaded.get("thumbnailLink")
    if thumbnail_url:
        thumbnail_url = str(thumbnail_url).replace("=s220", "=s1600")
    return {
        "id": file_id,
        "name": uploaded.get("name", filename),
        "webViewLink": uploaded.get("webViewLink", f"https://drive.google.com/file/d/{file_id}/view"),
        "image_url": direct_url,
        "thumbnail_url": thumbnail_url or direct_url,
    }


def upload_original_image_to_google_drive(
    image_bytes: bytes,
    filename: str,
    folder_id: str | None = None,
    credentials_file: str | None = None,
    make_public: bool = True,
) -> dict[str, str]:
    """Backward-compatible wrapper for older callers."""
    return upload_image_to_google_drive(
        image_bytes,
        filename,
        folder_id=folder_id,
        credentials_file=credentials_file,
        make_public=make_public,
    )


def build_notion_page_payload(database_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """Build a Notion create-page payload for one photo analysis result."""
    analysis = result.get("analysis", {}) or {}
    params = analysis.get("lightroom_parameters", {}) or {}
    filename = str(result.get("filename") or "Untitled photo")
    photo_title = str(analysis.get("photo_title") or filename).strip() or filename
    score = analysis.get("overall_score")
    selection_status = str(analysis.get("selection_status") or _selection_status_from_score(score)).strip()
    photo_tags = _notion_multi_select_names(analysis.get("photo_tags"))
    properties: dict[str, Any] = {
        "Name": {"title": [{"text": {"content": photo_title[:2000]}}]},
        "Score": {"number": int(score) if isinstance(score, (int, float)) else None},
        "Status": {"select": {"name": selection_status}},
        "Tags": {"multi_select": photo_tags},
        "Type": {"select": {"name": "Photo Analysis"}},
        "Composition Analysis": {"rich_text": [_notion_rich_text(analysis.get("composition_analysis", ""))]},
        "Lighting Analysis": {"rich_text": [_notion_rich_text(analysis.get("lighting_analysis", ""))]},
        "Color Analysis": {"rich_text": [_notion_rich_text(analysis.get("color_analysis", ""))]},
    }
    image_url = result.get("google_drive_image_url") or result.get("image_url")
    preview_image_url = result.get("google_drive_thumbnail_url") or result.get("thumbnail_url") or image_url
    notion_file_upload_id = result.get("notion_file_upload_id")
    notion_xmp_file_upload_id = result.get("notion_xmp_file_upload_id")
    if image_url:
        properties["Image URL"] = {"url": str(image_url)}
    if notion_xmp_file_upload_id:
        properties["XMP"] = {
            "files": [
                {
                    "name": str(result.get("notion_xmp_file_upload_filename") or Path(str(result.get("xmp_path") or "preset.xmp")).name),
                    "type": "file_upload",
                    "file_upload": {"id": str(notion_xmp_file_upload_id)},
                }
            ]
        }
    payload: dict[str, Any] = {"parent": {"database_id": database_id}, "properties": properties}
    if notion_file_upload_id:
        payload["cover"] = _notion_file_upload_object(notion_file_upload_id)

    children = [
        {"object": "block", "type": "heading_2", "heading_2": {"rich_text": [_notion_rich_text("攝影分析")] }},
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [_notion_rich_text(f"原始檔名：{filename}")]}},
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [_notion_rich_text(f"篩選狀態：{selection_status}")]}},
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [_notion_rich_text(f"標籤：{', '.join(tag['name'] for tag in photo_tags) or '—'}")]}},
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [_notion_rich_text(f"構圖：{analysis.get('composition_analysis', '—')}")]}},
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [_notion_rich_text(f"光影：{analysis.get('lighting_analysis', '—')}")]}},
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [_notion_rich_text(f"色彩：{analysis.get('color_analysis', '—')}")]}},
        {"object": "block", "type": "heading_3", "heading_3": {"rich_text": [_notion_rich_text("Lightroom 參數")] }},
        {"object": "block", "type": "code", "code": {"language": "json", "rich_text": [_notion_rich_text(json.dumps(params, ensure_ascii=False, indent=2))]}},
    ]
    if notion_file_upload_id:
        children.insert(
            0,
            {
                "object": "block",
                "type": "image",
                "image": {
                    **_notion_file_upload_object(notion_file_upload_id),
                },
            },
        )
    elif preview_image_url:
        children.insert(0, {"object": "block", "type": "image", "image": {"type": "external", "external": {"url": str(preview_image_url)}}})
    if result.get("compressed_image_path"):
        children.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": [_notion_rich_text(f"壓縮後照片路徑：{result['compressed_image_path']}")]}})
    if result.get("xmp_path"):
        children.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": [_notion_rich_text(f"XMP 路徑：{result['xmp_path']}")]}})
    if result.get("xmp"):
        children.append({"object": "block", "type": "heading_3", "heading_3": {"rich_text": [_notion_rich_text("XMP Preset")]}})
        children.append(
            {
                "object": "block",
                "type": "code",
                "code": {
                    "language": "xml",
                    "rich_text": _notion_rich_text_segments(result["xmp"]),
                },
            }
        )
    if notion_xmp_file_upload_id:
        children.append(
            {
                "object": "block",
                "type": "file",
                "file": {
                    **_notion_file_upload_object(notion_xmp_file_upload_id),
                },
            }
        )
    payload["children"] = children
    return payload


def _analysis_score(result: dict[str, Any]) -> int | None:
    score = (result.get("analysis") or {}).get("overall_score")
    if isinstance(score, (int, float)):
        return int(score)
    return None


def _analysis_title(result: dict[str, Any]) -> str:
    analysis = result.get("analysis", {}) or {}
    return str(analysis.get("photo_title") or result.get("filename") or "Untitled photo")


def build_batch_summary_notion_payload(database_id: str, batch_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a Notion page summarizing one completed batch analysis."""
    now_label = datetime.now().strftime("%Y-%m-%d %H:%M")
    scores = [score for result in batch_results if (score := _analysis_score(result)) is not None]
    average_score = round(sum(scores) / len(scores), 1) if scores else None

    status_counts: Counter[str] = Counter()
    tag_counts: Counter[str] = Counter()
    for result in batch_results:
        analysis = result.get("analysis", {}) or {}
        score = analysis.get("overall_score")
        status = str(analysis.get("selection_status") or _selection_status_from_score(score)).strip()
        status_counts[status] += 1
        for tag in analysis.get("photo_tags", []) or []:
            tag_name = str(tag).strip()
            if tag_name:
                tag_counts[tag_name] += 1

    top_results = sorted(
        batch_results,
        key=lambda result: _analysis_score(result) if _analysis_score(result) is not None else -1,
        reverse=True,
    )[:5]
    cover_upload_id = next((result.get("notion_file_upload_id") for result in top_results if result.get("notion_file_upload_id")), None)

    properties: dict[str, Any] = {
        "Name": {"title": [{"text": {"content": f"批次分析總結 - {now_label}"}}]},
        "Type": {"select": {"name": "Batch Summary"}},
    }
    if average_score is not None:
        properties["Score"] = {"number": average_score}
    if tag_counts:
        properties["Tags"] = {"multi_select": [{"name": name[:100]} for name, _ in tag_counts.most_common(8)]}

    children: list[dict[str, Any]] = [
        {"object": "block", "type": "heading_2", "heading_2": {"rich_text": [_notion_rich_text("批次分析總結")]}},
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [_notion_rich_text(f"本批共 {len(batch_results)} 張；平均分數 {average_score if average_score is not None else '—'}。")]},
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    _notion_rich_text(
                        "狀態統計："
                        f"精選 {status_counts.get('精選', 0)}、"
                        f"可修 {status_counts.get('可修', 0)}、"
                        f"淘汰 {status_counts.get('淘汰', 0)}。"
                    )
                ]
            },
        },
    ]

    if tag_counts:
        children.append(
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [_notion_rich_text("常見標籤：" + ", ".join(f"{name}({count})" for name, count in tag_counts.most_common(10)))]},
            }
        )

    children.append({"object": "block", "type": "heading_3", "heading_3": {"rich_text": [_notion_rich_text("Top 5 照片")]}})
    for index, result in enumerate(top_results, start=1):
        analysis = result.get("analysis", {}) or {}
        score = analysis.get("overall_score", "—")
        status = analysis.get("selection_status") or _selection_status_from_score(score)
        children.append(
            {
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": [_notion_rich_text(f"{index}. {_analysis_title(result)}｜{result.get('filename', '')}｜{score} 分｜{status}")]},
            }
        )

    children.extend(
        [
            {"object": "block", "type": "heading_3", "heading_3": {"rich_text": [_notion_rich_text("建議下一步")]}},
            {"object": "block", "type": "to_do", "to_do": {"rich_text": [_notion_rich_text("先處理精選照片，確認 XMP 套用後的膚色、白平衡與高光細節。")], "checked": False}},
            {"object": "block", "type": "to_do", "to_do": {"rich_text": [_notion_rich_text("可修照片先做裁切與曝光修正，再決定是否進入精選。")], "checked": False}},
            {"object": "block", "type": "to_do", "to_do": {"rich_text": [_notion_rich_text("淘汰照片保留分析紀錄即可，不建議投入細修時間。")], "checked": False}},
        ]
    )

    payload: dict[str, Any] = {"parent": {"database_id": database_id}, "properties": properties, "children": children}
    if cover_upload_id:
        payload["cover"] = _notion_file_upload_object(cover_upload_id)
    return payload


def export_result_to_notion(notion_token: str, database_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """Create one Notion page for a photo analysis result."""
    response = requests.post(
        "https://api.notion.com/v1/pages",
        headers={
            **_notion_headers(notion_token),
        },
        json=build_notion_page_payload(database_id, result),
        timeout=60,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Notion 匯出失敗 {response.status_code}: {response.text}")
    return response.json()


def export_batch_summary_to_notion(notion_token: str, database_id: str, batch_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Create a Notion page summarizing a completed batch analysis."""
    response = requests.post(
        "https://api.notion.com/v1/pages",
        headers={**_notion_headers(notion_token)},
        json=build_batch_summary_notion_payload(database_id, batch_results),
        timeout=60,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Notion 批次總結匯出失敗 {response.status_code}: {response.text}")
    return response.json()


def analyze_image(
    image_bytes: bytes,
    filename: str,
    api_key: str,
    model: str = DEFAULT_CLAUDE_MODEL,
    style_reference: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Prepare image, call Claude, parse structured JSON, and generate XMP content."""
    prepared_bytes, media_type = prepare_image_for_claude(image_bytes, filename)
    raw_response = call_claude_api(prepared_bytes, media_type, api_key=api_key, model=model, style_reference=style_reference)
    analysis = extract_tool_json(raw_response)
    xmp = generate_xmp(analysis, preset_name="Claude Photo Preset")
    return analysis, xmp
