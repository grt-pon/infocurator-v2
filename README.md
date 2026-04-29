# InfoCurator v2

キャンペーン・プロモーション情報を自動収集し、Claude AIでマーケター向けに分析するツール。  
GitHub Actions で毎朝自動実行 → GitHub Pages で公開。

---

## GitHub Pages 公開手順

### 1. GitHubにリポジトリを作成してプッシュ

```bash
cd infocurator-v2
git init
git add .
git commit -m "first commit"
git branch -M main
git remote add origin https://github.com/<あなたのユーザー名>/<リポジトリ名>.git
git push -u origin main
```

### 2. シークレット変数を設定

GitHubリポジトリの **Settings → Secrets and variables → Actions → New repository secret** で以下を追加：

| シークレット名 | 値 |
|--------------|-----|
| `ANTHROPIC_API_KEY` | Anthropic APIキー（`sk-ant-...`） |

### 3. GitHub Pages を有効化

1. **Settings → Pages** を開く
2. **Source** を `Deploy from a branch` に設定
3. **Branch** を `main` / `/ (root)` に設定
4. **Save** をクリック

数分後に `https://<ユーザー名>.github.io/<リポジトリ名>/` で公開されます。

### 4. 初回データ収集（Actions を手動実行）

1. **Actions タブ** → `Collect & Publish` ワークフロー
2. **Run workflow** ボタンをクリック
3. 完了後、GitHub Pages の URL にアクセスして確認

---

## 自動実行スケジュール

| タイミング | 内容 |
|-----------|------|
| 毎朝 8:00 JST | RSS収集 → AI分析 → `articles.json` と `index.html` を自動コミット |
| 手動 | Actions タブの `Run workflow` ボタン |

---

## ローカルでの実行

### セットアップ

```bash
pip install -r requirements.txt
cp .env.example .env
# .env を開いて ANTHROPIC_API_KEY を設定
```

### 記事収集

```bash
python collect.py
```

実行後、`index.html` をダブルクリックでブラウザ表示できます。

---

## ファイル構成

```
infocurator-v2/
├── .github/
│   └── workflows/
│       └── collect.yml     # GitHub Actions ワークフロー
├── collect.py              # RSS収集 + AI分析 + HTML生成
├── index.html              # フロントエンド（データ埋め込み済み）
├── articles.json           # 収集した記事データ
├── requirements.txt
├── .env                    # APIキー（gitignore済み・コミット禁止）
├── .env.example
└── .gitignore
```

---

## 情報源

| 媒体 | RSS |
|------|-----|
| PR Times | https://prtimes.jp/index.rdf |
| MarkeZine | https://markezine.jp/rss/new/20/index.xml |
| AdverTimes | https://www.advertimes.com/feed/ |

## フィルタリングキーワード

`キャンペーン` / `プロモーション` / `新発売` / `期間限定` / `コラボ` / `タイアップ` / `サンプリング` / `CM` / `広告`

## AI分析（Claude Haiku）

| 項目 | 内容 |
|------|------|
| **What** | 何のキャンペーン・施策か（2文以内） |
| **Why** | なぜこの施策か。企業戦略・業界背景から推測（3〜4文） |
| **So What** | マーケターが自分の仕事に使えるインサイト（2〜3文） |
