"""books.toscrape.com から書籍情報（タイトル・価格・在庫状況）を収集するスクリプト。

機能:
- robots.txt を読み込み、禁止パスへのアクセスを抑止する
- 各リクエストの間に 1〜3 秒のランダムな待機時間を設ける
- 取得結果を Markdown（books_〔日付〕.md）に保存する
- 接続エラー発生時はログにエラーを出力して終了する
"""

from __future__ import annotations

import logging
import random
import sys
import time
from datetime import date
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

# ------------------------------------------------------------------
# 設定
# ------------------------------------------------------------------
BASE_URL = "https://books.toscrape.com/"
START_PATH = "catalogue/page-1.html"  # 1 ページ目（各ページ 20 冊）
USER_AGENT = "BooksScraper/1.0 (Python; educational use)"
REQUEST_TIMEOUT = 10  # 秒
MIN_WAIT, MAX_WAIT = 1.0, 3.0  # リクエスト間の待機時間（秒）

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("scrape_books")


def build_robot_parser(base_url: str) -> RobotFileParser:
    """robots.txt を読み込む。存在しない（404）場合は全許可として扱う。"""
    robots_url = urljoin(base_url, "/robots.txt")
    parser = RobotFileParser()
    parser.set_url(robots_url)
    try:
        parser.read()
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


def fetch(session: requests.Session, url: str) -> str:
    """URL を取得して HTML 文字列を返す。接続エラーは呼び出し元に送出する。"""
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    response.encoding = response.apparent_encoding
    return response.text


def parse_books(html: str) -> list[dict[str, str]]:
    """一覧ページの HTML から書籍情報（タイトル・価格・在庫状況）を抽出する。"""
    soup = BeautifulSoup(html, "html.parser")
    books: list[dict[str, str]] = []
    for article in soup.select("article.product_pod"):
        title_tag = article.select_one("h3 a")
        price_tag = article.select_one(".price_color")
        stock_tag = article.select_one(".availability")

        title = title_tag["title"].strip() if title_tag and title_tag.has_attr("title") else "（不明）"
        price = price_tag.get_text(strip=True) if price_tag else "（不明）"
        stock = stock_tag.get_text(strip=True) if stock_tag else "（不明）"

        books.append({"title": title, "price": price, "stock": stock})
    return books


def find_next_url(html: str, current_url: str) -> str | None:
    """ページネーションの「next」リンクから次ページの絶対 URL を返す。無ければ None。"""
    soup = BeautifulSoup(html, "html.parser")
    next_link = soup.select_one("li.next a")
    if next_link and next_link.has_attr("href"):
        return urljoin(current_url, next_link["href"])
    return None


def scrape_all(parser: RobotFileParser) -> list[dict[str, str]]:
    """全ページを巡回して書籍情報を収集する。"""
    all_books: list[dict[str, str]] = []
    url = urljoin(BASE_URL, START_PATH)

    with requests.Session() as session:
        session.headers.update({"User-Agent": USER_AGENT})
        page_num = 0
        while url:
            if not can_fetch(parser, url):
                break

            page_num += 1
            logger.info("取得中 (page %d): %s", page_num, url)
            html = fetch(session, url)

            books = parse_books(html)
            all_books.extend(books)
            logger.info("  → %d 件取得（累計 %d 件）", len(books), len(all_books))

            next_url = find_next_url(html, url)
            url = next_url
            if url:
                polite_sleep()

    return all_books


def save_markdown(books: list[dict[str, str]]) -> str:
    """書籍情報を Markdown テーブルにまとめて保存し、ファイル名を返す。"""
    today = date.today().strftime("%Y%m%d")
    filename = f"books_{today}.md"

    lines = [
        f"# 書籍一覧（books.toscrape.com）",
        "",
        f"- 収集日: {date.today().strftime('%Y-%m-%d')}",
        f"- 収集元: {BASE_URL}",
        f"- 件数: {len(books)} 件",
        "",
        "| No. | タイトル | 価格 | 在庫状況 |",
        "| ---: | --- | --- | --- |",
    ]
    for i, book in enumerate(books, start=1):
        # セル内の | はエスケープしておく
        title = book["title"].replace("|", "\\|")
        price = book["price"].replace("|", "\\|")
        stock = book["stock"].replace("|", "\\|")
        lines.append(f"| {i} | {title} | {price} | {stock} |")

    content = "\n".join(lines) + "\n"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
    return filename


def main() -> int:
    try:
        parser = build_robot_parser(BASE_URL)
        books = scrape_all(parser)
    except requests.exceptions.RequestException as exc:
        # 接続エラー・タイムアウト・HTTP エラーなど
        logger.error("接続エラーが発生したため終了します: %s", exc)
        return 1

    if not books:
        logger.error("書籍情報を取得できませんでした。")
        return 1

    filename = save_markdown(books)
    logger.info("完了: %d 件を %s に保存しました", len(books), filename)
    return 0


if __name__ == "__main__":
    sys.exit(main())
