"""
Gmail OAuth2 初回認証セットアップスクリプト
==========================================
使い方:
  1. Google Cloud Console で OAuth2 クライアント ID を作成し、
     gmail_credentials.json としてこのフォルダに保存してください。
  2. 以下を実行するとブラウザが開きます：
       python gmail_setup.py
  3. Google アカウントでログインして権限を許可してください。
  4. gmail_token.json が生成されたら、その内容をコピーして
     GitHub の GMAIL_TOKEN シークレットに貼り付けてください。
"""
import os
import sys
import json
from pathlib import Path
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
load_dotenv(override=True)

try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
except ImportError:
    print("ERROR: Google API ライブラリが未インストールです。")
    print("  pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib")
    sys.exit(1)

GMAIL_SCOPES       = ["https://www.googleapis.com/auth/gmail.readonly"]
CREDENTIALS_FILE   = os.getenv("GMAIL_CREDENTIALS_FILE", "gmail_credentials.json")
TOKEN_FILE         = os.getenv("GMAIL_TOKEN_FILE",       "gmail_token.json")
TEST_SENDER        = "xtrend-e@nikkeibp.co.jp"


def main():
    print("=" * 60)
    print("Gmail OAuth2 セットアップ")
    print("=" * 60)

    if not Path(CREDENTIALS_FILE).exists():
        print(f"\nERROR: {CREDENTIALS_FILE} が見つかりません。")
        print(
            "\n手順:\n"
            "  1. https://console.cloud.google.com/ を開く\n"
            "  2. プロジェクトを選択（または新規作成）\n"
            "  3. 「APIとサービス」→「ライブラリ」→ Gmail API を有効化\n"
            "  4. 「APIとサービス」→「認証情報」→「認証情報を作成」\n"
            "     → 「OAuth クライアント ID」→「デスクトップアプリ」\n"
            "  5. ダウンロードした JSON を gmail_credentials.json に名前を変えて保存\n"
        )
        sys.exit(1)

    creds = None
    if Path(TOKEN_FILE).exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, GMAIL_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("アクセストークンを更新中...")
            creds.refresh(Request())
        else:
            print("\nブラウザが開きます。Google アカウントでログインして権限を許可してください...")
            flow  = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, GMAIL_SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
        print(f"\n✓ {TOKEN_FILE} を保存しました")

    # 動作確認
    print("\n接続テスト中...")
    service = build("gmail", "v1", credentials=creds)
    profile = service.users().getProfile(userId="me").execute()
    print(f"✓ ログイン成功: {profile.get('emailAddress')}")

    # 対象メールの確認（受信トレイ・過去7日以内）
    result = service.users().messages().list(
        userId="me",
        q=f"from:{TEST_SENDER} label:INBOX newer_than:7d",
        maxResults=5,
    ).execute()
    count = result.get("resultSizeEstimate", 0)
    print(f"✓ {TEST_SENDER} からのメール（受信トレイ・過去7日）: {count} 件見つかりました")

    print("\n" + "=" * 60)
    print("セットアップ完了！")
    print("=" * 60)
    print(f"\nGitHub Actions でも使う場合は、以下の内容を")
    print(f"GitHub シークレット「GMAIL_TOKEN」に設定してください：\n")

    token_content = Path(TOKEN_FILE).read_text(encoding="utf-8")
    token_json    = json.loads(token_content)
    print(json.dumps(token_json, ensure_ascii=False, indent=2))

    print(f"\n同様に gmail_credentials.json の内容を「GMAIL_CREDENTIALS」にも設定してください。")


if __name__ == "__main__":
    main()
