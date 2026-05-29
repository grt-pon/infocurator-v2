import os
import re
import sys
import json
import time
import base64
import argparse
import feedparser
import requests
import anthropic
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from dotenv import load_dotenv
from bs4 import BeautifulSoup

try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build as gmail_build
    GMAIL_AVAILABLE = True
except ImportError:
    GMAIL_AVAILABLE = False

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
load_dotenv(override=True)

# ── RSS情報源 ─────────────────────────────────────────────────
RSS_FEEDS = {
    "MarkeZine":  "https://markezine.jp/rss/new/20/index.xml",
    "AdverTimes": "https://www.advertimes.com/feed/",
    "DIGIDAY":    "https://digiday.jp/feed/",
    "ITmedia":    "https://rss.itmedia.co.jp/rss/2.0/marketing.xml",
}

# RSS用フィルタリングキーワード
KEYWORDS = [
    "キャンペーン", "プロモーション", "新発売", "期間限定",
    "コラボ", "タイアップ", "サンプリング", "CM", "広告",
    "マーケティング", "インフルエンサー", "ブランド", "SNS",
    "市場", "戦略", "メディア", "デジタル", "リテール",
]

PAYWALL_SIGNALS = [
    "会員登録をして続きを読む",
    "有料記事",
    "購読が必要",
    "会員限定",
    "ログインして続きを読む",
]

# ── Gmail（日経クロストレンド）設定 ───────────────────────────
GMAIL_SENDER  = "xtrend-e@nikkeibp.co.jp"
GMAIL_SCOPES  = ["https://www.googleapis.com/auth/gmail.readonly"]
NIKKEI_SOURCE = "日経クロストレンド"
NIKKEI_KEYWORDS = [
    "キャンペーン", "プロモーション", "新発売", "コラボ",
    "ブランド", "消費", "広告", "CM", "ヒット", "マーケ",
    "トレンド", "ランキング",
]

# ── ファイルパス・モデル ──────────────────────────────────────
OUTPUT_FILE           = Path("articles.json")
PROCESSED_EMAILS_FILE = Path("processed_emails.json")
HTML_FILE             = Path("index.html")
MODEL                 = "claude-haiku-4-5-20251001"   # RSS分析（安価）
MODEL_RELEVANCE       = "claude-haiku-4-5-20251001"   # フィルタ①②（安価）
MODEL_ANALYSIS        = "claude-sonnet-4-5-20251001"  # Gmail 本格分析（高精度）

FETCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

_ANALYSIS_SCHEMA = """{
  "skip": false,
  "what": "何をやった施策か。企業名・商品名・具体的な施策内容を含めて2文以内",
  "why": "なぜこの施策をやったか。業界の文脈・競合状況・企業の戦略的背景から推測。抽象論ではなく具体的に3〜4文",
  "so_what": "この施策から抽象化して、自分の企画・マーケに使える視点や問いを2〜3文。『〇〇という施策は△△という状況で有効』という形で書く"
}"""

_ANALYSIS_INSTRUCTIONS = """あなたはマーケティング実務家です。
以下の記事について分析してください。

【重要な前提】
- 「スキルの磨き方」「ハウツー」「ツール紹介」「広告・PR記事」は分析不要。
  その場合は {"skip": true, "reason": "スキップ理由"} のみ返してください。
- 表面的な要約ではなく、実務で使える具体的な洞察を書いてください。
- 「〇〇が重要です」「△△が必要です」のような抽象論は避けてください。

以下のJSON形式のみで回答してください：
"""

SYSTEM_PROMPT = _ANALYSIS_INSTRUCTIONS + _ANALYSIS_SCHEMA

WEB_SEARCH_SYSTEM_PROMPT = """あなたはマーケティング実務家です。
与えられた記事タイトルをWeb検索して関連情報を収集し、分析してください。

【重要な前提】
- 「スキルの磨き方」「ハウツー」「ツール紹介」「広告・PR記事」は分析不要。
  その場合は {"skip": true, "reason": "スキップ理由"} のみ返してください。
- 表面的な要約ではなく、実務で使える具体的な洞察を書いてください。
- 「〇〇が重要です」「△△が必要です」のような抽象論は避けてください。

以下のJSON形式のみで回答してください：
""" + _ANALYSIS_SCHEMA

# ── HTMLテンプレート ──────────────────────────────────────────
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>InfoCurator v2 – キャンペーン情報収集</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    :root {
      --bg: #f4f6f9;
      --card-bg: #ffffff;
      --primary: #2563eb;
      --primary-light: #eff6ff;
      --text: #1e293b;
      --muted: #64748b;
      --border: #e2e8f0;
      --tag-mz: #0891b2;
      --tag-at: #059669;
      --tag-dg: #7c3aed;
      --tag-it: #ea580c;
      --tag-nk: #003288;
      --radius: 12px;
      --shadow: 0 2px 12px rgba(0,0,0,0.08);
    }

    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Hiragino Sans", sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
    }

    header {
      background: linear-gradient(135deg, #1e3a8a 0%, #2563eb 100%);
      color: white;
      padding: 20px 24px;
      position: sticky;
      top: 0;
      z-index: 100;
      box-shadow: 0 2px 8px rgba(0,0,0,0.2);
    }

    .header-inner {
      max-width: 1200px;
      margin: 0 auto;
      display: flex;
      align-items: center;
      gap: 16px;
      flex-wrap: wrap;
    }

    .logo { font-size: 1.3rem; font-weight: 700; letter-spacing: -0.02em; white-space: nowrap; }
    .logo span { color: #93c5fd; }

    .controls { display: flex; gap: 10px; flex: 1; flex-wrap: wrap; }

    input[type="search"], select {
      padding: 8px 14px;
      border: none;
      border-radius: 8px;
      font-size: 0.9rem;
      background: rgba(255,255,255,0.15);
      color: white;
      outline: none;
      transition: background 0.2s;
    }

    input[type="search"]::placeholder { color: rgba(255,255,255,0.6); }
    input[type="search"] { flex: 1; min-width: 160px; }
    select { cursor: pointer; min-width: 160px; }
    select option { background: #1e3a8a; color: white; }
    input[type="search"]:focus, select:focus { background: rgba(255,255,255,0.25); }

    .count-badge {
      font-size: 0.8rem;
      background: rgba(255,255,255,0.2);
      padding: 4px 12px;
      border-radius: 20px;
      white-space: nowrap;
    }

    .updated { font-size: 0.72rem; opacity: 0.6; white-space: nowrap; }

    main { max-width: 1200px; margin: 28px auto; padding: 0 16px; }

    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
      gap: 20px;
    }

    .card {
      background: var(--card-bg);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      overflow: hidden;
      display: flex;
      flex-direction: column;
      transition: transform 0.15s, box-shadow 0.15s;
    }

    .card:hover { transform: translateY(-3px); box-shadow: 0 6px 24px rgba(0,0,0,0.12); }

    .card-header { padding: 16px 18px 12px; border-bottom: 1px solid var(--border); }

    .meta { display: flex; align-items: center; gap: 6px; margin-bottom: 8px; flex-wrap: wrap; }

    .source-tag {
      font-size: 0.7rem;
      font-weight: 700;
      letter-spacing: 0.04em;
      padding: 2px 8px;
      border-radius: 4px;
      color: white;
    }

    .source-tag.markezine  { background: var(--tag-mz); }
    .source-tag.advertimes { background: var(--tag-at); }
    .source-tag.digiday    { background: var(--tag-dg); }
    .source-tag.itmedia    { background: var(--tag-it); }
    .source-tag.nikkei     { background: var(--tag-nk); }

    .web-search-badge {
      font-size: 0.68rem;
      font-weight: 600;
      padding: 2px 7px;
      border-radius: 4px;
      background: #fef9c3;
      color: #854d0e;
      border: 1px solid #fde047;
      white-space: nowrap;
    }

    .date { font-size: 0.75rem; color: var(--muted); }

    .card-title { font-size: 0.95rem; font-weight: 600; line-height: 1.5; }
    .card-title a { color: inherit; text-decoration: none; transition: color 0.15s; }
    .card-title a:hover { color: var(--primary); }

    .analysis { padding: 8px 0; }

    .accordion-item { border-bottom: 1px solid var(--border); }
    .accordion-item:last-child { border-bottom: none; }

    .accordion-btn {
      width: 100%;
      background: none;
      border: none;
      padding: 10px 18px;
      display: flex;
      align-items: center;
      gap: 8px;
      cursor: pointer;
      font-size: 0.82rem;
      font-weight: 600;
      color: var(--muted);
      text-align: left;
      transition: background 0.15s, color 0.15s;
    }

    .accordion-btn:hover { background: var(--bg); color: var(--text); }
    .accordion-btn.open  { color: var(--primary); background: var(--primary-light); }

    .label-icon {
      width: 20px; height: 20px;
      border-radius: 50%;
      display: flex; align-items: center; justify-content: center;
      font-size: 0.7rem; font-weight: 700; flex-shrink: 0;
    }

    .what-icon   { background: #fef3c7; color: #d97706; }
    .why-icon    { background: #ede9fe; color: #7c3aed; }
    .sowhat-icon { background: #d1fae5; color: #059669; }

    .chevron { margin-left: auto; transition: transform 0.25s; font-size: 0.75rem; color: var(--muted); }
    .accordion-btn.open .chevron { transform: rotate(180deg); }

    .accordion-body { max-height: 0; overflow: hidden; transition: max-height 0.3s ease; }
    .accordion-body.open { max-height: 400px; }

    .accordion-body p {
      padding: 4px 18px 14px;
      font-size: 0.85rem;
      line-height: 1.7;
      color: var(--text);
    }

    .empty-state { grid-column: 1/-1; text-align: center; padding: 80px 20px; color: var(--muted); }
    .empty-state h3 { font-size: 1.1rem; margin-bottom: 8px; }
    .empty-state p  { font-size: 0.9rem; }

    @media (max-width: 600px) {
      .grid { grid-template-columns: 1fr; }
      .logo { font-size: 1.1rem; }
    }
  </style>
</head>
<body>

<header>
  <div class="header-inner">
    <div class="logo">Info<span>Curator</span> <span style="opacity:0.6;font-weight:400;">v2</span></div>
    <div class="controls">
      <input type="search" id="search" placeholder="キーワード検索..." />
      <select id="sourceFilter">
        <option value="">すべての情報源</option>
        <option value="MarkeZine">MarkeZine</option>
        <option value="AdverTimes">AdverTimes</option>
        <option value="DIGIDAY">DIGIDAY</option>
        <option value="ITmedia">ITmedia</option>
        <option value="日経クロストレンド">日経クロストレンド</option>
      </select>
    </div>
    <div class="count-badge" id="countBadge">0 件</div>
    <div class="updated">更新: __UPDATED__</div>
  </div>
</header>

<main>
  <div class="grid" id="grid"></div>
</main>

<script>
const ARTICLES_DATA = __ARTICLES_JSON__;

let allArticles = ARTICLES_DATA.map((a, i) => ({ ...a, _id: i }));

function sourceClass(source) {
  const map = {
    'MarkeZine':      'markezine',
    'AdverTimes':     'advertimes',
    'DIGIDAY':        'digiday',
    'ITmedia':        'itmedia',
    '日経クロストレンド': 'nikkei',
  };
  return map[source] || '';
}

function formatDate(raw) {
  if (!raw) return '';
  try {
    const d = new Date(raw);
    if (isNaN(d)) return raw;
    return d.toLocaleDateString('ja-JP', { year: 'numeric', month: 'short', day: 'numeric' });
  } catch { return raw; }
}

function createCard(article) {
  const card = document.createElement('div');
  card.className = 'card';

  const sections = [
    { key: 'what',    label: 'What',    iconClass: 'what-icon',    icon: 'W' },
    { key: 'why',     label: 'Why',     iconClass: 'why-icon',     icon: 'W' },
    { key: 'so_what', label: 'So What', iconClass: 'sowhat-icon',  icon: 'S' },
  ];

  const accordionHTML = sections.map(({ key, label, iconClass, icon }) => {
    const text = (article[key] || '情報なし').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return `
      <div class="accordion-item">
        <button class="accordion-btn" data-key="${key}">
          <span class="label-icon ${iconClass}">${icon}</span>
          ${label}
          <span class="chevron">&#9660;</span>
        </button>
        <div class="accordion-body" id="body-${key}-${article._id}">
          <p>${text.replace(/\\n/g, '<br>')}</p>
        </div>
      </div>`;
  }).join('');

  const safeTitle = article.title.replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const webBadge = article.web_searched
    ? '<span class="web-search-badge">&#128269; Web検索で分析</span>'
    : '';

  card.innerHTML = `
    <div class="card-header">
      <div class="meta">
        <span class="source-tag ${sourceClass(article.source)}">${article.source}</span>
        ${webBadge}
        <span class="date">${formatDate(article.published)}</span>
      </div>
      <div class="card-title">
        <a href="${article.url}" target="_blank" rel="noopener">${safeTitle}</a>
      </div>
    </div>
    <div class="analysis">${accordionHTML}</div>`;

  card.querySelectorAll('.accordion-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const body = document.getElementById(`body-${btn.dataset.key}-${article._id}`);
      btn.classList.toggle('open');
      body.classList.toggle('open');
    });
  });

  return card;
}

function render(articles) {
  const grid = document.getElementById('grid');
  grid.innerHTML = '';
  document.getElementById('countBadge').textContent = articles.length + ' 件';

  if (articles.length === 0) {
    grid.innerHTML = `
      <div class="empty-state">
        <h3>記事が見つかりません</h3>
        <p>検索条件を変えるか、python collect.py を実行してください。</p>
      </div>`;
    return;
  }

  const fragment = document.createDocumentFragment();
  articles.forEach(a => fragment.appendChild(createCard(a)));
  grid.appendChild(fragment);
}

function filter() {
  const query  = document.getElementById('search').value.toLowerCase();
  const source = document.getElementById('sourceFilter').value;
  const filtered = allArticles.filter(a => {
    const matchSource = !source || a.source === source;
    const matchQuery  = !query ||
      a.title.toLowerCase().includes(query) ||
      (a.what    || '').toLowerCase().includes(query) ||
      (a.why     || '').toLowerCase().includes(query) ||
      (a.so_what || '').toLowerCase().includes(query);
    return matchSource && matchQuery;
  });
  render(filtered);
}

document.getElementById('search').addEventListener('input', filter);
document.getElementById('sourceFilter').addEventListener('change', filter);

render(allArticles);
</script>
</body>
</html>"""


# ── データ入出力 ──────────────────────────────────────────────

def load_existing_articles() -> list[dict]:
    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []


def save_articles(articles: list[dict]) -> None:
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)


def load_processed_emails() -> set[str]:
    if PROCESSED_EMAILS_FILE.exists():
        with open(PROCESSED_EMAILS_FILE, encoding="utf-8-sig") as f:  # BOM付きUTF-8も許容
            return set(json.load(f))
    return set()


def save_processed_emails(ids: set[str]) -> None:
    with open(PROCESSED_EMAILS_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(ids), f, ensure_ascii=False, indent=2)


def build_html(articles: list[dict]) -> None:
    updated = datetime.now().strftime("%Y/%m/%d %H:%M")
    articles_json = json.dumps(articles, ensure_ascii=False)
    html = (HTML_TEMPLATE
            .replace("__ARTICLES_JSON__", articles_json)
            .replace("__UPDATED__", updated))
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"index.html を更新しました（{len(articles)} 件埋め込み）")


# ── フィルタリング ────────────────────────────────────────────

def matches_keywords(title: str, keyword_list: list[str] = None) -> bool:
    kws = keyword_list if keyword_list is not None else KEYWORDS
    return any(kw in title for kw in kws)


def is_similar_title(title: str, existing_titles: set[str], threshold: float = 0.8) -> bool:
    """既存タイトルと80%以上一致したら重複とみなす"""
    for existing in existing_titles:
        if SequenceMatcher(None, title, existing).ratio() >= threshold:
            return True
    return False


# ── 有料記事の判定 ────────────────────────────────────────────

def is_paywalled(url: str) -> bool:
    try:
        r = requests.get(url, headers=FETCH_HEADERS, timeout=8, allow_redirects=True)
        return any(signal in r.text for signal in PAYWALL_SIGNALS)
    except Exception:
        return False


# ── AI 分析 ───────────────────────────────────────────────────

def _extract_json(text: str) -> dict:
    """レスポンステキストから JSON オブジェクトを抽出してパース"""
    text = text.strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    start = text.find("{")
    end   = text.rfind("}") + 1
    if start != -1 and end > start:
        text = text[start:end]
    return json.loads(text)


def _extract_json_list(text: str) -> list:
    """レスポンステキストから JSON 配列を抽出してパース"""
    text = text.strip()
    if "```" in text:
        parts = text.split("```")
        for part in parts[1:]:
            if part.startswith("json"):
                part = part[4:]
            part = part.strip()
            if part.startswith("["):
                text = part
                break
    start = text.find("[")
    end   = text.rfind("]") + 1
    if start != -1 and end > start:
        text = text[start:end]
    return json.loads(text)


def analyze_normal(
    client: anthropic.Anthropic, title: str, summary: str, source: str,
    model: str = MODEL,
) -> dict:
    """通常記事：テキストで分析"""
    user_message = (
        f"情報源: {source}\n"
        f"タイトル: {title}\n"
        f"概要: {summary[:500] if summary else '（概要なし）'}"
    )
    message = client.messages.create(
        model=model,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    return _extract_json(message.content[0].text)


def analyze_with_web_search(
    client: anthropic.Anthropic, title: str, source: str,
    model: str = MODEL,
) -> dict:
    """有料記事・本文不足：web_search ツールで関連情報を収集して分析"""
    user_message = (
        f"「{title}」（情報源: {source}）について検索し、"
        "マーケティング施策として分析してください。"
        "検索結果をもとに、指定のJSON形式のみで回答してください。"
    )
    response = client.beta.messages.create(
        model=model,
        max_tokens=2048,
        system=WEB_SEARCH_SYSTEM_PROMPT,
        tools=[{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 3,
        }],
        messages=[{"role": "user", "content": user_message}],
        betas=["web-search-2025-03-05"],
    )
    for block in reversed(response.content):
        if hasattr(block, "text") and block.text.strip():
            return _extract_json(block.text)
    raise ValueError("web_search 分析でテキスト応答が得られませんでした")


# ── フィルタ①: AI一括フィルタ ────────────────────────────────

def batch_filter_titles(client: anthropic.Anthropic, titles: list[str]) -> set[str]:
    """
    全タイトルを1回のHaiku呼び出しで一括フィルタリング。
    マーケ実務で参考になる記事タイトルのセットを返す。
    パース失敗時は全タイトルを返す（安全側に倒す）。
    """
    if not titles:
        return set()

    titles_text = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(titles))
    prompt = (
        "以下の記事タイトルリストについて、マーケ・企画担当者が実務で参考にできる"
        "具体的な施策・事例・市場トレンドの記事かどうかを判断してください。\n\n"
        "除外する条件（いずれか該当すれば除外）：\n"
        "・アンケート・調査協力依頼（「ご協力お願いします」など）\n"
        "・ツール紹介・講座・セミナー・ウェビナー案内\n"
        "・メディア自社のお知らせ・周年記念\n"
        "・スキル論・ハウツー・本の紹介・キャリア論\n"
        "・抽象論のみで具体的な施策・事例がない記事\n"
        "・特定人物への言及・インタビュー企画の案内\n\n"
        f"タイトルリスト：\n{titles_text}\n\n"
        "「関連あり」と判断した記事の番号のみを、カンマ区切りで返してください。\n"
        "他のテキストは一切不要です。\n"
        "例: 1,3,5,8,12"
    )

    try:
        message = client.messages.create(
            model=MODEL_RELEVANCE,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        response_text = message.content[0].text.strip()
        # "1,3,5" または "1, 3, 5" 形式をパース
        relevant_indices = set()
        for part in re.split(r"[,\s]+", response_text):
            part = part.strip()
            if part.isdigit():
                idx = int(part) - 1  # 1始まりを0始まりに変換
                if 0 <= idx < len(titles):
                    relevant_indices.add(idx)
        return {titles[i] for i in relevant_indices}
    except Exception as e:
        print(f"  ⚠️  AI一括フィルタのパースに失敗: {e}")
        print("  → 全タイトルを通過させます（安全側）")
        return set(titles)


# ── フィルタ②: Gmail 関連性チェック ──────────────────────────

def check_relevance(client: anthropic.Anthropic, title: str) -> bool:
    """フィルタ②: ぐるっとポン視点での関連性をHaikuで判定（API1回・低コスト）"""
    prompt = (
        f"記事タイトル: 「{title}」\n\n"
        f"このタイトルのカテゴリを1〜6から選んでください。\n"
        f"1: B2B・SaaS・法人向けサービス\n"
        f"2: テレビ・ラジオ・Podcast・出版・メディア業界\n"
        f"3: 金融・保険・不動産・医療\n"
        f"4: マーケターのキャリア・スキル・業界展望の論評\n"
        f"5: 海外事例のみ（日本市場と無関係）\n"
        f"6: 消費財・小売・アプリ・生活者向けマーケ施策（上記以外）\n\n"
        f"カテゴリ番号が1〜5なら {{\"relevant\": false}}、6なら {{\"relevant\": true}} を返してください。\n"
        f'{{\"relevant\": true}} か {{\"relevant\": false}} のみ返してください。'
    )
    try:
        message = client.messages.create(
            model=MODEL_RELEVANCE,
            max_tokens=64,
            messages=[{"role": "user", "content": prompt}],
        )
        data = _extract_json(message.content[0].text)
        return bool(data.get("relevant", False))
    except Exception:
        return True  # 判定失敗時は通過させて本格分析へ


# ── Gmail 認証 ────────────────────────────────────────────────

def get_gmail_service():
    """Gmail API サービスを取得。初回はブラウザでOAuth認証を行う。"""
    if not GMAIL_AVAILABLE:
        raise RuntimeError(
            "Google API ライブラリが未インストールです。"
            "pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib"
        )

    credentials_file = os.getenv("GMAIL_CREDENTIALS_FILE", "gmail_credentials.json")
    token_file       = os.getenv("GMAIL_TOKEN_FILE",       "gmail_token.json")

    if not Path(credentials_file).exists():
        raise FileNotFoundError(
            f"Gmail認証ファイルが見つかりません: {credentials_file}\n"
            "README の「Gmail APIセットアップ」を参照してください。"
        )

    creds = None
    if Path(token_file).exists():
        creds = Credentials.from_authorized_user_file(token_file, GMAIL_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if os.getenv("CI"):
                raise RuntimeError(
                    "CI環境での初回Gmail認証はできません。"
                    "ローカルで python gmail_setup.py を実行し、"
                    "gmail_token.json の内容を GMAIL_TOKEN シークレットに設定してください。"
                )
            flow = InstalledAppFlow.from_client_secrets_file(credentials_file, GMAIL_SCOPES)
            creds = flow.run_local_server(port=0)

        with open(token_file, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    return gmail_build("gmail", "v1", credentials=creds)


# ── メールパース ──────────────────────────────────────────────

def _extract_email_html(msg: dict) -> str | None:
    """Gmail API メッセージオブジェクトから HTML ボディを再帰的に取得"""
    def _find_html(part: dict) -> str | None:
        if part.get("mimeType") == "text/html":
            data = part.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        for sub in part.get("parts", []):
            result = _find_html(sub)
            if result:
                return result
        return None

    return _find_html(msg["payload"])


def _extract_surrounding_text(a_tag) -> str:
    """<a> タグ周辺の説明文テキストを抽出（最大500文字）"""
    parent = a_tag.parent
    if parent is None:
        return ""

    # 同じ親要素内でリンク以外のテキストを集める
    parts = []
    for child in parent.children:
        if child == a_tag:
            continue
        text = child.get_text(strip=True) if hasattr(child, "get_text") else str(child).strip()
        if text:
            parts.append(text)

    surrounding = " ".join(parts).strip()

    # 親が短すぎる場合は祖父要素のテキストを補完
    if len(surrounding) < 30 and parent.parent:
        link_text    = a_tag.get_text(strip=True)
        grandp_text  = parent.parent.get_text(" ", strip=True)
        surrounding  = grandp_text.replace(link_text, "").strip()

    return surrounding[:500]


def _clean_nikkei_title(raw: str) -> str:
    """日経クロストレンドのリンクテキストからノイズを除去してタイトルを返す"""
    title = raw

    # 末尾の「» 記事を読む」「» 動画を見る」などを除去
    title = re.sub(r'[»›]\s*(記事を読む|動画を見る|続きを読む).*$', '', title)
    # カテゴリラベル「マーケ・消費」などを除去
    title = re.sub(r'マーケ・消費\s*$', '', title)
    # 末尾の「»」「›」を除去
    title = re.sub(r'[»›]\s*$', '', title)
    # 先頭のセクション見出し（「テーマ別まとめ記事」など）を除去
    title = re.sub(r'^(テーマ別まとめ記事|本日の|【[^】]*】)', '', title)

    return title.strip()


def parse_nikkei_email(html: str) -> list[dict]:
    """日経クロストレンドメールの HTML から記事リストを抽出"""
    soup = BeautifulSoup(html, "html.parser")
    seen_urls   = set()
    seen_titles = set()   # タイトル先頭20文字で重複チェック
    articles    = []

    # スキップするリンクテキスト（完全一致）
    SKIP_TEXTS = {
        "購読する", "ログイン", "登録", "解除", "配信停止",
        "お問い合わせ", "プライバシーポリシー", "利用規約",
        "メルマガ登録", "バックナンバー", "会員登録",
        "本日の最新記事一覧はこちら", "この記事を読む",
        "詳細はこちら", "続きを読む", "もっと見る",
        "ウェブで表示", "記事を読む", "動画を見る",
        "日経IDのパスワードをお忘れの方",
        "日経クロストレンドに関するよくある質問、お問い合わせ",
    }

    for a in soup.find_all("a", href=True):
        url       = a["href"].strip()
        raw_title = a.get_text(strip=True)
        title     = _clean_nikkei_title(raw_title)

        # バリデーション
        if not title or len(title) < 10:
            continue
        if not url.startswith("http"):
            continue
        if raw_title in SKIP_TEXTS or title in SKIP_TEXTS:
            continue
        # 広告・PR リンクを除外（URLに _ADV_ などのパターン）
        if "広告" in title and len(title) < 20:
            continue

        # URL・タイトル重複除外
        if url in seen_urls:
            continue
        title_key = title[:20]
        if title_key in seen_titles:
            continue

        seen_urls.add(url)
        seen_titles.add(title_key)
        surrounding = _extract_surrounding_text(a)
        articles.append({"title": title, "url": url, "text": surrounding})

    return articles


# ── Gmail 記事収集 ────────────────────────────────────────────

def fetch_gmail_articles(
    client: anthropic.Anthropic,
    existing_urls: set[str],
    existing_titles: set[str],
    processed_email_ids: set[str],
    limit: int | None = None,
) -> list[dict]:
    """日経クロストレンドのメールから記事を収集・分析して返す"""

    # Gmail認証ファイルが存在しない場合はスキップ
    credentials_file = os.getenv("GMAIL_CREDENTIALS_FILE", "gmail_credentials.json")
    token_file       = os.getenv("GMAIL_TOKEN_FILE",       "gmail_token.json")

    if not Path(credentials_file).exists() and not Path(token_file).exists():
        print(f"  Gmail認証ファイル未設定のためスキップします")
        print(f"  セットアップ: python gmail_setup.py")
        return []

    try:
        service = get_gmail_service()
    except Exception as e:
        print(f"  Gmail APIエラー: {e}")
        return []

    # 送信元・受信トレイ・過去7日以内に絞って検索
    try:
        result = service.users().messages().list(
            userId="me",
            q=f"from:{GMAIL_SENDER} label:INBOX newer_than:7d",
            maxResults=50,
        ).execute()
    except Exception as e:
        print(f"  メール一覧取得エラー: {e}")
        return []

    messages = result.get("messages", [])

    # ── 第1パス: 全メールから候補を収集 ─────────────────────────
    gmail_candidates = []   # {"title", "url", "text", "date", "msg_id"}
    seen_msg_ids     = []   # 今回処理したメールID（処理済みマーク用）

    for msg_ref in messages:
        msg_id = msg_ref["id"]
        if msg_id in processed_email_ids:
            continue

        try:
            msg = service.users().messages().get(
                userId="me", id=msg_id, format="full"
            ).execute()
        except Exception as e:
            print(f"  メール取得エラー ({msg_id}): {e}")
            continue

        headers   = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
        date_str  = headers.get("Date", "")
        subject   = headers.get("Subject", "(件名なし)")
        print(f"  メール: {subject[:50]}")

        html_body = _extract_email_html(msg)
        if not html_body:
            print(f"    HTMLボディなし → スキップ")
            processed_email_ids.add(msg_id)
            continue

        articles_data = parse_nikkei_email(html_body)
        print(f"    記事候補: {len(articles_data)} 件")

        seen_msg_ids.append(msg_id)

        for article_data in articles_data:
            title = article_data["title"]
            url   = article_data["url"]
            text  = article_data["text"]

            if url in existing_urls:
                continue
            if not matches_keywords(title, NIKKEI_KEYWORDS):
                continue
            if is_similar_title(title, existing_titles):
                continue

            gmail_candidates.append({
                "title":  title,
                "url":    url,
                "text":   text,
                "date":   date_str,
                "msg_id": msg_id,
            })
            existing_titles.add(title)  # 重複防止のため先に登録

    if not gmail_candidates:
        for msg_id in seen_msg_ids:
            processed_email_ids.add(msg_id)
        return []

    # ── フィルタ①: AI一括フィルタ ─────────────────────────────
    all_titles = [c["title"] for c in gmail_candidates]
    print(f"\n  [AI一括フィルタ] Gmail {len(all_titles)}件をHaikuで判定中...")
    relevant_titles = batch_filter_titles(client, all_titles)

    f1_excluded = [c for c in gmail_candidates if c["title"] not in relevant_titles]
    f1_passed   = [c for c in gmail_candidates if c["title"] in relevant_titles]

    for c in f1_excluded:
        print(f"    [AI除外①] {c['title'][:60]}")
        existing_urls.add(c["url"])

    # --limit が指定されている場合は上位 N 件に絞る
    if limit is not None:
        f1_passed = f1_passed[:limit]

    print(f"  フィルタ①: {len(f1_excluded)}件除外 / {len(f1_passed)}件通過"
          + (f"（上限 {limit} 件）" if limit is not None else ""))

    # ── フィルタ②: Haikuで関連性を判定 + 本格分析 ────────────
    new_articles = []
    cnt_f2_skip  = 0
    cnt_analyzed = 0

    for candidate in f1_passed:
        title    = candidate["title"]
        url      = candidate["url"]
        text     = candidate["text"]
        date_str = candidate["date"]

        # フィルタ②: ぐるっとポン関連性チェック
        if not check_relevance(client, title):
            cnt_f2_skip += 1
            print(f"    フィルタ②非関連: {title[:50]}")
            existing_urls.add(url)
            continue

        # 本格分析: Web検索 → Sonnetで分析
        cnt_analyzed += 1
        use_web_search = len(text) < 100
        print(f"    処理中: {title[:55]}...")
        if use_web_search:
            print(f"    本文不足 → Web検索で補完分析（Sonnet）")

        try:
            if use_web_search:
                analysis = analyze_with_web_search(
                    client, title, NIKKEI_SOURCE, model=MODEL_ANALYSIS
                )
            else:
                analysis = analyze_normal(
                    client, title, text, NIKKEI_SOURCE, model=MODEL_ANALYSIS
                )
        except Exception as e:
            print(f"    分析エラー: {e}")
            time.sleep(30)
            continue

        if analysis.get("skip"):
            print(f"    スキップ（AI判定）: {analysis.get('reason', '')}")
            existing_urls.add(url)
            time.sleep(30)
            continue

        article = {
            "url":          url,
            "title":        title,
            "source":       NIKKEI_SOURCE,
            "published":    date_str,
            "collected_at": datetime.now().isoformat(),
            "web_searched": use_web_search,
            "what":         analysis.get("what", ""),
            "why":          analysis.get("why", ""),
            "so_what":      analysis.get("so_what", ""),
        }

        new_articles.append(article)
        existing_urls.add(url)
        print(f"    ✓ 追加完了{'（Web検索）' if use_web_search else ''}")
        time.sleep(30)

    print(f"\n  ▶ フィルタ①で{len(f1_excluded)}件除外、"
          f"フィルタ②で{cnt_f2_skip}件除外、"
          f"{cnt_analyzed}件を本格分析")

    # 処理済みとしてマーク
    for msg_id in seen_msg_ids:
        processed_email_ids.add(msg_id)

    return new_articles


# ── ドライラン ────────────────────────────────────────────────

def dry_run() -> None:
    """
    --dry-run モード:
      RSS / Gmail からタイトルを取得し、AI一括フィルタ①を実行して結果を表示。
      本格分析（Web検索・Sonnet）は一切呼ばない。
      ※ Haiku API 1回分の課金が発生します。
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("⚠️  ANTHROPIC_API_KEY が未設定です。AI一括フィルタをスキップします。")
        client = None
    else:
        client = anthropic.Anthropic(api_key=api_key)

    existing        = load_existing_articles()
    existing_urls   = {a["url"] for a in existing}
    existing_titles = {a["title"] for a in existing}

    feedparser.USER_AGENT = FETCH_HEADERS["User-Agent"]

    total_dup   = 0
    candidates  = []   # (source, title)

    # ── RSS ──────────────────────────────────────────────────
    for source_name, feed_url in RSS_FEEDS.items():
        print(f"\n[{source_name}] フィード取得中...")
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            print(f"  取得エラー: {e}")
            continue

        for entry in feed.entries:
            url   = getattr(entry, "link",  "")
            title = getattr(entry, "title", "")

            if not url or url in existing_urls:
                continue
            if not matches_keywords(title):
                continue
            if is_similar_title(title, existing_titles):
                total_dup += 1
                print(f"  [重複]  {title[:65]}")
                continue

            candidates.append((source_name, title))
            existing_titles.add(title)

    total_fetched_rss = len(candidates) + total_dup

    # ── Gmail ─────────────────────────────────────────────────
    credentials_file = os.getenv("GMAIL_CREDENTIALS_FILE", "gmail_credentials.json")
    token_file       = os.getenv("GMAIL_TOKEN_FILE",       "gmail_token.json")
    gmail_candidate_count_before = len(candidates)

    if Path(credentials_file).exists() or Path(token_file).exists():
        print(f"\n[{NIKKEI_SOURCE}] Gmail取得中（ドライラン）...")
        try:
            service = get_gmail_service()
            result  = service.users().messages().list(
                userId="me",
                q=f"from:{GMAIL_SENDER} label:INBOX newer_than:7d",
                maxResults=50,
            ).execute()
            messages             = result.get("messages", [])
            processed_email_ids  = load_processed_emails()

            for msg_ref in messages:
                msg_id = msg_ref["id"]
                if msg_id in processed_email_ids:
                    continue
                msg = service.users().messages().get(
                    userId="me", id=msg_id, format="full"
                ).execute()
                headers   = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
                subject   = headers.get("Subject", "(件名なし)")
                html_body = _extract_email_html(msg)
                if not html_body:
                    continue

                articles_data = parse_nikkei_email(html_body)
                print(f"  メール: {subject[:50]}  ({len(articles_data)}件候補)")

                for art in articles_data:
                    title = art["title"]
                    url   = art["url"]
                    if url in existing_urls:
                        continue
                    if not matches_keywords(title, NIKKEI_KEYWORDS):
                        continue
                    if is_similar_title(title, existing_titles):
                        total_dup += 1
                        continue

                    candidates.append((NIKKEI_SOURCE, title))
                    existing_titles.add(title)

        except Exception as e:
            print(f"  Gmailエラー: {e}")
    else:
        print(f"\n[{NIKKEI_SOURCE}] Gmail認証未設定のためスキップ")

    # ── AI一括フィルタ（フィルタ①） ──────────────────────────
    print(f"\n{'=' * 60}")

    if client and candidates:
        print(f"⚠️  Haiku API 1回分の課金が発生します")
        print(f"\n[AI一括フィルタ] {len(candidates)}件をHaikuで判定中...")
        all_titles      = [t for _, t in candidates]
        relevant_titles = batch_filter_titles(client, all_titles)

        passed   = [(s, t) for s, t in candidates if t in relevant_titles]
        excluded = [(s, t) for s, t in candidates if t not in relevant_titles]
    else:
        if not client:
            print("  AI一括フィルタ: APIキー未設定のためスキップ（全件を候補として表示）")
        passed   = candidates
        excluded = []

    # ── 結果サマリ ────────────────────────────────────────────
    print(f"\n[ドライラン結果]")
    print(f"  取得件数（キーワード一致）: {len(candidates) + total_dup} 件")
    print(f"  重複スキップ              : {total_dup} 件")
    print(f"  AI一括フィルタ①除外      : {len(excluded)} 件")
    print(f"  残り（本番で分析対象）    : {len(passed)} 件")

    if excluded:
        print(f"\n  ── AI除外①タイトル ──")
        for src, ttl in excluded:
            print(f"    [{src}] {ttl[:65]}")

    if passed:
        print(f"\n  ── 本番分析予定タイトル ──")
        for src, ttl in passed:
            print(f"    [{src}] {ttl[:65]}")

    print("=" * 60)
    print("\n本番実行する場合: python3 collect.py")


# ── メイン収集処理 ────────────────────────────────────────────

def collect(limit: int | None = None) -> None:
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    existing            = load_existing_articles()
    existing_urls       = {a["url"] for a in existing}
    existing_titles     = {a["title"] for a in existing}
    processed_email_ids = load_processed_emails()
    new_articles        = []

    feedparser.USER_AGENT = FETCH_HEADERS["User-Agent"]

    # ── RSS: 第1パス（候補収集） ─────────────────────────────
    rss_candidates = []   # {"source", "url", "title", "summary", "published"}

    for source_name, feed_url in RSS_FEEDS.items():
        print(f"\n[{source_name}] フィード取得中: {feed_url}")
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            print(f"  フィード取得エラー: {e}")
            continue

        for entry in feed.entries:
            url       = getattr(entry, "link",      "")
            title     = getattr(entry, "title",     "")
            summary   = getattr(entry, "summary",   "")
            published = getattr(entry, "published", "")

            if not url or url in existing_urls:
                continue
            if not matches_keywords(title):
                continue
            if is_similar_title(title, existing_titles):
                print(f"  重複スキップ: {title[:60]}")
                continue

            rss_candidates.append({
                "source":    source_name,
                "url":       url,
                "title":     title,
                "summary":   summary,
                "published": published,
            })
            existing_titles.add(title)  # 重複防止のため先に登録

    # ── RSS: フィルタ①（AI一括） ─────────────────────────────
    filtered_rss = rss_candidates
    if rss_candidates:
        all_rss_titles = [c["title"] for c in rss_candidates]
        print(f"\n[AI一括フィルタ①] RSS {len(all_rss_titles)}件をHaikuで判定中...")
        relevant_titles = batch_filter_titles(client, all_rss_titles)
        filtered_rss    = [c for c in rss_candidates if c["title"] in relevant_titles]
        excluded_count  = len(rss_candidates) - len(filtered_rss)
        for c in rss_candidates:
            if c["title"] not in relevant_titles:
                print(f"  [AI除外①] {c['title'][:65]}")
        # --limit: RSSはlimit件まで
        if limit is not None:
            filtered_rss = filtered_rss[:limit]
        print(f"  → {len(filtered_rss)}件が関連あり（{excluded_count}件除外）"
              + (f"（上限 {limit} 件）" if limit is not None else ""))

    # ── RSS: 第2パス（本格分析） ─────────────────────────────
    for candidate in filtered_rss:
        url         = candidate["url"]
        title       = candidate["title"]
        summary     = candidate["summary"]
        published   = candidate["published"]
        source_name = candidate["source"]

        print(f"  処理中: {title[:60]}...")

        paywalled = is_paywalled(url)
        if paywalled:
            print(f"  有料記事を検出 → Web検索で分析します")

        try:
            if paywalled:
                analysis = analyze_with_web_search(client, title, source_name)
            else:
                analysis = analyze_normal(client, title, summary, source_name)
        except Exception as e:
            print(f"  分析エラー: {e}")
            time.sleep(30)
            continue

        if analysis.get("skip"):
            reason = analysis.get("reason", "")
            print(f"  スキップ（AI判定）: {reason}")
            existing_urls.add(url)
            time.sleep(30)
            continue

        article = {
            "url":          url,
            "title":        title,
            "source":       source_name,
            "published":    published,
            "collected_at": datetime.now().isoformat(),
            "web_searched": paywalled,
            "what":         analysis.get("what", ""),
            "why":          analysis.get("why", ""),
            "so_what":      analysis.get("so_what", ""),
        }

        new_articles.append(article)
        existing_urls.add(url)
        print(f"  ✓ 追加完了{'（Web検索）' if paywalled else ''}")
        time.sleep(30)

    # ── Gmail（日経クロストレンド） ──────────────────────────
    # --limit の残り枠を Gmail に引き渡す（RSS で limit 件消化済みなら 0 → スキップ）
    gmail_limit = None
    if limit is not None:
        gmail_limit = max(0, limit - len(filtered_rss))
        if gmail_limit == 0:
            print(f"\n[{NIKKEI_SOURCE}] --limit {limit} 件に達したためスキップ")
            gmail_articles = []
        else:
            print(f"\n[{NIKKEI_SOURCE}] Gmail取得中: {GMAIL_SENDER}（残り上限 {gmail_limit} 件）")
            gmail_articles = fetch_gmail_articles(
                client, existing_urls, existing_titles, processed_email_ids, limit=gmail_limit
            )
    else:
        print(f"\n[{NIKKEI_SOURCE}] Gmail取得中: {GMAIL_SENDER}")
        gmail_articles = fetch_gmail_articles(client, existing_urls, existing_titles, processed_email_ids)
    new_articles.extend(gmail_articles)

    # ── 保存 ────────────────────────────────────────────────
    all_articles = new_articles + existing
    save_articles(all_articles)
    save_processed_emails(processed_email_ids)
    build_html(all_articles)
    print(f"\n完了: {len(new_articles)} 件追加 / 合計 {len(all_articles)} 件")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="InfoCurator v2 記事収集スクリプト")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="AI一括フィルタ①のみ実行して本番分析対象タイトルを確認（Haiku API 1回分の課金あり）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="AI一括フィルタ通過後の記事を上位 N 件だけ本格分析して終了する",
    )
    args = parser.parse_args()

    if args.dry_run:
        dry_run()
    else:
        collect(limit=args.limit)
