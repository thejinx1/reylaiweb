from pathlib import Path
import shutil
import sys

from flask import render_template_string

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app
from render_scan_html import render_scan_html


def main():
    pdfjs_version = app._pdfjs_asset_version()
    with app.app.app_context():
        html = render_template_string(
            app.HTML,
            meb_logo_src="/static/meb_logo.png",
            reylai_icon_src="/static/reylai_icon.png",
            books_stack_src="/static/books_stack.png",
            books_remote_base_url=app.BOOKS_REMOTE_BASE_URL,
            pdfjs_lib_url=f"/pdfjs/pdf.min.js?v={pdfjs_version}",
            pdfjs_worker_url=f"/pdfjs/pdf.worker.min.js?v={pdfjs_version}",
        )
        terms_html = app.render_legal_page("terms")
        privacy_html = app.render_legal_page("privacy")
    Path("index.html").write_text(html, encoding="utf-8")
    Path("terms.html").write_text(terms_html, encoding="utf-8")
    Path("privacy.html").write_text(privacy_html, encoding="utf-8")
    terms_dir = ROOT / "terms"
    privacy_dir = ROOT / "privacy"
    library_dir = ROOT / "library"
    library_chat_dir = library_dir / "chat"
    message_dir = ROOT / "message"
    terms_dir.mkdir(exist_ok=True)
    privacy_dir.mkdir(exist_ok=True)
    library_dir.mkdir(exist_ok=True)
    library_chat_dir.mkdir(parents=True, exist_ok=True)
    message_dir.mkdir(exist_ok=True)
    (terms_dir / "index.html").write_text(terms_html, encoding="utf-8")
    (privacy_dir / "index.html").write_text(privacy_html, encoding="utf-8")
    (library_dir / "index.html").write_text(html, encoding="utf-8")
    (library_chat_dir / "index.html").write_text(html, encoding="utf-8")
    (message_dir / "index.html").write_text(html, encoding="utf-8")
    Path("_redirects").write_text(
        "/index.html / 301\n"
        "/library.html /library 301\n"
        "/library/chat.html /library/chat 301\n"
        "/message.html /message 301\n"
        "/terms.html /terms 301\n"
        "/privacy.html /privacy 301\n",
        encoding="utf-8",
    )
    pdfjs_dest = ROOT / "pdfjs"
    pdfjs_dest.mkdir(exist_ok=True)
    for filename in ("pdf.min.js", "pdf.worker.min.js"):
        source = ROOT / "static" / "pdfjs" / filename
        if source.exists():
            shutil.copy2(source, pdfjs_dest / filename)
    render_scan_html(ROOT)


if __name__ == "__main__":
    main()
