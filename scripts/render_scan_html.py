from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCAN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,200}$")
SCANS_DIR = Path("reylai_assets/scans")
TEXT_DIR = Path("reylai_assets/text")


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _load_library(root: Path) -> dict[str, dict[str, Any]]:
    path = root / "reylai_library.json"
    if not path.exists():
        return {}
    data = _read_json(path)
    if not isinstance(data, list):
        return {}
    books: dict[str, dict[str, Any]] = {}
    for book in data:
        if not isinstance(book, dict):
            continue
        for key in (book.get("book_id"), book.get("drive_id")):
            key_text = str(key or "").strip()
            if key_text and key_text not in books:
                books[key_text] = book
    return books


def _book_title(book_id: str, book: dict[str, Any] | None) -> str:
    if not book:
        return book_id
    return str(book.get("title") or book.get("name") or book_id).strip() or book_id


def _page_count(scan: dict[str, Any]) -> int:
    pages = scan.get("pages")
    if isinstance(scan.get("total_pages"), int):
        return int(scan["total_pages"])
    if isinstance(pages, list):
        return len(pages)
    return 0


def _head(title: str, description: str = "") -> str:
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="tr">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            '<meta name="robots" content="index,follow">',
            f"<title>{escape(title)}</title>",
            f'<meta name="description" content="{escape(description)}">',
            "<style>",
            ":root{color-scheme:light dark;font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;}",
            "body{margin:0;background:#f7f7f3;color:#1f2933;line-height:1.6;}",
            "main{max-width:980px;margin:0 auto;padding:32px 18px 64px;}",
            "header{border-bottom:1px solid #d8d8cf;margin-bottom:24px;padding-bottom:18px;}",
            "h1{font-size:clamp(1.8rem,4vw,3rem);line-height:1.1;margin:0 0 12px;}",
            "h2{font-size:1.2rem;margin:32px 0 8px;}",
            "p{white-space:pre-wrap;margin:0;}",
            "a{color:#0969da;}",
            ".meta{display:flex;flex-wrap:wrap;gap:10px;color:#52616b;font-size:.95rem;}",
            ".page{border-top:1px solid #d8d8cf;padding-top:18px;}",
            ".book-list{list-style:none;padding:0;margin:20px 0 0;}",
            ".book-list li{border-top:1px solid #d8d8cf;padding:14px 0;}",
            "@media (prefers-color-scheme:dark){body{background:#121417;color:#e8edf2}.meta{color:#aab6c2}.page,header,.book-list li{border-color:#30363d}a{color:#6cb6ff}}",
            "</style>",
            "</head>",
            "<body>",
        ]
    )


def _book_html(book_id: str, scan: dict[str, Any], book: dict[str, Any] | None, generated_at: str) -> str:
    title = _book_title(book_id, book)
    pages = scan.get("pages") if isinstance(scan.get("pages"), list) else []
    total_pages = _page_count(scan)
    extractor = str(scan.get("extractor") or "").strip()
    pdf_url = str((book or {}).get("pdf_url") or "").strip()
    description = f"{title} text extraction, {total_pages} pages."
    parts = [
        _head(title, description),
        "<main>",
        "<header>",
        f"<h1>{escape(title)}</h1>",
        '<div class="meta">',
        f"<span>book_id: {escape(book_id)}</span>",
        f"<span>pages: {total_pages}</span>",
        f"<span>generated: {escape(generated_at)}</span>",
        f"<span>extractor: {escape(extractor or 'unknown')}</span>",
        f'<a href="/reylai_assets/scans/{escape(book_id)}.json">source json</a>',
    ]
    if pdf_url:
        parts.append(f'<a href="{escape(pdf_url)}">source pdf</a>')
    parts.extend(["</div>", "</header>"])
    for raw_page in pages:
        if not isinstance(raw_page, dict):
            continue
        page_no = raw_page.get("page")
        text = str(raw_page.get("text") or "").strip()
        if not text:
            continue
        page_label = str(page_no or "")
        parts.extend(
            [
                f'<section class="page" id="page-{escape(page_label)}" data-page="{escape(page_label)}">',
                f"<h2>Sayfa {escape(page_label)}</h2>",
                f"<p>{escape(text)}</p>",
                "</section>",
            ]
        )
    parts.extend(["</main>", "</body>", "</html>", ""])
    return "\n".join(parts)


def _index_html(items: list[dict[str, Any]], generated_at: str) -> str:
    parts = [
        _head("ReylAI book texts", f"Static HTML text index for {len(items)} books."),
        "<main>",
        "<header>",
        "<h1>ReylAI book texts</h1>",
        '<div class="meta">',
        f"<span>books: {len(items)}</span>",
        f"<span>generated: {escape(generated_at)}</span>",
        "</div>",
        "</header>",
        '<ul class="book-list">',
    ]
    for item in items:
        title = escape(str(item["title"]))
        book_id = escape(str(item["book_id"]))
        pages = escape(str(item["pages"]))
        href = escape(str(item["href"]))
        parts.append(
            f'<li><a href="{href}">{title}</a><br><span class="meta">book_id: {book_id} | pages: {pages}</span></li>'
        )
    parts.extend(["</ul>", "</main>", "</body>", "</html>", ""])
    return "\n".join(parts)


def render_scan_html(root: Path = ROOT) -> list[Path]:
    scans_dir = root / SCANS_DIR
    text_dir = root / TEXT_DIR
    text_dir.mkdir(parents=True, exist_ok=True)
    books = _load_library(root)
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    written: list[Path] = []
    index_items: list[dict[str, Any]] = []

    for scan_path in sorted(scans_dir.glob("*.json")):
        book_id = scan_path.stem
        if not SCAN_ID_RE.fullmatch(book_id):
            continue
        scan = _read_json(scan_path)
        if not isinstance(scan, dict):
            continue
        book = books.get(book_id)
        title = _book_title(book_id, book)
        output_path = text_dir / f"{book_id}.html"
        output_path.write_text(_book_html(book_id, scan, book, generated_at), encoding="utf-8")
        written.append(output_path)
        index_items.append(
            {
                "book_id": book_id,
                "title": title,
                "pages": _page_count(scan),
                "href": f"{book_id}.html",
            }
        )

    (text_dir / "index.html").write_text(_index_html(index_items, generated_at), encoding="utf-8")
    written.append(text_dir / "index.html")
    return written


def main() -> None:
    written = render_scan_html(ROOT)
    print(f"Rendered {len(written)} text HTML files into {TEXT_DIR.as_posix()}")


if __name__ == "__main__":
    main()
