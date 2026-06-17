import hashlib
import io
import json
import base64
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import app as reylai_app


SAMPLE_PDF = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Count 1 /Kids [3 0 R] >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R >>
endobj
4 0 obj
<< /Length 44 >>
stream
BT /F1 12 Tf 20 100 Td (Hello ReylAI) Tj ET
endstream
endobj
trailer
<< /Root 1 0 R >>
%%EOF
"""

SAMPLE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEElEQVR4nGP8zwACTGCSAQANHQEDgslx/wAAAABJRU5ErkJggg=="
)


def _gemini_text_response(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


class AppApiTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.base = Path(self.tmpdir.name)
        self.books_dir = self.base / "books"
        self.scans_dir = self.base / "scans"
        self.covers_dir = self.base / "covers"
        self.db_file = self.base / "library.json"
        self.config_file = self.base / "config.json"
        self.chat_history_file = self.base / "chat_history.json"
        for directory in (self.books_dir, self.scans_dir, self.covers_dir):
            directory.mkdir(parents=True, exist_ok=True)

        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(reylai_app, "BOOKS_DIR", str(self.books_dir)))
        self.stack.enter_context(patch.object(reylai_app, "SCANS_DIR", str(self.scans_dir)))
        self.stack.enter_context(patch.object(reylai_app, "COVERS_DIR", str(self.covers_dir)))
        self.stack.enter_context(patch.object(reylai_app, "DB_FILE", str(self.db_file)))
        self.stack.enter_context(patch.object(reylai_app, "CONFIG_FILE", str(self.config_file)))
        self.stack.enter_context(
            patch.object(reylai_app, "CHAT_HISTORY_FILE", str(self.chat_history_file))
        )
        self.stack.enter_context(
            patch.object(reylai_app, "ADMIN_HASH", hashlib.sha256(b"test-pass").hexdigest())
        )
        self.stack.enter_context(patch.object(reylai_app, "_extract_cover", lambda *_a, **_k: None))
        self.stack.enter_context(
            patch.object(reylai_app, "_extract_title_from_cover", lambda *_a, **_k: None)
        )
        self.stack.enter_context(patch.object(reylai_app, "start_scan", lambda *_a, **_k: None))

        reylai_app._auth_tokens.clear()
        reylai_app.save_library([])
        self.client = reylai_app.app.test_client()

    def _auth_headers(self):
        response = self.client.post("/api/verify_password", json={"password": "test-pass"})
        data = response.get_json()
        self.assertTrue(data["success"])
        return {"X-Auth-Token": data["token"]}

    def test_wrong_admin_password_is_reported_and_logged(self):
        with self.assertLogs(reylai_app.app.logger.name, level="WARNING") as logs:
            response = self.client.post(
                "/api/verify_password",
                json={"password": "bad-pass"},
                headers={"User-Agent": "ui-test"},
            )

        data = response.get_json()
        self.assertFalse(data["success"])
        self.assertEqual(data["error"], "Yanlış şifre.")
        self.assertTrue(
            any("Yanlis yonetici sifresi denemesi" in item and "ui-test" in item for item in logs.output)
        )

    def _write_book(self, *, book_id="book-1", grade="9", drive_id="", local_name="demo.pdf"):
        grade_dir = self.books_dir / grade
        grade_dir.mkdir(parents=True, exist_ok=True)
        local_path = grade_dir / local_name
        local_path.write_bytes(SAMPLE_PDF)
        entry = {
            "book_id": book_id,
            "name": "Demo.pdf",
            "title": "Demo",
            "drive_id": drive_id,
            "local_path": str(local_path),
            "grade": grade,
            "scan_status": "done",
            "scan_pages": 1,
            "added_at": "2026-04-30T00:00:00",
        }
        reylai_app.save_library([entry])
        return entry, local_path

    def test_index_contains_analyze_button_and_formatting_hook(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn('id="analyzeBtn"', html)
        self.assertIn("Analiz Et", html)
        self.assertIn('id="chatSidebar"', html)
        self.assertIn('id="chatHistoryList"', html)
        self.assertIn("function renderMarkdown", html)
        self.assertIn("function collectMarkdownDefinitions", html)
        self.assertIn("function appendMath", html)
        self.assertIn("function normalizeImageSrc", html)
        self.assertIn("/api/page_image/", html)
        self.assertIn("chat-md-math", html)
        self.assertIn("chat-md-image-fallback", html)
        self.assertIn("chat-md-table", html)
        self.assertIn("chat-md-task-checkbox", html)
        self.assertIn("chat-inline-del", html)
        self.assertIn("CHAT_HISTORY_KEY", html)
        self.assertNotIn('id="scanAllBtn"', html)
        self.assertNotIn("Kitapları Analiz Et", html)
        self.assertNotIn('id="syncBtn"', html)
        self.assertIn("function parseMessageSegments", html)
        self.assertIn('id="pdfPageInput"', html)
        self.assertIn('id="pdfWheelZoomToggle"', html)
        self.assertIn("function pdfFitPage", html)
        self.assertIn("function pdfGoToPage", html)
        self.assertIn("rangeChunkSize", html)
        self.assertIn("disableStream: true", html)
        self.assertNotIn("xhr.open('GET', pdfUrl", html)
        self.assertNotIn("Cloud\\u0027a Aktar", html)
        self.assertIn("Yazıyor...", html)

    def test_chat_history_persists_and_can_delete_single_chat(self):
        store = {
            "chats": [
                {
                    "id": "chat-1",
                    "book_id": "book-1",
                    "book_title": "Demo",
                    "title": "Ilk sohbet",
                    "messages": [{"role": "user", "text": "Merhaba"}],
                    "created_at": "2026-05-31T01:00:00",
                    "updated_at": "2026-05-31T01:01:00",
                },
                {
                    "id": "chat-2",
                    "book_id": "book-2",
                    "book_title": "Demo 2",
                    "title": "Ikinci sohbet",
                    "messages": [{"role": "ai", "text": "Yanit"}],
                    "created_at": "2026-05-31T02:00:00",
                    "updated_at": "2026-05-31T02:01:00",
                },
            ]
        }

        save_response = self.client.post("/api/chat_history", json=store).get_json()
        self.assertTrue(save_response["success"])
        self.assertTrue(self.chat_history_file.exists())

        loaded = self.client.get("/api/chat_history").get_json()
        self.assertEqual([chat["id"] for chat in loaded["chats"]], ["chat-2", "chat-1"])

        delete_response = self.client.delete("/api/chat_history/chat-2").get_json()
        self.assertTrue(delete_response["success"])
        remaining = self.client.get("/api/chat_history").get_json()
        self.assertEqual([chat["id"] for chat in remaining["chats"]], ["chat-1"])

    def test_library_filters_by_grade(self):
        reylai_app.save_library(
            [
                {"book_id": "a", "title": "A", "grade": "9"},
                {"book_id": "b", "title": "B", "grade": "10"},
            ]
        )
        response = self.client.get("/api/library?grade=9")
        data = response.get_json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["book_id"], "a")

    def test_add_book_requires_auth_and_creates_drive_entry(self):
        unauthorized = self.client.post("/api/add_book", json={"file_id": "drive-1"})
        self.assertFalse(unauthorized.get_json()["success"])
        self.assertFalse(unauthorized.get_json()["auth"])

        response = self.client.post(
            "/api/add_book",
            json={"file_id": "drive-1", "name": "Cloud Matematik.pdf", "grade": "10"},
            headers=self._auth_headers(),
        )
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["book"]["drive_id"], "drive-1")
        self.assertEqual(data["book"]["title"], "Cloud Matematik")

        saved = reylai_app.load_library()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["grade"], "10")

    def test_upload_rejects_non_pdf_and_persists_pdf(self):
        headers = self._auth_headers()
        bad = self.client.post(
            "/api/upload",
            data={"file": (io.BytesIO(b"not-a-pdf"), "bad.txt"), "grade": "9"},
            headers=headers,
            content_type="multipart/form-data",
        )
        self.assertFalse(bad.get_json()["success"])

        good = self.client.post(
            "/api/upload",
            data={"file": (io.BytesIO(SAMPLE_PDF), "good.pdf"), "grade": "9"},
            headers=headers,
            content_type="multipart/form-data",
        )
        payload = good.get_json()
        self.assertTrue(payload["success"])
        local_path = Path(payload["book"]["local_path"])
        self.assertTrue(local_path.exists())
        self.assertEqual(len(reylai_app.load_library()), 1)

    def test_rename_and_delete_round_trip(self):
        entry, local_path = self._write_book()
        scan_path = self.scans_dir / f"{entry['book_id']}.json"
        scan_path.write_text(json.dumps({"total_pages": 1}), encoding="utf-8")

        headers = self._auth_headers()
        rename = self.client.post(
            "/api/rename_book",
            json={"book_id": entry["book_id"], "name": "Yeni Baslik"},
            headers=headers,
        )
        self.assertTrue(rename.get_json()["success"])
        self.assertEqual(reylai_app.load_library()[0]["title"], "Yeni Baslik")

        delete = self.client.post(
            "/api/delete", json={"book_id": entry["book_id"]}, headers=headers
        )
        self.assertTrue(delete.get_json()["success"])
        self.assertFalse(local_path.exists())
        self.assertFalse(scan_path.exists())
        self.assertEqual(reylai_app.load_library(), [])

    def test_update_cover_requires_auth_and_saves_thumbnail(self):
        entry, _local_path = self._write_book()

        unauthorized = self.client.post(
            "/api/update_cover",
            data={"book_id": entry["book_id"], "cover": (io.BytesIO(SAMPLE_PNG), "cover.png", "image/png")},
            content_type="multipart/form-data",
        )
        self.assertFalse(unauthorized.get_json()["success"])
        self.assertFalse(unauthorized.get_json()["auth"])

        headers = self._auth_headers()
        response = self.client.post(
            "/api/update_cover",
            data={"book_id": entry["book_id"], "cover": (io.BytesIO(SAMPLE_PNG), "cover.png", "image/png")},
            headers=headers,
            content_type="multipart/form-data",
        )
        data = response.get_json()
        self.assertTrue(data["success"])
        cover_path = self.covers_dir / f"{entry['book_id']}.jpg"
        self.assertTrue(cover_path.exists())
        self.assertGreater(cover_path.stat().st_size, 0)
        saved = reylai_app.load_library()[0]
        self.assertEqual(Path(saved["cover_path"]), cover_path)
        self.assertIn("cover_updated_at", saved)

        cover_response = self.client.get(f"/api/cover/{entry['book_id']}")
        self.assertEqual(cover_response.status_code, 200)
        self.assertGreater(len(cover_response.data), 0)

    def test_serve_pdf_supports_range_and_drive_cache(self):
        reylai_app.save_library(
            [
                {
                    "book_id": "",
                    "name": "Cloud",
                    "title": "Cloud",
                    "drive_id": "drive-serve",
                    "local_path": "",
                    "grade": "9",
                    "scan_status": "pending",
                    "scan_pages": 0,
                    "added_at": "2026-04-30T00:00:00",
                }
            ]
        )

        def fake_download(_drive_id, dest_path):
            Path(dest_path).write_bytes(SAMPLE_PDF)
            return True

        with patch.object(reylai_app, "_download_from_drive", fake_download):
            response = self.client.get(
                "/api/serve_pdf/drive-serve", headers={"Range": "bytes=0-9"}
            )

        self.assertEqual(response.status_code, 206)
        self.assertEqual(len(response.data), 10)
        saved = reylai_app.load_library()[0]
        self.assertTrue(Path(saved["local_path"]).exists())

    def test_library_exposes_remote_pdf_url_for_archive_books(self):
        book_id = "087fb698-7ba2-425b-9e97-d09b0f4fd1e1"
        reylai_app.save_library(
            [
                {
                    "book_id": book_id,
                    "name": "Tarih.pdf",
                    "title": "Tarih",
                    "drive_id": "",
                    "local_path": "",
                    "grade": "9",
                    "scan_status": "done",
                    "scan_pages": 1,
                    "added_at": "2026-06-03T00:00:00",
                }
            ]
        )

        data = self.client.get("/api/library?grade=9").get_json()

        self.assertEqual(data[0]["pdf_source"], "book_archive")
        self.assertEqual(
            data[0]["pdf_url"],
            "https://reyliar.github.io/blupblupreylai-books/" + book_id + ".pdf",
        )

    def test_serve_pdf_proxies_remote_range_without_full_download(self):
        book_id = "087fb698-7ba2-425b-9e97-d09b0f4fd1e1"
        reylai_app.save_library(
            [
                {
                    "book_id": book_id,
                    "name": "Tarih.pdf",
                    "title": "Tarih",
                    "drive_id": "",
                    "local_path": "",
                    "grade": "9",
                    "scan_status": "done",
                    "scan_pages": 1,
                    "added_at": "2026-06-03T00:00:00",
                }
            ]
        )

        captured = {}

        class FakeRemoteResponse:
            status_code = 206
            headers = {
                "Content-Type": "application/pdf",
                "Content-Length": "10",
                "Content-Range": "bytes 0-9/100",
            }

            def iter_content(self, chunk_size=65536):
                yield b"%PDF-1.7\n"

            def close(self):
                captured["closed"] = True

        def fake_get(url, **kwargs):
            captured["url"] = url
            captured["headers"] = kwargs.get("headers") or {}
            return FakeRemoteResponse()

        with patch.object(reylai_app.requests, "get", side_effect=fake_get), patch.object(
            reylai_app,
            "_ensure_local_pdf",
            side_effect=AssertionError("range proxy should not cache the full PDF"),
        ):
            response = self.client.get(
                f"/api/serve_pdf/{book_id}", headers={"Range": "bytes=0-9"}
            )

        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.data, b"%PDF-1.7\n")
        self.assertEqual(response.headers["Content-Range"], "bytes 0-9/100")
        self.assertEqual(captured["headers"]["Range"], "bytes=0-9")
        self.assertTrue(captured["url"].endswith("/" + book_id + ".pdf"))
        self.assertTrue(captured.get("closed"))
        self.assertEqual(reylai_app.load_library()[0]["local_path"], "")

    def test_ensure_local_pdf_keeps_remote_archive_books_uncached(self):
        book_id = "087fb698-7ba2-425b-9e97-d09b0f4fd1e1"
        entry = {
            "book_id": book_id,
            "name": "Tarih.pdf",
            "title": "Tarih",
            "drive_id": "",
            "local_path": "",
            "grade": "9",
            "scan_status": "pending",
            "scan_pages": 0,
            "added_at": "2026-06-03T00:00:00",
        }
        library = [entry]

        with patch.object(
            reylai_app,
            "_download_from_url",
            side_effect=AssertionError("remote archive PDFs should not be cached locally"),
        ):
            local_path = reylai_app._ensure_local_pdf(entry, library)

        self.assertEqual(local_path, "")
        self.assertEqual(entry["pdf_source"], "book_archive")
        self.assertTrue(entry["pdf_url"].endswith("/" + book_id + ".pdf"))
        self.assertEqual(reylai_app.load_library()[0]["local_path"], "")

    def test_cover_endpoint_does_not_download_remote_pdf_for_missing_cover(self):
        book_id = "087fb698-7ba2-425b-9e97-d09b0f4fd1e1"
        reylai_app.save_library(
            [
                {
                    "book_id": book_id,
                    "name": "Tarih.pdf",
                    "title": "Tarih",
                    "drive_id": "",
                    "local_path": "",
                    "grade": "9",
                    "scan_status": "done",
                    "scan_pages": 1,
                    "added_at": "2026-06-03T00:00:00",
                }
            ]
        )

        with patch.object(
            reylai_app,
            "_ensure_local_pdf",
            side_effect=AssertionError("cover lookup should not download remote PDFs"),
        ):
            response = self.client.get(f"/api/cover/{book_id}")

        self.assertEqual(response.status_code, 404)

    def test_page_image_endpoint_reports_missing_renderer(self):
        entry, _local_path = self._write_book()

        with patch.object(reylai_app, "_HAS_PDF2IMAGE", False):
            response = self.client.get(f"/api/page_image/{entry['book_id']}/1")

        self.assertEqual(response.status_code, 503)
        self.assertIn("pdf2image", response.get_data(as_text=True))

    def test_analyze_validation_errors_are_stable(self):
        with patch.object(reylai_app, "GEMINI_API_KEY", "test-key"):
            missing_prompt = self.client.post("/api/analyze", json={}).get_json()
            self.assertEqual(missing_prompt["error"], "Prompt eksik")

            missing_book = self.client.post("/api/analyze", json={"prompt": "Merhaba"}).get_json()
            self.assertEqual(missing_book["error"], "book_id eksik")

    def test_small_talk_skips_pdf_scan_and_model_call(self):
        with ExitStack() as stack:
            stack.enter_context(patch.object(reylai_app, "GEMINI_API_KEY", "test-key"))
            stack.enter_context(
                patch.object(
                    reylai_app,
                    "_ensure_local_pdf",
                    side_effect=AssertionError("small talk should not prepare PDFs"),
                )
            )
            stack.enter_context(
                patch.object(
                    reylai_app,
                    "_extract_pdf_pages",
                    side_effect=AssertionError("small talk should not scan PDFs"),
                )
            )
            stack.enter_context(
                patch.object(
                    reylai_app,
                    "_gemini_generate_content",
                    side_effect=AssertionError("small talk should not call Gemini"),
                )
            )

            response = self.client.post(
                "/api/analyze",
                json={
                    "book_id": "book-1",
                    "book_name": "Demo",
                    "prompt": "selam bugün nasılsın",
                    "analysis_id": "analysis-test-1",
                },
            )

        data = response.get_json()
        self.assertTrue(data["local"])
        self.assertIn("Kitaptan", data["result"])
        status = self.client.get("/api/analyze_status/analysis-test-1").get_json()
        self.assertTrue(status["done"])
        self.assertEqual(status["stage"], "local")
        self.assertIn("Kısa cevap", status["message"])

    def test_short_book_tasks_are_not_treated_as_small_talk(self):
        self.assertFalse(reylai_app._is_small_talk_prompt("30 sayfayi yap"))
        self.assertFalse(reylai_app._is_small_talk_prompt("etkinligi yap"))
        self.assertFalse(reylai_app._is_small_talk_prompt("performans odevi hazirla"))
        self.assertTrue(reylai_app._is_small_talk_prompt("selam bugun nasilsin"))
        self.assertTrue(reylai_app._is_small_talk_prompt("ne yapiyorsun"))

    def test_page_number_before_page_word_is_detected(self):
        self.assertEqual(reylai_app._extract_page_numbers("30 sayfayi yap"), [30])
        self.assertEqual(reylai_app._extract_page_numbers("30. sayfayi yap"), [30])

    def test_book_related_prompt_still_uses_analysis_path(self):
        entry, _local_path = self._write_book()
        scan_path = self.scans_dir / f"{entry['book_id']}.json"
        scan_path.write_text(
            json.dumps({"total_pages": 1, "pages": [{"page": 12, "text": "Ana konu burada"}]}),
            encoding="utf-8",
        )

        with patch.object(reylai_app, "GEMINI_API_KEY", "test-key"), patch.object(
            reylai_app, "_gemini_generate_content", return_value=_gemini_text_response("Kitap cevabı")
        ):
            response = self.client.post(
                "/api/analyze",
                json={
                    "book_id": entry["book_id"],
                    "book_name": "Demo",
                    "prompt": "12. sayfayı açıkla",
                    "analysis_id": "analysis-book-test",
                },
            )

        data = response.get_json()
        self.assertFalse(data.get("local", False))
        self.assertEqual(data["result"], "Kitap cevabı")

    def test_short_page_task_uses_requested_page_context(self):
        entry, _local_path = self._write_book()
        scan_path = self.scans_dir / f"{entry['book_id']}.json"
        scan_path.write_text(
            json.dumps(
                {
                    "total_pages": 31,
                    "pages": [
                        {"page": 29, "text": "Yirmi dokuzuncu sayfa"},
                        {"page": 30, "text": "Otuzuncu sayfa etkinligi"},
                        {"page": 31, "text": "Otuz birinci sayfa"},
                    ],
                }
            ),
            encoding="utf-8",
        )

        captured = {}

        def fake_gemini(messages, **_kwargs):
            captured["messages"] = messages
            return _gemini_text_response("Sayfa 30 cevabi")

        with patch.object(reylai_app, "GEMINI_API_KEY", "test-key"), patch.object(
            reylai_app, "_gemini_generate_content", side_effect=fake_gemini
        ):
            response = self.client.post(
                "/api/analyze",
                json={
                    "book_id": entry["book_id"],
                    "book_name": "Demo",
                    "prompt": "30 sayfayi yap",
                    "analysis_id": "analysis-page-30",
                },
            )

        data = response.get_json()
        self.assertEqual(data["result"], "Sayfa 30 cevabi")
        combined_prompt = "\n".join(str(message.get("content", "")) for message in captured["messages"])
        self.assertIn("Istenen sayfalar: 30", combined_prompt)
        self.assertIn("[Sayfa 30]", combined_prompt)
        self.assertIn("Otuzuncu sayfa etkinligi", combined_prompt)

    def test_analyze_uses_chat_history_and_returns_ai_title(self):
        entry, _local_path = self._write_book()
        scan_path = self.scans_dir / f"{entry['book_id']}.json"
        scan_path.write_text(
            json.dumps({"total_pages": 1, "pages": [{"page": 12, "text": "Ana konu burada"}]}),
            encoding="utf-8",
        )

        captured = {"messages": []}

        def fake_gemini(messages, **_kwargs):
            captured["messages"].append(messages)
            if len(captured["messages"]) == 2:
                return _gemini_text_response("Ana Konu CevabÄ±")
            return _gemini_text_response("Kitap cevabÄ±")

        with patch.object(reylai_app, "GEMINI_API_KEY", "test-key"), patch.object(
            reylai_app, "_gemini_generate_content", side_effect=fake_gemini
        ):
            response = self.client.post(
                "/api/analyze",
                json={
                    "book_id": entry["book_id"],
                    "book_name": "Demo",
                    "prompt": "devamÄ±nÄ± aÃ§Ä±kla",
                    "analysis_id": "analysis-title-test",
                    "title_requested": True,
                    "chat_history": [
                        {"role": "user", "text": "12. sayfayÄ± aÃ§Ä±kla"},
                        {"role": "ai", "text": "Ana konu kÄ±sa cevap."},
                    ],
                },
            )

        data = response.get_json()
        self.assertEqual(data["result"], "Kitap cevabÄ±")
        self.assertEqual(data["chat_title"], "Ana Konu CevabÄ±")
        combined_prompt = "\n".join(str(message.get("content", "")) for message in captured["messages"][0])
        self.assertIn("Onceki konusma", combined_prompt)
        self.assertIn("Kullanici: 12. sayfay", combined_prompt)
        status = self.client.get("/api/analyze_status/analysis-title-test").get_json()
        self.assertEqual(status["chat_title"], "Ana Konu CevabÄ±")

    def test_analyze_uses_only_existing_scan_files(self):
        entry, _local_path = self._write_book()

        with ExitStack() as stack:
            stack.enter_context(patch.object(reylai_app, "GEMINI_API_KEY", "test-key"))
            stack.enter_context(
                patch.object(
                    reylai_app,
                    "_ensure_local_pdf",
                    side_effect=AssertionError("analyze should not prepare PDFs"),
                )
            )
            stack.enter_context(
                patch.object(
                    reylai_app,
                    "_extract_pdf_pages",
                    side_effect=AssertionError("analyze should not scan PDFs"),
                )
            )

            response = self.client.post(
                "/api/analyze",
                json={
                    "book_id": entry["book_id"],
                    "book_name": "Demo",
                    "prompt": "12. sayfayı açıkla",
                    "analysis_id": "analysis-missing-scan",
                },
            )

        data = response.get_json()
        self.assertTrue(data["missing_scan"])
        self.assertIn("hazır tarama", data["error"])

    def test_analyze_can_prepare_missing_remote_scan(self):
        book_id = "087fb698-7ba2-425b-9e97-d09b0f4fd1e1"
        entry = {
            "book_id": book_id,
            "name": "Tarih.pdf",
            "title": "Tarih",
            "drive_id": "",
            "local_path": "",
            "grade": "9",
            "scan_status": "pending",
            "scan_pages": 0,
            "added_at": "2026-06-03T00:00:00",
        }
        reylai_app.save_library([entry])

        captured_scan = {}

        def fake_scan(scan_key, local_path=None, drive_id=None, remote_url=None):
            captured_scan["local_path"] = local_path
            captured_scan["drive_id"] = drive_id
            captured_scan["remote_url"] = remote_url
            scan_path = self.scans_dir / f"{scan_key}.json"
            scan_path.write_text(
                json.dumps(
                    {
                        "book_id": scan_key,
                        "total_pages": 1,
                        "pages": [{"page": 12, "text": "Remote PDF tarama metni"}],
                        "extractor": "pypdf",
                    }
                ),
                encoding="utf-8",
            )

        with patch.object(reylai_app, "GEMINI_API_KEY", "test-key"), patch.object(
            reylai_app,
            "_ensure_local_pdf",
            side_effect=AssertionError("analyze should scan remote PDFs without local cache"),
        ), patch.object(reylai_app, "_do_scan", side_effect=fake_scan), patch.object(
            reylai_app, "_gemini_generate_content", return_value=_gemini_text_response("Remote cevap")
        ):
            response = self.client.post(
                "/api/analyze",
                json={
                    "book_id": book_id,
                    "book_name": "Tarih",
                    "prompt": "12. sayfayi acikla",
                    "analysis_id": "analysis-remote-scan",
                },
            )

        data = response.get_json()
        self.assertEqual(data["result"], "Remote cevap")
        self.assertEqual(captured_scan["local_path"], None)
        self.assertEqual(captured_scan["drive_id"], None)
        self.assertTrue(captured_scan["remote_url"].endswith("/" + book_id + ".pdf"))
        self.assertTrue((self.scans_dir / f"{book_id}.json").exists())

    def test_scan_status_checks_selected_books_scan_file(self):
        entry, _local_path = self._write_book()
        entry["scan_status"] = "pending"
        entry["scan_pages"] = 0
        reylai_app.save_library([entry])
        scan_path = self.scans_dir / f"{entry['book_id']}.json"
        scan_path.write_text(
            json.dumps(
                {
                    "book_id": entry["book_id"],
                    "total_pages": 2,
                    "pages": [
                        {"page": 1, "text": "Birinci sayfa"},
                        {"page": 2, "text": "Ikinci sayfa"},
                    ],
                    "extractor": "pypdf",
                }
            ),
            encoding="utf-8",
        )

        data = self.client.get(f"/api/scan_status/{entry['book_id']}").get_json()

        self.assertEqual(data["scan_status"], "done")
        self.assertEqual(data["scan_pages"], 2)
        self.assertEqual(data["scan_extractor"], "pypdf")
        saved = reylai_app.load_library()[0]
        self.assertEqual(saved["scan_status"], "done")
        self.assertEqual(saved["scan_pages"], 2)

    def test_analyze_does_not_use_another_books_scan_when_ids_overlap(self):
        selected_entry, _local_path = self._write_book(
            book_id="selected-book",
            local_name="selected.pdf",
        )
        other_entry = {
            "book_id": "other-book",
            "name": "Other.pdf",
            "title": "Other",
            "drive_id": "selected-book",
            "local_path": "",
            "grade": "9",
            "scan_status": "done",
            "scan_pages": 1,
            "added_at": "2026-04-30T00:00:00",
        }
        reylai_app.save_library([other_entry, selected_entry])
        wrong_scan_path = self.scans_dir / "other-book.json"
        wrong_scan_path.write_text(
            json.dumps(
                {
                    "book_id": "other-book",
                    "drive_id": "selected-book",
                    "total_pages": 1,
                    "pages": [{"page": 12, "text": "Yanlis kitabin metni"}],
                }
            ),
            encoding="utf-8",
        )

        with patch.object(reylai_app, "GEMINI_API_KEY", "test-key"):
            response = self.client.post(
                "/api/analyze",
                json={
                    "book_id": "selected-book",
                    "book_name": "Selected",
                    "prompt": "12. sayfayi acikla",
                    "analysis_id": "analysis-overlap-scan",
                },
            )

        data = response.get_json()
        self.assertTrue(data["missing_scan"])
        self.assertIn("tarama", data["error"])

    def test_context_excerpt_prefers_requested_pages(self):
        pages = [
            {"page": 10, "text": "Giris"},
            {"page": 11, "text": "Hazirlik"},
            {"page": 12, "text": "Ana konu burada"},
            {"page": 13, "text": "Ornek sorular"},
            {"page": 14, "text": "Sonuc"},
        ]
        excerpt = reylai_app._build_context_excerpt(pages, "12. sayfayi acikla")
        self.assertIn("[Sayfa 12]", excerpt)
        self.assertIn("Ana konu burada", excerpt)

    def test_context_excerpt_keeps_explicit_page_ranges_tight(self):
        pages = [{"page": page, "text": f"Metin {page}"} for page in range(9, 15)]
        excerpt = reylai_app._build_context_excerpt(pages, "sayfa 10-12 arasını açıkla")
        self.assertNotIn("[Sayfa 9]", excerpt)
        self.assertIn("[Sayfa 10]", excerpt)
        self.assertIn("[Sayfa 11]", excerpt)
        self.assertIn("[Sayfa 12]", excerpt)

    def test_extract_pdf_pages_keeps_pypdf_when_one_page_fails(self):
        class FakePage:
            def __init__(self, text=None, error=False):
                self.text = text
                self.error = error

            def extract_text(self):
                if self.error:
                    raise ValueError("bad page")
                return self.text

        class FakeReader:
            def __init__(self, _source):
                self.pages = [
                    FakePage("Birinci sayfa metni"),
                    FakePage(error=True),
                    FakePage("Ucuncu sayfa metni"),
                ]

        with patch.object(reylai_app, "_HAS_PYPDF", True), patch.object(
            reylai_app, "_PdfReader", FakeReader
        ), patch.object(
            reylai_app, "_fallback_extract_pdf_text", lambda _source: [{"page": 1, "text": "bad fallback"}]
        ):
            pages, extractor = reylai_app._extract_pdf_pages("dummy.pdf")

        self.assertEqual(extractor, "pypdf")
        self.assertEqual(len(pages), 3)
        self.assertEqual(pages[1]["text"], "")

    def test_bad_basic_scan_is_not_usable(self):
        bad_scan = {
            "total_pages": 352,
            "extractor": "basic",
            "pages": [{"page": 1, "text": "x" * 300000}]
            + [{"page": page, "text": ""} for page in range(2, 353)],
        }
        self.assertFalse(reylai_app._scan_data_is_usable(bad_scan))

    def _scan_missing_books_job_completes_legacy(self):
        entry, local_path = self._write_book(book_id="scan-book", local_name="scan.pdf")
        scan_path = self.scans_dir / f"{entry['book_id']}.json"
        self.assertFalse(scan_path.exists())

        response = self.client.post("/api/scan_missing_books")
        data = response.get_json()
        self.assertTrue(data["success"])

        for _ in range(40):
            status = self.client.get("/api/scan_missing_books_status").get_json()
            if status.get("completed"):
                break
            time.sleep(0.1)
        else:
            self.fail("scan_missing_books işi zamanında tamamlanmadı")

        self.assertTrue(scan_path.exists())
        self.assertGreaterEqual(status["success"], 1)
        self.assertIn("Tarama tamamlandı", status["current_message"])


    def test_scan_missing_books_job_completes(self):
        entry, _local_path = self._write_book(book_id="scan-book-v2", local_name="scan-v2.pdf")
        scan_path = self.scans_dir / f"{entry['book_id']}.json"
        self.assertFalse(scan_path.exists())

        response = self.client.post("/api/scan_missing_books")
        data = response.get_json()
        self.assertTrue(data["success"])

        for _ in range(40):
            status = self.client.get("/api/scan_missing_books_status").get_json()
            if status.get("completed"):
                break
            time.sleep(0.1)
        else:
            self.fail("scan_missing_books işi zamanında tamamlanmadı")

        self.assertTrue(scan_path.exists())
        self.assertGreaterEqual(status["success"], 1)
        self.assertIn("Tarama tamamlandı", status["current_message"])

    def test_scan_missing_books_job_can_be_cancelled(self):
        self._write_book(book_id="scan-cancel", local_name="scan-cancel.pdf")

        def slow_scan(*_args, **_kwargs):
            time.sleep(0.35)

        with patch.object(reylai_app, "_do_scan", slow_scan):
            response = self.client.post("/api/scan_missing_books")
            data = response.get_json()
            self.assertTrue(data["success"])

            cancel = self.client.post("/api/scan_missing_books_cancel").get_json()
            self.assertTrue(cancel["success"])
            self.assertTrue(cancel["cancelled"])
            self.assertTrue(cancel["job"]["cancel_requested"])

            for _ in range(50):
                status = self.client.get("/api/scan_missing_books_status").get_json()
                if status.get("completed"):
                    break
                time.sleep(0.1)
            else:
                self.fail("scan_missing_books iptali zamanında tamamlanmadı")

            self.assertTrue(status["cancelled"])
            self.assertFalse(status["running"])
            self.assertIn("iptal", status["current_message"].lower())


if __name__ == "__main__":
    unittest.main()
