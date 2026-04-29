import os
import sys
import json
import time
import feedparser
import requests
import anthropic
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv(override=True)

RSS_FEEDS = {
    "MarkeZine":   "https://markezine.jp/rss/new/20/index.xml",
    "AdverTimes":  "https://www.advertimes.com/feed/",
    "DIGIDAY":     "https://digiday.jp/feed/",
    "ITmedia":     "https://rss.itmedia.co.jp/rss/2.0/marketing.xml",
}

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

OUTPUT_FILE = Path("articles.json")
HTML_FILE   = Path("index.html")
MODEL       = "claude-haiku-4-5-20251001"

FETCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

SYSTEM_PROMPT = """あなたはマーケティング専門家です。
与えられた記事のタイトルと概要をもとに、以下のJSON形式のみで回答してください。
説明文や前置きは一切不要です。JSONのみを出力してください。

{
  "what": "何のキャンペーン・施策か（2文以内）",
  "why": "なぜこの施策か。企業戦略・業界背景から推測（3〜4文）",
  "so_what": "マーケターが自分の仕事に使えるインサイト（2〜3文）"
}"""

WEB_SEARCH_SYSTEM_PROMPT = """あなたはマーケティング専門家です。
与えられた記事タイトルをWeb検索して関連情報を収集し、以下のJSON形式のみで回答してください。
説明文や前置きは一切不要です。JSONのみを出力してください。

{
  "what": "何のキャンペーン・施策か（2文以内）",
  "why": "なぜこの施策か。企業戦略・業界背景から推測（3〜4文）",
  "so_what": "マーケターが自分の仕事に使えるインサイト（2〜3文）"
}"""

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
    select { cursor: pointer; min-width: 140px; }
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
    'MarkeZine':  'markezine',
    'AdverTimes': 'advertimes',
    'DIGIDAY':    'digiday',
    'ITmedia':    'itmedia',
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

def matches_keywords(title: str) -> bool:
    return any(kw in title for kw in KEYWORDS)


# ── 有料記事の判定 ────────────────────────────────────────────

def is_paywalled(url: str) -> bool:
    try:
        r = requests.get(url, headers=FETCH_HEADERS, timeout=8, allow_redirects=True)
        return any(signal in r.text for signal in PAYWALL_SIGNALS)
    except Exception:
        return False


# ── AI 分析 ───────────────────────────────────────────────────

def _extract_json(text: str) -> dict:
    """レスポンステキストから JSON を抽出してパース"""
    text = text.strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    # JSON ブロックだけ取り出す
    start = text.find("{")
    end   = text.rfind("}") + 1
    if start != -1 and end > start:
        text = text[start:end]
    return json.loads(text)


def analyze_normal(client: anthropic.Anthropic, title: str, summary: str, source: str) -> dict:
    """通常記事：RSS 概要テキストで分析"""
    user_message = (
        f"情報源: {source}\n"
        f"タイトル: {title}\n"
        f"概要: {summary[:500] if summary else '（概要なし）'}"
    )
    message = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    return _extract_json(message.content[0].text)


def analyze_with_web_search(client: anthropic.Anthropic, title: str, source: str) -> dict:
    """有料記事：web_search ツールで関連情報を収集して分析"""
    user_message = (
        f"「{title}」（情報源: {source}）について検索し、"
        "マーケティング施策として分析してください。"
        "検索結果をもとに、指定のJSON形式のみで回答してください。"
    )
    response = client.beta.messages.create(
        model=MODEL,
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
    # テキストブロックを後ろから探す（最終回答を取得）
    for block in reversed(response.content):
        if hasattr(block, "text") and block.text.strip():
            return _extract_json(block.text)
    raise ValueError("web_search 分析でテキスト応答が得られませんでした")


# ── メイン収集処理 ────────────────────────────────────────────

def collect() -> None:
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    existing     = load_existing_articles()
    existing_urls = {a["url"] for a in existing}
    new_articles  = []
    feedparser.USER_AGENT = FETCH_HEADERS["User-Agent"]

    for source_name, feed_url in RSS_FEEDS.items():
        print(f"\n[{source_name}] フィード取得中: {feed_url}")
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            print(f"  フィード取得エラー: {e}")
            continue

        for entry in feed.entries:
            url       = getattr(entry, "link", "")
            title     = getattr(entry, "title", "")
            summary   = getattr(entry, "summary", "")
            published = getattr(entry, "published", "")

            if not url or url in existing_urls:
                continue
            if not matches_keywords(title):
                continue

            print(f"  処理中: {title[:60]}...")

            # 有料記事チェック
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

    all_articles = new_articles + existing
    save_articles(all_articles)
    build_html(all_articles)
    print(f"\n完了: {len(new_articles)} 件追加 / 合計 {len(all_articles)} 件")


if __name__ == "__main__":
    collect()
