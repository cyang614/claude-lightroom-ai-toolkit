"""Create an authorized_user OAuth token JSON for Google Drive uploads.

Usage:
    python3 create_google_drive_oauth_token.py /path/to/oauth_desktop_client_secret.json

The input must be an OAuth 2.0 Client ID JSON downloaded from Google Cloud
Console with application type "Desktop app". The output token is written to
./google_drive_oauth_token.json and can be used in the Streamlit sidebar as the
Google credentials JSON path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

SCOPES = ["https://www.googleapis.com/auth/drive"]
OUTPUT_PATH = Path("google_drive_oauth_token.json")


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _validate_oauth_client_secret(path: Path) -> bool:
    """Return True only for Google OAuth web/installed client secret JSON."""
    config = _load_json(path)
    if not isinstance(config, dict):
        print(f"不是有效的 JSON 檔案：{path}", file=sys.stderr)
        return False

    if config.get("type") == "service_account" or "private_key" in config:
        print(
            "你提供的是 Service Account JSON，不是 OAuth Desktop app client secret。\n\n"
            "這個工具需要的是 Google Cloud Console 下載的 OAuth 2.0 Client ID JSON：\n"
            "1. 到 https://console.cloud.google.com/apis/credentials\n"
            "2. Create Credentials → OAuth client ID\n"
            "3. Application type 選 Desktop app\n"
            "4. 下載該 OAuth client JSON\n"
            "5. 再執行：python3 create_google_drive_oauth_token.py /path/to/oauth_client_secret.json\n\n"
            "原因：Service Account 上傳到一般 My Drive 會遇到 Google 的 storage quota 限制；"
            "OAuth token 才會使用你的個人 Google Drive quota。",
            file=sys.stderr,
        )
        return False

    if "installed" in config:
        return True
    if "web" in config:
        print(
            "偵測到 Web OAuth client JSON。這可能可以使用，但建議改建立 Application type = Desktop app。",
            file=sys.stderr,
        )
        return True

    print(
        "這不是 Google OAuth client secret JSON。正確檔案最外層應該包含 'installed' 或 'web' 欄位。\n"
        "請到 Google Cloud Console → APIs & Services → Credentials → Create Credentials → OAuth client ID → Desktop app 下載。",
        file=sys.stderr,
    )
    return False


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python3 create_google_drive_oauth_token.py /path/to/oauth_desktop_client_secret.json", file=sys.stderr)
        return 2

    client_secret_path = Path(sys.argv[1]).expanduser()
    if not client_secret_path.exists():
        print(f"Client secret JSON not found: {client_secret_path}", file=sys.stderr)
        return 2

    if not _validate_oauth_client_secret(client_secret_path):
        return 2

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ModuleNotFoundError:
        print("Missing dependency: google-auth-oauthlib. Run: pip install -r requirements.txt", file=sys.stderr)
        return 2

    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), SCOPES)
    except ValueError as exc:
        print(
            f"無法載入 OAuth client secret：{exc}\n"
            "請確認下載的是 OAuth 2.0 Client ID（Desktop app），不是 Service Account key。",
            file=sys.stderr,
        )
        return 2

    credentials = flow.run_local_server(port=0)
    OUTPUT_PATH.write_text(credentials.to_json(), encoding="utf-8")

    token = json.loads(credentials.to_json())
    print(f"Created OAuth authorized_user token: {OUTPUT_PATH.resolve()}")
    print(f"Token type: {token.get('type', 'authorized_user')}")
    print("Use this file path in the Streamlit sidebar: Google credentials JSON 路徑")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
