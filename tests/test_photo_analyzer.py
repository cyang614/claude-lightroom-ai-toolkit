import base64
import csv
import io
import json
import tempfile
import unittest
import zipfile
from unittest.mock import Mock, patch

from photo_analyzer import (
    AnthropicAPIError,
    LIGHTROOM_TOOL_NAME,
    build_batch_summary_notion_payload,
    build_claude_request,
    build_notion_page_payload,
    build_portfolio_csv,
    build_xmp_zip,
    call_claude_api,
    google_drive_direct_image_url,
    load_google_drive_credentials,
    compress_image_for_claude,
    extract_tool_json,
    generate_xmp,
    upload_file_to_notion,
)


class PhotoAnalyzerTests(unittest.TestCase):
    def test_build_claude_request_forces_lightroom_tool_use_with_base64_image(self):
        image_bytes = b"fake-jpeg-bytes"
        payload = build_claude_request(image_bytes, "image/jpeg")

        self.assertEqual(payload["tool_choice"], {"type": "tool", "name": LIGHTROOM_TOOL_NAME})
        self.assertEqual(payload["tools"][0]["name"], LIGHTROOM_TOOL_NAME)
        image_source = payload["messages"][0]["content"][0]["source"]
        self.assertEqual(image_source["type"], "base64")
        self.assertEqual(image_source["media_type"], "image/jpeg")
        self.assertEqual(base64.b64decode(image_source["data"]), image_bytes)

    def test_build_claude_request_schema_requires_photo_title(self):
        payload = build_claude_request(b"fake-jpeg-bytes", "image/jpeg")
        schema = payload["tools"][0]["input_schema"]

        self.assertIn("photo_title", schema["required"])
        self.assertIn("photo_tags", schema["required"])
        self.assertIn("selection_status", schema["required"])
        self.assertIn("photo_title", schema["properties"])
        self.assertIn("photo_tags", schema["properties"])
        self.assertIn("selection_status", schema["properties"])
        instruction = payload["messages"][0]["content"][1]["text"]
        self.assertIn("照片命名", instruction)
        self.assertIn("標籤", instruction)

    def test_compress_image_for_claude_reduces_large_jpeg_under_limit(self):
        try:
            from PIL import Image
        except ModuleNotFoundError:
            self.skipTest("Pillow is not installed in this environment")
        from io import BytesIO

        image = Image.new("RGB", (800, 800), color=(128, 96, 64))
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=95)
        original = buffer.getvalue()

        compressed, media_type = compress_image_for_claude(original, "image/jpeg", max_bytes=4_000)

        self.assertEqual(media_type, "image/jpeg")
        self.assertLessEqual(len(compressed), 4_000)
        self.assertLess(len(compressed), len(original))

    def test_compress_image_for_claude_keeps_small_image_unchanged(self):
        original = b"small-image"

        compressed, media_type = compress_image_for_claude(original, "image/jpeg", max_bytes=100)

        self.assertEqual(compressed, original)
        self.assertEqual(media_type, "image/jpeg")

    def test_extract_tool_json_returns_input_from_tool_use_block(self):
        expected = {
            "composition_analysis": "主體位於三分線附近。",
            "lighting_analysis": "逆光偏強。",
            "color_analysis": "暖色調。",
            "overall_score": 82,
            "lightroom_parameters": {"Exposure": 0.35, "Contrast": 12, "Temp": 5400},
        }
        response = {"content": [{"type": "text", "text": "ok"}, {"type": "tool_use", "name": LIGHTROOM_TOOL_NAME, "input": expected}]}

        self.assertEqual(extract_tool_json(response), expected)

    def test_extract_tool_json_raises_when_tool_use_missing(self):
        with self.assertRaisesRegex(ValueError, "tool_use"):
            extract_tool_json({"content": [{"type": "text", "text": "no tool"}]})

    def test_generate_xmp_contains_standard_lightroom_camera_raw_settings(self):
        analysis = {
            "lightroom_parameters": {
                "Exposure": 0.5,
                "Contrast": 10,
                "Highlights": -35,
                "Shadows": 42,
                "Whites": 8,
                "Blacks": -12,
                "Temp": 5600,
                "Tint": 6,
                "Texture": 11,
                "Clarity": 9,
                "Dehaze": 5,
                "Vibrance": 18,
                "Saturation": -3,
            }
        }

        xmp = generate_xmp(analysis, preset_name="Claude Photo Preset")

        self.assertTrue(xmp.startswith("<?xpacket begin="))
        self.assertIn('crs:PresetType="Normal"', xmp)
        self.assertIn('crs:Name="Claude Photo Preset"', xmp)
        self.assertIn('crs:Exposure2012="+0.50"', xmp)
        self.assertIn('crs:Contrast2012="10"', xmp)
        self.assertIn('crs:Highlights2012="-35"', xmp)
        self.assertIn('crs:Shadow2012="42"', xmp)
        self.assertIn('crs:Temperature="5600"', xmp)

    def test_generate_xmp_does_not_emit_duplicate_attributes(self):
        analysis = {"lightroom_parameters": {"NoiseReduction": 20, "LuminanceSmoothing": 30}}

        xmp = generate_xmp(analysis)

        self.assertEqual(xmp.count("crs:LuminanceSmoothing="), 1)
    def test_call_claude_api_retries_method_not_allowed_with_auto_tool_choice(self):
        first_response = Mock(status_code=400, text='{"error":{"message":"Method Not Allowed"}}')
        first_response.json.return_value = {"error": {"message": "Method Not Allowed"}}
        second_response = Mock(status_code=200)
        second_response.json.return_value = {"content": []}

        with patch("photo_analyzer.requests.post", side_effect=[first_response, second_response]) as post:
            result = call_claude_api(b"fake", "image/jpeg", api_key="test-key")

        self.assertEqual(result, {"content": []})
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args_list[0].kwargs["json"]["tool_choice"], {"type": "tool", "name": LIGHTROOM_TOOL_NAME})
        self.assertEqual(post.call_args_list[1].kwargs["json"]["tool_choice"], {"type": "auto"})
        self.assertIn("You must call", post.call_args_list[1].kwargs["json"]["messages"][0]["content"][1]["text"])

    def test_call_claude_api_raises_readable_anthropic_error(self):
        response = Mock(status_code=400, text='{"error":{"message":"Bad schema"}}')
        response.json.return_value = {"error": {"message": "Bad schema"}}

        with patch("photo_analyzer.requests.post", return_value=response):
            with self.assertRaisesRegex(AnthropicAPIError, "Bad schema"):
                call_claude_api(b"fake", "image/jpeg", api_key="test-key")
    def test_build_claude_request_includes_reference_style_instruction(self):
        payload = build_claude_request(b"fake", "image/jpeg", style_reference="低對比、暖色調、保留底片感")

        text = payload["messages"][0]["content"][1]["text"]
        self.assertIn("參考風格", text)
        self.assertIn("低對比", text)

    def test_build_xmp_zip_contains_one_xmp_per_result(self):
        batch_results = [
            {"filename": "a.jpg", "xmp": "<xmp>a</xmp>"},
            {"filename": "nested/b.png", "xmp": "<xmp>b</xmp>"},
        ]

        zip_bytes = build_xmp_zip(batch_results)

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            self.assertEqual(set(archive.namelist()), {"a.xmp", "b.xmp"})
            self.assertEqual(archive.read("a.xmp").decode("utf-8"), "<xmp>a</xmp>")

    def test_build_portfolio_csv_flattens_scores_and_core_fields(self):
        rows = [
            {
                "filename": "a.jpg",
                "analysis": {
                    "photo_title": "城市晨光裡的飛鳥",
                    "selection_status": "精選",
                    "photo_tags": ["飛鳥", "城市", "晨光"],
                    "composition_analysis": "構圖穩定",
                    "lighting_analysis": "光線柔和",
                    "color_analysis": "色彩溫暖",
                    "overall_score": 88,
                    "lightroom_parameters": {"Exposure": 0.2, "Contrast": 8},
                },
            }
        ]

        csv_text = build_portfolio_csv(rows)
        parsed = list(csv.DictReader(io.StringIO(csv_text)))

        self.assertEqual(parsed[0]["filename"], "a.jpg")
        self.assertEqual(parsed[0]["photo_title"], "城市晨光裡的飛鳥")
        self.assertEqual(parsed[0]["selection_status"], "精選")
        self.assertEqual(parsed[0]["photo_tags"], "飛鳥, 城市, 晨光")
        self.assertEqual(parsed[0]["overall_score"], "88")
        self.assertEqual(parsed[0]["Exposure"], "0.2")
        self.assertIn("構圖穩定", parsed[0]["composition_analysis"])

    def test_build_notion_page_payload_contains_result_summary_and_download_links(self):
        result = {
            "filename": "a.jpg",
            "compressed_image_path": "outputs/a_compressed.jpg",
            "xmp_path": "outputs/a.xmp",
            "xmp": "<xmp>a</xmp>",
            "google_drive_image_url": "https://drive.google.com/uc?export=view&id=file123",
            "google_drive_thumbnail_url": "https://lh3.googleusercontent.com/thumbnail?id=file123&sz=w1600",
            "analysis": {
                "photo_title": "城市晨光裡的飛鳥",
                "selection_status": "精選",
                "photo_tags": ["飛鳥", "城市", "晨光"],
                "composition_analysis": "構圖穩定",
                "lighting_analysis": "光線柔和",
                "color_analysis": "色彩溫暖",
                "overall_score": 88,
                "lightroom_parameters": {"Exposure": 0.2, "Contrast": 8},
            },
        }

        payload = build_notion_page_payload("db123", result)

        self.assertEqual(payload["parent"], {"database_id": "db123"})
        self.assertEqual(payload["properties"]["Name"]["title"][0]["text"]["content"], "城市晨光裡的飛鳥")
        self.assertEqual(payload["properties"]["Score"]["number"], 88)
        self.assertEqual(payload["properties"]["Status"]["select"]["name"], "精選")
        self.assertEqual(payload["properties"]["Tags"]["multi_select"], [{"name": "飛鳥"}, {"name": "城市"}, {"name": "晨光"}])
        self.assertEqual(payload["properties"]["Composition Analysis"]["rich_text"][0]["text"]["content"], "構圖穩定")
        self.assertEqual(payload["properties"]["Lighting Analysis"]["rich_text"][0]["text"]["content"], "光線柔和")
        self.assertEqual(payload["properties"]["Color Analysis"]["rich_text"][0]["text"]["content"], "色彩溫暖")
        self.assertEqual(payload["properties"]["Image URL"]["url"], "https://drive.google.com/uc?export=view&id=file123")
        children_text = json.dumps(payload["children"], ensure_ascii=False)
        self.assertIn("原始檔名：a.jpg", children_text)
        self.assertIn("構圖穩定", children_text)
        self.assertIn("outputs/a_compressed.jpg", children_text)
        self.assertIn("outputs/a.xmp", children_text)
        self.assertIn("篩選狀態：精選", children_text)
        self.assertIn("標籤：飛鳥, 城市, 晨光", children_text)
        image_blocks = [block for block in payload["children"] if block["type"] == "image"]
        self.assertEqual(image_blocks[0]["image"]["external"]["url"], "https://lh3.googleusercontent.com/thumbnail?id=file123&sz=w1600")
        code_blocks = [block for block in payload["children"] if block["type"] == "code"]
        self.assertEqual(code_blocks[-1]["code"]["language"], "xml")
        self.assertIn("<xmp>a</xmp>", code_blocks[-1]["code"]["rich_text"][0]["text"]["content"])

    def test_google_drive_direct_image_url_is_notion_embeddable(self):
        self.assertEqual(
            google_drive_direct_image_url("abc123"),
            "https://drive.google.com/uc?export=view&id=abc123",
        )

    def test_build_notion_page_payload_uses_notion_file_upload_for_preview_image(self):
        result = {
            "filename": "a.jpg",
            "compressed_image_path": "outputs/a_compressed.jpg",
            "notion_file_upload_id": "upload123",
            "analysis": {
                "photo_title": "Photo",
                "overall_score": 88,
                "lightroom_parameters": {},
            },
        }

        payload = build_notion_page_payload("db123", result)

        image_blocks = [block for block in payload["children"] if block["type"] == "image"]
        self.assertEqual(image_blocks[0]["image"]["type"], "file_upload")
        self.assertEqual(image_blocks[0]["image"]["file_upload"]["id"], "upload123")
        self.assertEqual(payload["cover"]["type"], "file_upload")
        self.assertEqual(payload["cover"]["file_upload"]["id"], "upload123")

    def test_build_batch_summary_notion_payload_counts_statuses_and_sets_cover(self):
        results = [
            {
                "filename": "a.jpg",
                "notion_file_upload_id": "cover123",
                "analysis": {
                    "photo_title": "A",
                    "selection_status": "精選",
                    "photo_tags": ["街拍", "暖色"],
                    "overall_score": 92,
                },
            },
            {
                "filename": "b.jpg",
                "analysis": {
                    "photo_title": "B",
                    "selection_status": "可修",
                    "photo_tags": ["街拍"],
                    "overall_score": 71,
                },
            },
        ]

        payload = build_batch_summary_notion_payload("db123", results)
        children_text = json.dumps(payload["children"], ensure_ascii=False)

        self.assertEqual(payload["parent"], {"database_id": "db123"})
        self.assertEqual(payload["properties"]["Type"]["select"]["name"], "Batch Summary")
        self.assertEqual(payload["properties"]["Score"]["number"], 81.5)
        self.assertEqual(payload["cover"]["file_upload"]["id"], "cover123")
        self.assertIn("本批共 2 張", children_text)
        self.assertIn("Top 5 照片", children_text)

    def test_upload_file_to_notion_creates_and_sends_single_part_file_upload(self):
        create_response = Mock(status_code=200)
        create_response.json.return_value = {
            "id": "upload123",
            "upload_url": "https://api.notion.com/v1/file_uploads/upload123/send",
            "status": "pending",
        }
        send_response = Mock(status_code=200)
        send_response.json.return_value = {
            "id": "upload123",
            "filename": "a_compressed.jpg",
            "status": "uploaded",
        }

        with patch("photo_analyzer.requests.post", side_effect=[create_response, send_response]) as post:
            uploaded = upload_file_to_notion("ntn_test", b"jpeg", "a_compressed.jpg")

        self.assertEqual(uploaded["id"], "upload123")
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args_list[0].args[0], "https://api.notion.com/v1/file_uploads")
        self.assertEqual(post.call_args_list[0].kwargs["json"]["mode"], "single_part")
        self.assertIn("files", post.call_args_list[1].kwargs)
        self.assertNotIn("Content-Type", post.call_args_list[1].kwargs["headers"])

    def test_load_google_drive_credentials_supports_authorized_user_json(self):
        try:
            import google.oauth2.credentials  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("google-auth is not installed in this environment")
        token = {
            "type": "authorized_user",
            "client_id": "client-id",
            "client_secret": "client-secret",
            "refresh_token": "refresh-token",
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
            json.dump(token, handle)
            handle.flush()
            credentials, credential_type = load_google_drive_credentials(handle.name)

        self.assertEqual(credential_type, "authorized_user")
        self.assertEqual(credentials.refresh_token, "refresh-token")

    def test_load_google_drive_credentials_supports_google_oauth_token_without_type(self):
        try:
            import google.oauth2.credentials  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("google-auth is not installed in this environment")
        token = {
            "client_id": "client-id",
            "client_secret": "client-secret",
            "refresh_token": "refresh-token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "scopes": ["https://www.googleapis.com/auth/drive"],
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
            json.dump(token, handle)
            handle.flush()
            credentials, credential_type = load_google_drive_credentials(handle.name)

        self.assertEqual(credential_type, "authorized_user")
        self.assertEqual(credentials.refresh_token, "refresh-token")


if __name__ == "__main__":
    unittest.main()
