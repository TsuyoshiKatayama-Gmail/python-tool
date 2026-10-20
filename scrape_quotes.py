"""quotes.toscrape.com/js から名言（テキスト・著者名）を収集するスクリプト。

JavaScript で描画されるページのため、Playwright（Chromium）で実際に
ブラウザを起動してレンダリング後の DOM を取得する。

機能:
- robots.txt を読み込み、禁止パスへのアクセスを抑止する
- 各リクエストの間に 1〜3 秒のランダムな待機時間を設ける
- 取得結果を Markdown（quotes_〔日付〕.md）に保存する
- ページのスクリーンショットを quotes_〔日付〕.png に保存する
- ブラウザを画面に表示して実行する（ヘッドフルモード）
- 接続エラー発生時はログにエラーを出力して終了する
"""

from __future__ import annotations

import logging
import random
import sys
import time
import urllib.request
from datetime import date
from urllib.error import URLError
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

# ------------------------------------------------------------------
# 設定
# ------------------------------------------------------------------
BASE_URL = "https://quotes.toscrape.com/"
START_PATH = "js"  # JavaScript で名言が描画されるページ
USER_AGENT = "QuotesScraper/1.0 (Python; educational use)"
NAV_TIMEOUT = 30_000  # ページ遷移のタイムアウト（ミリ秒）
MIN_WAIT, MAX_WAIT = 1.0, 3.0  # リクエスト間の待機時間（秒）

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("scrape_quotes")


def build_robot_parser(base_url: str) -> RobotFileParser:
    """robots.txt を読み込む。存在しない（404）場合は全許可として扱う。"""
    robots_url = urljoin(base_url, "/robots.txt")
    parser = RobotFileParser()
    parser.set_url(robots_url)
    try:
        # RobotFileParser.read() は User-Agent を送らないため、自前で取得する
        request = urllib.request.Request(robots_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=10) as response:
            content = response.read().decode("utf-8", errors="replace")
        parser.parse(content.splitlines())
        logger.info("robots.txt を読み込みました: %s", robots_url)
    except Exception as exc:  # 読み込み失敗時は保守的に全許可扱いとするが警告を残す
        logger.warning("robots.txt を読み込めませんでした（全許可として続行）: %s", exc)
        parser.parse([])  # 空ルール = 全許可
    return parser


def can_fetch(parser: RobotFileParser, url: str) -> bool:
    """robots.txt のルールに照らして url へのアクセス可否を判定する。"""
    allowed = parser.can_fetch(USER_AGENT, url)
    if not allowed:
        logger.warning("robots.txt により禁止されているためスキップします: %s", url)
    return allowed


def polite_sleep() -> None:
    """1〜3 秒のランダムな待機を挟む。"""
    wait = random.uniform(MIN_WAIT, MAX_WAIT)
    logger.info("次のリクエストまで %.2f 秒待機します", wait)
    time.sleep(wait)


def parse_quotes(page) -> list[dict[str, str]]:
    """レンダリング済みページから名言（テキスト・著者名）を抽出する。"""
    quotes: list[dict[str, str]] = []
    for element in page.query_selector_all("div.quote"):
        text_el = element.query_selector("span.text")
        author_el = element.query_selector("small.author")
        text = text_el.inner_text().strip() if text_el else "（不明）"
        author = author_el.inner_text().strip() if author_el else "（不明）"
        quotes.append({"text": text, "author": author})
    return quotes


def find_next_path(page) -> str | None:
    """ページネーションの「Next」リンクから次ページのパスを返す。無ければ None。"""
    next_link = page.query_selector("li.next a")
    if next_link:
        return next_link.get_attribute("href")
    return None


def scrape_all(parser: RobotFileParser) -> list[dict[str, str]]:
    """全ページを巡回して名言を収集し、1 ページ目のスクリーンショットも保存する。"""
    all_quotes: list[dict[str, str]] = []
    url = urljoin(BASE_URL, START_PATH)
    screenshot_path = f"quotes_{date.today().strftime('%Y%m%d')}.png"

    with sync_playwright() as playwright:
        # headless=True でブラウザを画面に表示せずに実行する
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()
        page.set_default_navigation_timeout(NAV_TIMEOUT)

        try:
            page_num = 0
            while url:
                if not can_fetch(parser, url):
                    break

                page_num += 1
                logger.info("取得中 (page %d): %s", page_num, url)
                page.goto(url, wait_until="networkidle")

                # JavaScript による名言の描画を待つ
                page.wait_for_selector("div.quote", timeout=NAV_TIMEOUT)

                quotes = parse_quotes(page)
                all_quotes.extend(quotes)
                logger.info("  → %d 件取得（累計 %d 件）", len(quotes), len(all_quotes))

                # 1 ページ目のスクリーンショットを保存する
                if page_num == 1:
                    page.screenshot(path=screenshot_path, full_page=True)
                    logger.info("スクリーンショットを保存しました: %s", screenshot_path)

                next_path = find_next_path(page)
                url = urljoin(page.url, next_path) if next_path else None
                if url:
                    polite_sleep()
        finally:
            context.close()
            browser.close()

    return all_quotes


def save_markdown(quotes: list[dict[str, str]]) -> str:
    """名言を Markdown にまとめて保存し、ファイル名を返す。"""
    today = date.today().strftime("%Y%m%d")
    filename = f"quotes_{today}.md"
    screenshot_name = f"quotes_{today}.png"

    lines = [
        "# 名言一覧（quotes.toscrape.com/js）",
        "",
        f"- 収集日: {date.today().strftime('%Y-%m-%d')}",
        f"- 収集元: {urljoin(BASE_URL, START_PATH)}",
        f"- 件数: {len(quotes)} 件",
        f"- スクリーンショット: ![スクリーンショット]({screenshot_name})",
        "",
        "| No. | 名言 | 著者 |",
        "| ---: | --- | --- |",
    ]
    for i, quote in enumerate(quotes, start=1):
        # セル内の | と改行はエスケープ・除去しておく
        text = quote["text"].replace("|", "\\|").replace("\n", " ")
        author = quote["author"].replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {i} | {text} | {author} |")

    content = "\n".join(lines) + "\n"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
    return filename


def main() -> int:
    try:
        parser = build_robot_parser(BASE_URL)
        quotes = scrape_all(parser)
    except (PlaywrightError, PlaywrightTimeoutError, URLError) as exc:
        # 接続エラー・タイムアウト・ナビゲーション失敗など
        logger.error("接続エラーが発生したため終了します: %s", exc)
        return 1

    if not quotes:
        logger.error("名言を取得できませんでした。")
        return 1

    filename = save_markdown(quotes)
    logger.info("完了: %d 件を %s に保存しました", len(quotes), filename)
    return 0


if __name__ == "__main__":
    sys.exit(main())
