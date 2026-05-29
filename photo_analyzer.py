"""Core logic for the Claude-powered Lightroom preset Streamlit app."""

from __future__ import annotations

import base64
import csv
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
DEFAULT_DOTENV_PATH = Path(__file__).with_name(".env")


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

SCORE_SCHEMA_PROPERTIES: dict[str, dict[str, Any]] = {
    "composition_score": {
        "type": "integer",
        "minimum": 0,
        "maximum": 35,
        "description": "Composition score out of 35: subject clarity, balance, visual guidance, and cropping.",
    },
    "lighting_score": {
        "type": "integer",
        "minimum": 0,
        "maximum": 35,
        "description": "Lighting score out of 35: exposure, highlight/shadow detail, dynamic range, and dimensionality.",
    },
    "color_score": {
        "type": "integer",
        "minimum": 0,
        "maximum": 20,
        "description": "Color score out of 20: white balance, color harmony, saturation, and style consistency.",
    },
    "technical_score": {
        "type": "integer",
        "minimum": 0,
        "maximum": 10,
        "description": "Technical quality score out of 10: sharpness, noise, focus accuracy, and motion blur.",
    },
}

WEIGHTED_SCORE_RUBRIC = """評分維度與 Rubric：
構圖（35分）評估：主體明確性、畫面平衡、視覺引導、裁切。
- 29–35：主體極為突出，三分法/黃金比例等構圖原則運用自然，視線有明確引導，裁切完美無多餘元素。
- 21–28：構圖合理，主體清楚，但視覺引導或平衡感稍弱。
- 13–20：主體尚可辨識，但構圖平凡或存在明顯裁切失誤。
- 7–12：主體模糊不清，畫面雜亂，缺乏視覺重心。
- 0–6：構圖嚴重失敗，無法判斷主體或意圖。

光影（35分）評估：曝光準確性、高光細節保留、陰影細節保留、動態範圍表現、立體感。
- 29–35：曝光精準，高光無死白，陰影無死黑，動態範圍充分，光影塑造強烈立體感。
- 21–28：曝光基本正確，少量高光或陰影溢出，立體感尚可。
- 13–20：過曝或欠曝明顯，動態範圍壓縮感重，立體感弱。
- 7–12：嚴重曝光失誤，大量細節遺失。
- 0–6：幾乎無法辨識光影結構。

色彩（20分）評估：白平衡、色彩協調性、飽和度適當性、整體風格一致性。
- 17–20：白平衡準確或有意圖性偏移，色彩搭配協調，飽和度自然或風格化但一致。
- 13–16：色彩整體可接受，但白平衡略偏或局部色彩不協調。
- 8–12：白平衡明顯偏差，色彩顯髒或過飽和/過淡。
- 0–7：色彩嚴重失準，影響整體觀看體驗。

技術品質（10分）評估：主體清晰度、雜訊控制、失焦程度、動態模糊。
- 9–10：主體銳利，雜訊極低，無非預期失焦或動態模糊。
- 7–8：主體尚清晰，雜訊可接受，輕微瑕疵不影響觀看。
- 4–6：明顯雜訊或輕微失焦，影響細節呈現。
- 0–3：嚴重失焦、強烈雜訊或劇烈動態模糊。

overall_score = 四項加總，滿分 100。請確保 overall_score 等於 composition_score + lighting_score + color_score + technical_score。"""

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


def load_dotenv_file(path: str | Path = DEFAULT_DOTENV_PATH, override: bool = False) -> bool:
    """Load KEY=VALUE pairs from a local .env file into os.environ.

    Existing environment variables are preserved by default so deployed secrets
    or shell-provided values remain authoritative.
    """
    env_path = Path(path)
    if not env_path.exists():
        return False

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if override or key not in os.environ:
            os.environ[key] = value
    return True


def get_api_key() -> str | None:
    """Return Anthropic API key from environment, .env, or Streamlit secrets when available."""
    load_dotenv_file()
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
                "composition_score",
                "lighting_score",
                "color_score",
                "technical_score",
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
                **SCORE_SCHEMA_PROPERTIES,
                "overall_score": {"type": "integer", "minimum": 0, "maximum": 100, "description": "Sum of composition_score, lighting_score, color_score, and technical_score."},
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
        "接著分析這張照片的構圖、光影、色彩與技術品質，依下方權重制給出分項分數，"
        "並產生可直接轉成 Lightroom XMP preset 的完整調整參數。"
        f"\n\n{WEIGHTED_SCORE_RUBRIC}"
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
        "max_tokens": 2400,
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


def build_xmp_zip(results: list[dict[str, Any]]) -> bytes:
    """Return a zip archive containing one .xmp file per analysis result."""
    buffer = io.BytesIO()
    used_names: set[str] = set()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, result in enumerate(results, start=1):
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


def build_portfolio_csv(results: list[dict[str, Any]]) -> str:
    """Build a CSV portfolio report from one or more analysis results."""
    fieldnames = [
        "photo_title",
        "selection_status",
        "photo_tags",
        "filename",
        "composition_score",
        "lighting_score",
        "color_score",
        "technical_score",
        "overall_score",
        "composition_analysis",
        "lighting_analysis",
        "color_analysis",
        *LIGHTROOM_SCHEMA_PROPERTIES.keys(),
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for result in results:
        analysis = result.get("analysis", {}) or {}
        params = analysis.get("lightroom_parameters", {}) or {}
        row = {
            "photo_title": analysis.get("photo_title", ""),
            "selection_status": analysis.get("selection_status", _selection_status_from_score(analysis.get("overall_score"))),
            "photo_tags": ", ".join(str(tag) for tag in analysis.get("photo_tags", []) if str(tag).strip()),
            "filename": result.get("filename", ""),
            "composition_score": analysis.get("composition_score", ""),
            "lighting_score": analysis.get("lighting_score", ""),
            "color_score": analysis.get("color_score", ""),
            "technical_score": analysis.get("technical_score", ""),
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
        {"object": "block", "type": "heading_3", "heading_3": {"rich_text": [_notion_rich_text("評分明細")] }},
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [_notion_rich_text(f"構圖分數：{analysis.get('composition_score', '—')}/35")]}},
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [_notion_rich_text(f"光影分數：{analysis.get('lighting_score', '—')}/35")]}},
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [_notion_rich_text(f"色彩分數：{analysis.get('color_score', '—')}/20")]}},
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [_notion_rich_text(f"技術品質分數：{analysis.get('technical_score', '—')}/10")]}},
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [_notion_rich_text(f"總分：{analysis.get('overall_score', '—')}/100")]}},
        {"object": "block", "type": "heading_3", "heading_3": {"rich_text": [_notion_rich_text("分析摘要")] }},
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
