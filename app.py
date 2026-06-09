import os
import re
import time
import uuid
import json
import socket
import base64
import hashlib
import secrets
import sys
import threading
import tempfile
import requests
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse
from flask import Flask, request, jsonify, render_template_string, send_file, make_response, Response, stream_with_context, redirect

try:
    from dotenv import load_dotenv as _dotenv_load
except ImportError:
    _dotenv_load = None

try:
    import webview as _webview
    _HAS_WEBVIEW = True
except ImportError:
    _webview = None
    _HAS_WEBVIEW = False

try:
    from pypdf import PdfReader as _PdfReader
    _HAS_PYPDF = True
except ImportError:
    _HAS_PYPDF = False

try:
    from pdf2image import convert_from_path as _pdf2img
    _HAS_PDF2IMAGE = True
except ImportError:
    _HAS_PDF2IMAGE = False

try:
    from PIL import Image as _PILImage
    _HAS_PIL = True
except ImportError:
    _PILImage = None
    _HAS_PIL = False

app = Flask(__name__)
app.json.ensure_ascii = False

BASE_DIR = Path(__file__).resolve().parent
_data_url_cache = {}
_LEGACY_REMOTE_EXTRACTOR = ''.join(('ad', 'obe'))


def _file_data_url(path, fallback_url=""):
    asset_path = Path(path)
    try:
        stat = asset_path.stat()
        cache_key = str(asset_path.resolve())
        cached = _data_url_cache.get(cache_key)
        if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
            return cached[2]
        data = base64.b64encode(asset_path.read_bytes()).decode("ascii")
    except Exception:
        return fallback_url
    suffix = asset_path.suffix.lower()
    mime = "image/png"
    if suffix in (".jpg", ".jpeg"):
        mime = "image/jpeg"
    elif suffix == ".svg":
        mime = "image/svg+xml"
    elif suffix == ".webp":
        mime = "image/webp"
    data_url = f"data:{mime};base64,{data}"
    _data_url_cache[cache_key] = (stat.st_mtime_ns, stat.st_size, data_url)
    return data_url


def _asset_data_url(relative_path, fallback_url=""):
    return _file_data_url(BASE_DIR / relative_path, fallback_url)


def _bundle_dir():
    return Path(getattr(sys, "_MEIPASS", BASE_DIR)).resolve()


def _pdfjs_asset_path(filename):
    safe_name = os.path.basename(str(filename or ''))
    if safe_name not in ('pdf.min.js', 'pdf.worker.min.js'):
        return None
    for base_dir in (BASE_DIR, _bundle_dir()):
        candidate = base_dir / "static" / "pdfjs" / safe_name
        if candidate.exists():
            return candidate
    return None


def _pdfjs_asset_version():
    mtimes = []
    for filename in ('pdf.min.js', 'pdf.worker.min.js'):
        path = _pdfjs_asset_path(filename)
        if path:
            try:
                mtimes.append(str(path.stat().st_mtime_ns))
            except OSError:
                pass
    return hashlib.sha1('|'.join(mtimes).encode('utf-8')).hexdigest()[:12] if mtimes else 'missing'


def _resolve_app_path(path):
    if not path:
        return ''
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = BASE_DIR / candidate
    return str(candidate)


def _load_env_file(env_path):
    if _dotenv_load is not None:
        _dotenv_load(env_path)
        return
    env_path = Path(env_path)
    if not env_path.exists():
        return
    with open(env_path, "r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_env_file(BASE_DIR / ".env")


def _env(name, default=""):
    return os.environ.get(name, default).strip()


def _env_int(name, default):
    value = _env(name, str(default))
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _is_configured(value, *placeholders):
    return bool(value) and value not in placeholders


GAS_WEB_APP_URL     = _env("GAS_WEB_APP_URL")
MISTRAL_API_KEY     = _env("MISTRAL_API_KEY")
MISTRAL_MODEL       = _env("MISTRAL_MODEL", "mistral-small-latest") or "mistral-small-latest"
MISTRAL_VISION_MODEL = _env("MISTRAL_VISION_MODEL", MISTRAL_MODEL) or MISTRAL_MODEL
MISTRAL_CHAT_URL    = (
    _env("MISTRAL_CHAT_URL", "https://api.mistral.ai/v1/chat/completions")
    or "https://api.mistral.ai/v1/chat/completions"
)
OPENAI_API_KEY      = _env("OPENAI_API_KEY")
BOOKS_REMOTE_BASE_URL = (
    _env("BOOKS_REMOTE_BASE_URL", "https://thejinx1.github.io/blupblupreylai-books/")
    or "https://thejinx1.github.io/blupblupreylai-books/"
).rstrip("/") + "/"
_REMOTE_BOOK_ID_RE = re.compile(
    r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
)

SCANS_DIR   = str(BASE_DIR / "reylai_assets" / "scans")
BOOKS_DIR   = str(BASE_DIR / "reylai_assets" / "books")
COVERS_DIR  = str(BASE_DIR / "reylai_assets" / "covers")
PAGE_IMAGES_DIR = str(BASE_DIR / "reylai_assets" / "page_images")
DB_FILE     = str(BASE_DIR / "reylai_library.json")
CONFIG_FILE = str(BASE_DIR / "reylai_config.json")
CHAT_HISTORY_FILE = str(BASE_DIR / "reylai_chat_history.json")
PAGE_IMAGE_DPI = max(90, min(_env_int("PAGE_IMAGE_DPI", 140), 220))

_auth_tokens = set()

os.makedirs(SCANS_DIR, exist_ok=True)
os.makedirs(BOOKS_DIR, exist_ok=True)
os.makedirs(COVERS_DIR, exist_ok=True)
os.makedirs(PAGE_IMAGES_DIR, exist_ok=True)


def _utc_now_iso():
    return datetime.now(UTC).isoformat()


def _check_auth():
    token = request.headers.get('X-Auth-Token', '')
    if not token or token not in _auth_tokens:
        return False
    return True


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_config(cfg):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def load_library():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []


def save_library(lib):
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(lib, f, ensure_ascii=False)


CHAT_HISTORY_MAX_CHATS = 200
CHAT_HISTORY_MAX_MESSAGES = 120
CHAT_HISTORY_MAX_TEXT_CHARS = 12000
_chat_history_lock = threading.Lock()


def _clean_chat_string(value, limit=500):
    value = re.sub(r'\s+', ' ', str(value or '')).strip()
    return value[:limit]


def _chat_timestamp(value, fallback=''):
    value = _clean_chat_string(value, 64)
    return value or fallback or _utc_now_iso()


def _sanitize_persisted_chat_store(raw_store):
    raw_chats = raw_store.get('chats') if isinstance(raw_store, dict) else []
    if not isinstance(raw_chats, list):
        raw_chats = []

    clean_chats = []
    seen_ids = set()
    for raw_chat in raw_chats:
        if not isinstance(raw_chat, dict):
            continue
        chat_id = _clean_chat_string(raw_chat.get('id'), 140)
        if not chat_id or chat_id in seen_ids:
            chat_id = 'chat-' + uuid.uuid4().hex
        seen_ids.add(chat_id)

        messages = []
        raw_messages = raw_chat.get('messages')
        if isinstance(raw_messages, list):
            for raw_message in raw_messages[-CHAT_HISTORY_MAX_MESSAGES:]:
                if not isinstance(raw_message, dict):
                    continue
                role = raw_message.get('role')
                if role not in ('user', 'ai'):
                    continue
                text = str(raw_message.get('text') or '').strip()
                if not text:
                    continue
                message_id = _clean_chat_string(raw_message.get('id'), 140) or 'msg-' + uuid.uuid4().hex
                messages.append({
                    'id': message_id,
                    'role': role,
                    'text': text[:CHAT_HISTORY_MAX_TEXT_CHARS],
                    'created_at': _chat_timestamp(raw_message.get('created_at')),
                })

        created_at = _chat_timestamp(raw_chat.get('created_at'))
        updated_at = _chat_timestamp(raw_chat.get('updated_at'), created_at)
        clean_chats.append({
            'id': chat_id,
            'book_id': _clean_chat_string(raw_chat.get('book_id'), 180),
            'book_title': _clean_chat_string(raw_chat.get('book_title'), 240) or 'Kitap',
            'book_cover': _clean_chat_string(raw_chat.get('book_cover'), 500),
            'drive_id': _clean_chat_string(raw_chat.get('drive_id'), 180),
            'book_grade': _clean_chat_string(raw_chat.get('book_grade'), 16),
            'title': _clean_chat_string(raw_chat.get('title'), 120) or 'Yeni sohbet',
            'messages': messages,
            'created_at': created_at,
            'updated_at': updated_at,
        })

    clean_chats.sort(key=lambda chat: chat.get('updated_at') or '', reverse=True)
    return {'chats': clean_chats[:CHAT_HISTORY_MAX_CHATS]}


def load_chat_history():
    if os.path.exists(CHAT_HISTORY_FILE):
        try:
            with open(CHAT_HISTORY_FILE, 'r', encoding='utf-8') as f:
                return _sanitize_persisted_chat_store(json.load(f))
        except Exception:
            return {'chats': []}
    return {'chats': []}


def save_chat_history(store):
    clean_store = _sanitize_persisted_chat_store(store)
    history_path = Path(CHAT_HISTORY_FILE)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = history_path.with_suffix(history_path.suffix + '.' + uuid.uuid4().hex + '.tmp')
    with _chat_history_lock:
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(clean_store, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, history_path)
    return clean_store


ANALYSIS_CONTEXT_CHAR_LIMIT = _env_int("ANALYSIS_CONTEXT_CHAR_LIMIT", 22000)
ANALYSIS_FALLBACK_CHAR_LIMIT = _env_int("ANALYSIS_FALLBACK_CHAR_LIMIT", 6000)
ANALYSIS_MAX_PAGES = _env_int("ANALYSIS_MAX_PAGES", 4)
ANALYSIS_PAGE_RADIUS = _env_int("ANALYSIS_PAGE_RADIUS", 2)
ANALYSIS_TIMEOUT_MS = _env_int("ANALYSIS_TIMEOUT_MS", 120000)
ANALYSIS_MAX_OUTPUT_TOKENS = _env_int("ANALYSIS_MAX_OUTPUT_TOKENS", 0)
if ANALYSIS_MAX_OUTPUT_TOKENS <= 0:
    ANALYSIS_MAX_OUTPUT_TOKENS = None
ANALYSIS_RETRY_COUNT = max(1, _env_int("ANALYSIS_RETRY_COUNT", 1))
WEBVIEW_WINDOW_TITLE = "ReylAI"
WEBVIEW_START_TIMEOUT = 20
_batch_scan_lock = threading.Lock()
_batch_scan_job = {
    'job_id': 0,
    'running': False,
    'completed': False,
    'cancel_requested': False,
    'cancelled': False,
    'total': 0,
    'processed': 0,
    'success': 0,
    'failed': 0,
    'already_ready': 0,
    'current_title': '',
    'current_message': 'Hazır.',
    'logs': [],
    'started_at': '',
    'finished_at': '',
}
_analysis_status_lock = threading.Lock()
_analysis_status = {}
ANALYSIS_STATUS_TTL_SECONDS = 300
_PROMPT_STOPWORDS = {
    've', 'veya', 'ile', 'icin', 'için', 'bir', 'bu', 'su', 'şu', 'the', 'and',
    'ama', 'fakat', 'gibi', 'olan', 'olarak', 'neden', 'niye', 'hangi', 'nedenleri',
    'bana', 'bunu', 'kitap', 'sayfa', 'konu', 'soru', 'gore', 'göre', 'lütfen',
}
_SMALL_TALK_TERMS = {
    'selam', 'merhaba', 'hey', 'hi', 'hello', 'nasilsin', 'sa', 'naber'
}
_BOOK_INTENT_TERMS = {
    'kitap', 'sayfa', 'sf', 'soru', 'soruyu', 'sorular', 'cevap', 'cevabı',
    'cevabi', 'çöz', 'coz', 'çözüm', 'cozum', 'açıkla', 'açikla', 'acikla', 'anlat',
    'özet', 'ozet', 'analiz', 'incele', 'konu', 'ünite', 'unite', 'tema',
    'metin', 'paragraf', 'etkinlik', 'etkinli', 'alıştırma', 'aliştirma', 'alistirma', 'test',
    'örnek', 'ornek', 'pdf', 'oku', 'okuma', 'göster', 'goster', 'bul',
    'değerlendir', 'degerlendir', 'müfredat', 'mufredat', 'kazanım',
    'kazanim', 'ders', 'odev', 'ödev', 'ödevi', 'odevi', 'egzersiz', 'exercise',
    'activity', 'yap', 'yapar', 'yapabilir', 'hazırla', 'hazirla', 'tamamla',
    'performans', 'proje'
}
_CHAT_HELP_TERMS = {
    'yardım', 'yardim', 'ne yapabilirsin', 'nasıl kullanılır', 'nasil kullanilir',
    'ne ise yararsin', 'ne işe yararsın'
}
_SOLUTION_TERMS = {
    'çöz', 'coz', 'açıkla', 'acikla', 'anlat', 'göster', 'goster', 'ispatla',
    'bul', 'hesapla', 'cevapla', 'yanıtla', 'yorumla', 'değerlendir', 'degerlendir',
    'yap', 'hazırla', 'hazirla', 'tamamla'
}
_LIST_ONLY_TERMS = {
    'listele', 'sırala', 'sirala', 'yalnızca listele', 'yalnizca listele',
    'sadece listele', 'sadece yaz', 'yalnızca yaz', 'yalnizca yaz'
}
_EXERCISE_HINT_TERMS = {
    'örnek', 'ornek', 'sıra sizde', 'sira sizde', 'etkinlik', 'etkinli', 'çalışma',
    'calisma', 'uygulama', 'test', 'değerlendirme', 'degerlendirme',
    'aşağıdaki', 'asagidaki', 'çözüm', 'cozum', 'ispat', 'bulunuz',
    'gösteriniz', 'gosteriniz', 'açıklayınız', 'aciklayiniz', 'performans',
    'proje', 'ödev', 'odev', 'ödevi', 'odevi'
}


def _book_scan_key(book):
    return (book or {}).get('book_id') or (book or {}).get('drive_id', '')


def _book_cover_path(book):
    cover_path = _resolve_app_path((book or {}).get('cover_path') or '')
    if cover_path and os.path.exists(cover_path):
        return cover_path
    scan_key = _book_scan_key(book)
    if not scan_key:
        return ''
    candidate = os.path.join(COVERS_DIR, scan_key + '.jpg')
    return candidate if os.path.exists(candidate) else ''


def _book_cover_data_url(book):
    cover_path = _book_cover_path(book)
    return _file_data_url(cover_path) if cover_path else ''


def _public_book_payload(book):
    payload = dict(book or {})
    if str(payload.get('scan_extractor') or '').lower() == _LEGACY_REMOTE_EXTRACTOR:
        payload['scan_extractor'] = 'pypdf'
    remote_pdf_url = _book_remote_pdf_url(payload)
    if remote_pdf_url and (
        payload.get('pdf_url') or payload.get('source_url') or not _book_local_pdf_exists(payload)
    ):
        payload['pdf_url'] = remote_pdf_url
        payload.setdefault('pdf_source', 'book_archive')
    cover_data_url = _book_cover_data_url(payload)
    if cover_data_url:
        payload['cover_data_url'] = cover_data_url
    return payload


def _public_scan_extractor(extractor):
    extractor = str(extractor or '')
    return 'pypdf' if extractor.lower() == _LEGACY_REMOTE_EXTRACTOR else extractor


def _clean_book_identifier(value):
    return str(value or '').strip()


def _is_valid_remote_pdf_url(url):
    url = str(url or '').strip()
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    return parsed.scheme in ('http', 'https') and parsed.path.lower().endswith('.pdf')


def _book_local_pdf_exists(book):
    local_path = _resolve_app_path((book or {}).get('local_path', ''))
    return bool(local_path and os.path.exists(local_path))


def _book_remote_pdf_url(book):
    book = book or {}
    for field in ('pdf_url', 'source_url', 'remote_url'):
        url = str(book.get(field) or '').strip()
        if _is_valid_remote_pdf_url(url):
            return url

    book_id = _clean_book_identifier(book.get('book_id'))
    if not book_id or not BOOKS_REMOTE_BASE_URL or not _REMOTE_BOOK_ID_RE.fullmatch(book_id):
        return ''
    return urljoin(BOOKS_REMOTE_BASE_URL, quote(book_id + '.pdf'))


def _book_pdf_cache_path(book, source_url=''):
    book = book or {}
    grade = (book.get('grade') or '9').strip() or '9'
    grade_dir = os.path.join(BOOKS_DIR, grade)
    os.makedirs(grade_dir, exist_ok=True)
    cache_key = _clean_book_identifier(book.get('book_id'))
    if not cache_key:
        cache_key = hashlib.sha1(str(source_url or '').encode('utf-8')).hexdigest()
    cache_key = re.sub(r'[^0-9A-Za-z._-]+', '-', cache_key).strip('-') or 'book'
    return os.path.join(grade_dir, cache_key + '.pdf')


def _find_library_book_for_selection(library, selected_id):
    selected_id = _clean_book_identifier(selected_id)
    if not selected_id:
        return None
    exact_book = next(
        (book for book in library if _clean_book_identifier(book.get('book_id')) == selected_id),
        None,
    )
    if exact_book:
        return exact_book
    return next(
        (book for book in library if _clean_book_identifier(book.get('drive_id')) == selected_id),
        None,
    )


def _scan_keys_for_book(book, selected_id=''):
    keys = []
    for key in (selected_id, (book or {}).get('book_id'), (book or {}).get('drive_id')):
        key = _clean_book_identifier(key)
        if key and key not in keys:
            keys.append(key)
    return keys


def _scan_path_for(book_or_id):
    scan_key = book_or_id if isinstance(book_or_id, str) else _book_scan_key(book_or_id)
    return os.path.join(SCANS_DIR, scan_key + '.json') if scan_key else ''


def _book_needs_scan(book):
    scan_path = _scan_path_for(book)
    return bool(scan_path) and not os.path.exists(scan_path)


def _batch_scan_snapshot():
    with _batch_scan_lock:
        data = dict(_batch_scan_job)
        data['logs'] = list(_batch_scan_job.get('logs', []))
        return data


def _batch_scan_update(**kwargs):
    with _batch_scan_lock:
        _batch_scan_job.update(kwargs)


def _batch_scan_log(message):
    with _batch_scan_lock:
        logs = list(_batch_scan_job.get('logs', []))
        logs.append(message)
        _batch_scan_job['logs'] = logs[-80:]


def _finish_batch_scan(summary, *, processed=None, success=None, failed=None, cancelled=False):
    updates = {
        'running': False,
        'completed': True,
        'cancel_requested': False,
        'cancelled': cancelled,
        'current_title': '',
        'current_message': summary,
        'finished_at': _utc_now_iso(),
    }
    if processed is not None:
        updates['processed'] = processed
    if success is not None:
        updates['success'] = success
    if failed is not None:
        updates['failed'] = failed
    _batch_scan_update(**updates)


def _clean_analysis_id(analysis_id):
    analysis_id = str(analysis_id or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', analysis_id):
        return ''
    return analysis_id


def _analysis_status_update(analysis_id, message, stage='working', done=False, **extra):
    analysis_id = _clean_analysis_id(analysis_id)
    if not analysis_id:
        return
    now = time.time()
    with _analysis_status_lock:
        stale_ids = [
            key for key, value in _analysis_status.items()
            if now - float(value.get('updated_ts', now)) > ANALYSIS_STATUS_TTL_SECONDS
        ]
        for key in stale_ids:
            _analysis_status.pop(key, None)
        status = {
            'message': message,
            'stage': stage,
            'done': bool(done),
            'updated_at': _utc_now_iso(),
            'updated_ts': now,
        }
        status.update(extra)
        _analysis_status[analysis_id] = status


def _analysis_status_snapshot(analysis_id):
    analysis_id = _clean_analysis_id(analysis_id)
    if not analysis_id:
        return None
    with _analysis_status_lock:
        status = _analysis_status.get(analysis_id)
        return dict(status) if status else None


def _run_batch_scan_job(job_id):
    initial_snapshot = _batch_scan_snapshot()
    library = load_library()
    targets = [book for book in library if _book_scan_key(book) and _book_needs_scan(book)]
    already_ready = max(len([book for book in library if _book_scan_key(book)]) - len(targets), 0)

    _batch_scan_update(
        total=len(targets),
        processed=0,
        success=0,
        failed=0,
        already_ready=already_ready,
        current_title='',
        current_message='Taranacak kitaplar hazırlanıyor…',
        logs=list(initial_snapshot.get('logs', [])),
        started_at=_utc_now_iso(),
        finished_at='',
        completed=False,
        running=True,
        cancelled=False,
    )

    if not targets:
        _batch_scan_log('Tüm kitaplar zaten analiz için hazır.')
        _finish_batch_scan('Tüm kitaplar zaten analiz için hazır.')
        return

    success = 0
    failed = 0

    for index, book in enumerate(targets, start=1):
        snapshot = _batch_scan_snapshot()
        if snapshot.get('job_id') != job_id or snapshot.get('cancel_requested'):
            summary = f'Tarama iptal edildi. {success} kitap hazırlandı, {failed} kitapta sorun vardı.'
            _batch_scan_log('İptal isteği alındı. İşlem güvenli şekilde durduruldu.')
            _finish_batch_scan(summary, processed=index - 1, success=success, failed=failed, cancelled=True)
            return

        scan_key = _book_scan_key(book)
        title = book.get('title') or book.get('name') or scan_key
        _batch_scan_update(
            processed=index - 1,
            current_title=title,
            current_message=f'{index}/{len(targets)}: {title} taranıyor…',
        )
        _batch_scan_log(f'{index}/{len(targets)}: {title} taranıyor…')

        remote_url = _book_remote_pdf_url(book)
        local_path = '' if remote_url else _ensure_local_pdf(book, library)
        drive_id = '' if remote_url else book.get('drive_id', '')
        try:
            _do_scan(scan_key, local_path=local_path, drive_id=drive_id, remote_url=remote_url)
        except Exception:
            pass

        fresh_library = load_library()
        fresh_book = next((item for item in fresh_library if _book_scan_key(item) == scan_key), None)
        status = (fresh_book or {}).get('scan_status', '')
        pages = int((fresh_book or {}).get('scan_pages', 0) or 0)
        done = os.path.exists(_scan_path_for(scan_key)) and status == 'done'

        if done:
            success += 1
            _batch_scan_log(f'{title} hazır. {pages} sayfa tarandı.')
        else:
            failed += 1
            _batch_scan_log(f'{title} taranamadı. Lütfen kitabı kontrol edin.')

        _batch_scan_update(
            processed=index,
            success=success,
            failed=failed,
            current_title=title,
            current_message='İptal isteği alındı. Mevcut kitap tamamlandıktan sonra duracak…'
            if _batch_scan_snapshot().get('cancel_requested')
            else f'{index}/{len(targets)} kitap işlendi.',
        )

        if _batch_scan_snapshot().get('cancel_requested'):
            summary = f'Tarama iptal edildi. {success} kitap hazırlandı, {failed} kitapta sorun vardı.'
            _batch_scan_log('İptal isteği uygulandı. Tarama sonlandırıldı.')
            _finish_batch_scan(summary, processed=index, success=success, failed=failed, cancelled=True)
            return

    summary = (
        f'Tarama tamamlandı. {success} kitap hazır, {failed} kitapta sorun var.'
        if failed
        else f'Tarama tamamlandı. {success} kitap analiz için hazır.'
    )
    _batch_scan_log(summary)
    _finish_batch_scan(summary, success=success, failed=failed)


_PDF_LITERAL_ESCAPES = {
    ord('n'): '\n',
    ord('r'): '\r',
    ord('t'): '\t',
    ord('b'): '\b',
    ord('f'): '\f',
}


def _decode_pdf_literal(raw):
    out = bytearray()
    index = 0
    size = len(raw)
    while index < size:
        value = raw[index]
        if value == 92 and index + 1 < size:  # backslash
            index += 1
            nxt = raw[index]
            if nxt in _PDF_LITERAL_ESCAPES:
                out.extend(_PDF_LITERAL_ESCAPES[nxt].encode('utf-8'))
            elif nxt in (40, 41, 92):  # (, ), \
                out.append(nxt)
            elif 48 <= nxt <= 55:
                octal = bytearray([nxt])
                while index + 1 < size and len(octal) < 3 and 48 <= raw[index + 1] <= 55:
                    index += 1
                    octal.append(raw[index])
                out.append(int(octal.decode('ascii'), 8))
            elif nxt in (10, 13):
                if nxt == 13 and index + 1 < size and raw[index + 1] == 10:
                    index += 1
            else:
                out.append(nxt)
        else:
            out.append(value)
        index += 1

    text = out.decode('utf-8', errors='ignore').strip()
    if not text:
        text = out.decode('latin-1', errors='ignore').strip()
    return text


def _fallback_extract_pdf_text(source_path):
    try:
        raw = Path(source_path).read_bytes()
    except Exception:
        return None

    page_count = len(re.findall(rb'/Type\s*/Page\b', raw))
    fragments = []

    for match in re.finditer(rb'\((?:\\.|[^\\()])*\)\s*Tj', raw, re.S):
        literal = match.group(0).rsplit(b')', 1)[0][1:]
        text = _decode_pdf_literal(literal)
        if text:
            fragments.append(text)

    for block in re.finditer(rb'\[(.*?)\]\s*TJ', raw, re.S):
        for literal in re.finditer(rb'\((?:\\.|[^\\()])*\)', block.group(1), re.S):
            text = _decode_pdf_literal(literal.group(0)[1:-1])
            if text:
                fragments.append(text)

    joined = ' '.join(part for part in fragments if part)
    joined = re.sub(r'\s+', ' ', joined).strip()

    if not page_count and not joined:
        return None

    if page_count <= 0:
        page_count = 1

    if page_count > 1:
        return None

    pages = []
    for page_number in range(1, page_count + 1):
        pages.append({'page': page_number, 'text': joined if page_number == 1 else ''})
    return pages


def _scan_pages_are_usable(pages, total_pages=None):
    if not pages:
        return False
    total_pages = total_pages or len(pages)
    lengths = [len((page.get('text') or '').strip()) for page in pages]
    total_chars = sum(lengths)
    nonempty_count = sum(1 for length in lengths if length > 0)
    max_len = max(lengths) if lengths else 0
    if total_chars < 5 or nonempty_count <= 0:
        return False
    if total_pages > 3 and nonempty_count <= 1:
        return False
    if total_pages > 10 and nonempty_count <= 2 and max_len > 200000:
        return False
    return True


def _scan_data_is_usable(scan_data):
    if not isinstance(scan_data, dict):
        return False
    pages = scan_data.get('pages') or []
    return _scan_pages_are_usable(pages, scan_data.get('total_pages') or len(pages))


def _scan_data_matches_book(scan_data, book, selected_id=''):
    embedded_keys = [
        _clean_book_identifier(scan_data.get('book_id')),
        _clean_book_identifier(scan_data.get('drive_id')),
        _clean_book_identifier(scan_data.get('scan_key')),
    ]
    embedded_keys = [key for key in embedded_keys if key]
    if not embedded_keys:
        return True
    expected_keys = set(_scan_keys_for_book(book, selected_id))
    return any(key in expected_keys for key in embedded_keys)


def _load_scan_data_for_book(book, selected_id=''):
    for scan_key in _scan_keys_for_book(book, selected_id):
        scan_path = _scan_path_for(scan_key)
        if not scan_path or not os.path.exists(scan_path):
            continue
        try:
            with open(scan_path, 'r', encoding='utf-8') as f:
                scan_data = json.load(f)
        except Exception:
            continue
        if _scan_data_is_usable(scan_data) and _scan_data_matches_book(scan_data, book, selected_id):
            return scan_data, scan_path, scan_key
    return None, '', ''


def _sync_library_scan_status(library, book, scan_data):
    if not book or not _scan_data_is_usable(scan_data):
        return False
    pages = int(scan_data.get('total_pages') or len(scan_data.get('pages') or []) or 0)
    extractor = _public_scan_extractor(scan_data.get('extractor') or '')
    changed = False
    if book.get('scan_status') != 'done':
        book['scan_status'] = 'done'
        changed = True
    if pages and int(book.get('scan_pages', 0) or 0) != pages:
        book['scan_pages'] = pages
        changed = True
    if extractor and book.get('scan_extractor') != extractor:
        book['scan_extractor'] = extractor
        changed = True
    if changed:
        save_library(library)
    return changed


def _extract_pdf_pages(source_path):
    if _HAS_PYPDF:
        try:
            reader = _PdfReader(source_path)
            pages_data = []
            for i, page in enumerate(reader.pages):
                try:
                    text = (page.extract_text() or '').strip()
                except Exception:
                    text = ''
                pages_data.append({'page': i + 1, 'text': text})
            if _scan_pages_are_usable(pages_data, len(reader.pages)):
                return pages_data, 'pypdf'
        except Exception:
            pass

    fallback_data = _fallback_extract_pdf_text(source_path)
    if _scan_pages_are_usable(fallback_data):
        return fallback_data, 'basic'

    return None, ''


def _query_terms(text):
    words = re.findall(r"[0-9A-Za-zÇĞİÖŞÜçğıöşü]{2,}", (text or "").lower())
    return [word for word in words if word not in _PROMPT_STOPWORDS]


def _normalize_prompt_text(prompt_text):
    clean = (prompt_text or '').casefold()
    clean = clean.replace('ı', 'i')
    clean = re.sub(r"[^\wÇĞİÖŞÜçğıöşü]+", " ", clean, flags=re.UNICODE)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def _is_book_related_prompt(prompt_text):
    clean = _normalize_prompt_text(prompt_text)
    if not clean:
        return False
    tokens = set(clean.split())
    stem_terms = {
        'kitap', 'sayfa', 'soru', 'cevap', 'çöz', 'coz', 'açıkla', 'açikla',
        'acikla', 'özet', 'ozet', 'analiz', 'konu', 'ünite', 'unite', 'tema',
        'metin', 'paragraf', 'etkinli', 'alıştırma', 'aliştirma', 'alistirma',
        'örnek', 'ornek', 'değerlendir', 'degerlendir', 'müfredat', 'mufredat',
        'kazanım', 'kazanim', 'ödev', 'odev', 'performans', 'proje', 'egzersiz',
        'exercise', 'activity', 'hazırla', 'hazirla', 'tamamla'
    }
    if tokens & _BOOK_INTENT_TERMS:
        return True
    if any(term in clean for term in stem_terms):
        return True
    if re.search(r"\b\d{1,4}\b", clean):
        return True
    if any(pattern in clean for pattern in ('bunu yap', 'bunu cevapla', 'bunu cozer', 'bunu çöz')):
        return True
    return False


def _is_explicit_small_talk_prompt(prompt_text):
    clean = _normalize_prompt_text(prompt_text)
    if not clean:
        return False
    tokens = set(clean.split())
    if clean in _SMALL_TALK_TERMS:
        return True
    if any(term in clean for term in ('tesekkur', 'teşekkür', 'sag ol', 'sağ ol', 'eyvallah')):
        return True
    if any(term in clean for term in ('kimsin', 'sen nesin', 'adın ne', 'adin ne')):
        return True
    if any(term in clean for term in ('yavas', 'yavaş', 'bekliyorum', 'cevap vermiyorsun')):
        return True
    if any(term in clean for term in _CHAT_HELP_TERMS):
        return True
    if any(term in clean for term in (
        'nasilsin', 'nasılsın', 'naber', 'ne haber', 'napıyorsun',
        'napiyorsun', 'yapıyorsun', 'yapiyorsun'
    )):
        return True
    if tokens and tokens.issubset(_SMALL_TALK_TERMS):
        return True
    return False


def _is_small_talk_prompt(prompt_text):
    clean = _normalize_prompt_text(prompt_text)
    if not clean:
        return True
    if _is_book_related_prompt(prompt_text):
        return False
    return _is_explicit_small_talk_prompt(prompt_text)


def _small_talk_response(prompt_text):
    clean = _normalize_prompt_text(prompt_text)
    if any(term in clean for term in ('tesekkur', 'teşekkür', 'sag ol', 'sağ ol', 'eyvallah')):
        return 'Rica ederim. Buradayım; kitapla ilgili bir soru, sayfa veya konu yazarsan hemen yardımcı olurum.'
    if any(term in clean for term in ('kimsin', 'sen nesin', 'adın ne', 'adin ne')):
        return 'Ben ReylAI. Ders kitaplarındaki sayfa, soru ve konuları hızlıca açıklamak için buradayım.'
    if any(term in clean for term in ('yavas', 'yavaş', 'bekliyorum', 'cevap vermiyorsun')):
        return 'Haklısın, kısa sohbetlerde bekletmemem gerekiyor. Kitap dışı mesajlara hızlı cevap vereceğim.'
    if any(term in clean for term in _CHAT_HELP_TERMS):
        return 'Bir sayfa numarası, soru ya da konu yazarsan seçili kitaba göre açıklama, özet veya çözüm hazırlayabilirim.'
    if any(term in clean for term in (
        'nasilsin', 'nasılsın', 'naber', 'ne haber', 'napıyorsun',
        'napiyorsun', 'yapıyorsun', 'yapiyorsun'
    )):
        return 'İyiyim, buradayım. Kitaptan bir sayfa, soru veya konu yazarsan hemen yardımcı olurum.'
    if any(term in clean for term in ('selam', 'merhaba', 'mrb', 'slm', 'sa', 'hey', 'hello', 'hi')):
        return 'Merhaba, buradayım. Kitaptaki bir soru, sayfa veya konuyu yaz; hemen yardımcı olayım.'
    return 'Buradayım. Kitapla ilgili değilse kısa sohbeti hızlıca cevaplarım; kitap için sayfa, soru veya konu yazman yeterli.'


def _analysis_timeout_seconds():
    return max(1, ANALYSIS_TIMEOUT_MS / 1000)


def _mistral_generation_params(max_tokens=None, temperature=0.2):
    params = {'temperature': temperature}
    token_limit = ANALYSIS_MAX_OUTPUT_TOKENS if max_tokens is None else max_tokens
    if token_limit:
        params['max_tokens'] = token_limit
    return params


def _mistral_content_to_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for chunk in content:
            if isinstance(chunk, dict):
                text = chunk.get('text')
                if text is None:
                    text = chunk.get('content')
                if text:
                    parts.append(str(text))
            elif chunk is not None:
                parts.append(str(chunk))
        return ''.join(parts)
    if content is None:
        return ''
    return str(content)


def _mistral_response_text(payload):
    choices = payload.get('choices') if isinstance(payload, dict) else None
    if not choices:
        return ''
    message = choices[0].get('message') if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        return ''
    return _mistral_content_to_text(message.get('content')).strip()


def _mistral_error_message(response):
    try:
        payload = response.json()
    except ValueError:
        return response.text[:500]
    error = payload.get('error') if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return str(error.get('message') or error.get('type') or error)[:500]
    if error:
        return str(error)[:500]
    if isinstance(payload, dict) and payload.get('message'):
        return str(payload.get('message'))[:500]
    return response.text[:500]


def _mistral_chat_complete(messages, *, model=None, max_tokens=None, temperature=0.2):
    payload = {
        'model': model or MISTRAL_MODEL,
        'messages': messages,
        'stream': False,
    }
    payload.update(_mistral_generation_params(max_tokens=max_tokens, temperature=temperature))
    try:
        response = requests.post(
            MISTRAL_CHAT_URL,
            headers={
                'Authorization': f'Bearer {MISTRAL_API_KEY}',
                'Content-Type': 'application/json',
            },
            json=payload,
            timeout=_analysis_timeout_seconds(),
        )
    except requests.RequestException as exc:
        raise RuntimeError(f'Mistral API bağlantı hatası: {exc}') from exc
    if response.status_code >= 400:
        message = _mistral_error_message(response) or response.reason
        raise RuntimeError(f'Mistral API hatası ({response.status_code}): {message}')
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError('Mistral API geçersiz JSON yanıtı döndürdü.') from exc


def _sanitize_chat_history(raw_history):
    if not isinstance(raw_history, list):
        return []
    clean_history = []
    for item in raw_history[-10:]:
        if not isinstance(item, dict):
            continue
        role = item.get('role')
        if role not in ('user', 'ai'):
            continue
        text = re.sub(r'\s+', ' ', str(item.get('text') or '')).strip()
        if not text:
            continue
        clean_history.append({'role': role, 'text': text[:1800]})
    return clean_history


def _build_chat_history_context(chat_history):
    if not chat_history:
        return ''
    lines = []
    for item in chat_history:
        label = 'Kullanici' if item.get('role') == 'user' else 'ReylAI'
        lines.append(f"{label}: {item.get('text', '')}")
    return '\n'.join(lines)


def _clean_chat_title(title):
    title = re.sub(r'[`*_>#\[\]()"“”‘’]+', ' ', str(title or ''))
    title = re.sub(r'\s+', ' ', title).strip(' .:-')
    if not title:
        return ''
    if len(title) > 64:
        title = title[:61].rstrip() + '...'
    return title


def _fallback_chat_title(prompt_text):
    clean = _clean_chat_title(prompt_text)
    return clean or 'Yeni sohbet'


def _generate_chat_title(book_name, prompt_text, response_text):
    fallback = _fallback_chat_title(prompt_text)
    title_prompt = (
        'Aşağıdaki ders kitabı sohbeti için Türkçe, kısa ve doğal bir başlık yaz. '
        'Sadece başlığı döndür; tırnak, açıklama veya madde işareti kullanma. '
        'En fazla 6 kelime olsun.\n\n'
        f'Kitap: {book_name}\n'
        f'Kullanıcı sorusu: {prompt_text}\n'
        f'Cevap özeti: {str(response_text or "")[:700]}'
    )
    try:
        title_response = _mistral_chat_complete(
            [{'role': 'user', 'content': title_prompt}],
            max_tokens=32,
            temperature=0.1,
        )
        return _clean_chat_title(_mistral_response_text(title_response)) or fallback
    except Exception:
        return fallback


def _is_solution_request(prompt_text):
    prompt_lower = (prompt_text or "").lower()
    return any(term in prompt_lower for term in _SOLUTION_TERMS)


def _is_list_only_request(prompt_text):
    prompt_lower = (prompt_text or "").lower()
    return any(term in prompt_lower for term in _LIST_ONLY_TERMS)


def _is_expanded_work_request(prompt_text):
    clean = _normalize_prompt_text(prompt_text)
    if not clean:
        return False
    tokens = set(clean.split())
    action_terms = {'yap', 'yapar', 'yapabilir', 'hazırla', 'hazirla', 'tamamla'}
    work_terms = {
        'etkinlik', 'etkinli', 'performans', 'proje', 'ödev', 'odev',
        'çalışma', 'çalişma', 'calisma', 'alıştırma', 'aliştirma', 'alistirma',
        'hazırla', 'hazirla', 'tamamla'
    }
    return bool(tokens & action_terms) or any(term in clean for term in work_terms)


def _extract_page_numbers(prompt_text):
    text = (prompt_text or "").lower()
    found = []

    for start_str, end_str in re.findall(r"sayfa\s*(\d{1,4})\s*[-–]\s*(\d{1,4})", text):
        start = int(start_str)
        end = int(end_str)
        if start > end:
            start, end = end, start
        found.extend(range(start, end + 1))

    for start_str, end_str in re.findall(r"(\d{1,4})\.\s*sayfa\s*[-–]\s*(\d{1,4})\.\s*sayfa", text):
        start = int(start_str)
        end = int(end_str)
        if start > end:
            start, end = end, start
        found.extend(range(start, end + 1))

    for num_str in re.findall(r"(?:sayfa|sf)\s*(\d{1,4})", text):
        found.append(int(num_str))

    for num_str in re.findall(r"(\d{1,4})\.?\s*(?:sayfa|sf)\w*", text):
        found.append(int(num_str))

    page_numbers = []
    seen = set()
    for page_no in found:
        if page_no not in seen:
            seen.add(page_no)
            page_numbers.append(page_no)
    return page_numbers


def _append_page_window(target, seen_pages, pages_by_no, center_page, radius):
    for page_no in range(center_page - radius, center_page + radius + 1):
        text = pages_by_no.get(page_no)
        if text and page_no not in seen_pages:
            target.append({'page': page_no, 'text': text})
            seen_pages.add(page_no)


def _pick_context_pages(scan_pages, prompt_text):
    if not scan_pages or _is_small_talk_prompt(prompt_text):
        return []

    prompt_lower = (prompt_text or "").lower()
    terms = [word for word in _query_terms(prompt_text) if len(word) >= 3][:12]
    pages_by_no = {
        page.get('page'): (page.get('text') or '').strip()
        for page in scan_pages
        if page.get('page') and (page.get('text') or '').strip()
    }
    selected_pages = []
    seen_pages = set()
    requested_pages = _extract_page_numbers(prompt_text)
    nearby_request = any(term in prompt_lower for term in ('civar', 'yakın', 'yaklasik', 'yaklaşık'))
    radius = ANALYSIS_PAGE_RADIUS if nearby_request else (1 if len(requested_pages) == 1 else 0)

    for page_no in requested_pages:
        _append_page_window(selected_pages, seen_pages, pages_by_no, page_no, radius)

    if selected_pages:
        selected_pages = sorted(selected_pages, key=lambda item: item['page'])
        if len(selected_pages) > ANALYSIS_MAX_PAGES:
            selected_pages = selected_pages[:ANALYSIS_MAX_PAGES]
        return selected_pages

    scored_pages = []
    solution_request = _is_solution_request(prompt_text) and not _is_list_only_request(prompt_text)

    for page in scan_pages:
        page_no = page.get('page')
        text = (page.get('text') or '').strip()
        if not page_no or not text:
            continue

        lower_text = text.lower()
        score = 0
        if prompt_lower and prompt_lower in lower_text:
            score += 20
        for term in terms:
            hits = lower_text.count(term)
            if hits:
                score += min(hits, 5) * 3
        if solution_request and any(term in lower_text for term in _EXERCISE_HINT_TERMS):
            score += 8

        if score > 0:
            scored_pages.append((score, page_no, text))

    if not scored_pages:
        return []

    top_pages = sorted(scored_pages, key=lambda item: (-item[0], item[1]))[:ANALYSIS_MAX_PAGES]
    neighbor_radius = 1 if solution_request else 0
    for _, page_no, _ in sorted(top_pages, key=lambda item: item[1]):
        _append_page_window(selected_pages, seen_pages, pages_by_no, page_no, neighbor_radius)

    selected_pages = sorted(selected_pages, key=lambda item: item['page'])
    if len(selected_pages) > ANALYSIS_MAX_PAGES:
        selected_pages = selected_pages[:ANALYSIS_MAX_PAGES]
    return selected_pages


def _build_context_excerpt(scan_pages, prompt_text):
    selected_pages = _pick_context_pages(scan_pages, prompt_text)
    char_limit = ANALYSIS_CONTEXT_CHAR_LIMIT if selected_pages else ANALYSIS_FALLBACK_CHAR_LIMIT

    if not selected_pages and not _is_small_talk_prompt(prompt_text):
        selected_pages = []
        for page in scan_pages:
            text = (page.get('text') or '').strip()
            if text:
                selected_pages.append({'page': page.get('page'), 'text': text})
            if len(selected_pages) >= 3:
                break

    excerpt_parts = []
    total_chars = 0
    for page in selected_pages:
        page_no = page.get('page')
        text = (page.get('text') or '').strip()
        if not page_no or not text:
            continue

        part = f"[Sayfa {page_no}]\n{text}"
        if total_chars + len(part) > char_limit:
            remaining = char_limit - total_chars
            if remaining > 200:
                excerpt_parts.append(part[:remaining])
            break

        excerpt_parts.append(part)
        total_chars += len(part)

    return '\n\n'.join(excerpt_parts)


def _clean_title(filename):
    """Clean up a Drive filename to use as display title."""
    t = filename
    if t.lower().endswith('.pdf'):
        t = t[:-4]
    return t.replace('_', ' ').replace('-', ' ').strip()


def _download_from_drive(drive_id, dest_path):
    """Download a file from Google Drive by file_id. Returns True on success."""
    try:
        url = f'https://drive.google.com/uc?export=download&id={drive_id}'
        r = requests.get(url, timeout=90, stream=True)
        if r.status_code == 200:
            with open(dest_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
            return True
    except Exception:
        pass
    return False


def _download_from_url(url, dest_path):
    """Download a remote PDF to a local cache path. Returns True on success."""
    if not _is_valid_remote_pdf_url(url):
        return False
    tmp_path = dest_path + '.' + uuid.uuid4().hex + '.tmp'
    response = None
    try:
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        response = requests.get(
            url,
            timeout=180,
            stream=True,
            allow_redirects=True,
            headers={'User-Agent': 'ReylAI/1.0'},
        )
        if response.status_code != 200:
            return False
        with open(tmp_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) < 5:
            return False
        with open(tmp_path, 'rb') as f:
            header = f.read(1024)
        if b'%PDF' not in header:
            return False
        os.replace(tmp_path, dest_path)
        return True
    except Exception:
        return False
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def _decorate_pdf_response(response, content_length=None):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Range'
    response.headers['Accept-Ranges'] = 'bytes'
    response.headers['Cache-Control'] = 'public, max-age=3600, immutable'
    if content_length is not None:
        response.headers['Content-Length'] = content_length
    return response


def _proxy_remote_pdf(url, range_header=''):
    if not _is_valid_remote_pdf_url(url):
        return None
    headers = {'User-Agent': 'ReylAI/1.0'}
    if range_header:
        headers['Range'] = range_header
    remote = None
    try:
        remote = requests.get(
            url,
            headers=headers,
            timeout=120,
            stream=True,
            allow_redirects=True,
        )
        if remote.status_code not in (200, 206):
            try:
                remote.close()
            except Exception:
                pass
            return None

        def generate():
            try:
                for chunk in remote.iter_content(chunk_size=65536):
                    if chunk:
                        yield chunk
            finally:
                try:
                    remote.close()
                except Exception:
                    pass

        response = Response(
            stream_with_context(generate()),
            status=remote.status_code,
            mimetype='application/pdf',
        )
        for header_name in ('Content-Length', 'Content-Range', 'Last-Modified', 'ETag'):
            header_value = remote.headers.get(header_name)
            if header_value:
                response.headers[header_name] = header_value
        content_type = remote.headers.get('Content-Type') or ''
        response.headers['Content-Type'] = content_type if 'pdf' in content_type.lower() else 'application/pdf'
        return _decorate_pdf_response(response)
    except Exception:
        if remote is not None:
            try:
                remote.close()
            except Exception:
                pass
        return None


def _serve_local_pdf_response(local_path):
    file_size = os.path.getsize(local_path)
    range_header = request.headers.get('Range')
    if range_header:
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', range_header.strip())
        if match:
            start_text, end_text = match.groups()
            start = int(start_text) if start_text else 0
            end = int(end_text) if end_text else file_size - 1
            end = min(end, file_size - 1)
            if start <= end and start < file_size:
                length = end - start + 1
                with open(local_path, 'rb') as f:
                    f.seek(start)
                    data = f.read(length)
                resp = make_response(data)
                resp.status_code = 206
                resp.headers['Content-Type'] = 'application/pdf'
                resp.headers['Content-Range'] = 'bytes %d-%d/%d' % (start, end, file_size)
                return _decorate_pdf_response(resp, length)
        resp = make_response('')
        resp.status_code = 416
        resp.headers['Content-Range'] = 'bytes */%d' % file_size
        return _decorate_pdf_response(resp, 0)

    response = make_response(send_file(local_path, mimetype='application/pdf'))
    return _decorate_pdf_response(response, file_size)


def _ensure_local_pdf(book, library=None):
    """Ensure a book has a readable local PDF path for local/Drive sources only."""
    if not book:
        return ''

    local_path = _resolve_app_path(book.get('local_path', ''))
    if local_path and os.path.exists(local_path):
        return local_path

    remote_url = _book_remote_pdf_url(book)
    if remote_url:
        changed = False
        if book.get('pdf_url') != remote_url:
            book['pdf_url'] = remote_url
            changed = True
        if book.get('pdf_source') != 'book_archive':
            book['pdf_source'] = 'book_archive'
            changed = True
        if changed and library is not None:
            save_library(library)
        return ''

    drive_id = book.get('drive_id', '')
    if not drive_id:
        return ''

    grade = (book.get('grade') or '9').strip() or '9'
    grade_dir = os.path.join(BOOKS_DIR, grade)
    os.makedirs(grade_dir, exist_ok=True)

    cache_key = book.get('book_id') or drive_id
    cache_path = os.path.join(grade_dir, cache_key + '.pdf')
    if not os.path.exists(cache_path):
        if not _download_from_drive(drive_id, cache_path):
            return ''

    book['local_path'] = cache_path
    if library is not None:
        save_library(library)
    return cache_path


def _extract_cover(book_id, local_path):
    """Render first page of PDF as a cover JPEG. Returns cover_path or None."""
    if not _HAS_PDF2IMAGE or not os.path.exists(local_path):
        return None
    cover_path = os.path.join(COVERS_DIR, book_id + '.jpg')
    if os.path.exists(cover_path):
        return cover_path
    try:
        pages = _pdf2img(local_path, first_page=1, last_page=1, dpi=150)
        if pages:
            pages[0].save(cover_path, 'JPEG', quality=85)
            return cover_path
    except Exception:
        pass
    return None


def _page_image_cache_path(book_id, page_no):
    safe_book_id = re.sub(r'[^0-9A-Za-z._-]+', '-', str(book_id or '')).strip('-') or 'book'
    return os.path.join(PAGE_IMAGES_DIR, f'{safe_book_id}-p{int(page_no)}.jpg')


def _render_pdf_page_image(book, book_id, page_no):
    """Render one PDF page to a cached JPEG for chat image previews."""
    if not _HAS_PDF2IMAGE:
        return '', 'PDF sayfa görseli için pdf2image paketi gerekli.'
    if page_no < 1:
        return '', 'Sayfa numarası geçersiz.'

    library = load_library()
    book = book or _find_library_book_for_selection(library, book_id)
    if not book:
        return '', 'Kitap bulunamadı.'

    tmp_path = None
    remote_url = _book_remote_pdf_url(book)
    if remote_url:
        tmp_path = os.path.join(SCANS_DIR, f'_tmp_page_{uuid.uuid4().hex}.pdf')
        local_path = tmp_path if _download_from_url(remote_url, tmp_path) else ''
    else:
        local_path = _ensure_local_pdf(book, library)
    if not local_path or not os.path.exists(local_path):
        return '', 'PDF dosyası bulunamadı.'

    cache_path = _page_image_cache_path(book_id or _book_scan_key(book), page_no)
    try:
        source_mtime = os.path.getmtime(local_path)
        if os.path.exists(cache_path) and os.path.getmtime(cache_path) >= source_mtime:
            return cache_path, ''

        pages = _pdf2img(
            local_path,
            first_page=page_no,
            last_page=page_no,
            dpi=PAGE_IMAGE_DPI,
        )
        if not pages:
            return '', 'Sayfa görseli üretilemedi.'
        image = pages[0]
        if image.mode != 'RGB':
            image = image.convert('RGB')
        image.thumbnail((1400, 1800))
        image.save(cache_path, 'JPEG', quality=86, optimize=True)
        return cache_path, ''
    except Exception:
        return '', 'Sayfa görseli üretilemedi.'
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def _extract_title_from_cover(cover_path):
    """Use Mistral Vision to read the book title from the cover image."""
    if not _is_configured(MISTRAL_API_KEY, "YOUR_MISTRAL_API_KEY"):
        return None
    if not cover_path or not os.path.exists(cover_path):
        return None
    try:
        with open(cover_path, 'rb') as f:
            img_bytes = f.read()
        image_url = 'data:image/jpeg;base64,' + base64.b64encode(img_bytes).decode('ascii')
        response = _mistral_chat_complete(
            [
                {
                    'role': 'user',
                    'content': [
                        {
                            'type': 'text',
                            'text': (
                                'Bu bir ders kitabinin kapagidir. '
                                'Kapakta yazan kitabin tam adini Turkce olarak ver. '
                                'Sadece kitap adini yaz, baska hicbir sey ekleme. '
                                'Kapakta isim yoksa "Bilinmeyen Kitap" yaz.'
                            ),
                        },
                        {'type': 'image_url', 'image_url': image_url},
                    ],
                }
            ],
            model=MISTRAL_VISION_MODEL,
            max_tokens=80,
            temperature=0.1,
        )
        title = _mistral_response_text(response).strip()
        # Remove surrounding quotes if any
        title = title.strip('"').strip("'").strip()
        return title if title and title != 'Bilinmeyen Kitap' else None
    except Exception:
        return None


def _do_scan(book_id, local_path=None, drive_id=None, remote_url=None):
    """Extract text from PDF with local readers, then lightweight fallback."""
    scan_path = os.path.join(SCANS_DIR, book_id + '.json')
    if os.path.exists(scan_path):
        try:
            with open(scan_path, 'r', encoding='utf-8') as f:
                existing_scan = json.load(f)
        except Exception:
            existing_scan = None
        if _scan_data_is_usable(existing_scan):
            lib = load_library()
            for b in lib:
                bid = b.get('book_id') or b.get('drive_id', '')
                if bid == book_id and b.get('scan_status') == 'pending':
                    b['scan_status'] = 'done'
                    b['scan_pages']  = existing_scan.get('total_pages', 0)
                    if existing_scan.get('extractor'):
                        b['scan_extractor'] = existing_scan.get('extractor')
                    break
            save_library(lib)
            return
        try:
            os.remove(scan_path)
        except Exception:
            pass

    source_path = local_path if (local_path and os.path.exists(local_path)) else None
    tmp_path = None
    remote_url = remote_url if _is_valid_remote_pdf_url(remote_url) else ''
    if not source_path and remote_url:
        tmp_path = os.path.join(SCANS_DIR, f'_tmp_{uuid.uuid4().hex}.pdf')
        if not _download_from_url(remote_url, tmp_path):
            tmp_path = None
        else:
            source_path = tmp_path
    if not source_path and drive_id:
        tmp_path = os.path.join(SCANS_DIR, f'_tmp_{book_id}.pdf')
        if not _download_from_drive(drive_id, tmp_path):
            tmp_path = None
        else:
            source_path = tmp_path

    def _mark(status, pages=0, extractor=''):
        lib = load_library()
        for b in lib:
            bid = b.get('book_id') or b.get('drive_id', '')
            if bid == book_id:
                b['scan_status'] = status
                if pages:
                    b['scan_pages'] = pages
                if extractor:
                    b['scan_extractor'] = extractor
                break
        save_library(lib)

    if not source_path:
        _mark('failed')
        return

    try:
        pages_data, extractor = _extract_pdf_pages(source_path)
        if pages_data is None:
            _mark('failed')
            return

        with open(scan_path, 'w', encoding='utf-8') as f:
            json.dump({'book_id': book_id,
                       'drive_id': drive_id or '',
                       'scan_key': book_id,
                       'source_url': remote_url,
                       'total_pages': len(pages_data), 'pages': pages_data,
                       'scanned_at': _utc_now_iso(),
                       'extractor': extractor},
                      f, ensure_ascii=False, indent=2)
        _mark('done', len(pages_data), extractor)
    except Exception:
        _mark('failed')
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def start_scan(book_id, local_path=None, drive_id=None, remote_url=None):
    scan_path = os.path.join(SCANS_DIR, book_id + '.json')
    if os.path.exists(scan_path):
        try:
            with open(scan_path, 'r', encoding='utf-8') as f:
                scan_data = json.load(f)
                if _scan_data_is_usable(scan_data):
                    library = load_library()
                    book = _find_library_book_for_selection(library, book_id)
                    _sync_library_scan_status(library, book, scan_data)
                    return
        except Exception:
            pass
    t = threading.Thread(target=_do_scan,
                         args=(book_id,),
                         kwargs={'local_path': local_path, 'drive_id': drive_id, 'remote_url': remote_url},
                         daemon=True)
    t.start()


HTML = """
<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ReylAI</title>
<meta name="theme-color" content="#030712">
<link rel="icon" type="image/png" href="{{ reylai_icon_src }}">
<link rel="apple-touch-icon" href="{{ reylai_icon_src }}">
<script>
(function() {
  if (!window.history || !window.history.replaceState) return;
  var path = window.location.pathname;
  var nextPath = "";
  if (/\/index\.html$/i.test(path)) nextPath = path.replace(/index\.html$/i, "");
  else if (/\/terms\.html$/i.test(path)) nextPath = path.replace(/terms\.html$/i, "terms");
  else if (/\/privacy\.html$/i.test(path)) nextPath = path.replace(/privacy\.html$/i, "privacy");
  if (nextPath && nextPath !== path) window.history.replaceState(null, "", nextPath + window.location.search + window.location.hash);
})();
</script>
<style id="reylai-boot-paint">
  html { background: #030712; }
  body {
    background: #030712;
    color: #eef5ff;
    opacity: 0;
    animation: reylaiBootIn 0.46s cubic-bezier(0.22, 1, 0.36, 1) forwards;
  }
  @keyframes reylaiBootIn { to { opacity: 1; } }
</style>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Pacifico&family=Inter:wght@300;400;500;600;700;800&family=Manrope:wght@500;600;700;800&display=swap" rel="stylesheet">
<script src="https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit" async defer></script>
<style>
:root {
  --bg-deep:    #07070f;
  --bg-surface: #0f0f1a;
  --bg-card:    #141424;
  --bg-glass:   rgba(20, 20, 40, 0.6);
  --border:     rgba(37,99,235, 0.18);
  --accent:     #2563eb;
  --accent-glow:#1d4ed8;
  --accent-soft:rgba(37,99,235,0.12);
  --green:      #60a5fa;
  --amber:      #fbbf24;
  --red:        #f87171;
  --text-primary: #eeeeff;
  --text-secondary: #8888aa;
  --text-muted:  #44445a;
  --radius-sm:  10px;
  --radius-md:  16px;
  --radius-lg:  24px;
  --radius-xl:  32px;
  --shadow-card: 0 4px 32px rgba(0,0,0,0.5);
  --shadow-glow: 0 0 32px rgba(37,99,235,0.25);
  --transition: 0.45s cubic-bezier(0.22, 1, 0.36, 1);
}

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

html, body {
  height: 100%;
  overflow: hidden;
  background: var(--bg-deep);
  color: var(--text-primary);
  font-family: 'Inter', sans-serif;
  -webkit-font-smoothing: antialiased;
}

body::before {
  content: '';
  position: fixed;
  inset: 0;
  background-image:
    linear-gradient(rgba(37,99,235,0.04) 1px, transparent 1px),
    linear-gradient(90deg, rgba(37,99,235,0.04) 1px, transparent 1px);
  background-size: 48px 48px;
  pointer-events: none;
  z-index: 0;
}

body::after {
  content: '';
  position: fixed;
  top: -200px;
  left: -200px;
  width: 600px;
  height: 600px;
  background: radial-gradient(circle, rgba(37,99,235,0.12) 0%, transparent 70%);
  pointer-events: none;
  z-index: 0;
  animation: blobFloat 12s ease-in-out infinite alternate;
}

@keyframes blobFloat {
  0%   { transform: translate(0, 0) scale(1); }
  100% { transform: translate(300px, 200px) scale(1.2); }
}

.screen {
  position: fixed;
  inset: 0;
  z-index: 1;
  display: flex;
  flex-direction: column;
  transition: opacity var(--transition), transform var(--transition);
  will-change: opacity, transform;
}

.screen.hidden {
  opacity: 0;
  pointer-events: none;
}

#libraryScreen.hidden  { transform: scale(0.94); opacity: 0; }
#analysisScreen        { transform: translateX(56px); opacity: 0; }
#analysisScreen.active { transform: translateX(0);    opacity: 1; pointer-events: all; }
#analysisScreen.hidden { transform: translateX(56px); opacity: 0; }

.app-loading {
  position: fixed;
  inset: 0;
  z-index: 70;
  display: grid;
  place-items: center;
  padding: 24px;
  background:
    linear-gradient(135deg, rgba(5,10,24,0.96), rgba(12,18,28,0.94)),
    radial-gradient(circle at 50% 30%, rgba(29,78,216,0.18), transparent 44%);
  transition: opacity 0.55s cubic-bezier(0.22, 1, 0.36, 1), transform 0.55s cubic-bezier(0.22, 1, 0.36, 1);
}

.app-loading.done {
  opacity: 0;
  transform: scale(1.025);
  pointer-events: none;
}

.loading-core {
  width: auto;
  display: grid;
  place-items: center;
  animation: loadingCoreIn 0.7s cubic-bezier(0.16, 1, 0.3, 1) both;
}

@keyframes loadingCoreIn {
  from { opacity: 0; transform: translateY(18px) scale(0.98); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}

.loading-orbit {
  width: clamp(168px, 22vw, 220px);
  height: clamp(168px, 22vw, 220px);
  margin: 0 auto;
  border-radius: 50%;
  display: grid;
  place-items: center;
  border: 0;
  background: transparent;
  box-shadow: 0 20px 70px rgba(0,0,0,0.30);
  position: relative;
}

.loading-orbit::before {
  content: '';
  position: absolute;
  inset: -3px;
  border-radius: inherit;
  border: 2px solid rgba(147,197,253,0.14);
  border-top-color: rgba(147,197,253,0.94);
  border-right-color: rgba(37,99,235,0.74);
  animation: loadingSpin 1.18s linear infinite;
}

.loading-orbit::after {
  content: '';
  position: absolute;
  inset: 18%;
  border-radius: inherit;
  background: radial-gradient(circle, rgba(37,99,235,0.18), transparent 66%);
  filter: blur(18px);
}

@keyframes loadingSpin { to { transform: rotate(360deg); } }

.loading-logo {
  width: 58%;
  height: 58%;
  border-radius: 28px;
  object-fit: contain;
  animation: loadingLogoPulse 1.45s ease-in-out infinite;
  position: relative;
  z-index: 1;
}

@keyframes loadingLogoPulse {
  0%, 100% { opacity: 0.62; transform: scale(0.96); filter: drop-shadow(0 0 10px rgba(96,165,250,0.20)); }
  50% { opacity: 1; transform: scale(1.03); filter: drop-shadow(0 0 22px rgba(96,165,250,0.55)); }
}

.account-auth-screen {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: grid;
  grid-template-columns: 1fr;
  place-items: center;
  padding: clamp(16px, 4vw, 40px);
  background:
    linear-gradient(135deg, rgba(3,7,18, 1) 0%, rgba(16, 9, 31, 0.99) 50%, rgba(5, 4, 14, 1) 100%),
    linear-gradient(90deg, rgba(29,78,216, 0.12), transparent 38%, rgba(96,165,250, 0.075));
  opacity: 0;
  pointer-events: none;
  visibility: hidden;
  transform: scale(1.018);
  overflow: hidden;
  transition:
    opacity 0.58s cubic-bezier(0.22, 1, 0.36, 1),
    transform 0.58s cubic-bezier(0.22, 1, 0.36, 1),
    visibility 0.58s step-end;
  isolation: isolate;
}

.account-auth-screen::before {
  content: '';
  position: absolute;
  inset: 0;
  background:
    repeating-linear-gradient(90deg, rgba(96,165,250, 0.04) 0 1px, transparent 1px 40px),
    repeating-linear-gradient(0deg, rgba(96,165,250, 0.026) 0 1px, transparent 1px 40px);
  opacity: 0.38;
  pointer-events: none;
}

.account-auth-screen::after {
  content: '';
  position: absolute;
  inset: 0;
  background:
    linear-gradient(135deg, rgba(29,78,216, 0.12), transparent 38%),
    linear-gradient(315deg, transparent 52%, rgba(96,165,250, 0.09));
  opacity: 0.68;
  transform: translate3d(-2%, -1%, 0);
  animation: authLiquidSheen 9s ease-in-out infinite alternate;
  pointer-events: none;
}

.account-auth-screen.active {
  opacity: 1;
  pointer-events: all;
  visibility: visible;
  transform: scale(1);
  transition:
    opacity 0.58s cubic-bezier(0.22, 1, 0.36, 1),
    transform 0.58s cubic-bezier(0.22, 1, 0.36, 1),
    visibility 0s step-start;
}

body:not(.app-ready) #libraryScreen,
body:not(.app-ready) #analysisScreen,
body.account-auth-visible #libraryScreen,
body.account-auth-visible #analysisScreen {
  opacity: 0 !important;
  visibility: hidden !important;
  pointer-events: none !important;
}

body.app-ready #libraryScreen {
  animation: appScreenReveal 0.72s cubic-bezier(0.16, 1, 0.3, 1) both;
}

@keyframes appScreenReveal {
  from { opacity: 0; transform: translateY(18px) scale(0.985); filter: blur(10px); }
  to { opacity: 1; transform: translateY(0) scale(1); filter: none; }
}

@keyframes authLiquidSheen {
  from { transform: translate3d(-2%, -1%, 0); opacity: 0.46; }
  to { transform: translate3d(2%, 1.5%, 0); opacity: 0.78; }
}

.auth-hero {
  display: none;
}

.auth-brand-row {
  display: flex;
  align-items: center;
  gap: 14px;
}

.auth-logo-box {
  width: 66px;
  height: 66px;
  display: grid;
  place-items: center;
  border-radius: 20px;
  border: 1px solid rgba(255,255,255,0.20);
  background: rgba(255,255,255,0.10);
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.18), 0 24px 70px rgba(0,0,0,0.30);
  backdrop-filter: blur(18px);
}

.auth-logo-box img {
  width: 44px;
  height: 44px;
  object-fit: contain;
}

.auth-brand-name {
  font-family: 'Manrope', sans-serif;
  font-size: 24px;
  font-weight: 900;
}

.auth-brand-sub {
  margin-top: 2px;
  color: rgba(243,239,255,0.62);
  font-size: 13px;
  font-weight: 700;
}

.auth-kicker {
  width: fit-content;
  padding: 8px 12px;
  border-radius: 999px;
  border: 1px solid rgba(147,197,253,0.20);
  background: rgba(29,78,216,0.12);
  color: #bfdbfe;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}

.auth-hero-title {
  font-family: 'Manrope', sans-serif;
  font-size: clamp(34px, 6.5vw, 72px);
  line-height: 0.94;
  font-weight: 900;
  letter-spacing: 0;
}

.auth-hero-text {
  max-width: 520px;
  color: rgba(243,239,255,0.70);
  font-size: 16px;
  line-height: 1.7;
}

.auth-benefits {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.auth-benefit {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 34px;
  padding: 0 12px;
  border-radius: 999px;
  border: 1px solid rgba(255,255,255,0.13);
  background: rgba(255,255,255,0.08);
  color: rgba(243,239,255,0.78);
  font-size: 12px;
  font-weight: 800;
  backdrop-filter: blur(18px);
}

.auth-benefit-dot {
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: linear-gradient(135deg, #1d4ed8, #bfdbfe);
  box-shadow: 0 0 18px rgba(29,78,216,0.36);
}

.auth-panel-card {
  width: min(404px, calc(100vw - 32px));
  justify-self: center;
  align-self: center;
  padding: 18px;
  border-radius: 22px;
  border: 1px solid rgba(147,197,253, 0.18);
  background:
    linear-gradient(145deg, rgba(18,31,58, 0.90), rgba(6,14,31, 0.86)),
    var(--material-glass);
  backdrop-filter: blur(24px) saturate(1.14);
  box-shadow:
    var(--shadow-float),
    0 0 42px rgba(15,42,95, 0.18);
  position: relative;
  z-index: 1;
  overflow: hidden;
  animation: accountPanelFloat 0.82s cubic-bezier(0.16, 1, 0.3, 1) both;
}

.auth-panel-card::before {
  content: '';
  position: absolute;
  inset: 0;
  background:
    linear-gradient(135deg, rgba(255, 255, 255, 0.12), transparent 48%, rgba(29,78,216, 0.16));
  opacity: 0.72;
  pointer-events: none;
}

.auth-panel-card > * {
  position: relative;
  z-index: 1;
}

@keyframes accountPanelFloat {
  from { opacity: 0; transform: translateY(28px) scale(0.97); filter: blur(8px); }
  to { opacity: 1; transform: translateY(0); }
}

@keyframes authHeroIn {
  from { opacity: 0; transform: translateX(-22px); filter: blur(10px); }
  to { opacity: 1; transform: translateX(0); filter: none; }
}

.auth-panel-top {
  display: grid;
  gap: 5px;
  margin-bottom: 15px;
}

.auth-panel-label {
  color: #bfdbfe;
  font-size: 11px;
  font-weight: 900;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.auth-panel-title {
  font-family: 'Manrope', sans-serif;
  font-size: 24px;
  line-height: 1.1;
  font-weight: 900;
}

.auth-panel-lead {
  color: rgba(243,239,255,0.62);
  font-size: 12px;
  line-height: 1.55;
}

.auth-tabs {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0;
  min-height: 46px;
  margin: 0 -18px 16px;
  border-top: 1px solid rgba(255,255,255,0.10);
  border-bottom: 1px solid rgba(255,255,255,0.10);
  background: rgba(5,10,24, 0.46);
}

.auth-tab {
  position: relative;
  height: 46px;
  border: 0;
  background: transparent;
  color: rgba(243,239,255,0.48);
  font-weight: 800;
  cursor: pointer;
  transition: color 0.28s cubic-bezier(0.22, 1, 0.36, 1), background 0.28s cubic-bezier(0.22, 1, 0.36, 1);
}

.auth-tab.active {
  color: #ffffff;
  background: rgba(29,78,216, 0.13);
}

.auth-tab.active::after {
  content: '';
  position: absolute;
  left: 18%;
  right: 18%;
  bottom: -1px;
  height: 3px;
  border-radius: 999px 999px 0 0;
  background: linear-gradient(90deg, #071a3d, #1d4ed8, #bfdbfe);
  box-shadow: 0 0 22px rgba(37,99,235, 0.38);
}

.account-form {
  display: grid;
  gap: 11px;
}

.account-field {
  display: grid;
  gap: 8px;
  transition:
    opacity 0.34s cubic-bezier(0.22, 1, 0.36, 1),
    transform 0.34s cubic-bezier(0.22, 1, 0.36, 1),
    max-height 0.34s cubic-bezier(0.22, 1, 0.36, 1),
    margin 0.34s cubic-bezier(0.22, 1, 0.36, 1);
}

.account-field-optional {
  max-height: 0;
  margin-top: -8px;
  opacity: 0;
  transform: translateY(-8px);
  overflow: hidden;
  pointer-events: none;
}

.account-field-optional.active {
  max-height: 96px;
  margin-top: 0;
  opacity: 1;
  transform: translateY(0);
  pointer-events: auto;
}

.account-label {
  color: rgba(243,239,255,0.68);
  font-size: 11px;
  font-weight: 800;
}

.account-input {
  width: 100%;
  min-height: 48px;
  border-radius: 14px;
  border: 1px solid rgba(255,255,255,0.12);
  background: rgba(255,255,255,0.075);
  color: var(--text-primary);
  padding: 0 14px;
  outline: none;
  font: inherit;
  transition: var(--transition);
}

.account-input-shell {
  display: grid;
  grid-template-columns: 22px 1fr;
  align-items: center;
  gap: 12px;
  min-height: 50px;
  padding: 0 14px;
  border-radius: 999px;
  border: 1px solid rgba(147,197,253, 0.18);
  background: rgba(6,14,31, 0.56);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.06);
  transition:
    border-color 0.28s cubic-bezier(0.22, 1, 0.36, 1),
    box-shadow 0.28s cubic-bezier(0.22, 1, 0.36, 1),
    background 0.28s cubic-bezier(0.22, 1, 0.36, 1),
    transform 0.28s cubic-bezier(0.22, 1, 0.36, 1);
}

.account-input-shell:focus-within {
  border-color: rgba(37,99,235, 0.66);
  background: rgba(15,23,42, 0.64);
  box-shadow: 0 0 0 4px rgba(30,64,175, 0.18), inset 0 1px 0 rgba(255, 255, 255, 0.08);
  transform: translateY(-1px);
}

.account-field-icon {
  display: grid;
  place-items: center;
  color: rgba(243,239,255,0.62);
}

.account-field-icon svg {
  width: 20px;
  height: 20px;
  stroke-width: 2;
}

.account-input-shell .account-input {
  min-height: 48px;
  padding: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
}

.account-input:focus {
  border-color: rgba(147,197,253,0.56);
  box-shadow: 0 0 0 4px rgba(29,78,216,0.16);
}

.account-input-shell .account-input:focus {
  box-shadow: none;
}

.account-input::placeholder {
  color: rgba(193,202,214,0.54);
}

.remember-row {
  display: flex;
  align-items: center;
  gap: 11px;
  width: fit-content;
  color: rgba(243,239,255,0.70);
  font-size: 13px;
  font-weight: 800;
  cursor: pointer;
  user-select: none;
}

.remember-row input {
  position: absolute;
  opacity: 0;
  pointer-events: none;
}

.remember-check {
  width: 26px;
  height: 26px;
  display: grid;
  place-items: center;
  border-radius: 999px;
  border: 1px solid rgba(255,255,255,0.18);
  background: rgba(255,255,255,0.08);
  color: transparent;
  transition: var(--transition);
}

.remember-check svg {
  width: 16px;
  height: 16px;
}

.remember-row input:checked + .remember-check {
  color: #eef5ff;
  background: linear-gradient(135deg, #071a3d, #1d4ed8);
  border-color: rgba(147,197,253,0.48);
  box-shadow: 0 12px 28px rgba(37,99,235, 0.24);
}

.auth-field-note {
  margin-top: -4px;
  color: rgba(243,239,255,0.48);
  font-size: 11px;
  line-height: 1.45;
  font-weight: 700;
}

.turnstile-wrap {
  min-height: 76px;
  display: grid;
  align-items: center;
  justify-items: center;
  gap: 8px;
  padding: 12px;
  border-radius: 16px;
  border: 1px solid rgba(147,197,253, 0.14);
  background: rgba(6,14,31, 0.48);
  overflow: hidden;
}

.turnstile-note,
.account-auth-error {
  color: rgba(251,191,36,0.94);
  font-size: 12px;
  line-height: 1.45;
  text-align: center;
}

.account-auth-error {
  min-height: 18px;
  color: var(--red);
  font-weight: 700;
  text-align: left;
}

.account-submit {
  height: 50px;
  border: 0;
  border-radius: 999px;
  color: #fff;
  background: var(--material-stained);
  font-weight: 900;
  cursor: pointer;
  box-shadow: var(--shadow-glow);
  transition: var(--transition);
}

.account-submit:hover:not(:disabled) {
  transform: translateY(-2px);
  background: linear-gradient(135deg, rgba(29,78,216, 1), rgba(6,26,58, 0.96));
  box-shadow: var(--shadow-glow);
}

.account-submit:disabled {
  cursor: not-allowed;
  opacity: 0.58;
}

.account-switch-note {
  text-align: center;
  color: rgba(243,239,255,0.60);
  font-size: 13px;
  font-weight: 700;
}

.account-switch-note button {
  border: 0;
  background: transparent;
  color: #bfdbfe;
  font-weight: 900;
  cursor: pointer;
}

.account-chip {
  display: none;
  align-items: center;
  gap: 9px;
  height: 36px;
  padding: 0 12px 0 6px;
  border-radius: 999px;
  border: 1px solid rgba(255,255,255,0.12);
  background: rgba(255,255,255,0.08);
  color: var(--text-primary);
  cursor: pointer;
  transition: var(--transition);
}

.account-chip.visible {
  display: inline-flex;
}

.account-chip:hover {
  border-color: rgba(147,197,253,0.38);
  background: rgba(255,255,255,0.12);
  transform: translateY(-1px);
}

.account-avatar {
  width: 26px;
  height: 26px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  background: linear-gradient(135deg, #071a3d, #1d4ed8);
  color: #071015;
  font-size: 12px;
  font-weight: 900;
  background-size: cover;
  background-position: center;
  box-shadow: inset 0 0 0 1px rgba(255,255,255,0.24);
}

.account-chip-name {
  max-width: 130px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  font-weight: 800;
}

.account-chip-role {
  display: none;
  align-items: center;
  gap: 4px;
  height: 22px;
  padding: 0 8px;
  border-radius: 999px;
  border: 1px solid rgba(147,197,253,0.26);
  background: rgba(29,78,216,0.16);
  color: #bfdbfe;
  font-size: 11px;
  font-weight: 900;
}

.account-chip-role.visible {
  display: inline-flex;
}

.account-menu {
  position: fixed;
  top: 72px;
  right: 24px;
  width: min(352px, calc(100vw - 28px));
  padding: 16px;
  border-radius: 22px;
  border: 1px solid rgba(255,255,255,0.14);
  background: linear-gradient(145deg, rgba(18,31,58, 0.94), rgba(6,14,31, 0.92));
  backdrop-filter: blur(22px) saturate(1.25);
  -webkit-backdrop-filter: blur(22px) saturate(1.25);
  box-shadow: 0 28px 86px rgba(0,0,0,0.48), inset 0 1px rgba(255,255,255,0.08);
  opacity: 0;
  transform: translateY(-10px) scale(0.98);
  pointer-events: none;
  transition: var(--transition);
  z-index: 360;
}

.account-menu.active,
body.account-menu-open .account-menu {
  opacity: 1;
  transform: translateY(0);
  pointer-events: auto;
}

.account-menu-head {
  display: grid;
  grid-template-columns: 58px 1fr;
  gap: 12px;
  align-items: center;
}

.account-menu-avatar-wrap {
  position: relative;
  width: 52px;
  height: 52px;
}

.account-menu-avatar {
  width: 52px;
  height: 52px;
  border-radius: 18px;
  display: grid;
  place-items: center;
  background: linear-gradient(135deg, #071a3d, #1d4ed8);
  color: #eef5ff;
  font-size: 20px;
  font-weight: 950;
  background-size: cover;
  background-position: center;
  box-shadow: inset 0 0 0 1px rgba(255,255,255,0.28), 0 14px 34px rgba(0,0,0,0.22);
}

.account-menu-name {
  font-weight: 900;
  margin-bottom: 2px;
  color: var(--text-primary);
}

.account-menu-email {
  color: var(--text-secondary);
  font-size: 12px;
  overflow-wrap: anywhere;
}

.account-role-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  margin-top: 12px;
}

.account-role-badge,
.account-verify-state {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 28px;
  padding: 0 10px;
  border-radius: 999px;
  border: 1px solid rgba(147,197,253,0.18);
  background: rgba(255,255,255,0.08);
  color: var(--text-primary);
  font-size: 12px;
  font-weight: 900;
}

.account-role-badge.admin,
.account-role-badge.staff {
  border-color: rgba(147,197,253,0.30);
  background: rgba(29,78,216,0.16);
  color: #bfdbfe;
}

.account-verify-state {
  width: 100%;
  justify-content: center;
  margin-top: 12px;
  color: rgba(251,191,36,0.96);
}

.account-verify-state.verified {
  color: #bfdbfe;
}

.presence-picker {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px;
  margin-top: 12px;
  padding: 5px;
  border-radius: 18px;
  border: 1px solid rgba(147,197,253,0.14);
  background: rgba(3,7,18,0.18);
}

.presence-btn {
  min-height: 32px;
  border: 1px solid transparent;
  border-radius: 14px;
  background: transparent;
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 900;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  transition: var(--transition);
}

.presence-btn:hover,
.presence-btn.active {
  color: var(--text-primary);
  background: rgba(37,99,235,0.18);
  border-color: rgba(147,197,253,0.22);
}

.presence-mini-dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: #22c55e;
}

.presence-btn[data-presence="online"] .presence-mini-dot { box-shadow: 0 0 12px rgba(34,197,94,0.72); }
.presence-btn[data-presence="idle"] .presence-mini-dot { background: #facc15; }
.presence-btn[data-presence="dnd"] .presence-mini-dot { background: #f87171; }
.presence-btn[data-presence="invisible"] .presence-mini-dot { background: #94a3b8; }

.account-menu-actions {
  display: grid;
  gap: 9px;
  margin-top: 14px;
}

.account-menu-btn {
  border: 1px solid rgba(255,255,255,0.12);
  border-radius: 999px;
  background: rgba(255,255,255,0.08);
  color: var(--text-primary);
  font-weight: 800;
  cursor: pointer;
  padding: 0 14px;
  min-height: 38px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  transition: var(--transition);
}

.account-menu-btn:hover {
  background: rgba(255,255,255,0.12);
  border-color: rgba(147,197,253,0.28);
  transform: translateY(-1px);
}

.account-menu-btn.danger {
  color: var(--red);
}

.dm-overlay {
  position: fixed;
  inset: 0;
  z-index: 330;
  display: grid;
  place-items: center;
  padding: 18px;
  background: rgba(3,7,18,0.62);
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.28s var(--motion-smooth);
}

.dm-overlay.active {
  opacity: 1;
  pointer-events: auto;
}

.dm-panel {
  width: min(1120px, calc(100vw - 32px));
  height: min(760px, calc(100dvh - 32px));
  display: grid;
  grid-template-columns: 330px minmax(0, 1fr);
  overflow: hidden;
  border-radius: 28px;
  border: 1px solid var(--glass-edge);
  background: linear-gradient(145deg, rgba(18,31,58,0.92), rgba(6,14,31,0.88));
  box-shadow: 0 34px 110px rgba(0,0,0,0.46);
  backdrop-filter: blur(28px) saturate(1.55);
  -webkit-backdrop-filter: blur(28px) saturate(1.55);
  transform: translateY(18px) scale(0.985);
  transition: transform 0.34s var(--motion-spring);
}

.dm-overlay.active .dm-panel {
  transform: translateY(0) scale(1);
}

.dm-people,
.dm-chat {
  min-width: 0;
  min-height: 0;
}

.dm-people {
  display: grid;
  grid-template-rows: auto auto 1fr;
  gap: 14px;
  padding: 20px;
  border-right: 1px solid rgba(147,197,253,0.14);
  background: rgba(3,7,18,0.22);
}

.dm-head,
.dm-chat-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.dm-kicker {
  color: #93c5fd;
  font-size: 11px;
  font-weight: 950;
  letter-spacing: .14em;
  text-transform: uppercase;
}

.dm-title {
  margin-top: 3px;
  font-size: 24px;
  font-weight: 950;
  color: var(--text-primary);
}

.dm-close,
.dm-back,
.dm-tool-btn,
.dm-send-btn {
  border: 1px solid rgba(147,197,253,0.18);
  background: rgba(255,255,255,0.08);
  color: var(--text-primary);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: var(--transition);
}

.dm-close,
.dm-back,
.dm-tool-btn {
  width: 40px;
  height: 40px;
  border-radius: 15px;
}

.dm-close:hover,
.dm-back:hover,
.dm-tool-btn:hover,
.dm-send-btn:hover {
  background: rgba(37,99,235,0.22);
  border-color: rgba(147,197,253,0.34);
  transform: translateY(-1px);
}

.dm-tool-btn:disabled,
.dm-send-btn:disabled {
  opacity: 0.58;
  cursor: wait;
  transform: none;
}

.dm-back { display: none; }

.dm-search {
  width: 100%;
  min-height: 44px;
  border-radius: 999px;
  border: 1px solid rgba(147,197,253,0.16);
  background: rgba(3,7,18,0.28);
  color: var(--text-primary);
  padding: 0 16px;
  outline: none;
}

.dm-thread-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow-y: auto;
  padding-right: 2px;
}

.dm-thread {
  border: 1px solid transparent;
  background: transparent;
  border-radius: 18px;
  color: var(--text-primary);
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr) auto;
  align-items: center;
  gap: 11px;
  padding: 10px;
  text-align: left;
  cursor: pointer;
  transition: var(--transition);
}

.dm-thread:hover,
.dm-thread.active {
  background: rgba(37,99,235,0.16);
  border-color: rgba(147,197,253,0.20);
}

.dm-avatar {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  overflow: hidden;
  background: linear-gradient(135deg, #dbeafe, #1d4ed8);
  color: #071326;
  font-weight: 950;
  box-shadow: inset 0 0 0 1px rgba(255,255,255,0.24);
}

.dm-avatar-wrap {
  position: relative;
  width: 44px;
  height: 44px;
  flex-shrink: 0;
}

.dm-avatar-wrap .dm-avatar {
  width: 100%;
  height: 100%;
}

.dm-presence-dot {
  position: absolute;
  right: -1px;
  bottom: -1px;
  width: 13px;
  height: 13px;
  border-radius: 999px;
  border: 2px solid rgba(6,14,31,0.96);
  background: #64748b;
}

.dm-presence-dot.online { background: #22c55e; box-shadow: 0 0 14px rgba(34,197,94,0.72); }
.dm-presence-dot.idle { background: #facc15; }
.dm-presence-dot.dnd { background: #f87171; }
.dm-presence-dot.invisible { background: #64748b; }
.dm-presence-dot.offline { background: #64748b; }

.dm-avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.dm-thread-name {
  font-size: 14px;
  font-weight: 900;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dm-thread-snippet,
.dm-chat-subtitle,
.dm-time {
  color: var(--text-secondary);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dm-unread,
.dm-bottom-badge {
  min-width: 20px;
  height: 20px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: #60a5fa;
  color: #06101f;
  font-size: 11px;
  font-weight: 950;
}

.dm-bottom-badge:empty { display: none; }

.dm-chat {
  display: grid;
  grid-template-rows: auto 1fr auto;
  background:
    radial-gradient(circle at 80% 0%, rgba(37,99,235,0.16), transparent 36%),
    rgba(3,7,18,0.10);
}

.dm-chat-head {
  min-height: 76px;
  padding: 14px 18px;
  border-bottom: 1px solid rgba(147,197,253,0.13);
}

.dm-chat-user {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.dm-chat-title {
  font-size: 16px;
  font-weight: 950;
}

.dm-empty-state {
  display: grid;
  place-items: center;
  padding: 28px;
  color: var(--text-secondary);
  text-align: center;
  font-weight: 800;
}

.dm-message-list {
  min-height: 0;
  overflow-y: auto;
  padding: 22px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.dm-chat.switching .dm-message-list,
.dm-chat.switching .dm-chat-user {
  animation: dmConversationSwitch 0.28s var(--motion-smooth) both;
}

.dm-message {
  display: flex;
  flex-direction: column;
  max-width: min(72%, 560px);
  gap: 5px;
  animation: dmMessageIn 0.24s var(--motion-smooth) both;
}

.dm-message.out { align-self: flex-end; align-items: flex-end; }
.dm-message.in { align-self: flex-start; align-items: flex-start; }

.dm-bubble {
  border: 1px solid rgba(147,197,253,0.16);
  border-radius: 21px;
  padding: 11px 14px;
  background: rgba(255,255,255,0.08);
  color: var(--text-primary);
  line-height: 1.48;
  overflow-wrap: anywhere;
}

.dm-message.out .dm-bubble {
  background: linear-gradient(135deg, rgba(29,78,216,0.86), rgba(15,42,95,0.92));
  border-color: rgba(147,197,253,0.25);
}

.dm-forward-card,
.dm-attachment-card {
  margin-top: 7px;
  border: 1px solid rgba(147,197,253,0.16);
  border-radius: 16px;
  background: rgba(3,7,18,0.22);
  padding: 10px;
}

.dm-forward-label,
.dm-attachment-name {
  color: #bfdbfe;
  font-size: 12px;
  font-weight: 950;
}

.dm-forward-text,
.dm-attachment-meta {
  margin-top: 4px;
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.45;
}

.dm-forward-text.message-body {
  color: var(--text-secondary);
}

.dm-forward-text .chat-md-heading {
  margin: 8px 0 6px;
  font-size: 14px;
}

.dm-forward-text .chat-md-pre,
.dm-forward-text .chat-md-table-wrap,
.dm-forward-text .chat-md-quote {
  margin: 7px 0;
}

.dm-forward-text .chat-md-table {
  min-width: 260px;
}

.dm-attachment-card img {
  display: block;
  width: min(260px, 100%);
  max-height: 260px;
  border-radius: 12px;
  object-fit: cover;
}

.dm-attachment-card audio {
  width: min(280px, 100%);
  margin-top: 8px;
}

.dm-composer {
  padding: 14px 18px 18px;
  border-top: 1px solid rgba(147,197,253,0.13);
  display: grid;
  gap: 10px;
}

.dm-pending {
  display: none;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  border: 1px solid rgba(147,197,253,0.16);
  background: rgba(255,255,255,0.07);
  border-radius: 16px;
  padding: 9px 11px;
  color: var(--text-secondary);
  font-size: 12px;
}

.dm-pending.active { display: flex; }

.dm-compose-row {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: end;
  gap: 9px;
}

.dm-input {
  min-height: 42px;
  max-height: 120px;
  resize: none;
  border: 1px solid rgba(147,197,253,0.16);
  border-radius: 18px;
  background: rgba(3,7,18,0.28);
  color: var(--text-primary);
  padding: 11px 13px;
  outline: none;
}

.dm-send-btn {
  min-width: 48px;
  height: 42px;
  border-radius: 18px;
  background: var(--material-stained);
}

@keyframes dmMessageIn {
  from { opacity: 0; transform: translateY(8px) scale(0.99); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}

@keyframes dmConversationSwitch {
  from { opacity: 0; transform: translateY(10px); filter: saturate(0.86); }
  to { opacity: 1; transform: translateY(0); filter: none; }
}

@media (max-width: 860px) {
  .account-auth-screen {
    grid-template-columns: 1fr;
    align-content: center;
    justify-items: center;
    overflow-y: auto;
    gap: 0;
  }
  .auth-hero {
    max-width: none;
    gap: 14px;
  }
  .auth-hero-title {
    font-size: clamp(30px, 10vw, 48px);
  }
  .auth-benefits {
    display: none;
  }
  .auth-panel-card {
    justify-self: center;
    width: min(404px, 100%);
  }
}

@media (max-width: 620px) {
  .account-auth-screen {
    padding: 14px 16px max(18px, env(safe-area-inset-bottom));
    gap: 12px;
  }
  .auth-logo-box {
    width: 54px;
    height: 54px;
    border-radius: 17px;
  }
  .auth-logo-box img {
    width: 36px;
    height: 36px;
  }
  .auth-brand-name {
    font-size: 20px;
  }
  .auth-kicker {
    display: none;
  }
  .auth-hero-title {
    font-size: clamp(26px, 8.5vw, 34px);
    line-height: 1.02;
  }
  .auth-hero-text {
    font-size: 12px;
    line-height: 1.5;
  }
  .account-auth-screen.signup-mode .auth-hero {
    gap: 8px;
  }
  .account-auth-screen.signup-mode .auth-hero-text {
    display: none;
  }
  .account-auth-screen.signup-mode .auth-hero-title {
    font-size: clamp(25px, 8vw, 32px);
  }
  .account-auth-screen.signup-mode .auth-panel-card {
    margin-top: 0;
  }
  .auth-panel-card {
    border-radius: 20px;
    padding: 16px;
  }
  .auth-tabs {
    min-height: 48px;
    margin-left: -16px;
    margin-right: -16px;
  }
  .auth-panel-title {
    font-size: 22px;
  }
  .auth-panel-top {
    margin-bottom: 14px;
  }
  .account-form {
    gap: 11px;
  }
  .account-input-shell {
    min-height: 50px;
    border-radius: 18px;
  }
  .account-input-shell .account-input {
    min-height: 48px;
  }
  .account-submit {
    height: 50px;
  }
  .turnstile-wrap {
    min-height: 72px;
    justify-items: start;
    padding-left: 8px;
    padding-right: 8px;
  }
  .account-chip-name {
    display: none;
  }
  .account-chip {
    padding-right: 6px;
  }
  .account-menu {
    right: 12px;
    top: 62px;
  }
}

/* Navbar */
.navbar {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  padding: 8px 20px;
  background: rgba(5,10,24,0.7);
  backdrop-filter: blur(16px);
  border-bottom: 1px solid var(--border);
  position: relative;
  z-index: 2;
  flex-shrink: 0;
  min-height: 56px;
  gap: 12px;
}

.nav-left  { display: flex; align-items: center; gap: 10px; justify-self: start; }
.nav-center { display: flex; flex-direction: column; align-items: center; gap: 4px; justify-self: center; }
.nav-right { display: flex; align-items: center; gap: 8px; justify-self: end; }

.tagline {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary);
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.meb-logo { width: 28px; height: 28px; object-fit: contain; }

.nav-logo-shell {
  position: relative;
  width: 62px;
  height: 52px;
  display: grid;
  place-items: center;
  isolation: isolate;
}

.nav-logo-shell::before {
  content: "";
  position: absolute;
  inset: 5px 2px;
  border-radius: 999px;
  background:
    radial-gradient(circle, rgba(96,165,250,0.48) 0%, rgba(37,99,235,0.2) 42%, transparent 72%);
  filter: blur(8px);
  opacity: 0.95;
  z-index: -2;
}

.nav-logo-shell::after {
  content: "";
  position: absolute;
  inset: 8px 7px;
  border-radius: 999px;
  border: 1px solid rgba(147,197,253,0.58);
  box-shadow:
    0 0 12px rgba(96,165,250,0.75),
    0 0 28px rgba(29,78,216,0.55),
    inset 0 0 12px rgba(147,197,253,0.16);
  opacity: 0.88;
  z-index: -1;
}

.nav-logo {
  width: 50px;
  height: 50px;
  object-fit: contain;
  filter:
    drop-shadow(0 0 7px rgba(255,255,255,0.55))
    drop-shadow(0 0 13px rgba(96,165,250,0.92))
    drop-shadow(0 0 28px rgba(37,99,235,0.74));
  transform: translateY(1px);
}

@media (max-width: 820px) {
  .navbar { padding: 8px 12px; gap: 6px; }
  .tagline { display: none !important; }
  .nav-logo-shell { width: 58px; height: 48px; }
  .nav-logo { width: 46px; height: 46px; }
}

@media (max-width: 600px) {
  .navbar {
    grid-template-columns: 1fr;
    padding: 8px 12px;
    gap: 4px;
    min-height: auto;
  }
  .nav-left { display: none; }
  .nav-center {
    justify-self: center;
    flex-direction: row;
    align-items: center;
    gap: 10px;
  }
  .nav-right {
    justify-self: center;
    gap: 6px;
  }
  .nav-logo-shell { width: 52px; height: 44px; }
  .nav-logo { width: 42px; height: 42px; }
  .status-pill { display: none; }
  .grade-label { display: none; }
  .upload-nav-btn, .history-nav-btn, .dm-nav-btn, .sync-btn, .scan-nav-btn {
    font-size: 11px;
    padding: 5px 10px;
  }
  .upload-nav-btn svg, .history-nav-btn svg, .dm-nav-btn svg, .sync-btn .sync-icon, .scan-nav-btn svg {
    width: 10px;
    height: 10px;
  }
  .book-grid-wrap {
    padding: 16px 12px;
  }
  .book-grid {
    grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
    gap: 14px;
  }
}

/* Grade strip (below navbar) */
.grade-strip {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 12px 24px 8px;
  gap: 14px;
  flex-shrink: 0;
}

/* Neon search bar */
.search-wrap {
  position: relative;
  width: 340px;
  max-width: 50vw;
}
.search-wrap::before {
  content: '';
  position: absolute;
  inset: -2px;
  border-radius: 14px;
  background: linear-gradient(135deg, #2563eb, #60a5fa, #60a5fa, #2563eb);
  background-size: 300% 300%;
  animation: neonShift 4s ease infinite;
  opacity: 0;
  transition: opacity 0.4s ease;
  z-index: 0;
  filter: blur(6px);
}
.search-wrap:focus-within::before {
  opacity: 0.7;
}
.search-wrap::after {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: 12px;
  background: var(--bg-surface);
  z-index: 1;
}
@keyframes neonShift {
  0%, 100% { background-position: 0% 50%; }
  50%      { background-position: 100% 50%; }
}

.search-input {
  position: relative;
  z-index: 2;
  width: 100%;
  background: transparent;
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 9px 14px 9px 36px;
  font-family: 'Inter', sans-serif;
  font-size: 13px;
  color: var(--text-primary);
  outline: none;
  transition: border-color 0.3s ease, box-shadow 0.3s ease;
}
.search-input::placeholder {
  color: var(--text-muted);
  font-weight: 500;
}
.search-input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 20px rgba(37,99,235,0.25), 0 0 40px rgba(37,99,235,0.1);
}
.search-icon {
  position: absolute;
  left: 11px;
  top: 50%;
  transform: translateY(-50%);
  z-index: 3;
  color: var(--text-muted);
  transition: color 0.3s ease;
  pointer-events: none;
}
.search-wrap:focus-within .search-icon {
  color: var(--accent);
}

@media (max-width: 600px) {
  .search-wrap {
    max-width: 60vw;
    width: 200px;
  }
}

/* Search empty state */
.search-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 60px 20px;
  color: var(--text-muted);
  font-size: 14px;
  font-weight: 500;
  opacity: 0;
  transform: translateY(12px) scale(0.97);
  transition: opacity 0.4s cubic-bezier(0.22,1,0.36,1), transform 0.4s cubic-bezier(0.22,1,0.36,1);
  text-align: center;
}
.search-empty.show {
  opacity: 1;
  transform: none;
}
.search-empty svg {
  color: var(--accent);
  opacity: 0.45;
}

/* Grade bar */
.grade-bar {
  display: flex;
  align-items: center;
  gap: 3px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-xl);
  padding: 4px 8px;
}

.grade-label {
  font-size: 10px;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-right: 2px;
}

.grade-btn {
  background: none;
  border: none;
  color: var(--text-secondary);
  font-family: 'Inter', sans-serif;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  border-radius: var(--radius-sm);
  padding: 4px 10px;
  transition: color 0.18s ease, background 0.22s cubic-bezier(0.34,1.56,0.64,1), transform 0.18s ease;
  position: relative;
}

.grade-btn:hover  { color: var(--text-primary); background: var(--accent-soft); transform: scale(1.06); }
.grade-btn.active { color: #fff; background: var(--accent); transform: scale(1.08); box-shadow: 0 2px 12px rgba(37,99,235,0.4); }

/* Sync button */
.sync-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  background: var(--accent-soft);
  border: 1px solid rgba(37,99,235,0.35);
  border-radius: var(--radius-xl);
  color: #93c5fd;
  font-family: 'Inter', sans-serif;
  font-size: 12px;
  font-weight: 600;
  padding: 6px 12px;
  cursor: pointer;
  transition: all 0.22s ease;
  letter-spacing: 0.01em;
  white-space: nowrap;
}

.sync-btn:hover:not(:disabled) {
  background: var(--accent);
  color: #fff;
  border-color: var(--accent);
  box-shadow: var(--shadow-glow);
}

.sync-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.sync-btn .sync-icon {
  transition: transform 0.5s ease;
}

.sync-btn.syncing .sync-icon {
  animation: spinCW 0.85s linear infinite;
}

@keyframes spinCW { to { transform: rotate(360deg); } }

.scan-nav-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: linear-gradient(135deg, rgba(37,99,235,0.18), rgba(29,78,216,0.3));
  border: 1px solid rgba(96,165,250,0.42);
  border-radius: var(--radius-xl);
  color: #dbe3ff;
  font-family: 'Inter', sans-serif;
  font-size: 12px;
  font-weight: 700;
  padding: 6px 12px;
  cursor: pointer;
  transition: all 0.22s ease;
  white-space: nowrap;
}

.scan-nav-btn:hover:not(:disabled) {
  background: linear-gradient(135deg, var(--accent), var(--accent-glow));
  color: #fff;
  border-color: var(--accent);
  box-shadow: 0 10px 28px rgba(37,99,235,0.28);
}

.scan-nav-btn:disabled {
  opacity: 0.7;
  cursor: wait;
}

/* Status pill */
.status-pill {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  border-radius: var(--radius-xl);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  font-size: 12px;
  font-weight: 500;
  color: var(--green);
  white-space: nowrap;
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  flex-shrink: 0;
}

/* Book grid */
.book-grid-wrap {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 32px 40px;
  scrollbar-width: thin;
  scrollbar-color: rgba(37,99,235,0.3) transparent;
}

.book-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(178px, 1fr));
  gap: 24px;
  transition: opacity 0.22s cubic-bezier(0.4,0,0.2,1), transform 0.22s cubic-bezier(0.4,0,0.2,1);
}

.book-grid.fading {
  opacity: 0;
  transform: translateY(6px);
  pointer-events: none;
}

.book-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  overflow: visible;
  cursor: pointer;
  transition: border-color 0.22s ease, transform 0.32s cubic-bezier(0.34,1.56,0.64,1), box-shadow 0.25s ease, opacity 0.22s ease;
  position: relative;
  opacity: 0;
  transform: translateY(14px) scale(0.97);
  will-change: transform, opacity;
}

.book-card.visible { opacity: 1; transform: none; }

.book-card:hover {
  border-color: var(--accent);
  transform: translateY(-5px) scale(1.025);
  box-shadow: 0 8px 40px rgba(37,99,235,0.3);
}

.card-cover {
  width: 100%;
  aspect-ratio: 3/4;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 48px;
  background: linear-gradient(160deg, var(--bg-surface) 0%, #1a1a30 100%);
  border-radius: var(--radius-md) var(--radius-md) 0 0;
  overflow: hidden;
}

.card-cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.card-name {
  padding: 10px 12px 4px;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-primary);
  line-height: 1.35;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.card-meta-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 0 12px 10px;
}

.card-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 12px 12px;
}

.card-analyze-btn {
  width: 100%;
  border: 1px solid rgba(37,99,235,0.35);
  background: rgba(37,99,235,0.12);
  color: #bfdbfe;
  border-radius: 12px;
  min-height: 36px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 8px 12px;
  cursor: pointer;
  font-family: 'Inter', sans-serif;
  font-size: 12.5px;
  font-weight: 700;
  transition: all 0.18s ease;
}

.card-analyze-btn:hover {
  background: var(--accent);
  color: #fff;
  border-color: var(--accent);
  box-shadow: 0 6px 18px rgba(37,99,235,0.28);
}

.card-del-btn {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 26px;
  height: 26px;
  border-radius: 50%;
  background: rgba(248,113,113,0.15);
  border: 1px solid rgba(248,113,113,0.35);
  color: var(--red);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.18s ease, background 0.18s ease;
}

.book-card:hover .card-del-btn { opacity: 1; }
.card-del-btn:hover { background: rgba(248,113,113,0.3); }

/* Scan badge */
/* Upload progress overlay */
.upload-overlay {
  display: none;
  position: fixed;
  inset: 0;
  z-index: 9999;
  background: rgba(5,10,24,0.82);
  backdrop-filter: blur(12px);
  align-items: center;
  justify-content: center;
}
.upload-overlay.active { display: flex; }

.upload-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-xl);
  padding: 28px 32px;
  width: min(460px, 90vw);
  box-shadow: 0 32px 80px rgba(0,0,0,0.5), 0 0 0 1px rgba(37,99,235,0.15);
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.upload-header {
  display: flex;
  align-items: center;
  gap: 10px;
  color: #93c5fd;
  font-size: 15px;
  font-weight: 700;
}

.upload-counter {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-muted);
  letter-spacing: 0.04em;
}

.upload-filename {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}

.upload-bar-wrap {
  display: flex;
  align-items: center;
  gap: 10px;
}

.upload-bar-track {
  flex: 1;
  height: 8px;
  background: rgba(37,99,235,0.12);
  border-radius: 99px;
  overflow: hidden;
}

.upload-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, #2563eb, #60a5fa);
  border-radius: 99px;
  transition: width 0.12s ease;
}

.upload-pct {
  font-size: 12px;
  font-weight: 700;
  color: #93c5fd;
  min-width: 36px;
  text-align: right;
}

.upload-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.upload-speed {
  font-size: 13px;
  font-weight: 700;
  color: var(--text-primary);
  font-feature-settings: "tnum";
}

.upload-size {
  font-size: 11px;
  color: var(--text-muted);
}

.upload-overall-wrap {
  margin-top: 2px;
}

.upload-overall-track {
  height: 4px;
  background: rgba(255,255,255,0.06);
  border-radius: 99px;
  overflow: hidden;
}

.upload-overall-fill {
  height: 100%;
  background: rgba(37,99,235,0.4);
  border-radius: 99px;
  transition: width 0.2s ease;
}

.upload-overall-label {
  font-size: 10px;
  color: var(--text-muted);
  margin-top: 4px;
  letter-spacing: 0.04em;
}

.sync-badge-local {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 9px;
  font-weight: 600;
  color: #fbbf24;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  margin: 4px 10px 0;
  opacity: 0.85;
}

.scan-badge {
  display: flex;
  align-items: center;
  gap: 5px;
  margin: 4px 10px 10px;
  padding: 3px 8px;
  border-radius: var(--radius-sm);
  font-size: 11px;
  font-weight: 600;
}

.scan-badge.done    { background: rgba(29,78,216,0.14); color: var(--green); }
.scan-badge.pending { background: rgba(37,99,235,0.1); color: #93c5fd; }
.scan-badge.failed  { background: rgba(248,113,113,0.1); color: var(--red); }

.scan-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  flex-shrink: 0;
}

.scan-dot.spin {
  border-radius: 50%;
  border: 1.5px solid transparent;
  border-top-color: currentColor;
  background: transparent;
  animation: spinCW 0.7s linear infinite;
  width: 7px;
  height: 7px;
}

/* Empty state */
.empty-state {
  grid-column: 1 / -1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 18px;
  padding: 72px 20px;
  text-align: center;
  color: var(--text-secondary);
  animation: emptyFadeIn 0.5s cubic-bezier(0.22,1,0.36,1) both;
}

@keyframes emptyFadeIn {
  from { opacity: 0; transform: translateY(16px); }
  to   { opacity: 1; transform: translateY(0); }
}

.empty-books-img {
  width: 96px;
  height: 96px;
  object-fit: contain;
  filter: drop-shadow(0 4px 16px rgba(37,99,235,0.25));
  animation: floatBooks 4s ease-in-out infinite alternate;
}

@keyframes floatBooks {
  0%   { transform: translateY(0px) rotate(-1deg); }
  100% { transform: translateY(-8px) rotate(1deg); }
}

.empty-state h3 { font-size: 19px; font-weight: 700; color: var(--text-primary); margin: 0; }
.empty-state p  { font-size: 13.5px; max-width: 300px; margin: 0; line-height: 1.6; }

/* Sync button inside empty state */
.sync-btn-empty {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: var(--accent);
  border: none;
  border-radius: var(--radius-xl);
  color: #fff;
  font-family: 'Inter', sans-serif;
  font-size: 14px;
  font-weight: 700;
  padding: 11px 24px;
  cursor: pointer;
  transition: all 0.25s cubic-bezier(0.34,1.56,0.64,1);
  box-shadow: 0 4px 20px rgba(37,99,235,0.35);
  margin-top: 4px;
}

.sync-btn-empty:hover:not(:disabled) {
  transform: translateY(-2px) scale(1.04);
  box-shadow: 0 8px 32px rgba(37,99,235,0.5);
}

.sync-btn-empty:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  transform: none;
}

/* Network status indicator (footer inline) */
.net-indicator {
  display: none !important;
}

.net-indicator {
  position: relative;
  display: none;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.25s ease;
  user-select: none;
  background: rgba(255,255,255,0.06);
  border: 1px solid rgba(255,255,255,0.08);
}
.net-indicator:hover {
  background: rgba(255,255,255,0.12);
  transform: scale(1.1);
}
.net-indicator:active { transform: scale(0.95); }

.net-indicator.online   { color: #60a5fa; }
.net-indicator.offline  { color: #f87171; }
.net-indicator.wifi     { color: #93c5fd; }
.net-indicator.ethernet { color: #93c5fd; }

.net-indicator svg { flex-shrink: 0; }

.net-dot {
  position: absolute;
  top: 3px;
  right: 3px;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  border: 1.5px solid var(--bg-base);
}
.net-indicator.online .net-dot,
.net-indicator.wifi .net-dot,
.net-indicator.ethernet .net-dot { background: #60a5fa; }
.net-indicator.offline .net-dot  { background: #f87171; }

/* Buttons */
.btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 10px 20px;
  border-radius: var(--radius-md);
  font-family: 'Inter', sans-serif;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  border: none;
  transition: all 0.2s ease;
}

.btn-primary {
  background: var(--accent);
  color: #fff;
}
.btn-primary:hover {
  background: var(--accent-glow);
  box-shadow: var(--shadow-glow);
}

.btn-ghost {
  background: var(--accent-soft);
  color: #93c5fd;
  border: 1px solid rgba(37,99,235,0.25);
}
.btn-ghost:hover {
  background: rgba(37,99,235,0.2);
  color: #fff;
}

/* Footer */
.lib-footer {
  display: block;
  padding: clamp(54px, 8vw, 96px) 40px calc(92px + env(safe-area-inset-bottom, 0px));
  border-top: 1px solid rgba(147,197,253,0.18);
  flex-shrink: 0;
  position: relative;
  overflow: hidden;
  background:
    radial-gradient(circle at 18% 0%, rgba(37,99,235,0.14), transparent 38%),
    radial-gradient(circle at 82% 55%, rgba(96,165,250,0.08), transparent 36%),
    linear-gradient(180deg, rgba(6,8,18,0.98), rgba(10,12,24,0.98));
}

.lib-footer::before {
  content: "";
  position: absolute;
  inset: 0 0 auto;
  height: 1px;
  background: linear-gradient(90deg, transparent, rgba(96,165,250,0.62), transparent);
  pointer-events: none;
}

.lib-footer .net-indicator {
  position: absolute;
  right: 20px;
}

.footer-inner {
  width: min(1376px, 100%);
  margin: 0 auto;
  position: relative;
  z-index: 1;
}

.footer-main {
  display: grid;
  grid-template-columns: minmax(260px, 1.45fr) repeat(3, minmax(140px, 0.65fr));
  gap: clamp(24px, 4vw, 72px);
  align-items: start;
}

.footer-brand-block {
  min-width: 0;
}

.footer-logo-row {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 18px;
}

.footer-logo {
  width: 58px;
  height: 58px;
  object-fit: contain;
  filter:
    drop-shadow(0 0 10px rgba(255,255,255,0.42))
    drop-shadow(0 0 18px rgba(96,165,250,0.42));
}

.footer-brand-name {
  color: var(--text-primary);
  font-size: 26px;
  line-height: 1;
  font-weight: 950;
  letter-spacing: 0;
}

.footer-tagline {
  max-width: 520px;
  color: var(--text-secondary);
  font-size: 15px;
  line-height: 1.7;
}

.footer-col {
  display: grid;
  gap: 19px;
}

.footer-col-title {
  color: var(--text-primary);
  font-size: 13px;
  line-height: 1.2;
  font-weight: 950;
  text-transform: uppercase;
  letter-spacing: 0;
}

.footer-col-list {
  display: grid;
  gap: 17px;
}

.footer-copy {
  font-size: 14px;
  color: rgba(167,178,199,0.70);
  transition: color 0.3s ease;
  white-space: pre-wrap;
}

.footer-copy:hover,
.footer-copy:hover .footer-brand,
.footer-link:hover {
  color: var(--accent);
  text-shadow: 0 0 8px rgba(37,99,235,0.6);
}

.footer-brand { color: #93c5fd; font-weight: 700; }

.footer-link {
  color: var(--text-secondary);
  text-decoration: none;
  font-size: 14px;
  font-weight: 750;
  line-height: 1.3;
  transition: color 0.2s ease, text-shadow 0.2s ease, transform 0.2s ease;
}

.footer-link:hover {
  transform: translateX(2px);
}

.footer-divider {
  height: 1px;
  margin: clamp(42px, 6vw, 76px) 0 28px;
  background: linear-gradient(90deg, rgba(147,197,253,0.02), rgba(147,197,253,0.16), rgba(147,197,253,0.02));
}

.footer-bottom {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  color: rgba(167,178,199,0.66);
}

.footer-made {
  color: rgba(167,178,199,0.70);
  font-size: 14px;
  font-weight: 750;
}

#libraryScreen {
  overflow-y: auto;
  overflow-x: hidden;
  scroll-behavior: smooth;
  scrollbar-width: thin;
  scrollbar-color: rgba(96,165,250,0.34) transparent;
}

#libraryScreen .book-grid-wrap {
  flex: 0 0 auto;
  min-height: max(420px, calc(100svh - 190px));
  overflow: visible;
}

@media (max-width: 900px) {
  .footer-main {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .footer-brand-block {
    grid-column: 1 / -1;
  }
}

@media (max-width: 640px) {
  .lib-footer {
    padding: 44px 22px calc(108px + env(safe-area-inset-bottom, 0px));
  }

  .footer-main {
    grid-template-columns: 1fr;
    gap: 30px;
  }

  .footer-logo-row {
    margin-bottom: 14px;
  }

  .footer-logo {
    width: 50px;
    height: 50px;
  }

  .footer-brand-name {
    font-size: 24px;
  }

  .footer-col {
    gap: 13px;
  }

  .footer-col-list {
    gap: 12px;
  }

  .footer-bottom {
    align-items: flex-start;
    flex-direction: column;
  }
}

/* Library chat history drawer */
.chat-history-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(5,5,12,0.48);
  backdrop-filter: blur(6px);
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.28s ease;
  z-index: 24;
}

.chat-history-backdrop.active {
  opacity: 1;
  pointer-events: auto;
}

.chat-sidebar {
  position: fixed;
  top: 0;
  left: 0;
  bottom: 0;
  width: min(390px, 92vw);
  max-width: calc(100vw - 18px);
  background: rgba(13,13,25,0.98);
  border-right: 1px solid var(--border);
  box-shadow: 24px 0 70px rgba(0,0,0,0.42);
  padding: 22px 16px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  overflow: hidden;
  transform: translateX(0);
  transition: transform 0.34s cubic-bezier(0.22,1,0.36,1),
              opacity 0.24s ease;
  z-index: 25;
  font-family: 'Manrope', 'Inter', sans-serif;
  letter-spacing: 0;
  will-change: transform, opacity;
}

.chat-sidebar.collapsed {
  transform: translateX(-105%);
  opacity: 0;
  pointer-events: none;
}

.chat-sidebar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 4px 4px;
}

.chat-sidebar-label {
  font-size: 11px;
  font-weight: 800;
  color: #8f9ab8;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.chat-sidebar-title {
  margin-top: 3px;
  font-size: 21px;
  font-weight: 800;
  color: var(--text-primary);
}

.chat-sidebar-toggle,
.chat-history-delete {
  width: 34px;
  height: 34px;
  border-radius: 10px;
  border: 1px solid rgba(37,99,235,0.2);
  background: rgba(37,99,235,0.08);
  color: #bfdbfe;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.18s ease;
}

.chat-sidebar-toggle:hover,
.chat-history-delete:hover {
  background: rgba(37,99,235,0.18);
  border-color: rgba(37,99,235,0.42);
  color: #fff;
}

.new-chat-btn {
  min-height: 40px;
  border-radius: 12px;
  border: 1px solid rgba(37,99,235,0.28);
  background: rgba(37,99,235,0.12);
  color: #e0e7ff;
  font-family: 'Inter', sans-serif;
  font-size: 13px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  cursor: pointer;
  transition: all 0.18s ease;
}

.new-chat-btn:hover {
  transform: translateY(-1px);
  background: rgba(37,99,235,0.2);
  box-shadow: 0 12px 28px rgba(0,0,0,0.22);
}

.chat-history-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 9px;
  padding-right: 4px;
  scrollbar-width: thin;
  scrollbar-color: rgba(37,99,235,0.28) transparent;
}

.chat-history-empty {
  margin: 10px 4px;
  padding: 18px 14px;
  border: 1px dashed rgba(37,99,235,0.22);
  border-radius: 12px;
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 600;
  line-height: 1.55;
}

.chat-history-item {
  text-align: left;
  border: 1px solid rgba(96,165,250,0.16);
  background: rgba(255,255,255,0.035);
  color: var(--text-primary);
  border-radius: 12px;
  padding: 13px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
  cursor: pointer;
  opacity: 0;
  transform: translateY(8px);
  animation: chatHistoryIn 0.32s cubic-bezier(0.22,1,0.36,1) forwards;
  transition: border-color 0.18s ease, background 0.18s ease, transform 0.18s ease;
}

.chat-history-item:hover,
.chat-history-item.active {
  background: rgba(37,99,235,0.1);
  border-color: rgba(37,99,235,0.36);
  transform: translateY(-1px);
}

.chat-history-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.chat-history-title {
  font-size: 14.5px;
  font-weight: 800;
  color: var(--text-primary);
  line-height: 1.28;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat-history-book,
.chat-history-snippet,
.chat-history-time {
  font-size: 12px;
  line-height: 1.4;
}

.chat-history-book {
  color: #93c5fd;
  font-weight: 700;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat-history-snippet {
  color: var(--text-secondary);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.chat-history-time { color: var(--text-muted); }

.chat-history-actions {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: flex-end;
}

.chat-history-continue {
  border: none;
  border-radius: 10px;
  background: rgba(29,78,216,0.14);
  color: #bfdbfe;
  font-family: 'Manrope', 'Inter', sans-serif;
  font-size: 11.5px;
  font-weight: 800;
  padding: 6px 10px;
  cursor: pointer;
}

.chat-history-delete {
  width: 28px;
  height: 28px;
  border-radius: 8px;
  color: var(--text-secondary);
}

@keyframes chatHistoryIn {
  to { opacity: 1; transform: translateY(0); }
}

/* Analysis screen */
.analysis-left {
  width: 280px;
  min-width: 260px;
  flex-shrink: 0;
  background: var(--bg-surface);
  border-right: 1px solid var(--border);
  padding: 32px 24px;
  display: flex;
  flex-direction: column;
  gap: 20px;
  overflow-y: auto;
  z-index: 2;
}

#analysisScreen {
  flex-direction: row;
  min-height: 0;
}

.selected-book-cover {
  width: 100%;
  aspect-ratio: 3/4;
  border-radius: var(--radius-md);
  overflow: hidden;
  background: linear-gradient(160deg, var(--bg-card) 0%, #1a1a30 100%);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 52px;
  border: 1px solid var(--border);
}

.selected-book-cover img { width: 100%; height: 100%; object-fit: cover; }

.selected-book-info { display: flex; flex-direction: column; gap: 4px; }
.book-label { font-size: 10.5px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.07em; }
.book-title { font-size: 15px; font-weight: 700; color: var(--text-primary); line-height: 1.4; }

.analysis-status {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--green);
  padding: 8px 12px;
  background: rgba(29,78,216,0.10);
  border-radius: var(--radius-sm);
  border: 1px solid rgba(147,197,253,0.16);
}

/* PDF viewer button */
.read-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--accent-soft);
  border: 1px solid rgba(37,99,235,0.35);
  border-radius: var(--radius-md);
  color: #93c5fd;
  font-family: 'Inter', sans-serif;
  font-size: 13px;
  font-weight: 600;
  padding: 9px 16px;
  cursor: pointer;
  transition: all 0.2s ease;
  width: 100%;
  justify-content: center;
}

.read-btn:hover {
  background: var(--accent);
  color: #fff;
  border-color: var(--accent);
  box-shadow: 0 4px 16px rgba(37,99,235,0.35);
}

/* Analysis right (chat) */
.analysis-right {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  background: var(--bg-deep);
  position: relative;
}

.chat-sidebar-open {
  display: inline-flex;
}

.chat-flow {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 32px 40px;
  display: flex;
  flex-direction: column;
  gap: 20px;
  scrollbar-width: thin;
  scrollbar-color: rgba(37,99,235,0.3) transparent;
}

.chat-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 24px;
}

.prompt-heading {
  font-size: 22px;
  font-weight: 700;
  color: var(--text-primary);
  text-align: center;
}

.quick-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
  max-width: 520px;
}

.quick-chips.hidden { display: none; }

.chip {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-xl);
  color: var(--text-secondary);
  font-size: 12.5px;
  font-weight: 500;
  padding: 6px 14px;
  cursor: pointer;
  transition: all 0.18s ease;
}

.chip:hover { background: var(--accent-soft); color: #93c5fd; border-color: rgba(37,99,235,0.35); }

.chat-msg {
  display: flex;
  flex-direction: column;
  gap: 6px;
  opacity: 0;
  transform: translateY(6px);
  animation: chatMsgIn 0.22s ease forwards;
}
.chat-msg.user { align-items: flex-end; }
.chat-msg.ai   { align-items: flex-start; }

@keyframes chatMsgIn {
  to { opacity: 1; transform: translateY(0); }
}

.chat-bubble {
  background: var(--accent);
  color: #fff;
  padding: 12px 18px;
  border-radius: 18px 18px 4px 18px;
  font-size: 14px;
  line-height: 1.55;
  max-width: 72%;
  overflow-wrap: anywhere;
}

.chat-bubble strong,
.chat-text strong { font-weight: 700; }

.chat-bubble em,
.chat-text em { font-style: italic; }

.chat-inline-strong { font-weight: 700; }
.chat-inline-em { font-style: italic; }

.chat-text {
  background: var(--bg-card);
  border: 1px solid var(--border);
  color: var(--text-primary);
  padding: 14px 20px;
  border-radius: 4px 18px 18px 18px;
  font-size: 14px;
  line-height: 1.7;
  max-width: 82%;
  overflow-wrap: anywhere;
}

.message-body {
  position: relative;
  word-break: break-word;
}

.message-body > :first-child { margin-top: 0; }
.message-body > :last-child { margin-bottom: 0; }

.message-body p {
  margin: 0 0 10px;
  white-space: pre-wrap;
}

.message-body ul,
.message-body ol {
  margin: 8px 0 10px 20px;
  padding: 0;
}

.message-body li { margin: 4px 0; }

.message-body li > p {
  margin: 0;
}

.message-body li > p + p {
  margin-top: 8px;
}

.chat-md-heading {
  margin: 12px 0 8px;
  font-size: 16px;
  line-height: 1.35;
  font-weight: 800;
}

.chat-md-heading.level-1 { font-size: 18px; }
.chat-md-heading.level-2 { font-size: 17px; }
.chat-md-heading.level-4,
.chat-md-heading.level-5,
.chat-md-heading.level-6 { font-size: 15px; }

.chat-md-quote {
  margin: 10px 0;
  padding: 8px 12px;
  border-left: 3px solid rgba(37,99,235,0.55);
  background: rgba(37,99,235,0.08);
  border-radius: 8px;
  color: var(--text-secondary);
}

.chat-md-code {
  font-family: 'Consolas', 'Menlo', monospace;
  font-size: 0.92em;
  background: rgba(0,0,0,0.24);
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 6px;
  padding: 1px 5px;
}

.chat-md-pre {
  margin: 10px 0;
  padding: 12px;
  overflow-x: auto;
  border-radius: 10px;
  background: rgba(0,0,0,0.28);
  border: 1px solid rgba(255,255,255,0.08);
}

.chat-md-pre code {
  font-family: 'Consolas', 'Menlo', monospace;
  font-size: 12.5px;
  line-height: 1.55;
  white-space: pre;
}

.chat-md-link {
  color: #bfdbfe;
  font-weight: 700;
  text-decoration: none;
  border-bottom: 1px solid rgba(191,219,254,0.35);
}

.chat-md-link:hover { color: #fff; border-bottom-color: #fff; }

.chat-inline-del {
  text-decoration: line-through;
  text-decoration-thickness: 1.5px;
  color: var(--text-muted);
}

.chat-md-hr {
  border: 0;
  border-top: 1px solid rgba(255,255,255,0.12);
  margin: 14px 0;
}

.chat-md-table-wrap {
  width: 100%;
  overflow-x: auto;
  margin: 12px 0;
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 8px;
}

.chat-md-table {
  width: 100%;
  min-width: 360px;
  border-collapse: collapse;
  font-size: 13px;
}

.chat-md-table th,
.chat-md-table td {
  border-bottom: 1px solid rgba(255,255,255,0.08);
  padding: 8px 10px;
  text-align: left;
  vertical-align: top;
}

.chat-md-table th {
  background: rgba(255,255,255,0.05);
  color: var(--text-primary);
  font-weight: 800;
}

.chat-md-table tr:last-child td { border-bottom: 0; }

.chat-md-task-list {
  list-style: none;
  margin-left: 4px;
}

.chat-md-task-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

.chat-md-task-checkbox {
  width: 15px;
  height: 15px;
  margin-top: 3px;
  accent-color: var(--accent);
  flex: 0 0 auto;
}

.chat-md-task-content {
  min-width: 0;
}

.chat-md-image {
  display: block;
  max-width: min(100%, 420px);
  max-height: 260px;
  object-fit: contain;
  margin: 10px 0;
  border-radius: 8px;
  border: 1px solid rgba(255,255,255,0.08);
}

.chat-md-image-link {
  display: block;
  width: fit-content;
  max-width: 100%;
  border: 0;
  text-decoration: none;
}

.chat-md-page-image {
  cursor: zoom-in;
}

.chat-md-image-fallback {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-width: min(100%, 420px);
  margin: 10px 0;
  padding: 10px 12px;
  border-radius: 8px;
  border: 1px solid rgba(255,255,255,0.10);
  background: rgba(0,0,0,0.18);
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.45;
}

.chat-md-image-fallback-title {
  color: var(--text-primary);
  font-weight: 800;
}

.chat-md-image-fallback-src {
  color: var(--text-muted);
  overflow-wrap: anywhere;
}

.chat-md-math {
  font-family: 'Cambria Math', 'Times New Roman', serif;
  letter-spacing: 0;
  word-break: normal;
  overflow-wrap: normal;
}

.chat-md-math.inline {
  display: inline-flex;
  align-items: baseline;
  gap: 2px;
  max-width: 100%;
  padding: 0 2px;
  vertical-align: -0.12em;
  white-space: nowrap;
}

.chat-md-math.display {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  margin: 10px 0;
  padding: 10px 12px;
  overflow-x: auto;
  border-radius: 8px;
  border: 1px solid rgba(255,255,255,0.08);
  background: rgba(0,0,0,0.18);
  color: var(--text-primary);
  font-size: 17px;
  line-height: 1.5;
}

.chat-math-frac {
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  margin: 0 2px;
  vertical-align: middle;
  line-height: 1.05;
}

.chat-math-num {
  min-width: 100%;
  padding: 0 4px 1px;
  border-bottom: 1px solid currentColor;
  text-align: center;
}

.chat-math-den {
  min-width: 100%;
  padding: 1px 4px 0;
  text-align: center;
}

.chat-math-sqrt {
  display: inline-flex;
  align-items: flex-start;
  margin: 0 2px;
}

.chat-math-root {
  font-size: 0.7em;
  transform: translateY(-0.25em);
}

.chat-math-radical {
  font-size: 1.25em;
  line-height: 1;
}

.chat-math-radicand {
  padding: 1px 3px 0;
  border-top: 1px solid currentColor;
}

.chat-math-text {
  font-family: 'Manrope', 'Inter', sans-serif;
  font-style: normal;
}

.chat-md-math sup,
.chat-md-math sub {
  font-size: 0.72em;
  line-height: 0;
}

.chat-md-footnote-ref {
  margin-left: 2px;
  font-size: 0.78em;
}

.chat-md-footnotes {
  margin-top: 14px;
  padding-top: 10px;
  border-top: 1px solid rgba(255,255,255,0.1);
  color: var(--text-secondary);
  font-size: 12.5px;
}

.chat-md-footnotes ol {
  margin: 6px 0 0 18px;
}

.chat-md-footnote-backref {
  margin-left: 6px;
  color: #bfdbfe;
  text-decoration: none;
}

.chat-typing {
  display: none;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 4px 18px 18px 18px;
  width: fit-content;
  max-width: min(440px, calc(100% - 24px));
}

.chat-typing.active { display: flex; }

.typing-spinner {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  border: 2px solid rgba(165,180,252,0.2);
  border-top-color: #93c5fd;
  animation: spin 0.9s linear infinite;
}

.typing-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
  line-height: 1.35;
  overflow-wrap: anywhere;
}

.response-banner {
  position: absolute;
  top: 18px;
  left: 50%;
  transform: translate(-50%, -20px) scale(0.96);
  opacity: 0;
  pointer-events: none;
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 12px 18px;
  border-radius: 999px;
  border: 1px solid rgba(147,197,253,0.28);
  background: rgba(18,10,34,0.92);
  color: #eaf2ff;
  box-shadow: 0 14px 36px rgba(0,0,0,0.35);
  z-index: 6;
}

.response-banner.active {
  animation: responseBannerIn 2.4s cubic-bezier(0.22,1,0.36,1) forwards;
}

.response-banner-text {
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.01em;
}

@keyframes responseBannerIn {
  0% { transform: translate(-50%, -24px) scale(0.94); opacity: 0; }
  12%, 74% { transform: translate(-50%, 0) scale(1); opacity: 1; }
  100% { transform: translate(-50%, -10px) scale(0.96); opacity: 0; }
}

/* Chat input bar */
.chat-input-bar {
  padding: 20px 40px 24px;
  background: var(--bg-deep);
  border-top: 1px solid var(--border);
  display: flex;
  gap: 12px;
  align-items: flex-end;
  flex-shrink: 0;
}

@media (max-width: 900px) {
  #analysisScreen {
    flex-direction: column;
  }

  .chat-sidebar {
    position: fixed;
    top: 0;
    left: 0;
    bottom: 0;
    width: min(340px, 88vw);
    min-width: 0;
    transform: translateX(-105%);
    opacity: 0;
    box-shadow: 18px 0 52px rgba(0,0,0,0.42);
  }

  .chat-sidebar:not(.collapsed) {
    transform: translateX(0);
    opacity: 1;
  }

  .chat-sidebar.collapsed {
    width: min(340px, 88vw);
    padding: 20px 14px;
    transform: translateX(-105%);
  }

  .analysis-left {
    width: 100%;
    min-width: 0;
    padding: 18px 16px 14px;
    border-right: none;
    border-bottom: 1px solid var(--border);
    flex-direction: row;
    flex-wrap: wrap;
    align-items: center;
    gap: 12px;
    overflow: visible;
  }

  .selected-book-cover {
    width: 72px;
    min-width: 72px;
    aspect-ratio: 3/4;
  }

  .selected-book-info {
    flex: 1;
    min-width: 0;
  }

  .book-title {
    font-size: 14px;
  }

  .analysis-status {
    width: 100%;
    order: 4;
  }

  .read-btn,
  .analysis-left .btn {
    width: auto;
    min-height: 40px;
    flex: 1 1 160px;
  }

  .chat-flow {
    padding: 20px 16px 14px;
  }

  .chat-input-bar {
    padding: 14px 16px calc(14px + env(safe-area-inset-bottom, 0px));
    position: sticky;
    bottom: 0;
    z-index: 3;
  }

  .chat-bubble,
  .chat-text {
    max-width: 100%;
  }
}

@media (max-width: 520px) {
  .chat-input-bar {
    flex-wrap: wrap;
    align-items: stretch;
  }

  .chat-input-wrap {
    flex-basis: 100%;
  }

  #analyzeBtn {
    width: 100%;
    min-width: 0;
    justify-content: center;
  }
}

.chat-input-wrap {
  flex: 1;
  min-width: 0;
  background: var(--bg-card);
  border: 1.5px solid var(--border);
  border-radius: var(--radius-md);
  display: flex;
  align-items: flex-end;
  padding: 2px 4px;
  transition: border-color 0.2s ease;
}

.chat-input-wrap:focus-within { border-color: var(--accent); }

#promptInput {
  flex: 1;
  min-width: 0;
  background: none;
  border: none;
  outline: none;
  color: var(--text-primary);
  font-family: 'Inter', sans-serif;
  font-size: 14px;
  padding: 12px 14px;
  resize: none;
  max-height: 180px;
  line-height: 1.55;
}

#promptInput::placeholder { color: var(--text-muted); }

#analyzeBtn {
  flex-shrink: 0;
  min-width: 112px;
  height: 44px;
  padding: 0 16px;
  gap: 8px;
  border-radius: var(--radius-md);
  background: var(--accent);
  border: none;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.2s ease;
  font-family: 'Inter', sans-serif;
  font-size: 13px;
  font-weight: 700;
}

#analyzeBtn svg { flex-shrink: 0; }
.analyze-btn-label { white-space: nowrap; }

#analyzeBtn:hover { background: var(--accent-glow); box-shadow: var(--shadow-glow); }
#analyzeBtn.loading { opacity: 0.6; pointer-events: none; }

/* PDF Viewer overlay */
.pdf-viewer-overlay {
  position: fixed;
  inset: 0;
  z-index: 100;
  background: rgba(5,10,24,0.96);
  display: flex;
  flex-direction: column;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.3s ease;
}

.pdf-viewer-overlay.active {
  opacity: 1;
  pointer-events: all;
}

.pdf-viewer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 24px;
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  gap: 12px;
}

.pdf-viewer-header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.pdf-viewer-controls {
  display: flex;
  align-items: center;
  gap: 4px;
  background: rgba(255,255,255,0.05);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 3px 6px;
}

.pdf-ctrl-btn {
  width: 30px;
  height: 30px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s ease;
}
.pdf-ctrl-btn:hover {
  background: var(--accent-soft);
  color: var(--accent);
}

.pdf-ctrl-btn.active {
  background: var(--accent-soft);
  color: var(--accent);
  box-shadow: inset 0 0 0 1px rgba(96,165,250,0.25);
}

.pdf-page-info, .pdf-zoom-info, .pdf-page-total {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  user-select: none;
}

.pdf-page-info, .pdf-zoom-info {
  min-width: 56px;
  text-align: center;
}

.pdf-page-jump {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  min-width: 82px;
}

.pdf-page-input {
  width: 42px;
  height: 26px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: rgba(5,10,24,0.42);
  color: var(--text-primary);
  font: 700 12px 'Inter', sans-serif;
  text-align: center;
  outline: none;
}

.pdf-page-input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px rgba(37,99,235,0.18);
}

.pdf-page-input::-webkit-outer-spin-button,
.pdf-page-input::-webkit-inner-spin-button {
  margin: 0;
}

.pdf-ctrl-sep {
  width: 1px;
  height: 20px;
  background: var(--border);
  margin: 0 4px;
}

.pdf-viewer-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 300px;
}

.pdf-viewer-close {
  width: 36px;
  height: 36px;
  border-radius: var(--radius-sm);
  background: rgba(248,113,113,0.1);
  border: 1px solid rgba(248,113,113,0.25);
  color: var(--red);
  font-size: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: background 0.18s ease;
}

.pdf-viewer-close:hover { background: rgba(248,113,113,0.25); }

.pdf-viewer-body {
  flex: 1;
  display: flex;
  align-items: stretch;
  justify-content: center;
  overflow: hidden;
  background: #1a1a2e;
}

#pdfCanvasWrap {
  width: 100%;
  height: 100%;
  overflow: auto;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 12px;
  box-sizing: border-box;
  background: #1a1a2e;
  scroll-behavior: smooth;
}

#pdfCanvasWrap.pdf-pannable {
  align-items: flex-start;
  justify-content: flex-start;
}

#pdfCanvas {
  display: block;
  margin: auto;
  flex-shrink: 0;
  box-shadow: 0 4px 32px rgba(0,0,0,0.5);
}

.pdf-viewer-overlay.iframe-mode .pdf-viewer-controls {
  display: none;
}

.pdf-viewer-overlay.iframe-mode #pdfCanvasWrap {
  padding: 0;
  overflow: hidden;
  align-items: stretch;
  justify-content: stretch;
}

.pdf-viewer-overlay.iframe-mode #pdfCanvas {
  display: none;
}

#pdfFrame {
  display: none;
  width: 100%;
  height: 100%;
  border: 0;
  background: #fff;
}

.pdf-viewer-overlay.iframe-mode #pdfFrame {
  display: block;
}

#pdfLoadingOverlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: rgba(5,10,24,0.92);
  backdrop-filter: blur(12px);
  z-index: 20;
  transition: opacity 0.4s ease;
}
#pdfLoadingOverlay.hidden { opacity: 0; pointer-events: none; }
.pdf-load-ring {
  position: relative;
  width: 120px; height: 120px;
  margin-bottom: 24px;
}
.pdf-load-ring svg { width: 120px; height: 120px; transform: rotate(-90deg); }
.pdf-load-ring-bg { fill: none; stroke: #1e1e3a; stroke-width: 6; }
.pdf-load-ring-fg {
  fill: none;
  stroke: #2563eb;
  stroke-width: 6;
  stroke-linecap: round;
  stroke-dasharray: 339.292;
  stroke-dashoffset: 339.292;
  transition: stroke-dashoffset 0.3s ease;
  filter: drop-shadow(0 0 6px rgba(37,99,235,0.5));
}
.pdf-load-pct {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 26px;
  font-weight: 700;
  color: #fff;
  font-family: 'Inter', sans-serif;
}
.pdf-load-label {
  color: #94a3b8;
  font-size: 14px;
  font-family: 'Inter', sans-serif;
  margin-top: 4px;
  letter-spacing: 0.5px;
}
.pdf-load-title {
  color: #e2e8f0;
  font-size: 16px;
  font-weight: 600;
  font-family: 'Inter', sans-serif;
  margin-bottom: 20px;
  max-width: 300px;
  text-align: center;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pdf-load-bytes {
  color: #64748b;
  font-size: 12px;
  font-family: 'Inter', sans-serif;
  margin-top: 8px;
}
@keyframes pdfLoadPulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}
.pdf-load-indeterminate .pdf-load-ring-fg {
  stroke-dashoffset: 84.823;
  animation: pdfRingSpin 1.5s linear infinite;
}
@keyframes pdfRingSpin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
.pdf-load-indeterminate .pdf-load-ring svg {
  animation: pdfRingSpin 1.5s linear infinite;
}

@media (max-width: 600px) {
  .pdf-viewer-controls { gap: 1px; padding: 2px 3px; }
  .pdf-ctrl-btn { width: 26px; height: 26px; }
  .pdf-page-info, .pdf-zoom-info, .pdf-page-total { font-size: 11px; min-width: 40px; }
  .pdf-page-jump { min-width: 68px; }
  .pdf-page-input { width: 34px; height: 24px; font-size: 11px; }
  .pdf-viewer-title { max-width: 120px; font-size: 13px; }
}

/* Delete overlay */
.del-overlay {
  position: fixed;
  inset: 0;
  z-index: 200;
  background: rgba(5,10,24,0.75);
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.22s ease;
  backdrop-filter: blur(4px);
}

.del-overlay.active { opacity: 1; pointer-events: all; }

.del-panel {
  background: var(--bg-card);
  border: 1px solid rgba(248,113,113,0.25);
  border-radius: var(--radius-lg);
  padding: 32px;
  max-width: 400px;
  width: 90%;
  display: flex;
  flex-direction: column;
  gap: 20px;
  box-shadow: 0 8px 48px rgba(0,0,0,0.6);
  animation: panelIn 0.25s cubic-bezier(0.16, 1, 0.3, 1);
}

@keyframes panelIn {
  from { transform: scale(0.92); opacity: 0; }
  to   { transform: scale(1);    opacity: 1; }
}

.del-panel-title { font-size: 18px; font-weight: 700; color: var(--text-primary); }
.del-panel-sub   { font-size: 13.5px; color: var(--text-secondary); line-height: 1.5; }
.del-panel-name  { font-weight: 700; color: var(--text-primary); }

.del-actions { display: flex; gap: 10px; justify-content: flex-end; }

/* Settings overlay */
.cfg-overlay {
  position: fixed;
  inset: 0;
  z-index: 200;
  background: rgba(5,10,24,0.78);
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.22s ease;
  backdrop-filter: blur(6px);
}

.cfg-overlay.active { opacity: 1; pointer-events: all; }

.cfg-panel {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 28px 32px 32px;
  max-width: 480px;
  width: 93%;
  display: flex;
  flex-direction: column;
  gap: 20px;
  box-shadow: 0 8px 48px rgba(0,0,0,0.6);
  animation: panelIn 0.25s cubic-bezier(0.16,1,0.3,1);
}

.cfg-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.cfg-panel-title { font-size: 17px; font-weight: 700; color: var(--text-primary); }
.cfg-close-btn   { background: none; border: none; color: var(--text-secondary); font-size: 18px; cursor: pointer; padding: 4px 6px; border-radius: 6px; transition: background 0.15s; }
.cfg-close-btn:hover { background: var(--border); }

.cfg-desc {
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.55;
  background: rgba(37,99,235,0.07);
  border: 1px solid rgba(37,99,235,0.15);
  border-radius: var(--radius-sm);
  padding: 10px 13px;
}

.cfg-desc a { color: #93c5fd; text-decoration: underline; }

.cfg-grade-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.cfg-grade-label {
  width: 70px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  flex-shrink: 0;
}

.cfg-input {
  flex: 1;
  background: var(--bg-main);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  font-family: 'Inter', monospace;
  font-size: 12.5px;
  padding: 8px 11px;
  outline: none;
  transition: border-color 0.18s;
}

.cfg-input:focus { border-color: var(--accent); }
.cfg-input::placeholder { color: rgba(148,163,184,0.5); }

.cfg-actions { display: flex; gap: 10px; justify-content: flex-end; margin-top: 4px; }

.profile-settings-overlay,
.admin-tools-overlay {
  position: fixed;
  inset: 0;
  z-index: 320;
  background: rgba(6,14,31, 0.72);
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.24s ease;
  backdrop-filter: blur(24px) saturate(1.25);
  -webkit-backdrop-filter: blur(24px) saturate(1.25);
  padding: 18px;
}

.profile-settings-overlay.active,
.admin-tools-overlay.active {
  opacity: 1;
  pointer-events: all;
}

.profile-settings-panel,
.admin-tools-panel {
  width: min(560px, 100%);
  max-height: min(760px, calc(100vh - 34px));
  overflow-y: auto;
  border: 1px solid rgba(147,197,253,0.18);
  border-radius: 26px;
  background: linear-gradient(145deg, rgba(18,31,58, 0.94), rgba(6,14,31, 0.90));
  box-shadow: 0 30px 90px rgba(0,0,0,0.46), inset 0 1px rgba(255,255,255,0.08);
  padding: 22px;
  transform: translateY(18px) scale(0.98);
  transition: transform 0.28s cubic-bezier(0.16,1,0.3,1);
}

.admin-tools-panel {
  width: min(880px, 100%);
}

.profile-settings-overlay.active .profile-settings-panel,
.admin-tools-overlay.active .admin-tools-panel {
  transform: none;
}

.profile-settings-head,
.admin-tools-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  margin-bottom: 18px;
}

.profile-settings-kicker,
.admin-tools-kicker {
  color: #bfdbfe;
  font-size: 11px;
  font-weight: 950;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}

.profile-settings-title,
.admin-tools-title {
  margin-top: 5px;
  color: var(--text-primary);
  font-size: 22px;
  font-weight: 950;
  line-height: 1.1;
}

.profile-settings-grid {
  display: grid;
  grid-template-columns: 150px 1fr;
  gap: 18px;
  align-items: start;
}

.profile-photo-card {
  display: grid;
  justify-items: center;
  gap: 10px;
  padding: 14px;
  border-radius: 20px;
  border: 1px solid rgba(147,197,253,0.14);
  background: rgba(255,255,255,0.06);
  position: relative;
}

.profile-photo-preview-wrap {
  position: relative;
  width: 96px;
  height: 96px;
}

.profile-photo-preview {
  width: 96px;
  height: 96px;
  border-radius: 28px;
  display: grid;
  place-items: center;
  background: linear-gradient(135deg, #071a3d, #1d4ed8);
  color: #eef5ff;
  font-size: 34px;
  font-weight: 950;
  background-size: cover;
  background-position: center;
  box-shadow: inset 0 0 0 1px rgba(255,255,255,0.24);
}

.profile-status-toggle {
  position: absolute;
  left: -7px;
  bottom: -7px;
  width: 38px;
  height: 38px;
  border-radius: 999px;
  border: 1px solid rgba(147,197,253,0.28);
  background: rgba(6,14,31,0.92);
  color: var(--text-primary);
  display: grid;
  place-items: center;
  cursor: pointer;
  box-shadow: 0 12px 26px rgba(0,0,0,0.30), inset 0 1px rgba(255,255,255,0.14);
  transition: var(--transition);
  z-index: 2;
}

.profile-status-toggle:hover,
.profile-status-toggle.active {
  background: rgba(29,78,216,0.78);
  border-color: rgba(191,219,254,0.46);
  transform: translateY(-1px);
}

.profile-status-toggle .presence-mini-dot {
  width: 11px;
  height: 11px;
}

.profile-status-toggle[data-presence="idle"] .presence-mini-dot { background: #facc15; }
.profile-status-toggle[data-presence="dnd"] .presence-mini-dot { background: #f87171; }
.profile-status-toggle[data-presence="invisible"] .presence-mini-dot { background: #94a3b8; }

.profile-presence-popover {
  display: none;
  width: 100%;
}

.profile-presence-popover.active {
  display: block;
}

.profile-presence-popover .presence-picker {
  margin-top: 0;
}

.account-menu-presence-popover {
  margin-top: 14px;
}

.profile-photo-input {
  display: none;
}

.settings-form {
  display: grid;
  gap: 12px;
}

.settings-field {
  display: grid;
  gap: 7px;
}

.settings-label {
  color: rgba(243,239,255,0.72);
  font-size: 12px;
  font-weight: 900;
}

.settings-label-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.settings-email-state {
  min-height: 24px;
  display: inline-flex;
  align-items: center;
  padding: 0 9px;
  border-radius: 999px;
  border: 1px solid rgba(147,197,253,0.18);
  background: rgba(29,78,216,0.12);
  color: #bfdbfe;
  font-size: 10.5px;
  font-weight: 950;
  white-space: nowrap;
}

.settings-email-state.verified {
  background: rgba(29,78,216,0.18);
  color: #eef5ff;
}

.settings-email-state.changed,
.settings-email-state.pending {
  border-color: rgba(251,191,36,0.24);
  background: rgba(251,191,36,0.08);
  color: #fbbf24;
}

.settings-email-control {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 8px;
  align-items: center;
}

.settings-email-control .settings-input {
  min-width: 0;
}

.settings-input {
  width: 100%;
  min-height: 44px;
  border: 1px solid rgba(147,197,253,0.16);
  border-radius: 16px;
  background: rgba(6,14,31,0.45);
  color: var(--text-primary);
  padding: 0 14px;
  outline: none;
  font-family: 'Inter', sans-serif;
  font-weight: 700;
}

.settings-input:focus {
  border-color: rgba(147,197,253,0.45);
  box-shadow: 0 0 0 4px rgba(29,78,216,0.14);
}

.email-verify-btn {
  min-height: 44px;
  padding: 0 14px;
  border: 1px solid rgba(147,197,253,0.20);
  border-radius: 999px;
  background: linear-gradient(135deg, rgba(15,42,95,0.88), rgba(29,78,216,0.62));
  color: #eef5ff;
  font-weight: 900;
  cursor: pointer;
  box-shadow: 0 14px 32px rgba(0,0,0,0.24), inset 0 1px rgba(255,255,255,0.10);
  transition: transform 0.28s cubic-bezier(0.16,1,0.3,1), border-color 0.24s ease, opacity 0.24s ease;
}

.email-verify-btn:hover:not(:disabled) {
  transform: translateY(-1px);
  border-color: rgba(191,219,254,0.34);
}

.email-verify-btn:disabled {
  opacity: 0.54;
  cursor: default;
}

.settings-hint {
  color: rgba(243,239,255,0.52);
  font-size: 11.5px;
  font-weight: 750;
  line-height: 1.5;
}

.email-code-overlay {
  position: fixed;
  inset: 0;
  z-index: 345;
  display: grid;
  place-items: center;
  padding: 18px;
  background: rgba(3,7,18,0.58);
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.32s cubic-bezier(0.22,1,0.36,1);
  backdrop-filter: blur(26px) saturate(1.25);
  -webkit-backdrop-filter: blur(26px) saturate(1.25);
}

.email-code-overlay.active {
  opacity: 1;
  pointer-events: all;
}

.email-code-panel {
  width: min(430px, 100%);
  padding: 20px;
  border: 1px solid rgba(147,197,253,0.20);
  border-radius: 26px;
  background:
    linear-gradient(145deg, rgba(18,31,58,0.95), rgba(6,14,31,0.92)),
    var(--material-glass);
  box-shadow: 0 30px 90px rgba(0,0,0,0.48), 0 0 42px rgba(15,42,95,0.20), inset 0 1px rgba(255,255,255,0.10);
  transform: translateY(18px) scale(0.96);
  opacity: 0;
  transition:
    transform 0.42s cubic-bezier(0.16,1,0.3,1),
    opacity 0.34s cubic-bezier(0.22,1,0.36,1);
}

.email-code-overlay.active .email-code-panel {
  transform: none;
  opacity: 1;
}

.email-code-back {
  min-height: 34px;
  padding: 0 12px;
  margin: 0 0 12px;
  border: 1px solid rgba(147,197,253,0.16);
  border-radius: 999px;
  background: rgba(255,255,255,0.07);
  color: rgba(243,239,255,0.86);
  font-weight: 900;
  cursor: pointer;
}

.email-code-kicker {
  color: #bfdbfe;
  font-size: 11px;
  font-weight: 950;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}

.email-code-title {
  margin-top: 7px;
  font-size: 24px;
  font-weight: 950;
  line-height: 1.12;
}

.email-code-lead,
.email-code-target,
.email-code-status {
  margin-top: 8px;
  color: rgba(243,239,255,0.64);
  font-size: 13px;
  font-weight: 750;
  line-height: 1.55;
}

.email-code-target {
  overflow-wrap: anywhere;
  color: #bfdbfe;
}

.email-code-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 8px;
  margin: 18px 0 10px;
}

.email-code-cell {
  width: 100%;
  aspect-ratio: 1;
  min-height: 48px;
  border: 1px solid rgba(147,197,253,0.18);
  border-radius: 16px;
  background: rgba(6,14,31,0.48);
  color: #fff;
  text-align: center;
  font-size: 24px;
  font-weight: 950;
  outline: none;
  box-shadow: inset 0 1px rgba(255,255,255,0.06);
  transition: transform 0.26s cubic-bezier(0.16,1,0.3,1), border-color 0.22s ease, box-shadow 0.22s ease, background 0.22s ease;
}

.email-code-cell:focus {
  transform: translateY(-2px);
  border-color: rgba(191,219,254,0.48);
  background: rgba(24,16,45,0.72);
  box-shadow: 0 0 0 4px rgba(29,78,216,0.18), inset 0 1px rgba(255,255,255,0.08);
}

.email-code-cell.filled {
  border-color: rgba(147,197,253,0.34);
  background: rgba(29,78,216,0.16);
}

.email-code-status.error {
  color: var(--red);
}

.email-code-status.success {
  color: #bfdbfe;
}

.email-code-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 16px;
}

.avatar-crop-overlay,
.password-change-overlay,
.verify-required-overlay,
.existing-account-overlay {
  position: fixed;
  inset: 0;
  z-index: 346;
  display: grid;
  place-items: center;
  padding: 18px;
  background: rgba(3,7,18,0.62);
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.32s cubic-bezier(0.22,1,0.36,1);
  backdrop-filter: blur(26px) saturate(1.25);
  -webkit-backdrop-filter: blur(26px) saturate(1.25);
}

.avatar-crop-overlay.active,
.password-change-overlay.active,
.verify-required-overlay.active,
.existing-account-overlay.active {
  opacity: 1;
  pointer-events: all;
}

.avatar-crop-panel,
.password-change-panel,
.verify-required-panel,
.existing-account-panel {
  width: min(460px, 100%);
  max-height: calc(100vh - 36px);
  overflow-y: auto;
  padding: 20px;
  border: 1px solid rgba(147,197,253,0.20);
  border-radius: 26px;
  background: linear-gradient(145deg, rgba(18,31,58,0.96), rgba(6,14,31,0.94));
  box-shadow: 0 30px 90px rgba(0,0,0,0.50), 0 0 42px rgba(37,99,235,0.20), inset 0 1px rgba(255,255,255,0.10);
  transform: translateY(18px) scale(0.96);
  opacity: 0;
  transition:
    transform 0.42s cubic-bezier(0.16,1,0.3,1),
    opacity 0.34s cubic-bezier(0.22,1,0.36,1);
}

.avatar-crop-overlay.active .avatar-crop-panel,
.password-change-overlay.active .password-change-panel,
.verify-required-overlay.active .verify-required-panel,
.existing-account-overlay.active .existing-account-panel {
  transform: none;
  opacity: 1;
}

.existing-account-actions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin-top: 18px;
}

.forgot-password-btn {
  width: fit-content;
  border: 0;
  background: transparent;
  color: #bfdbfe;
  font-size: 12px;
  font-weight: 900;
  cursor: pointer;
  justify-self: start;
  margin-top: -4px;
  padding: 0;
}

.forgot-password-btn:hover {
  color: #eef5ff;
  text-decoration: underline;
}

.forgot-password-btn.hidden {
  display: none;
}

.avatar-crop-canvas {
  width: min(300px, 100%);
  aspect-ratio: 1;
  display: block;
  margin: 0 auto 16px;
  border-radius: 28px;
  border: 1px solid rgba(147,197,253,0.22);
  background: rgba(6,14,31,0.62);
  box-shadow: inset 0 1px rgba(255,255,255,0.08);
}

.avatar-crop-controls,
.password-new-fields {
  display: grid;
  gap: 12px;
}

.settings-range {
  width: 100%;
  accent-color: #2563eb;
}

.password-new-fields {
  display: none;
  margin-top: 12px;
}

.password-change-panel.password-ready .password-code-grid {
  display: none;
}

.password-change-panel.password-ready .password-new-fields {
  display: grid;
}

.settings-password-btn {
  width: 100%;
}

.settings-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 16px;
}

.admin-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}

.admin-stat {
  min-height: 78px;
  padding: 13px;
  border-radius: 18px;
  border: 1px solid rgba(147,197,253,0.14);
  background: rgba(255,255,255,0.06);
}

.admin-stat-value {
  color: var(--text-primary);
  font-size: 22px;
  font-weight: 950;
}

.admin-stat-label {
  margin-top: 4px;
  color: var(--text-secondary);
  font-size: 11.5px;
  font-weight: 800;
}

.admin-account-list {
  display: grid;
  gap: 9px;
}

.admin-sensitive-actions {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 10px;
  align-items: center;
  margin: 0 0 14px;
  padding: 12px;
  border-radius: 18px;
  border: 1px solid rgba(147,197,253,0.14);
  background: linear-gradient(135deg, rgba(15,23,42, 0.72), rgba(6,14,31, 0.62));
}

.admin-sensitive-note {
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.45;
}

.admin-sensitive-list {
  display: grid;
  gap: 10px;
  margin-bottom: 14px;
}

.admin-sensitive-card {
  display: grid;
  gap: 10px;
  padding: 14px;
  border-radius: 20px;
  border: 1px solid rgba(147,197,253,0.14);
  background: linear-gradient(145deg, rgba(18,31,58, 0.72), rgba(6,14,31, 0.70));
  box-shadow: inset 0 1px rgba(255,255,255,0.06);
}

.admin-sensitive-title {
  color: var(--text-primary);
  font-weight: 950;
  overflow-wrap: anywhere;
}

.admin-sensitive-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.admin-sensitive-field {
  min-height: 58px;
  padding: 10px;
  border-radius: 14px;
  border: 1px solid rgba(147,197,253,0.12);
  background: rgba(255,255,255,0.05);
}

.admin-sensitive-label {
  color: var(--text-muted);
  font-size: 10.5px;
  font-weight: 900;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.admin-sensitive-value {
  margin-top: 5px;
  color: var(--text-primary);
  font-size: 12.5px;
  font-weight: 800;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.admin-session-list {
  display: grid;
  gap: 7px;
}

.admin-session-row {
  padding: 10px;
  border-radius: 14px;
  border: 1px solid rgba(147,197,253,0.10);
  background: rgba(6,14,31,0.42);
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}

.admin-account-row {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 12px;
  padding: 13px;
  border-radius: 18px;
  border: 1px solid rgba(147,197,253,0.14);
  background: rgba(255,255,255,0.055);
}

.admin-account-main {
  min-width: 0;
}

.admin-account-name {
  color: var(--text-primary);
  font-weight: 900;
  overflow-wrap: anywhere;
}

.admin-account-meta {
  margin-top: 4px;
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.55;
  overflow-wrap: anywhere;
}

.admin-account-flags {
  display: flex;
  align-items: flex-start;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 6px;
}

.admin-mini-badge {
  min-height: 24px;
  padding: 0 8px;
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  border: 1px solid rgba(147,197,253,0.16);
  background: rgba(255,255,255,0.07);
  color: var(--text-secondary);
  font-size: 11px;
  font-weight: 900;
}

.admin-mini-badge.good {
  color: #bfdbfe;
  border-color: rgba(147,197,253,0.24);
  background: rgba(29,78,216,0.10);
}

.admin-mini-badge.warn {
  color: rgba(251,191,36,0.96);
  border-color: rgba(251,191,36,0.22);
  background: rgba(251,191,36,0.08);
}

@media (max-width: 720px) {
  .profile-settings-grid {
    grid-template-columns: 1fr;
  }
  .settings-email-control,
  .email-code-actions {
    grid-template-columns: 1fr;
  }
  .settings-email-control {
    display: grid;
  }
  .email-code-actions {
    display: grid;
  }
  .email-code-grid {
    gap: 6px;
  }
  .admin-stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .admin-sensitive-actions,
  .admin-sensitive-grid {
    grid-template-columns: 1fr;
  }
  .admin-account-row {
    grid-template-columns: 1fr;
  }
  .admin-account-flags {
    justify-content: flex-start;
  }
}

/* Auth modal */
.auth-overlay {
  position: fixed;
  inset: 0;
  z-index: 380;
  background: rgba(5,10,24,0.82);
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.3s ease;
  backdrop-filter: blur(10px);
}
.auth-overlay.active { opacity: 1; pointer-events: all; }

.auth-panel {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 32px 28px 24px;
  width: 340px;
  max-width: 90vw;
  text-align: center;
  transform: translateY(20px) scale(0.95);
  transition: transform 0.35s cubic-bezier(0.22,1,0.36,1);
}
.auth-overlay.active .auth-panel {
  transform: none;
}

.auth-lock-icon {
  width: 48px;
  height: 48px;
  margin: 0 auto 14px;
  background: var(--accent-soft);
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--accent);
}

.auth-title {
  font-size: 16px;
  font-weight: 700;
  color: var(--text-primary);
  margin-bottom: 6px;
}

.auth-desc {
  font-size: 12.5px;
  color: var(--text-muted);
  margin-bottom: 18px;
  line-height: 1.5;
}

.auth-input {
  width: 100%;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 11px 14px;
  font-family: 'Inter', sans-serif;
  font-size: 14px;
  color: var(--text-primary);
  outline: none;
  transition: border-color 0.25s ease, box-shadow 0.25s ease;
  text-align: center;
  letter-spacing: 2px;
}
.auth-input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 16px rgba(37,99,235,0.2);
}
.auth-input.shake {
  animation: authShake 0.45s ease;
  border-color: #f87171;
}
@keyframes authShake {
  0%, 100% { transform: translateX(0); }
  20%, 60% { transform: translateX(-8px); }
  40%, 80% { transform: translateX(8px); }
}

.auth-error {
  font-size: 12px;
  color: #f87171;
  margin-top: 8px;
  min-height: 18px;
  transition: opacity 0.2s ease;
}

.auth-actions {
  display: flex;
  gap: 10px;
  margin-top: 16px;
}
.auth-actions .btn { flex: 1; justify-content: center; }

/* Rename overlay */
.rename-overlay {
  position: fixed;
  inset: 0;
  z-index: 250;
  background: rgba(5,10,24,0.78);
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.25s ease;
  backdrop-filter: blur(6px);
}
.rename-overlay.active { opacity: 1; pointer-events: all; }
.rename-panel {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 24px;
  width: 360px;
  max-width: 90vw;
  transform: translateY(16px) scale(0.96);
  transition: transform 0.3s cubic-bezier(0.22,1,0.36,1);
}
.rename-overlay.active .rename-panel { transform: none; }
.rename-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--text-primary);
  margin-bottom: 14px;
}

.rename-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 14px;
}

.rename-label {
  font-size: 11px;
  font-weight: 700;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.rename-input {
  width: 100%;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 10px 14px;
  font-family: 'Inter', sans-serif;
  font-size: 13px;
  color: var(--text-primary);
  outline: none;
  transition: border-color 0.2s ease;
}
.rename-input:focus { border-color: var(--accent); }
.rename-cover-row {
  display: grid;
  grid-template-columns: 82px minmax(0, 1fr);
  gap: 14px;
  align-items: center;
}

.rename-cover-preview {
  width: 82px;
  aspect-ratio: 3/4;
  border-radius: 10px;
  overflow: hidden;
  background: linear-gradient(160deg, var(--bg-surface), #1a1a30);
  border: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 28px;
}

.rename-cover-preview img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.rename-cover-controls {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.rename-cover-note {
  font-size: 12px;
  line-height: 1.45;
  color: var(--text-secondary);
}

.rename-cover-file { display: none; }
.rename-cover-btn {
  min-height: 38px;
  border-radius: 10px;
  border: 1px solid rgba(37,99,235,0.28);
  background: rgba(37,99,235,0.1);
  color: #bfdbfe;
  font-family: 'Inter', sans-serif;
  font-size: 12.5px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  cursor: pointer;
  transition: all 0.18s ease;
}

.rename-cover-btn:hover {
  background: rgba(37,99,235,0.18);
  border-color: rgba(37,99,235,0.45);
  color: #fff;
}
.rename-actions { display: flex; gap: 10px; margin-top: 14px; }
.rename-actions .btn { flex: 1; justify-content: center; }

/* Card edit button */
.card-edit-btn {
  position: absolute;
  top: 6px;
  left: 6px;
  z-index: 3;
  width: 26px;
  height: 26px;
  border-radius: 50%;
  border: none;
  background: rgba(37,99,235,0.85);
  color: #fff;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  transform: scale(0.8);
  transition: all 0.2s ease;
}
.book-card:hover .card-edit-btn { opacity: 1; transform: scale(1); }
.card-edit-btn:hover { background: var(--accent); transform: scale(1.1) !important; }

/* Add Book overlay (same pattern as cfg-overlay) */
.add-overlay {
  position: fixed;
  inset: 0;
  z-index: 200;
  background: rgba(5,10,24,0.78);
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.22s ease;
  backdrop-filter: blur(6px);
}

.add-overlay.active { opacity: 1; pointer-events: all; }

.add-panel {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 28px 32px 32px;
  max-width: 480px;
  width: 93%;
  display: flex;
  flex-direction: column;
  gap: 18px;
  box-shadow: 0 8px 48px rgba(0,0,0,0.6);
  animation: panelIn 0.25s cubic-bezier(0.16,1,0.3,1);
}

.add-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.add-panel-title { font-size: 17px; font-weight: 700; color: var(--text-primary); }

.add-desc {
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.55;
  background: rgba(37,99,235,0.07);
  border: 1px solid rgba(37,99,235,0.15);
  border-radius: var(--radius-sm);
  padding: 10px 13px;
}

.add-field { display: flex; flex-direction: column; gap: 6px; }
.add-label { font-size: 12px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.05em; }
.add-input {
  background: var(--bg-main);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  font-family: 'Inter', monospace;
  font-size: 13px;
  padding: 10px 12px;
  outline: none;
  transition: border-color 0.18s;
  width: 100%;
  box-sizing: border-box;
}

.add-input:focus { border-color: var(--accent); }
.add-input::placeholder { color: rgba(148,163,184,0.4); }

.add-grade-select {
  background: var(--bg-main);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  font-family: 'Inter', sans-serif;
  font-size: 13px;
  padding: 10px 12px;
  outline: none;
  cursor: pointer;
  transition: border-color 0.18s;
  width: 100%;
  box-sizing: border-box;
}

.add-grade-select:focus { border-color: var(--accent); }

.add-actions { display: flex; gap: 10px; justify-content: flex-end; margin-top: 4px; }

/* Library nav action buttons */
.upload-nav-btn,
.history-nav-btn,
.dm-nav-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  background: rgba(37,99,235,0.12);
  border: 1px solid rgba(37,99,235,0.3);
  border-radius: var(--radius-xl);
  color: #93c5fd;
  font-family: 'Inter', sans-serif;
  font-size: 12px;
  font-weight: 600;
  padding: 6px 12px;
  cursor: pointer;
  transition: all 0.2s ease;
  flex-shrink: 0;
  white-space: nowrap;
}

.upload-nav-btn:hover,
.history-nav-btn:hover,
.dm-nav-btn:hover {
  background: rgba(37,99,235,0.22);
  border-color: var(--accent);
  color: #fff;
}

.history-nav-btn {
  background: rgba(255,255,255,0.045);
  border-color: rgba(96,165,250,0.22);
  color: #bfdbfe;
}

.header-dm-btn {
  position: relative;
  min-height: 36px;
  padding: 0 12px;
  gap: 7px;
}

.header-dm-btn svg {
  width: 16px;
  height: 16px;
  stroke-width: 2.35;
}

.header-dm-btn .dm-header-badge {
  margin-left: -2px;
}

/* Hidden file input */
#pdfFileInput { display: none; }

.btn-danger {
  background: rgba(248,113,113,0.15);
  color: var(--red);
  border: 1px solid rgba(248,113,113,0.35);
  border-radius: var(--radius-md);
  font-family: 'Inter', sans-serif;
  font-size: 14px;
  font-weight: 600;
  padding: 9px 20px;
  cursor: pointer;
  transition: background 0.2s ease;
}
.btn-danger:hover { background: rgba(248,113,113,0.28); }

/* Bildirimler */
#toastContainer {
  position: fixed;
  top: calc(18px + env(safe-area-inset-top, 0px));
  right: 18px;
  left: auto;
  bottom: auto;
  z-index: 360;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  justify-content: flex-start;
  gap: 14px;
  width: min(430px, calc(100vw - 28px));
  pointer-events: none;
}

.toast {
  pointer-events: all;
  display: flex;
  align-items: center;
  gap: 16px;
  background: rgba(15,15,26,0.94);
  border: 1px solid var(--border);
  border-radius: 24px;
  padding: 18px 20px;
  min-width: 320px;
  max-width: min(520px, 92vw);
  box-shadow: var(--shadow-card);
  backdrop-filter: blur(14px);
  animation: toastIn 0.34s cubic-bezier(0.16, 1, 0.3, 1) forwards;
  position: relative;
  overflow: hidden;
  opacity: 0;
  transform: translateX(36px) translateY(-8px) scale(0.98);
}

@keyframes toastIn {
  from { transform: translateX(36px) translateY(-8px) scale(0.98); opacity: 0; filter: saturate(0.86); }
  to   { transform: translateX(0) translateY(0) scale(1); opacity: 1; filter: none; }
}

.toast.leaving {
  animation: toastOut 0.28s ease forwards;
}

@keyframes toastOut {
  to { transform: translateX(44px) translateY(-6px) scale(0.98); opacity: 0; filter: saturate(0.86); }
}

.toast.success { border-color: rgba(147,197,253,0.34); }
.toast.error   { border-color: rgba(248,113,113,0.34); }
.toast.warning { border-color: rgba(251,191,36,0.34); }
.toast.info    { border-color: rgba(37,99,235,0.34); }

.toast-icon {
  width: 54px;
  height: 54px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  position: relative;
}

.toast-icon svg { width: 54px; height: 54px; display: block; }
.toast.success .toast-icon { color: var(--green); }
.toast.error .toast-icon { color: var(--red); }
.toast.warning .toast-icon { color: var(--amber); }
.toast.info .toast-icon { color: #93c5fd; }

.toast-body { flex: 1; min-width: 0; }
.toast-title { font-size: 15px; font-weight: 800; color: var(--text-primary); }
.toast-msg   { font-size: 13px; color: var(--text-secondary); margin-top: 4px; line-height: 1.55; }

.toast-close {
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 14px;
  padding: 4px;
  flex-shrink: 0;
  line-height: 1.2;
}

.toast-close:hover { color: var(--text-primary); }

.toast-progress {
  position: absolute;
  bottom: 0;
  left: 0;
  height: 2px;
  animation: toastProgress linear forwards;
}
.toast.success .toast-progress { background: var(--green); }
.toast.error   .toast-progress { background: var(--red); }
.toast.warning .toast-progress { background: var(--amber); }
.toast.info    .toast-progress { background: var(--accent); }
@keyframes toastProgress { from { width: 100%; } to { width: 0%; } }

@keyframes checkStroke {
  100% { stroke-dashoffset: 0; }
}

.checkmark-circle {
  fill: rgba(29,78,216,0.14);
  stroke: rgba(147,197,253,0.42);
  stroke-width: 2;
  stroke-dasharray: 166;
  stroke-dashoffset: 166;
  animation: checkStroke 0.5s ease forwards;
}

.checkmark-check {
  fill: none;
  stroke: currentColor;
  stroke-width: 3.2;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-dasharray: 48;
  stroke-dashoffset: 48;
  animation: checkStroke 0.3s 0.24s ease forwards;
}

/* Görev durumu penceresi */
.task-overlay {
  position: fixed;
  inset: 0;
  z-index: 340;
  background: rgba(5,10,24,0.82);
  backdrop-filter: blur(14px);
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.24s ease;
}

.task-overlay.active {
  opacity: 1;
  pointer-events: all;
}

.task-panel {
  width: min(560px, 92vw);
  background: rgba(15,15,26,0.96);
  border: 1px solid var(--border);
  border-radius: 28px;
  box-shadow: 0 24px 72px rgba(0,0,0,0.46);
  padding: 26px 28px 24px;
  display: flex;
  flex-direction: column;
  gap: 18px;
  position: relative;
  transform: scale(0.96);
  transition: transform 0.26s cubic-bezier(0.22,1,0.36,1);
}

.task-overlay.active .task-panel { transform: scale(1); }

.task-dismiss-btn {
  position: absolute;
  top: 14px;
  right: 14px;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  border: 1px solid rgba(96,165,250,0.22);
  background: rgba(255,255,255,0.04);
  color: #bfdbfe;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  cursor: pointer;
  transition: all 0.18s ease;
}

.task-dismiss-btn:hover:not(:disabled) {
  background: rgba(248,113,113,0.14);
  border-color: rgba(248,113,113,0.32);
  color: #fecaca;
}

.task-dismiss-btn:disabled {
  opacity: 0.55;
  cursor: wait;
}

.task-hero {
  display: flex;
  align-items: center;
  gap: 16px;
}

.task-spinner {
  width: 58px;
  height: 58px;
  border-radius: 50%;
  border: 3px solid rgba(96,165,250,0.18);
  border-top-color: #93c5fd;
  animation: spin 0.9s linear infinite;
  flex-shrink: 0;
}

.task-success-icon {
  display: none;
  width: 58px;
  height: 58px;
  flex-shrink: 0;
  color: var(--green);
}

.task-overlay.done .task-spinner { display: none; }
.task-overlay.done .task-success-icon { display: block; }

.task-title {
  font-size: 18px;
  font-weight: 800;
  color: var(--text-primary);
}

.task-subtitle {
  margin-top: 5px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-secondary);
}

.task-status {
  font-size: 13px;
  font-weight: 700;
  color: #bfdbfe;
  min-height: 20px;
}

.task-progress-track {
  height: 10px;
  border-radius: 999px;
  background: rgba(255,255,255,0.06);
  overflow: hidden;
}

.task-progress-fill {
  height: 100%;
  width: 0%;
  border-radius: inherit;
  background: linear-gradient(90deg, #071a3d, #1d4ed8);
  transition: width 0.25s ease;
}

.task-metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.task-metric {
  background: rgba(255,255,255,0.03);
  border: 1px solid rgba(37,99,235,0.12);
  border-radius: 16px;
  padding: 12px 10px;
}

.task-metric-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.task-metric-value {
  margin-top: 6px;
  font-size: 20px;
  font-weight: 800;
  color: var(--text-primary);
}

.task-log {
  max-height: 220px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-right: 4px;
}

.task-log-item {
  background: rgba(255,255,255,0.03);
  border: 1px solid rgba(37,99,235,0.08);
  border-radius: 14px;
  padding: 10px 12px;
  font-size: 12.5px;
  line-height: 1.55;
  color: var(--text-secondary);
}

.task-actions {
  display: flex;
  justify-content: flex-end;
}

.task-actions.cancel-pending {
  justify-content: space-between;
  align-items: center;
}

.task-cancel-note {
  font-size: 12px;
  font-weight: 600;
  color: var(--amber);
}

.task-close-btn {
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.2s ease;
}

.task-overlay.done .task-close-btn {
  opacity: 1;
  pointer-events: all;
}

@keyframes spin { to { transform: rotate(360deg); } }

/* Liquid Glass redesign ---------------------------------------------------- */
:root {
  color-scheme: light;
  --bg-deep: #edf3f8;
  --bg-surface: rgba(255, 255, 255, 0.58);
  --bg-card: rgba(255, 255, 255, 0.52);
  --bg-glass: rgba(255, 255, 255, 0.38);
  --bg-main: rgba(255, 255, 255, 0.46);
  --bg-base: #edf3f8;
  --border: rgba(42, 56, 78, 0.16);
  --accent: #1d4ed8;
  --accent-glow: #60a5fa;
  --accent-soft: rgba(29,78,216, 0.14);
  --green: #1d4ed8;
  --amber: #b56200;
  --red: #d92d20;
  --text-primary: #132238;
  --text-secondary: #526173;
  --text-muted: #8793a3;
  --radius-sm: 10px;
  --radius-md: 14px;
  --radius-lg: 22px;
  --radius-xl: 999px;
  --material-sheet: linear-gradient(180deg, rgba(250, 253, 255, 0.86), rgba(241, 247, 252, 0.66));
  --material-glass: linear-gradient(135deg, rgba(255, 255, 255, 0.72), rgba(255, 255, 255, 0.34));
  --material-clear: linear-gradient(135deg, rgba(255, 255, 255, 0.50), rgba(255, 255, 255, 0.20));
  --material-stained: linear-gradient(135deg, rgba(15,42,95, 0.94), rgba(29,78,216, 0.76));
  --glass-border: rgba(255, 255, 255, 0.68);
  --glass-edge: rgba(40, 58, 83, 0.14);
  --shadow-card: 0 18px 55px rgba(48, 64, 92, 0.16), inset 0 1px 0 rgba(255, 255, 255, 0.74);
  --shadow-glow: 0 18px 46px rgba(29,78,216, 0.22), inset 0 1px 0 rgba(255, 255, 255, 0.42);
  --shadow-float: 0 28px 82px rgba(38, 54, 81, 0.22), 0 1px 0 rgba(255, 255, 255, 0.72) inset;
  --transition: 0.42s cubic-bezier(0.22, 1, 0.36, 1);
}

html,
body {
  background: #edf3f8;
  color: var(--text-primary);
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Inter", sans-serif;
}

body::before {
  background:
    linear-gradient(115deg, rgba(29,78,216, 0.16), transparent 30%),
    linear-gradient(245deg, rgba(29,78,216, 0.14), transparent 38%),
    linear-gradient(180deg, rgba(255, 214, 102, 0.12), transparent 48%),
    linear-gradient(180deg, #f9fcff 0%, #edf4f9 42%, #f7fbff 100%);
  background-size: cover;
  opacity: 1;
}

body::after {
  top: 0;
  left: 0;
  width: auto;
  height: auto;
  inset: 0;
  background:
    linear-gradient(90deg, rgba(255, 255, 255, 0.42), rgba(255, 255, 255, 0) 24%, rgba(12, 28, 54, 0.035) 52%, rgba(255, 255, 255, 0.18)),
    repeating-linear-gradient(90deg, rgba(255, 255, 255, 0.18) 0 1px, transparent 1px 36px);
  animation: none;
  filter: none;
  opacity: 0.62;
}

::selection {
  background: rgba(29,78,216, 0.20);
}

button,
input,
textarea,
select {
  font-family: inherit !important;
}

.screen {
  background: transparent;
}

.navbar,
.grade-bar,
.status-pill,
.search-wrap::after,
.upload-nav-btn,
.history-nav-btn,
.dm-nav-btn,
.library-bottom-menu,
.bottom-grade-cluster,
.sync-btn,
.scan-nav-btn,
.sync-btn-empty,
.btn-ghost,
.net-indicator,
.dm-panel,
.dm-thread,
.chat-sidebar,
.analysis-left,
.selected-book-cover,
.analysis-status,
.read-btn,
.chip,
.chat-text,
.chat-typing,
.chat-input-wrap,
.pdf-viewer-header,
.pdf-viewer-controls,
.del-panel,
.cfg-panel,
.auth-panel,
.rename-panel,
.add-panel,
.upload-card,
.toast,
.task-panel,
.chat-history-item,
.new-chat-btn,
.task-metric,
.task-log-item {
  background: var(--material-glass);
  border-color: var(--glass-edge);
  box-shadow: var(--shadow-card);
  backdrop-filter: blur(26px) saturate(1.55);
  -webkit-backdrop-filter: blur(26px) saturate(1.55);
}

.navbar {
  margin: 12px 16px 0;
  border: 1px solid var(--glass-edge);
  border-radius: 28px;
  min-height: 64px;
  overflow: hidden;
}

.navbar::before,
.grade-bar::before,
.library-bottom-menu::before,
.book-card::before,
.chat-input-bar::before,
.pdf-viewer-header::before {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.72), rgba(255, 255, 255, 0.04) 48%, rgba(29,78,216, 0.08));
  opacity: 0.78;
}

.nav-left,
.nav-center,
.nav-right,
.grade-bar > *,
.library-bottom-menu > *,
.book-card > *,
.chat-input-bar > *,
.pdf-viewer-header > * {
  position: relative;
  z-index: 1;
}

.tagline,
.grade-label,
.footer-copy,
.book-label,
.rename-label,
.add-label,
.cfg-desc,
.add-desc,
.auth-desc,
.chat-history-snippet,
.task-subtitle,
.task-log-item {
  color: var(--text-secondary);
}

.meb-logo,
.nav-logo {
  filter: drop-shadow(0 10px 18px rgba(50, 74, 108, 0.16));
}

.nav-logo-shell::before {
  inset: 6px 4px;
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.88), rgba(29,78,216, 0.16));
  filter: blur(10px);
  opacity: 0.82;
}

.nav-logo-shell::after {
  inset: 8px 7px;
  border-color: rgba(255, 255, 255, 0.7);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.8), 0 12px 28px rgba(29,78,216, 0.16);
}

.grade-strip {
  padding: 16px 24px 10px;
}

.grade-strip {
  justify-content: center;
}

.search-wrap {
  border-radius: 999px;
  isolation: isolate;
}

.search-wrap::before {
  inset: -1px;
  border-radius: inherit;
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.78), rgba(29,78,216, 0.30), rgba(147,197,253, 0.18));
  filter: blur(12px);
  opacity: 0.32;
  animation: none;
}

.search-wrap:focus-within::before {
  opacity: 0.62;
}

.search-wrap::after,
.search-input {
  border-radius: 999px;
}

.search-input {
  border-color: rgba(31, 48, 75, 0.14);
  color: var(--text-primary);
  padding: 11px 16px 11px 40px;
}

.search-input::placeholder,
#promptInput::placeholder,
.auth-input::placeholder,
.cfg-input::placeholder,
.add-input::placeholder {
  color: var(--text-muted);
}

.search-input:focus,
.cfg-input:focus,
.add-input:focus,
.add-grade-select:focus,
.rename-input:focus,
.auth-input:focus,
.pdf-page-input:focus,
.chat-input-wrap:focus-within {
  border-color: rgba(29,78,216, 0.56);
  box-shadow: 0 0 0 4px rgba(29,78,216, 0.12), inset 0 1px 0 rgba(255, 255, 255, 0.68);
}

.search-icon,
.search-wrap:focus-within .search-icon,
.footer-brand,
.chat-history-book,
.chat-md-link,
.chat-md-footnote-backref {
  color: var(--accent);
}

.grade-bar {
  position: relative;
  overflow: hidden;
  padding: 5px;
}

.grade-btn {
  color: var(--text-secondary);
  border-radius: 999px;
  padding: 7px 13px;
}

.grade-btn:hover {
  color: var(--text-primary);
  background: rgba(255, 255, 255, 0.46);
  transform: translateY(-1px);
}

.grade-btn.active {
  color: #fff;
  background: var(--material-stained);
  box-shadow: var(--shadow-glow);
  transform: none;
}

.upload-nav-btn,
.history-nav-btn,
.dm-nav-btn,
.bottom-menu-item,
.sync-btn,
.scan-nav-btn,
.read-btn,
.btn,
.new-chat-btn,
.rename-cover-btn,
.card-analyze-btn,
#analyzeBtn,
.sync-btn-empty,
.task-close-btn {
  border-radius: 999px;
}

.upload-nav-btn,
.history-nav-btn,
.dm-nav-btn,
.bottom-menu-item,
.sync-btn,
.scan-nav-btn,
.read-btn,
.btn-ghost,
.new-chat-btn,
.rename-cover-btn,
.sync-btn-empty {
  color: var(--accent);
}

.upload-nav-btn:hover,
.history-nav-btn:hover,
.dm-nav-btn:hover,
.bottom-menu-item:hover,
.sync-btn:hover:not(:disabled),
.scan-nav-btn:hover:not(:disabled),
.read-btn:hover,
.btn-ghost:hover,
.new-chat-btn:hover,
.rename-cover-btn:hover,
.sync-btn-empty:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.72);
  border-color: rgba(29,78,216, 0.38);
  color: var(--accent-glow);
  box-shadow: var(--shadow-glow);
}

.btn-primary,
#analyzeBtn,
.card-analyze-btn:hover,
.sync-btn-empty {
  background: var(--material-stained);
  color: #fff;
  border: 1px solid rgba(255, 255, 255, 0.38);
  box-shadow: var(--shadow-glow);
}

.btn-primary:hover,
#analyzeBtn:hover {
  background: linear-gradient(135deg, #1e40af, #60a5fa);
  box-shadow: var(--shadow-glow);
}

.status-pill {
  color: var(--green);
}

.book-grid-wrap {
  padding: 30px 40px 132px;
  scrollbar-color: rgba(29,78,216, 0.25) transparent;
}

.book-card {
  background: var(--material-sheet);
  border-color: rgba(32, 48, 74, 0.12);
  border-radius: 8px;
  box-shadow: 0 18px 44px rgba(44, 61, 88, 0.14), inset 0 1px 0 rgba(255, 255, 255, 0.74);
  overflow: hidden;
}

.book-card:hover {
  border-color: rgba(29,78,216, 0.34);
  transform: translateY(-6px) scale(1.018);
  box-shadow: 0 24px 70px rgba(32, 57, 92, 0.22), 0 12px 34px rgba(29,78,216, 0.16);
}

.card-cover,
.rename-cover-preview {
  background:
    linear-gradient(145deg, rgba(255, 255, 255, 0.72), rgba(227, 236, 246, 0.72)),
    linear-gradient(45deg, rgba(29,78,216, 0.14), rgba(147,197,253, 0.10));
  border-color: rgba(32, 48, 74, 0.12);
}

.card-cover {
  border-radius: 8px 8px 0 0;
}

.card-name,
.book-title,
.prompt-heading,
.chat-sidebar-title,
.task-title,
.task-metric-value,
.toast-title {
  color: var(--text-primary);
}

.sync-badge-local {
  color: #8a5a00;
}

.scan-badge {
  border: 1px solid rgba(255, 255, 255, 0.5);
}

.scan-badge.done {
  background: rgba(29,78,216, 0.13);
  color: var(--green);
}

.scan-badge.pending {
  background: rgba(29,78,216, 0.12);
  color: var(--accent);
}

.scan-badge.failed,
.btn-danger,
.card-del-btn,
.pdf-viewer-close {
  color: var(--red);
}

.card-analyze-btn {
  background: rgba(255, 255, 255, 0.46);
  border-color: rgba(29,78,216, 0.22);
  color: var(--accent);
  min-height: 38px;
}

.card-del-btn,
.card-edit-btn,
.chat-sidebar-toggle,
.chat-history-delete,
.task-dismiss-btn,
.pdf-ctrl-btn,
.cfg-close-btn,
.toast-close {
  background: rgba(255, 255, 255, 0.44);
  border: 1px solid rgba(34, 50, 76, 0.12);
  color: var(--text-secondary);
  backdrop-filter: blur(16px) saturate(1.35);
  -webkit-backdrop-filter: blur(16px) saturate(1.35);
}

.card-del-btn:hover,
.btn-danger:hover,
.pdf-viewer-close:hover,
.task-dismiss-btn:hover:not(:disabled) {
  background: rgba(255, 69, 58, 0.14);
  border-color: rgba(217, 45, 32, 0.26);
  color: var(--red);
}

.card-edit-btn:hover,
.chat-sidebar-toggle:hover,
.chat-history-delete:hover,
.pdf-ctrl-btn:hover,
.pdf-ctrl-btn.active,
.cfg-close-btn:hover,
.toast-close:hover {
  background: rgba(29,78,216, 0.13);
  border-color: rgba(29,78,216, 0.24);
  color: var(--accent);
}

.lib-footer {
  border-top-color: rgba(32, 48, 74, 0.08);
  backdrop-filter: blur(18px) saturate(1.35);
  -webkit-backdrop-filter: blur(18px) saturate(1.35);
}

.library-bottom-menu {
  position: fixed;
  left: 50%;
  bottom: 42px;
  z-index: 340;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  width: fit-content;
  max-width: calc(100vw - 28px);
  min-height: 60px;
  padding: 7px 10px;
  border: 1px solid rgba(32, 48, 74, 0.12);
  border-radius: 22px;
  transform: translateX(-50%);
  overflow: hidden;
  isolation: isolate;
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.68), rgba(248, 252, 255, 0.34));
  box-shadow:
    0 26px 76px rgba(0, 0, 0, 0.34),
    0 8px 28px rgba(15, 42, 95, 0.18),
    inset 0 1px 0 rgba(255, 255, 255, 0.82);
  backdrop-filter: blur(28px) saturate(1.6);
  -webkit-backdrop-filter: blur(28px) saturate(1.6);
}

.library-bottom-menu::after {
  content: "";
  position: absolute;
  inset: auto 20px -10px;
  height: 20px;
  border-radius: 999px;
  background: rgba(26, 40, 62, 0.16);
  filter: blur(18px);
  z-index: -1;
}

.bottom-menu-item {
  min-width: 90px;
  height: 46px;
  padding: 5px 11px;
  border: 1px solid transparent;
  background: transparent;
  box-shadow: none;
  color: var(--text-secondary);
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 3px;
  cursor: pointer;
  white-space: nowrap;
}

.bottom-menu-item svg {
  width: 21px;
  height: 21px;
  stroke-width: 2.25;
}

.bottom-menu-item span {
  font-size: 12px;
  line-height: 1;
  font-weight: 700;
}

.bottom-menu-label {
  display: none;
}

.bottom-menu-item:hover {
  transform: translateY(-1px);
}

.bottom-grade-cluster {
  min-width: 106px;
  height: 46px;
  padding: 5px 8px;
  border: 1px solid rgba(255, 255, 255, 0.58);
  border-radius: 18px;
  color: var(--accent);
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: center;
  gap: 0;
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.82), rgba(255, 255, 255, 0.46));
  box-shadow: 0 12px 34px rgba(31, 48, 75, 0.14), inset 0 1px 0 rgba(255, 255, 255, 0.86);
}

.bottom-grade-options {
  display: grid;
  grid-template-columns: repeat(2, 42px);
  gap: 4px;
  padding: 3px;
  border-radius: 999px;
  background: rgba(32, 48, 74, 0.07);
  border: 1px solid rgba(32, 48, 74, 0.08);
}

.bottom-grade-btn.grade-btn {
  width: 42px;
  min-width: 42px;
  height: 28px;
  padding: 0;
  font-size: 13px;
  font-weight: 800;
  border-radius: 999px;
}

.chat-history-backdrop,
.upload-overlay,
.del-overlay,
.cfg-overlay,
.auth-overlay,
.rename-overlay,
.add-overlay,
.task-overlay {
  background: rgba(235, 242, 249, 0.56);
  backdrop-filter: blur(24px) saturate(1.35);
  -webkit-backdrop-filter: blur(24px) saturate(1.35);
}

.chat-sidebar {
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.80), rgba(243, 248, 252, 0.58));
  border-right-color: rgba(32, 48, 74, 0.13);
  box-shadow: 28px 0 80px rgba(39, 57, 84, 0.20), inset -1px 0 rgba(255, 255, 255, 0.55);
}

.chat-sidebar-label,
.chat-history-time,
.upload-counter,
.upload-size,
.upload-overall-label,
.task-metric-label {
  color: var(--text-muted);
}

.chat-history-empty {
  background: rgba(255, 255, 255, 0.34);
  border-color: rgba(29,78,216, 0.20);
  color: var(--text-secondary);
}

.chat-history-item:hover,
.chat-history-item.active {
  background: rgba(255, 255, 255, 0.72);
  border-color: rgba(29,78,216, 0.30);
}

#analysisScreen {
  gap: 14px;
  padding: 12px 14px 14px;
}

.analysis-left {
  border: 1px solid rgba(32, 48, 74, 0.12);
  border-radius: 28px;
  padding: 28px 22px;
}

.analysis-right {
  background:
    linear-gradient(135deg, rgba(255, 255, 255, 0.32), rgba(255, 255, 255, 0.10)),
    rgba(255, 255, 255, 0.18);
  border: 1px solid rgba(32, 48, 74, 0.10);
  border-radius: 28px;
  overflow: hidden;
  backdrop-filter: blur(20px) saturate(1.25);
  -webkit-backdrop-filter: blur(20px) saturate(1.25);
}

.analysis-status {
  color: var(--green);
  background: rgba(29,78,216, 0.14);
  border-color: rgba(147,197,253, 0.22);
}

.chat-flow {
  scrollbar-color: rgba(29,78,216, 0.25) transparent;
}

.chat-empty {
  color: var(--text-primary);
}

.chip {
  background: rgba(255, 255, 255, 0.42);
  border-color: rgba(32, 48, 74, 0.12);
  color: var(--text-secondary);
}

.chip:hover {
  background: rgba(255, 255, 255, 0.72);
  color: var(--accent);
  border-color: rgba(29,78,216, 0.24);
}

.chat-bubble {
  background: linear-gradient(135deg, rgba(15,42,95, 0.95), rgba(29,78,216, 0.76));
  color: #fff;
  border: 1px solid rgba(255, 255, 255, 0.36);
  box-shadow: 0 18px 42px rgba(29,78,216, 0.20), inset 0 1px 0 rgba(255, 255, 255, 0.35);
  backdrop-filter: blur(18px) saturate(1.45);
  -webkit-backdrop-filter: blur(18px) saturate(1.45);
}

.chat-text {
  background: rgba(255, 255, 255, 0.62);
  border-color: rgba(32, 48, 74, 0.12);
  color: var(--text-primary);
}

.chat-md-quote,
.cfg-desc,
.add-desc {
  background: rgba(29,78,216, 0.08);
  border-color: rgba(29,78,216, 0.18);
}

.chat-md-code,
.chat-md-pre,
.chat-md-table-wrap,
.chat-md-math.display,
.chat-md-image-fallback {
  background: rgba(255, 255, 255, 0.54);
  border-color: rgba(32, 48, 74, 0.12);
}

.chat-md-table th {
  background: rgba(29,78,216, 0.08);
}

.chat-md-table th,
.chat-md-table td,
.chat-md-hr,
.chat-md-footnotes {
  border-color: rgba(32, 48, 74, 0.12);
}

.chat-inline-del {
  color: var(--text-muted);
}

.chat-typing {
  color: var(--text-secondary);
}

.typing-spinner,
.task-spinner {
  border-color: rgba(29,78,216, 0.18);
  border-top-color: var(--accent);
}

.response-banner {
  background: rgba(15,42,95, 0.88);
  border-color: rgba(147,197,253, 0.24);
  color: var(--green);
  box-shadow: var(--shadow-float);
  backdrop-filter: blur(22px) saturate(1.4);
  -webkit-backdrop-filter: blur(22px) saturate(1.4);
}

.chat-input-bar {
  position: relative;
  background: rgba(255, 255, 255, 0.48);
  border-top-color: rgba(32, 48, 74, 0.10);
  backdrop-filter: blur(28px) saturate(1.45);
  -webkit-backdrop-filter: blur(28px) saturate(1.45);
  overflow: hidden;
}

.chat-input-wrap,
#promptInput {
  background: transparent;
}

.chat-input-wrap {
  border-color: rgba(32, 48, 74, 0.12);
}

#promptInput,
.auth-input,
.rename-input,
.cfg-input,
.add-input,
.add-grade-select,
.pdf-page-input {
  color: var(--text-primary);
}

.pdf-viewer-overlay {
  background: rgba(237, 243, 248, 0.94);
}

.pdf-viewer-header {
  margin: 12px;
  border: 1px solid rgba(32, 48, 74, 0.12);
  border-radius: 24px;
  overflow: hidden;
}

.pdf-viewer-body,
#pdfCanvasWrap {
  background:
    linear-gradient(135deg, rgba(226, 235, 244, 0.88), rgba(250, 252, 255, 0.74));
}

#pdfCanvas {
  box-shadow: 0 28px 70px rgba(35, 49, 74, 0.22);
  border-radius: 6px;
}

#pdfLoadingOverlay {
  background: rgba(237, 243, 248, 0.76);
  backdrop-filter: blur(24px) saturate(1.35);
  -webkit-backdrop-filter: blur(24px) saturate(1.35);
}

.pdf-load-ring-bg {
  stroke: rgba(32, 48, 74, 0.10);
}

.pdf-load-ring-fg {
  stroke: var(--accent);
  filter: drop-shadow(0 0 8px rgba(29,78,216, 0.28));
}

.pdf-load-pct,
.pdf-load-title {
  color: var(--text-primary);
}

.pdf-load-label,
.pdf-load-bytes,
.pdf-page-info,
.pdf-zoom-info,
.pdf-page-total {
  color: var(--text-secondary);
}

.cfg-input,
.add-input,
.add-grade-select,
.auth-input,
.rename-input,
.pdf-page-input {
  background: rgba(255, 255, 255, 0.48);
  border-color: rgba(32, 48, 74, 0.12);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.62);
}

.del-panel,
.cfg-panel,
.auth-panel,
.rename-panel,
.add-panel,
.upload-card,
.task-panel {
  background: linear-gradient(145deg, rgba(255, 255, 255, 0.82), rgba(244, 249, 253, 0.58));
  border-color: rgba(32, 48, 74, 0.13);
  box-shadow: var(--shadow-float);
}

.auth-lock-icon {
  background: rgba(29,78,216, 0.12);
  color: var(--accent);
}

.btn-danger {
  background: rgba(255, 69, 58, 0.12);
  border-color: rgba(217, 45, 32, 0.22);
}

.upload-header,
.upload-pct,
.upload-speed,
.task-status,
.toast.info .toast-icon {
  color: var(--accent);
}

.upload-bar-track,
.upload-overall-track,
.task-progress-track {
  background: rgba(32, 48, 74, 0.08);
}

.upload-bar-fill,
.toast.info .toast-progress {
  background: linear-gradient(90deg, #0f2a5f, #60a5fa);
}

.upload-overall-fill {
  background: rgba(29,78,216, 0.34);
}

.toast {
  background: rgba(255, 255, 255, 0.78);
  border-radius: 24px;
}

.toast-msg {
  color: var(--text-secondary);
}

.task-progress-fill {
  background: linear-gradient(90deg, #0f2a5f, #60a5fa);
}

.task-cancel-note {
  color: var(--amber);
}

.empty-books-img {
  filter: drop-shadow(0 18px 34px rgba(49, 72, 105, 0.18));
}

.empty-state svg {
  color: var(--accent);
}

.net-indicator.online,
.net-indicator.wifi,
.net-indicator.ethernet {
  color: var(--green);
}

.net-indicator.offline {
  color: var(--red);
}

.net-indicator.online .net-dot,
.net-indicator.wifi .net-dot,
.net-indicator.ethernet .net-dot {
  background: var(--green);
}

.net-indicator.offline .net-dot {
  background: var(--red);
}

@media (max-width: 900px) {
  #analysisScreen {
    padding: 10px;
    gap: 10px;
  }

  .analysis-left {
    border-radius: 24px;
    border-right: 1px solid rgba(32, 48, 74, 0.12);
  }

  .analysis-right {
    border-radius: 24px;
  }

  .chat-input-bar {
    border-radius: 0 0 24px 24px;
  }
}

@media (max-width: 600px) {
  .navbar {
    margin: 8px 10px 0;
    border-radius: 24px;
  }

  .grade-strip {
    padding: 12px 12px 8px;
  }

  .book-grid-wrap {
    padding: 14px 12px 124px;
  }

  .search-input {
    padding-top: 10px;
    padding-bottom: 10px;
  }

  .pdf-viewer-header {
    margin: 8px;
    border-radius: 20px;
  }

  .library-bottom-menu {
    bottom: 8px;
    gap: 5px;
    min-height: 50px;
    padding: 5px 7px;
    border-radius: 20px;
  }

  .bottom-menu-item {
    min-width: 72px;
    height: 40px;
    padding: 4px 7px;
  }

  .bottom-menu-item svg {
    width: 18px;
    height: 18px;
  }

  .bottom-menu-item span {
    font-size: 11.5px;
  }

  .bottom-grade-cluster {
    min-width: 92px;
    height: 40px;
    padding: 4px 6px;
    border-radius: 16px;
  }

  .bottom-grade-options {
    grid-template-columns: repeat(2, 36px);
    gap: 3px;
  }

  .bottom-grade-btn.grade-btn {
    width: 36px;
    min-width: 36px;
    height: 24px;
    font-size: 12px;
  }
}

/* Dark blue theme and grid action alignment ----------------------------- */
:root {
  color-scheme: dark;
  --bg-deep: #030712;
  --bg-surface: rgba(14, 9, 27, 0.84);
  --bg-card: rgba(10,25,54, 0.80);
  --bg-glass: rgba(24, 16, 45, 0.54);
  --bg-main: rgba(10, 7, 21, 0.78);
  --bg-base: #030712;
  --border: rgba(96,165,250, 0.16);
  --accent: #2563eb;
  --accent-glow: #1e40af;
  --accent-soft: rgba(29,78,216, 0.18);
  --green: #60a5fa;
  --amber: #fbbf24;
  --red: #fb7185;
  --text-primary: #eef5ff;
  --text-secondary: #a8b7d1;
  --text-muted: #6f7f9a;
  --material-sheet: linear-gradient(180deg, rgba(10,25,54, 0.94), rgba(8, 5, 19, 0.92));
  --material-glass: linear-gradient(135deg, rgba(15,42,95, 0.72), rgba(10, 6, 22, 0.50));
  --material-clear: linear-gradient(135deg, rgba(30,64,175, 0.50), rgba(8, 5, 19, 0.28));
  --material-stained: linear-gradient(135deg, rgba(30,64,175, 0.98), rgba(15,42,95, 0.94));
  --glass-border: rgba(191,219,254, 0.20);
  --glass-edge: rgba(96,165,250, 0.16);
  --shadow-card: 0 18px 55px rgba(0, 0, 0, 0.42), inset 0 1px 0 rgba(255, 255, 255, 0.10);
  --shadow-glow: 0 18px 46px rgba(30,64,175, 0.34), inset 0 1px 0 rgba(255, 255, 255, 0.14);
  --shadow-float: 0 28px 82px rgba(0, 0, 0, 0.50), 0 1px 0 rgba(255, 255, 255, 0.12) inset;
}

html,
body {
  background: var(--bg-deep);
  color: var(--text-primary);
}

body::before {
  background:
    linear-gradient(135deg, rgba(3,7,18, 1) 0%, rgba(16, 9, 31, 0.99) 50%, rgba(5, 4, 14, 1) 100%),
    linear-gradient(90deg, rgba(29,78,216, 0.12), transparent 38%, rgba(96,165,250, 0.075));
  background-size: cover;
  opacity: 1;
}

body::after {
  background:
    repeating-linear-gradient(90deg, rgba(96,165,250, 0.04) 0 1px, transparent 1px 40px),
    repeating-linear-gradient(0deg, rgba(96,165,250, 0.026) 0 1px, transparent 1px 40px);
  opacity: 0.38;
}

.navbar,
.search-wrap::after,
.status-pill,
.library-bottom-menu,
.bottom-grade-cluster,
.upload-nav-btn,
.history-nav-btn,
.dm-nav-btn,
.sync-btn,
.scan-nav-btn,
.sync-btn-empty,
.btn-ghost,
.net-indicator,
.chat-sidebar,
.analysis-left,
.selected-book-cover,
.analysis-status,
.read-btn,
.chip,
.chat-text,
.chat-typing,
.chat-input-wrap,
.pdf-viewer-header,
.pdf-viewer-controls,
.del-panel,
.cfg-panel,
.auth-panel,
.rename-panel,
.add-panel,
.upload-card,
.toast,
.task-panel,
.chat-history-item,
.new-chat-btn,
.task-metric,
.task-log-item {
  background: var(--material-glass);
  border-color: var(--glass-edge);
  box-shadow: var(--shadow-card);
}

.navbar::before,
.library-bottom-menu::before,
.book-card::before,
.chat-input-bar::before,
.pdf-viewer-header::before {
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.12), transparent 48%, rgba(29,78,216, 0.16));
  opacity: 0.72;
}

.search-wrap::before {
  background: linear-gradient(135deg, rgba(29,78,216, 0.52), rgba(147,197,253, 0.16));
}

.search-input,
.cfg-input,
.add-input,
.add-grade-select,
.auth-input,
.rename-input,
.pdf-page-input {
  background: rgba(6,14,31, 0.56);
  border-color: rgba(147,197,253, 0.18);
  color: var(--text-primary);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.06);
}

.search-input:focus,
.cfg-input:focus,
.add-input:focus,
.add-grade-select:focus,
.rename-input:focus,
.auth-input:focus,
.pdf-page-input:focus,
.chat-input-wrap:focus-within {
  border-color: rgba(37,99,235, 0.66);
  box-shadow: 0 0 0 4px rgba(30,64,175, 0.18), inset 0 1px 0 rgba(255, 255, 255, 0.08);
}

.search-icon,
.search-wrap:focus-within .search-icon,
.footer-brand,
.chat-history-book,
.chat-md-link,
.chat-md-footnote-backref,
.upload-nav-btn,
.history-nav-btn,
.dm-nav-btn,
.bottom-menu-item,
.sync-btn,
.scan-nav-btn,
.read-btn,
.btn-ghost,
.new-chat-btn,
.rename-cover-btn,
.sync-btn-empty {
  color: var(--accent);
}

.status-pill,
.analysis-status,
.net-indicator.online,
.net-indicator.wifi,
.net-indicator.ethernet {
  color: var(--green);
}

.status-pill {
  min-height: 34px;
  gap: 8px;
  padding: 7px 13px;
  border-radius: 999px;
  background: linear-gradient(135deg, rgba(6,14,31, 0.82), rgba(15,42,95, 0.60));
  border-color: rgba(147,197,253, 0.26);
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.34), 0 0 22px rgba(29,78,216, 0.12), inset 0 1px 0 rgba(255, 255, 255, 0.09);
  font-weight: 750;
  letter-spacing: 0.01em;
}

.status-dot {
  position: relative;
  width: 8px;
  height: 8px;
  box-shadow: 0 0 12px currentColor;
}

.status-dot::after {
  content: '';
  position: absolute;
  inset: -4px;
  border: 1px solid currentColor;
  border-radius: inherit;
  opacity: 0.18;
}

.analysis-status {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 38px;
  padding: 9px 12px;
  border-radius: 12px;
  background: linear-gradient(135deg, rgba(10, 7, 21, 0.86), rgba(10,25,54, 0.68));
  border-color: rgba(147,197,253, 0.22);
  box-shadow: 0 12px 30px rgba(0, 0, 0, 0.30), 0 0 20px rgba(29,78,216, 0.12), inset 0 1px 0 rgba(255, 255, 255, 0.08);
  font-weight: 800;
  letter-spacing: 0.01em;
}

.analysis-status::before {
  content: '';
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: currentColor;
  box-shadow: 0 0 12px currentColor;
  flex: 0 0 auto;
}

.book-grid-wrap {
  scrollbar-color: rgba(96,165,250, 0.34) transparent;
}

.book-card {
  display: flex;
  flex-direction: column;
  background: var(--material-sheet);
  border-color: rgba(147,197,253, 0.16);
  border-radius: 8px;
  box-shadow: 0 18px 46px rgba(0, 0, 0, 0.36), inset 0 1px 0 rgba(255, 255, 255, 0.09);
  overflow: hidden;
}

.book-card::after {
  content: '';
  position: absolute;
  left: 12px;
  right: 12px;
  bottom: 10px;
  height: 1px;
  background: linear-gradient(90deg, transparent, rgba(37,99,235, 0.42), transparent);
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.22s ease;
}

.book-card:hover {
  border-color: rgba(37,99,235, 0.50);
  box-shadow: 0 24px 68px rgba(0, 0, 0, 0.48), 0 12px 32px rgba(30,64,175, 0.24);
}

.book-card:hover::after {
  opacity: 1;
}

.card-cover,
.rename-cover-preview {
  background:
    linear-gradient(145deg, rgba(10,25,54, 0.92), rgba(6,14,31, 0.94)),
    linear-gradient(45deg, rgba(96,165,250, 0.16), rgba(29,78,216, 0.12));
  border-color: rgba(147,197,253, 0.16);
}

.card-name {
  min-height: 52px;
  padding: 12px 12px 6px;
}

.card-meta-row {
  min-height: 42px;
  margin-top: auto;
  align-items: center;
  padding: 4px 12px 14px;
}

.card-actions {
  display: none !important;
}

.card-analyze-btn {
  display: none !important;
}

.card-del-btn,
.card-edit-btn {
  position: absolute !important;
  top: 10px !important;
  bottom: auto !important;
  width: 30px;
  height: 30px;
  z-index: 6;
  opacity: 0;
  pointer-events: none;
  transform: scale(0.92);
  background: rgba(6,14,31, 0.62);
  border: 1px solid rgba(233, 213, 255, 0.22);
  color: #eaf2ff;
  box-shadow: 0 10px 26px rgba(0, 0, 0, 0.32);
}

.card-del-btn {
  right: 10px !important;
  left: auto !important;
}

.card-edit-btn {
  left: 10px !important;
  right: auto !important;
}

.book-card:hover .card-del-btn,
.book-card:hover .card-edit-btn,
.book-card:focus-within .card-del-btn,
.book-card:focus-within .card-edit-btn {
  opacity: 1 !important;
  pointer-events: auto !important;
  transform: scale(1) !important;
}

.card-del-btn:hover {
  background: rgba(251, 113, 133, 0.20);
  border-color: rgba(251, 113, 133, 0.36);
  color: var(--red);
}

.card-edit-btn:hover {
  background: rgba(29,78,216, 0.24);
  border-color: rgba(37,99,235, 0.42);
  color: #fff;
  transform: scale(1.06) !important;
}

.sync-badge-local {
  color: #f6c567;
}

.scan-badge {
  border-color: rgba(255, 255, 255, 0.08);
}

.scan-badge.done {
  background: rgba(29,78,216, 0.16);
  color: var(--green);
}

.scan-badge.pending {
  background: rgba(29,78,216, 0.16);
  color: #bfdbfe;
}

.scan-badge.failed {
  background: rgba(251, 113, 133, 0.13);
}

.library-bottom-menu {
  background: linear-gradient(135deg, rgba(18,31,58, 0.86), rgba(8, 5, 19, 0.74));
  border-color: rgba(96,165,250, 0.20);
  box-shadow: 0 18px 54px rgba(0, 0, 0, 0.50), 0 0 34px rgba(30,64,175, 0.14), inset 0 1px 0 rgba(255, 255, 255, 0.09);
}

.bottom-grade-cluster {
  background: linear-gradient(135deg, rgba(15,42,95, 0.82), rgba(3,7,18, 0.72));
  border-color: rgba(191,219,254, 0.16);
}

.bottom-grade-options {
  background: rgba(5,10,24, 0.46);
  border-color: rgba(147,197,253, 0.12);
}

.grade-btn:hover,
.bottom-menu-item:hover,
.upload-nav-btn:hover,
.history-nav-btn:hover,
.dm-nav-btn:hover,
.read-btn:hover,
.btn-ghost:hover,
.new-chat-btn:hover,
.rename-cover-btn:hover {
  background: rgba(29,78,216, 0.18);
  border-color: rgba(37,99,235, 0.34);
  color: #fff;
}

.grade-btn.active,
.btn-primary,
#analyzeBtn,
.sync-btn-empty {
  background: var(--material-stained);
  color: #fff;
  box-shadow: var(--shadow-glow);
}

.lib-footer {
  background:
    radial-gradient(circle at 18% 0%, rgba(37,99,235,0.14), transparent 38%),
    radial-gradient(circle at 82% 55%, rgba(96,165,250,0.08), transparent 36%),
    linear-gradient(180deg, rgba(6,8,18,0.98), rgba(10,12,24,0.98));
  border-top-color: rgba(147,197,253, 0.18);
}

.chat-history-backdrop,
.upload-overlay,
.del-overlay,
.cfg-overlay,
.auth-overlay,
.rename-overlay,
.add-overlay,
.dm-overlay,
.profile-settings-overlay,
.admin-tools-overlay,
.task-overlay {
  background: rgba(6,14,31, 0.66);
}

.chat-sidebar,
.dm-panel,
.del-panel,
.cfg-panel,
.auth-panel,
.rename-panel,
.add-panel,
.profile-settings-panel,
.admin-tools-panel,
.upload-card,
.task-panel,
.toast {
  background: linear-gradient(145deg, rgba(18,31,58, 0.90), rgba(6,14,31, 0.86));
  border-color: rgba(147,197,253, 0.18);
}

#analysisScreen.active {
  background: rgba(3,7,18, 0.98);
}

.analysis-left {
  background: linear-gradient(180deg, rgba(10,25,54, 0.96), rgba(10, 7, 22, 0.96));
}

.analysis-right {
  background:
    linear-gradient(135deg, rgba(10,25,54, 0.94), rgba(6,14,31, 0.92)),
    rgba(9, 7, 20, 0.96);
  border-color: rgba(147,197,253, 0.20);
}

.chat-input-bar {
  background: rgba(6,14,31, 0.74);
  border-top-color: rgba(147,197,253, 0.12);
}

.chat-bubble,
#analyzeBtn {
  background: var(--material-stained);
}

.btn-primary:hover,
#analyzeBtn:hover {
  background: linear-gradient(135deg, rgba(29,78,216, 1), rgba(6,26,58, 0.96));
  box-shadow: var(--shadow-glow);
}

.chat-text {
  background: rgba(10,25,54, 0.76);
  border-color: rgba(147,197,253, 0.16);
}

.chat-md-quote,
.cfg-desc,
.add-desc {
  background: rgba(29,78,216, 0.12);
  border-color: rgba(37,99,235, 0.22);
}

.chat-md-code,
.chat-md-pre,
.chat-md-table-wrap,
.chat-md-math.display,
.chat-md-image-fallback {
  background: rgba(5,10,24, 0.50);
  border-color: rgba(147,197,253, 0.14);
}

.chat-md-table th {
  background: rgba(29,78,216, 0.14);
}

.response-banner {
  background: rgba(17, 45, 43, 0.82);
  border-color: rgba(147,197,253, 0.28);
}

.pdf-viewer-overlay {
  background: rgba(6,14,31, 0.96);
}

.pdf-viewer-body,
#pdfCanvasWrap {
  background: linear-gradient(135deg, rgba(6,14,31, 0.96), rgba(10,25,54, 0.94));
}

#pdfLoadingOverlay {
  background: rgba(6,14,31, 0.76);
}

.pdf-load-ring-bg {
  stroke: rgba(147,197,253, 0.12);
}

.pdf-load-ring-fg {
  stroke: var(--accent);
}

.upload-bar-track,
.upload-overall-track,
.task-progress-track {
  background: rgba(255, 255, 255, 0.08);
}

.upload-bar-fill,
.task-progress-fill,
.toast.info .toast-progress {
  background: linear-gradient(90deg, #071a3d, #1d4ed8, #bfdbfe);
}

@media (max-width: 600px) {
  .book-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
  }

  .book-card {
    min-height: 0;
  }

  .card-name {
    min-height: 46px;
    padding: 10px 10px 5px;
    font-size: 12px;
  }

  .card-meta-row {
    min-height: 38px;
    gap: 6px;
    padding: 2px 10px 12px;
  }

  .book-card::after {
    left: 10px;
    right: 10px;
    bottom: 8px;
  }
}

/* Dark blue motion refresh ---------------------------------------------- */
:root {
  --motion-smooth: cubic-bezier(0.22, 1, 0.36, 1);
  --motion-spring: cubic-bezier(0.16, 1, 0.3, 1);
}

body::after {
  animation: ambientGrid 18s ease-in-out infinite alternate;
}

.screen {
  transition:
    opacity 0.48s var(--motion-smooth),
    transform 0.48s var(--motion-smooth);
}

.navbar,
.grade-strip,
.book-grid-wrap,
.lib-footer {
  animation: surfaceFadeIn 0.58s var(--motion-smooth) both;
}

.nav-logo-shell::before {
  animation: logoBreath 3.8s ease-in-out infinite;
}

.status-dot::after {
  animation: statusRingPulse 2.25s ease-in-out infinite;
}

.analysis-status::before {
  animation: statusCorePulse 2.25s ease-in-out infinite;
}

.search-wrap::before {
  background-size: 240% 240%;
  animation: neonShift 6.5s ease-in-out infinite;
}

.book-card.visible {
  animation: cardMaterialize 0.54s var(--motion-smooth) both;
}

.empty-state {
  animation: emptyFadeIn 0.58s var(--motion-smooth) both;
}

.empty-books-img {
  animation: floatBooks 5.6s ease-in-out infinite alternate;
}

.chat-history-item {
  animation: chatHistoryIn 0.42s var(--motion-smooth) forwards;
}

.chat-msg {
  animation: chatMsgIn 0.30s var(--motion-smooth) forwards;
}

.response-banner.active {
  animation: responseBannerIn 2.35s var(--motion-smooth) forwards;
}

.del-panel,
.cfg-panel,
.add-panel {
  animation: panelIn 0.34s var(--motion-spring);
}

.toast {
  animation: toastIn 0.38s var(--motion-spring) forwards;
}

.toast.leaving {
  animation: toastOut 0.24s var(--motion-smooth) forwards;
}

.typing-spinner,
.task-spinner,
.scan-dot.spin,
.sync-btn.syncing .sync-icon {
  animation-duration: 1.05s;
}

.pdf-load-indeterminate .pdf-load-ring-fg,
.pdf-load-indeterminate .pdf-load-ring svg {
  animation-duration: 1.65s;
}

@keyframes ambientGrid {
  from { background-position: 0 0, 0 0; opacity: 0.30; }
  to { background-position: 72px 36px, 36px 72px; opacity: 0.46; }
}

@keyframes surfaceFadeIn {
  from { opacity: 0; filter: saturate(0.82) brightness(0.88); }
  to { opacity: 1; filter: none; }
}

@keyframes logoBreath {
  0%, 100% { transform: scale(0.98); opacity: 0.82; filter: blur(8px); }
  50% { transform: scale(1.06); opacity: 1; filter: blur(9px); }
}

@keyframes statusRingPulse {
  0%, 100% { opacity: 0.18; transform: scale(1); }
  50% { opacity: 0.44; transform: scale(1.42); }
}

@keyframes statusCorePulse {
  0%, 100% { opacity: 0.82; transform: scale(1); }
  50% { opacity: 1; transform: scale(1.14); }
}

@keyframes cardMaterialize {
  from { filter: saturate(0.78) brightness(0.90); }
  to { filter: none; }
}

@keyframes neonShift {
  0%, 100% { background-position: 0% 50%; }
  50% { background-position: 100% 50%; }
}

@keyframes emptyFadeIn {
  from { opacity: 0; transform: translateY(18px) scale(0.98); filter: saturate(0.85); }
  to { opacity: 1; transform: none; filter: none; }
}

@keyframes floatBooks {
  0% { transform: translateY(0) rotate(-0.8deg); filter: drop-shadow(0 6px 18px rgba(30,64,175, 0.20)); }
  100% { transform: translateY(-9px) rotate(0.8deg); filter: drop-shadow(0 14px 26px rgba(96,165,250, 0.16)); }
}

@keyframes chatHistoryIn {
  from { opacity: 0; transform: translateY(10px) scale(0.985); filter: saturate(0.86); }
  to { opacity: 1; transform: translateY(0) scale(1); filter: none; }
}

@keyframes chatMsgIn {
  from { opacity: 0; transform: translateY(8px) scale(0.99); filter: saturate(0.88); }
  to { opacity: 1; transform: translateY(0) scale(1); filter: none; }
}

@keyframes responseBannerIn {
  0% { transform: translate(-50%, -24px) scale(0.94); opacity: 0; filter: saturate(0.86); }
  14%, 74% { transform: translate(-50%, 0) scale(1); opacity: 1; filter: none; }
  100% { transform: translate(-50%, -10px) scale(0.96); opacity: 0; filter: saturate(0.92); }
}

@keyframes panelIn {
  from { transform: translateY(16px) scale(0.96); opacity: 0; filter: saturate(0.86); }
  to { transform: translateY(0) scale(1); opacity: 1; filter: none; }
}

@keyframes toastIn {
  from { transform: translateX(36px) translateY(-8px) scale(0.98); opacity: 0; filter: saturate(0.86); }
  to { transform: translateX(0) translateY(0) scale(1); opacity: 1; filter: none; }
}

@keyframes toastOut {
  to { transform: translateX(44px) translateY(-6px) scale(0.98); opacity: 0; filter: saturate(0.86); }
}

.chat-msg {
  gap: 5px;
}

.message-actions {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  opacity: 0.62;
  transform: translateY(0) scale(0.98);
  transition:
    opacity 0.24s var(--motion-smooth),
    transform 0.32s var(--motion-spring);
  pointer-events: auto;
}

.chat-msg:hover .message-actions,
.chat-msg:focus-within .message-actions {
  opacity: 1;
  transform: translateY(0) scale(1);
  pointer-events: auto;
}

.message-action-btn {
  width: 28px;
  height: 28px;
  border: 1px solid rgba(147,197,253, 0.18);
  border-radius: 10px;
  display: inline-grid;
  place-items: center;
  color: var(--text-secondary);
  background: rgba(20, 14, 36, 0.64);
  box-shadow: 0 8px 20px rgba(0, 0, 0, 0.20), inset 0 1px 0 rgba(255, 255, 255, 0.07);
  backdrop-filter: blur(18px) saturate(1.35);
  -webkit-backdrop-filter: blur(18px) saturate(1.35);
  cursor: pointer;
  transition:
    transform 0.28s var(--motion-spring),
    color 0.24s var(--motion-smooth),
    border-color 0.24s var(--motion-smooth),
    background 0.24s var(--motion-smooth);
}

.message-action-btn svg {
  width: 14px;
  height: 14px;
}

.message-action-btn:hover {
  transform: translateY(-2px) scale(1.04);
  color: var(--text-primary);
  border-color: rgba(96,165,250, 0.38);
  background: rgba(15,42,95, 0.46);
}

.chat-input-wrap.editing {
  border-color: rgba(147,197,253, 0.42);
  box-shadow: 0 0 0 1px rgba(147,197,253, 0.16), 0 0 28px rgba(29,78,216, 0.16);
}

#analyzeBtn.loading {
  opacity: 1;
  pointer-events: auto;
}

#analyzeBtn.stop-mode {
  background: linear-gradient(135deg, rgba(239, 68, 68, 0.92), rgba(127, 29, 29, 0.96));
  border-color: rgba(248, 113, 113, 0.34);
}

#analyzeBtn.edit-mode {
  background: linear-gradient(135deg, rgba(15,42,95, 0.96), rgba(30,58,138, 0.94));
  border-color: rgba(147,197,253, 0.30);
}

.library-bottom-menu {
  animation: dockLiquidIn 0.72s var(--motion-spring) both;
  transition:
    transform 0.42s var(--motion-spring),
    opacity 0.26s var(--motion-smooth),
    filter 0.30s var(--motion-smooth),
    border-color 0.30s var(--motion-smooth),
    box-shadow 0.30s var(--motion-smooth);
}

body:not(.app-ready) .library-bottom-menu,
body.account-auth-visible .library-bottom-menu,
body.analysis-mode .library-bottom-menu {
  opacity: 0;
  pointer-events: none;
  filter: blur(2px) saturate(0.8);
  transform: translateX(-50%) translateY(18px) scale(0.98) !important;
}

.library-bottom-menu:hover {
  transform: translateX(-50%) translateY(-2px);
  box-shadow: 0 22px 58px rgba(0, 0, 0, 0.34), 0 0 40px rgba(15,42,95, 0.22), inset 0 1px 0 rgba(255,255,255,0.12);
}

.bottom-menu-item,
.bottom-grade-cluster,
.bottom-grade-btn.grade-btn {
  transition:
    transform 0.36s var(--motion-spring),
    background 0.28s var(--motion-smooth),
    border-color 0.28s var(--motion-smooth),
    color 0.24s var(--motion-smooth),
    filter 0.32s var(--motion-spring);
}

.bottom-menu-item:hover,
.bottom-grade-cluster:hover {
  filter: saturate(1.16) brightness(1.06);
}

.bottom-grade-btn.grade-btn.active {
  animation: liquidSelect 0.42s var(--motion-spring) both;
}

@keyframes dockLiquidIn {
  from { opacity: 0; transform: translateX(-50%) translateY(22px) scale(0.96); filter: blur(8px) saturate(0.76); }
  62% { opacity: 1; transform: translateX(-50%) translateY(-3px) scale(1.012); filter: blur(0) saturate(1.08); }
  to { opacity: 1; transform: translateX(-50%) translateY(0) scale(1); filter: none; }
}

@keyframes liquidSelect {
  0% { transform: scale(0.96); filter: saturate(0.9); }
  58% { transform: scale(1.09); filter: saturate(1.22) brightness(1.08); }
  100% { transform: scale(1.04); filter: none; }
}

@keyframes spin { to { transform: rotate(360deg); } }
@keyframes spinCW { to { transform: rotate(360deg); } }
@keyframes pdfRingSpin { to { transform: rotate(360deg); } }

/* Mobile ergonomics -------------------------------------------------------- */
@media (hover: none) and (pointer: coarse) {
  .book-card:hover,
  .bottom-menu-item:hover,
  .read-btn:hover,
  .btn-ghost:hover,
  .account-menu-btn:hover {
    transform: none;
  }

  .card-del-btn,
  .card-edit-btn,
  .book-card:focus-within .card-del-btn,
  .book-card:focus-within .card-edit-btn {
    opacity: 1;
    transform: scale(1);
  }
}

@media (max-width: 720px) {
  html,
  body {
    width: 100%;
    min-width: 0;
    overflow-x: hidden;
  }

  .screen {
    min-height: 100svh;
    max-width: 100vw;
  }

  .account-auth-screen {
    min-height: 100svh;
    padding: max(12px, env(safe-area-inset-top, 0px)) 12px max(14px, env(safe-area-inset-bottom, 0px));
    align-content: center;
  }

  .account-auth-screen::after {
    opacity: 0.48;
  }

  .auth-panel-card {
    width: min(390px, 100%);
    max-height: calc(100svh - 28px);
    overflow-y: auto;
    padding: 16px;
    border-radius: 22px;
  }

  .auth-tabs {
    margin-left: -16px;
    margin-right: -16px;
  }

  .account-input-shell,
  .account-submit,
  .turnstile-wrap {
    min-height: 48px;
  }

  .navbar {
    grid-template-columns: auto 1fr auto;
    min-height: 58px;
    margin: max(8px, env(safe-area-inset-top, 0px)) 10px 0;
    padding: 8px 10px;
    gap: 8px;
    border-radius: 22px;
    position: sticky;
    top: max(8px, env(safe-area-inset-top, 0px));
    z-index: 24;
  }

  .nav-left {
    display: none !important;
  }

  .nav-center {
    justify-self: start;
    min-width: 0;
  }

  .nav-logo-shell {
    width: 48px;
    height: 42px;
  }

  .nav-logo {
    width: 40px;
    height: 40px;
  }

  .nav-right {
    justify-self: end;
    min-width: 0;
    gap: 7px;
  }

  .header-dm-btn {
    width: 42px;
    min-width: 42px;
    height: 42px;
    min-height: 42px;
    padding: 0;
    justify-content: center;
  }

  .header-dm-btn svg {
    width: 18px;
    height: 18px;
  }

  .header-dm-label {
    display: none;
  }

  .header-dm-btn .dm-header-badge {
    position: absolute;
    top: -4px;
    right: -4px;
    min-width: 18px;
    height: 18px;
    font-size: 10px;
    margin-left: 0;
  }

  .account-chip {
    max-width: min(58vw, 236px);
    min-height: 42px;
    padding: 4px 9px 4px 5px;
    border-radius: 999px;
  }

  .account-avatar {
    width: 32px;
    height: 32px;
    font-size: 13px;
  }

  .account-chip-name {
    min-width: 0;
    max-width: 128px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 12.5px;
  }

  .account-chip-presence {
    height: 22px;
    padding: 0 7px;
    font-size: 10.5px;
  }

  .account-chip-role {
    display: none !important;
  }

  .status-pill {
    display: none !important;
  }

  .account-menu {
    position: fixed;
    top: calc(62px + env(safe-area-inset-top, 0px));
    left: 10px;
    right: 10px;
    width: auto;
    max-height: calc(100svh - 86px - env(safe-area-inset-bottom, 0px));
    overflow-y: auto;
    border-radius: 22px;
    z-index: 370;
  }

  .grade-strip {
    padding: 10px 12px 6px;
  }

  .search-wrap {
    width: 100%;
    max-width: none;
  }

  .search-input {
    min-height: 44px;
    font-size: 15px;
  }

  .book-grid-wrap {
    padding: 12px 10px calc(112px + env(safe-area-inset-bottom, 0px));
    overscroll-behavior: contain;
  }

  .book-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10px;
  }

  .book-card {
    border-radius: 8px;
    overflow: hidden;
  }

  .card-cover {
    border-radius: 8px 8px 0 0;
  }

  .card-name {
    min-height: 42px;
    padding: 9px 9px 4px;
    font-size: 11.8px;
    line-height: 1.28;
    -webkit-line-clamp: 2;
  }

  .card-meta-row {
    min-height: 36px;
    padding: 0 9px 10px;
    gap: 5px;
  }

  .sync-badge,
  .scan-badge {
    max-width: 100%;
    font-size: 10.5px;
  }

  .card-actions {
    top: 7px;
    right: 7px;
    gap: 5px;
  }

  .card-del-btn,
  .card-edit-btn {
    width: 30px;
    height: 30px;
    border-radius: 999px;
    background: rgba(6,14,31, 0.66);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
  }

  .library-bottom-menu {
    left: 8px;
    right: 8px;
    bottom: max(8px, env(safe-area-inset-bottom, 0px));
    width: auto;
    max-width: none;
    min-height: 58px;
    padding: 6px;
    gap: 5px;
    border-radius: 22px;
    transform: none !important;
  }

  body:not(.app-ready) .library-bottom-menu,
  body.account-auth-visible .library-bottom-menu,
  body.analysis-mode .library-bottom-menu {
    transform: translateY(18px) scale(0.98) !important;
  }

  .bottom-menu-item {
    flex: 1 1 0;
    min-width: 0;
    height: 46px;
    padding: 4px 5px;
  }

  .bottom-menu-item svg {
    width: 19px;
    height: 19px;
  }

  .bottom-menu-item span {
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    font-size: 11px;
  }

  .bottom-grade-cluster {
    flex: 1.28 1 0;
    min-width: 120px;
    height: 46px;
    padding: 4px 5px;
  }

  .bottom-grade-options {
    width: 100%;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 3px;
  }

  .bottom-grade-btn.grade-btn {
    width: auto;
    min-width: 0;
    height: 32px;
    font-size: 12px;
  }

  #analysisScreen {
    min-height: 100svh;
    padding: 8px;
    gap: 8px;
    overflow: hidden;
  }

  #analysisScreen.active,
  #libraryScreen.active {
    transform: none !important;
  }

  .analysis-left {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    width: 100%;
    min-width: 0;
    max-height: none;
    padding: 10px;
    gap: 9px;
    border-radius: 20px;
    overflow: hidden;
  }

  .selected-book-cover {
    order: 1;
    flex: 0 0 54px;
    width: 54px;
    min-width: 54px;
    border-radius: 12px;
    font-size: 24px;
  }

  .selected-book-info {
    order: 1;
    flex: 1 1 calc(100% - 70px);
    min-width: 0;
    gap: 2px;
  }

  .book-label {
    font-size: 9px;
  }

  .book-title {
    font-size: 12.5px;
    line-height: 1.25;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .analysis-status {
    order: 2;
    flex: 1 1 calc(50% - 5px);
    width: auto;
    min-height: 30px;
    padding: 6px 8px;
    font-size: 11px;
    border-radius: 12px;
  }

  .read-btn,
  .analysis-left .btn {
    order: 3;
    flex: 1 1 calc(50% - 5px);
    min-height: 36px;
    padding: 7px 9px;
    font-size: 11.5px;
    border-radius: 999px;
  }

  .read-btn {
    order: 2;
  }

  .analysis-right {
    min-height: 0;
    border-radius: 22px;
  }

  .chat-flow {
    padding: 14px 10px 10px;
    gap: 10px;
  }

  .chat-msg {
    gap: 4px;
  }

  .chat-bubble,
  .chat-text {
    max-width: 100%;
    border-radius: 18px;
    font-size: 13.5px;
  }

  .message-actions {
    opacity: 1;
  }

  .message-action-btn {
    width: 32px;
    height: 32px;
    border-radius: 12px;
  }

  .chat-input-bar {
    padding: 8px 8px max(8px, env(safe-area-inset-bottom, 0px));
    gap: 8px;
    border-radius: 0 0 22px 22px;
  }

  .chat-input-wrap {
    min-height: 46px;
    border-radius: 18px;
  }

  #promptInput {
    min-height: 58px;
    max-height: 116px;
    padding: 10px 11px;
    font-size: 15px;
  }

  #analyzeBtn {
    min-width: 0;
    height: 46px;
    border-radius: 999px;
    padding: 0 14px;
  }

  .analyze-btn-label {
    font-size: 12.5px;
  }

  .chat-sidebar {
    width: min(360px, 92vw);
    padding: max(16px, env(safe-area-inset-top, 0px)) 12px max(16px, env(safe-area-inset-bottom, 0px));
    border-radius: 0 24px 24px 0;
    z-index: 90;
  }

  .chat-sidebar:not(.collapsed) + .chat-history-backdrop,
  .chat-history-backdrop.active {
    opacity: 1;
    pointer-events: auto;
  }

  .profile-settings-overlay,
  .admin-tools-overlay,
  .email-code-overlay,
  .avatar-crop-overlay,
  .password-change-overlay,
  .verify-required-overlay,
  .cfg-overlay,
  .add-overlay,
  .rename-overlay,
  .del-overlay,
  .upload-overlay,
  .task-overlay {
    align-items: stretch;
    padding: max(10px, env(safe-area-inset-top, 0px)) 10px max(10px, env(safe-area-inset-bottom, 0px));
  }

  .profile-settings-panel,
  .admin-tools-panel,
  .email-code-panel,
  .avatar-crop-panel,
  .password-change-panel,
  .verify-required-panel,
  .cfg-panel,
  .add-panel,
  .rename-panel,
  .del-panel,
  .upload-card,
  .task-panel {
    width: 100%;
    max-height: calc(100svh - 20px - env(safe-area-inset-top, 0px) - env(safe-area-inset-bottom, 0px));
    overflow-y: auto;
    border-radius: 24px;
  }

  .profile-settings-panel,
  .admin-tools-panel,
  .email-code-panel,
  .avatar-crop-panel,
  .password-change-panel,
  .verify-required-panel {
    padding: 18px;
  }

  .profile-settings-head,
  .admin-tools-head {
    position: sticky;
    top: -18px;
    z-index: 2;
    padding-top: 2px;
    padding-bottom: 10px;
    background: linear-gradient(180deg, rgba(18,31,58, 0.98), rgba(18,31,58, 0.86), transparent);
  }

  .profile-settings-title,
  .admin-tools-title,
  .email-code-title {
    font-size: 20px;
  }

  .profile-settings-grid {
    grid-template-columns: 1fr;
    gap: 14px;
  }

  .profile-photo-card {
    grid-template-columns: 1fr 1fr;
    align-items: center;
    justify-items: stretch;
  }

  .profile-photo-preview-wrap {
    grid-column: 1 / -1;
    justify-self: center;
    width: 76px;
    height: 76px;
  }

  .profile-photo-preview {
    border-radius: 22px;
    font-size: 26px;
  }

  .profile-presence-popover {
    grid-column: 1 / -1;
  }

  .settings-email-control {
    grid-template-columns: 1fr;
  }

  .email-verify-btn,
  .settings-input,
  .account-menu-btn,
  .btn {
    min-height: 44px;
  }

  .email-code-grid {
    gap: 6px;
  }

  .email-code-cell {
    min-height: 44px;
    border-radius: 14px;
    font-size: 21px;
  }

  .email-code-actions,
  .settings-actions {
    display: grid;
    grid-template-columns: 1fr;
  }

  .admin-stats,
  .admin-sensitive-grid {
    grid-template-columns: 1fr;
  }

  .admin-account-row {
    grid-template-columns: 1fr;
  }

  .admin-account-flags {
    justify-content: flex-start;
  }

  .toast-container,
  #toastContainer {
    top: calc(10px + env(safe-area-inset-top, 0px));
    right: 10px;
    left: 10px;
    bottom: auto;
    width: auto;
    align-items: stretch;
  }

  .toast {
    width: 100%;
    max-width: none;
  }
}

@media (max-width: 720px) {
  .dm-overlay {
    padding: 0;
  }

  .dm-panel {
    width: 100vw;
    height: 100dvh;
    border-radius: 0;
    grid-template-columns: 1fr;
  }

  .dm-people {
    border-right: 0;
  }

  .dm-chat {
    display: none;
  }

  .dm-overlay.chat-open .dm-people {
    display: none;
  }

  .dm-overlay.chat-open .dm-chat {
    display: grid;
  }

  .dm-back {
    display: inline-flex;
  }

  .dm-message {
    max-width: 86%;
  }

  .dm-compose-row {
    grid-template-columns: auto minmax(0, 1fr) auto;
  }
}

@media (max-width: 420px) {
  .account-chip {
    max-width: 52vw;
  }

  .account-chip-name {
    max-width: 92px;
  }

  .book-grid {
    gap: 8px;
  }

  .card-name {
    font-size: 11.2px;
  }

  .bottom-menu-item span {
    font-size: 10.5px;
  }

  .bottom-grade-cluster {
    min-width: 104px;
  }

  .email-code-cell {
    min-height: 40px;
    font-size: 19px;
  }
}

@media (max-width: 360px) {
  .book-grid {
    grid-template-columns: 1fr;
  }

  .book-card {
    max-width: 250px;
    justify-self: center;
    width: 100%;
  }

  .bottom-menu-item span {
    display: none;
  }

  .bottom-menu-item {
    height: 44px;
  }
}

@keyframes authShake {
  0%, 100% { transform: translateX(0); }
  20%, 60% { transform: translateX(-6px); }
  40%, 80% { transform: translateX(6px); }
}

/* Dark blue liquid glass final polish ------------------------------------- */
:root {
  --bg-deep: #020617;
  --bg-surface: rgba(7, 20, 42, 0.88);
  --bg-card: rgba(10, 25, 54, 0.86);
  --bg-glass: rgba(8, 18, 38, 0.62);
  --bg-main: rgba(5, 14, 31, 0.82);
  --bg-base: #020617;
  --border: rgba(96, 165, 250, 0.18);
  --accent: #2563eb;
  --accent-glow: #1d4ed8;
  --accent-soft: rgba(37, 99, 235, 0.16);
  --green: #60a5fa;
  --text-primary: #eef5ff;
  --text-secondary: #a8b7d1;
  --text-muted: #6f7f9a;
  --material-sheet: linear-gradient(180deg, rgba(10, 25, 54, 0.94), rgba(3, 7, 18, 0.94));
  --material-glass: linear-gradient(135deg, rgba(15, 42, 95, 0.70), rgba(3, 7, 18, 0.58));
  --material-clear: linear-gradient(135deg, rgba(30, 64, 175, 0.24), rgba(3, 7, 18, 0.34));
  --material-stained: linear-gradient(135deg, rgba(7, 26, 61, 0.98), rgba(29, 78, 216, 0.96), rgba(96, 165, 250, 0.84));
  --glass-border: rgba(147, 197, 253, 0.20);
  --glass-edge: rgba(96, 165, 250, 0.16);
  --shadow-card: 0 18px 55px rgba(0, 0, 0, 0.44), inset 0 1px 0 rgba(255, 255, 255, 0.09);
  --shadow-glow: 0 18px 46px rgba(29, 78, 216, 0.26), inset 0 1px 0 rgba(255, 255, 255, 0.13);
  --shadow-float: 0 28px 82px rgba(0, 0, 0, 0.52), inset 0 1px 0 rgba(255, 255, 255, 0.10);
}

html,
body {
  background: #020617 !important;
  color: var(--text-primary);
}

body::before,
.account-auth-screen {
  background:
    radial-gradient(circle at 18% 0%, rgba(37, 99, 235, 0.18), transparent 32%),
    radial-gradient(circle at 86% 18%, rgba(96, 165, 250, 0.11), transparent 34%),
    linear-gradient(135deg, #020617 0%, #061a3a 48%, #020617 100%) !important;
}

body::after,
.account-auth-screen::before {
  background:
    repeating-linear-gradient(90deg, rgba(96, 165, 250, 0.035) 0 1px, transparent 1px 42px),
    repeating-linear-gradient(0deg, rgba(96, 165, 250, 0.024) 0 1px, transparent 1px 42px) !important;
}

.navbar,
.grade-bar,
.status-pill,
.library-bottom-menu,
.bottom-grade-cluster,
.account-panel-card,
.auth-panel-card,
.account-menu,
.dm-panel,
.dm-thread,
.dm-input,
.dm-pending,
.dm-bubble,
.profile-settings-panel,
.admin-tools-panel,
.email-code-panel,
.avatar-crop-panel,
.password-change-panel,
.verify-required-panel,
.chat-sidebar,
.analysis-left,
.analysis-right,
.chat-input-bar,
.chat-input-wrap,
.book-card,
.chat-history-item,
.toast,
.task-panel,
.del-panel,
.cfg-panel,
.auth-panel,
.rename-panel,
.add-panel,
.upload-card {
  background: var(--material-glass) !important;
  border-color: var(--glass-edge) !important;
  box-shadow: var(--shadow-card) !important;
}

.book-card,
.profile-settings-panel,
.admin-tools-panel,
.email-code-panel,
.avatar-crop-panel,
.password-change-panel,
.verify-required-panel,
.auth-panel-card,
.account-menu {
  background: var(--material-sheet) !important;
}

.nav-logo-shell {
  isolation: auto;
}

.nav-logo-shell::before,
.nav-logo-shell::after {
  display: none !important;
}

.nav-logo {
  filter: drop-shadow(0 0 12px rgba(96, 165, 250, 0.38)) !important;
}

.grade-btn:hover,
.bottom-menu-item:hover,
.upload-nav-btn:hover,
.history-nav-btn:hover,
.dm-nav-btn:hover,
.sync-btn:hover:not(:disabled),
.scan-nav-btn:hover:not(:disabled),
.read-btn:hover,
.btn-ghost:hover,
.new-chat-btn:hover,
.rename-cover-btn:hover,
.account-menu-btn:hover,
.chip:hover,
.chat-history-item:hover,
.chat-history-item.active,
.card-edit-btn:hover,
.pdf-ctrl-btn:hover,
.cfg-close-btn:hover {
  background: linear-gradient(135deg, rgba(29, 78, 216, 0.24), rgba(7, 26, 61, 0.58)) !important;
  border-color: rgba(96, 165, 250, 0.34) !important;
  color: #eef5ff !important;
  box-shadow: 0 14px 36px rgba(0, 0, 0, 0.26), 0 0 24px rgba(37, 99, 235, 0.14) !important;
}

.btn-primary,
#analyzeBtn,
.grade-btn.active,
.account-submit,
.sync-btn-empty {
  background: var(--material-stained) !important;
  color: #ffffff !important;
  border-color: rgba(147, 197, 253, 0.30) !important;
}

.remember-row input:checked + .remember-check,
.settings-email-state.verified,
.account-verify-state.verified,
.admin-mini-badge.good,
.scan-badge.done,
.toast.success .toast-icon {
  color: #93c5fd !important;
}

.remember-row input:checked + .remember-check,
.admin-mini-badge.good,
.scan-badge.done {
  background: rgba(37, 99, 235, 0.16) !important;
  border-color: rgba(96, 165, 250, 0.28) !important;
}

.toast.success .toast-progress,
.net-indicator.online .net-dot,
.net-indicator.wifi .net-dot,
.net-indicator.ethernet .net-dot {
  background: #60a5fa !important;
}

.response-banner {
  background: linear-gradient(135deg, rgba(15, 42, 95, 0.90), rgba(3, 7, 18, 0.86)) !important;
  border-color: rgba(96, 165, 250, 0.28) !important;
  color: #93c5fd !important;
}

.chat-text,
.chat-md-code,
.chat-md-pre,
.chat-md-table-wrap,
.chat-md-math.display,
.chat-md-image-fallback {
  background: rgba(5, 14, 31, 0.62) !important;
  border-color: rgba(147, 197, 253, 0.14) !important;
}

.settings-input,
.account-input-shell,
.search-input,
.cfg-input,
.add-input,
.add-grade-select,
.auth-input,
.rename-input,
.pdf-page-input {
  background: rgba(3, 7, 18, 0.46) !important;
  border-color: rgba(147, 197, 253, 0.16) !important;
  color: var(--text-primary) !important;
}

@media (max-width: 720px) {
  .account-menu {
    right: 10px;
    top: 70px;
    width: min(340px, calc(100vw - 20px));
  }

  .profile-photo-card {
    grid-template-columns: 1fr;
  }

  .profile-photo-card .account-menu-btn {
    grid-column: auto;
  }

  .avatar-crop-canvas {
    width: min(280px, 100%);
    border-radius: 22px;
  }
}

@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation-duration: 0.001ms !important;
    animation-iteration-count: 1 !important;
    scroll-behavior: auto !important;
    transition-duration: 0.001ms !important;
  }
}
</style>
</head>
<body>

<div class="app-loading" id="appLoadingOverlay" aria-live="polite">
  <div class="loading-core">
    <div class="loading-orbit">
      <img src="{{ reylai_icon_src }}" class="loading-logo" alt="ReylAI">
    </div>
  </div>
</div>

<section class="account-auth-screen" id="accountAuthScreen" aria-label="ReylAI hesap girişi">
  <div class="auth-hero">
    <div class="auth-brand-row">
      <div class="auth-logo-box">
        <img src="{{ reylai_icon_src }}" alt="ReylAI">
      </div>
      <div>
        <div class="auth-brand-name">ReylAI</div>
        <div class="auth-brand-sub">MEB kitapları için kişisel AI alanı</div>
      </div>
    </div>
    <div class="auth-kicker" id="authModeKicker">Güvenli oturum</div>
    <h1 class="auth-hero-title" id="accountAuthTitle">ReylAI'ye hoş geldin.</h1>
    <p class="auth-hero-text" id="accountAuthSubtitle">Kitapların, sohbet geçmişin ve çalışma alanın hesabına bağlı şekilde açılır.</p>
    <div class="auth-benefits" aria-label="Hesap özellikleri">
      <div class="auth-benefit"><span class="auth-benefit-dot"></span><span>Cloudflare doğrulaması</span></div>
      <div class="auth-benefit"><span class="auth-benefit-dot"></span><span>Korunan şifreler</span></div>
      <div class="auth-benefit"><span class="auth-benefit-dot"></span><span>Cihazda oturum</span></div>
    </div>
  </div>
  <div class="auth-panel-card">
    <div class="auth-panel-top">
      <div class="auth-panel-label">Hesap</div>
      <h2 class="auth-panel-title" id="authPanelTitle">Giriş yap</h2>
      <p class="auth-panel-lead" id="authPanelLead">Kayıtlı e-posta ve şifrenle devam et.</p>
    </div>
    <div class="auth-tabs" role="tablist" aria-label="Hesap işlemleri">
      <button class="auth-tab active" id="loginTabBtn" type="button" onclick="setAccountAuthMode('login')">Giriş</button>
      <button class="auth-tab" id="signupTabBtn" type="button" onclick="setAccountAuthMode('signup')">Kayıt ol</button>
    </div>
    <form class="account-form" id="accountAuthForm" onsubmit="submitAccountAuth(event)">
      <div class="account-field account-field-optional" id="displayNameField">
        <label class="account-label" for="accountDisplayName">Görünen ad</label>
        <div class="account-input-shell">
          <span class="account-field-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21a8 8 0 0 0-16 0"/><circle cx="12" cy="7" r="4"/></svg>
          </span>
          <input class="account-input" id="accountDisplayName" type="text" autocomplete="name" maxlength="40" placeholder="Adın nasıl görünsün?">
        </div>
      </div>
      <div class="account-field">
        <label class="account-label" for="accountEmail">E-posta</label>
        <div class="account-input-shell">
          <span class="account-field-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/></svg>
          </span>
          <input class="account-input" id="accountEmail" type="email" autocomplete="email" maxlength="254" placeholder="eposta@example.com" required>
        </div>
      </div>
      <div class="account-field">
        <label class="account-label" for="accountPassword">Şifre</label>
        <div class="account-input-shell">
          <span class="account-field-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="10" width="16" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>
          </span>
          <input class="account-input" id="accountPassword" type="password" autocomplete="current-password" minlength="8" maxlength="128" placeholder="En az 8 karakter" required>
        </div>
      </div>
      <div class="auth-field-note" id="accountPasswordHint">Şifren en az 8 karakter olmalı ve güvenli biçimde korunur.</div>
      <button class="forgot-password-btn" id="forgotPasswordBtn" type="button" onclick="startForgotPassword()">Şifremi unuttum</button>
      <label class="remember-row">
        <input id="rememberDevice" type="checkbox" checked>
        <span class="remember-check" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>
        </span>
        <span>Bu cihazda oturumu hatırla</span>
      </label>
      <div class="turnstile-wrap">
        <div id="accountTurnstile"></div>
        <div class="turnstile-note" id="turnstileNote">Cloudflare doğrulaması hazırlanıyor...</div>
      </div>
      <div class="account-auth-error" id="accountAuthError"></div>
      <button class="account-submit" id="accountSubmitBtn" type="submit">Giriş yap</button>
      <div class="account-switch-note">
        <span id="accountSwitchText">Hesabın yok mu?</span>
        <button type="button" id="accountSwitchBtn" onclick="setAccountAuthMode('signup')">Kayıt ol</button>
      </div>
    </form>
  </div>
</section>

<!-- ── Library Screen ──────────────────────────────────────────────────────── -->
<div class="screen" id="libraryScreen">
  <input type="file" id="pdfFileInput" accept=".pdf" multiple>

  <!-- ── Upload Progress Overlay ─────────────────────────────── -->
  <div class="upload-overlay" id="uploadOverlay">
    <div class="upload-card">
      <div class="upload-header">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
        <span>Kitaplar Y&#252;kleniyor</span>
      </div>
      <div class="upload-counter" id="uploadCounter">0 / 0 dosya</div>
      <div class="upload-filename" id="uploadFilename">Haz&#305;rlan&#305;yor...</div>
      <div class="upload-bar-wrap">
        <div class="upload-bar-track">
          <div class="upload-bar-fill" id="uploadBarFill" style="width:0%"></div>
        </div>
        <span class="upload-pct" id="uploadPct">0%</span>
      </div>
      <div class="upload-meta">
        <span class="upload-speed" id="uploadSpeed">0 KB/s</span>
        <span class="upload-size" id="uploadSize"></span>
      </div>
      <div class="upload-overall-wrap">
        <div class="upload-overall-track">
          <div class="upload-overall-fill" id="uploadOverallFill" style="width:0%"></div>
        </div>
      </div>
      <div class="upload-overall-label" id="uploadOverallLabel">Genel ilerleme</div>
    </div>
  </div>
  <div class="chat-history-backdrop" id="chatHistoryBackdrop" onclick="closeChatSidebar()"></div>
  <aside class="chat-sidebar collapsed" id="chatSidebar" aria-label="Sohbet geçmişi">
    <div class="chat-sidebar-header">
      <div>
        <div class="chat-sidebar-label">Sohbet Geçmişi</div>
        <div class="chat-sidebar-title">Konuşmalar</div>
      </div>
      <button class="chat-sidebar-toggle" type="button" onclick="closeChatSidebar()" aria-label="Sohbet geçmişini gizle">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>
      </button>
    </div>
    <div class="chat-history-list" id="chatHistoryList"></div>
  </aside>
  <nav class="navbar">
    <div class="nav-left">
      <img src="{{ meb_logo_src }}" class="meb-logo" alt="MEB">
      <div class="tagline">Ders Kitaplar&#305; &#304;&#231;in AI Tool\u0027u</div>
    </div>
    <div class="nav-center">
      <div class="nav-logo-shell" aria-label="ReylAI">
        <img src="{{ reylai_icon_src }}" class="nav-logo" alt="ReylAI">
      </div>
    </div>
    <div class="nav-right">
      <button class="dm-nav-btn header-dm-btn" type="button" onclick="openDmOverlay()" aria-label="Mesajları aç">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.4 8.4 0 0 1-9 8.5 8.7 8.7 0 0 1-3.6-.78L3 21l1.78-5.2A8.28 8.28 0 0 1 4 12a8.5 8.5 0 1 1 17-.5Z"/><path d="M8 10h8"/><path d="M8 14h5"/></svg>
        <span class="header-dm-label">Mesajlar</span>
        <span class="dm-bottom-badge dm-header-badge" id="dmHeaderBadge"></span>
      </button>
      <button class="account-chip" id="accountChip" type="button" onclick="toggleAccountMenu(event)" aria-label="Hesap menüsünü aç" aria-expanded="false">
        <span class="account-avatar" id="accountAvatar">R</span>
        <span class="account-chip-name" id="accountChipName">Hesap</span>
        <span class="account-chip-role" id="accountChipRole">Admin</span>
      </button>
      <div class="status-pill" id="libStatus">
        <span class="status-dot"></span>
        <span id="libStatusText">Haz&#305;r</span>
      </div>
      <div class="account-menu" id="accountMenu">
        <div class="account-menu-head">
          <div class="account-menu-avatar-wrap">
            <div class="account-menu-avatar" id="accountMenuAvatar">R</div>
            <button class="profile-status-toggle" id="profileStatusToggle" type="button" onclick="toggleProfilePresencePicker(event)" aria-label="Durum değiştir" title="Durum değiştir">
              <span class="presence-mini-dot"></span>
            </button>
          </div>
          <div>
            <div class="account-menu-name" id="accountMenuName">Hesap</div>
            <div class="account-menu-email" id="accountMenuEmail"></div>
          </div>
        </div>
        <div class="profile-presence-popover account-menu-presence-popover" id="profilePresencePopover">
          <div class="presence-picker" id="presencePicker" aria-label="Durum seçimi">
            <button class="presence-btn" data-presence="online" type="button" onclick="setPresenceStatus('online')"><span class="presence-mini-dot"></span>Çevrimiçi</button>
            <button class="presence-btn" data-presence="idle" type="button" onclick="setPresenceStatus('idle')"><span class="presence-mini-dot"></span>Boşta</button>
            <button class="presence-btn" data-presence="dnd" type="button" onclick="setPresenceStatus('dnd')"><span class="presence-mini-dot"></span>Rahatsız etmeyin</button>
            <button class="presence-btn" data-presence="invisible" type="button" onclick="setPresenceStatus('invisible')"><span class="presence-mini-dot"></span>Görünmez</button>
          </div>
        </div>
        <div class="account-role-badges" id="accountRoleBadges"></div>
        <div class="account-verify-state" id="accountVerifyState">E-posta doğrulaması bekliyor</div>
        <div class="account-menu-actions">
          <button class="account-menu-btn" type="button" onclick="openProfileSettings()">⚙ Ayarlar</button>
          <button class="account-menu-btn" id="adminToolsMenuBtn" type="button" onclick="openAdminTools()" style="display:none">✦ Admin Araçları</button>
          <button class="account-menu-btn danger" type="button" onclick="logoutAccount()">Çıkış yap</button>
        </div>
      </div>
    </div>
  </nav>
  <div class="grade-strip">
    <div class="search-wrap">
      <svg class="search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
      <input class="search-input" id="bookSearch" type="text" placeholder="Kitap ara..." oninput="filterBooks(this.value)">
    </div>
  </div>
  <div class="book-grid-wrap">
    <div class="book-grid" id="bookGrid"></div>
  </div>
  <div class="library-bottom-menu" id="libraryBottomMenu" aria-label="Kütüphane kontrolleri">
    <button class="history-nav-btn bottom-menu-item" type="button" onclick="openChatSidebar()" aria-label="Sohbet geçmişini aç">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z"/><path d="M8 9h8"/><path d="M8 13h5"/></svg>
      <span>Sohbetler</span>
    </button>
    <div class="bottom-grade-cluster" role="group" aria-label="Sınıf seçimi">
      <div class="bottom-grade-options">
        <button class="grade-btn bottom-grade-btn active" data-grade="9"  onclick="selectGrade('9')" aria-label="9. sınıf">9</button>
        <button class="grade-btn bottom-grade-btn"        data-grade="10" onclick="selectGrade('10')" aria-label="10. sınıf">10</button>
      </div>
    </div>
    <button class="upload-nav-btn bottom-menu-item" type="button" onclick="openPdfPicker()" aria-label="Kitap yükle">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
      <span>Kitap Y&#252;kle</span>
    </button>
  </div>
  <footer class="lib-footer">
    <div class="footer-inner">
      <div class="footer-main">
        <div class="footer-brand-block">
          <div class="footer-logo-row">
            <img src="{{ reylai_icon_src }}" class="footer-logo" alt="ReylAI">
            <div class="footer-brand-name">ReylAI</div>
          </div>
          <p class="footer-tagline">Ders kitapları, sohbetler ve ReylAI araçları için tek, sakin ve hızlı çalışma alanın.</p>
        </div>
        <nav class="footer-col" aria-label="Site bağlantıları">
          <div class="footer-col-title">Bağlantılar</div>
          <div class="footer-col-list">
            <a class="footer-link" href="#libraryScreen">Kütüphane</a>
            <a class="footer-link" href="#" onclick="openChatSidebar(); return false;">Sohbetler</a>
            <a class="footer-link" href="#" onclick="openPdfPicker(); return false;">Kitap Yükle</a>
            <a class="footer-link" href="#" onclick="openDmOverlay(); return false;">Mesajlar</a>
          </div>
        </nav>
        <nav class="footer-col" aria-label="Yasal bağlantılar">
          <div class="footer-col-title">Yasal</div>
          <div class="footer-col-list">
            <a class="footer-link" href="/privacy">Gizlilik Politikası</a>
            <a class="footer-link" href="/terms">Kullanım Şartları</a>
          </div>
        </nav>
        <nav class="footer-col" aria-label="İletişim bağlantıları">
          <div class="footer-col-title">İletişim</div>
          <div class="footer-col-list">
            <a class="footer-link" href="mailto:contact@reyliar.xyz">Bize Ulaş</a>
            <a class="footer-link" href="mailto:contact@reyliar.xyz">E-posta</a>
          </div>
        </nav>
      </div>
      <div class="footer-divider"></div>
      <div class="footer-bottom">
        <span class="footer-copy">©2026 ReylAI. All Rights Reserved.</span>
        <span class="footer-made">made with ❤️ by reyli</span>
      </div>
    </div>
  </footer>
</div>

<!-- ── Analysis Screen ────────────────────────────────────────────────────── -->
<div class="screen hidden" id="analysisScreen">
  <div class="analysis-left">
    <div class="selected-book-cover" id="selectedCover">📄</div>
    <div class="selected-book-info">
      <div class="book-label">Seçili Kitap</div>
      <div class="book-title" id="selectedTitle">—</div>
    </div>
    <button class="read-btn" id="readBtn" onclick="openPdfViewer()">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/></svg>
      Kitabı Oku
    </button>
    <div class="analysis-status" id="analysisStatus">Hazır</div>
    <button class="btn btn-ghost" onclick="startNewChat()">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14"/><path d="M5 12h14"/></svg>
      Yeni Sohbet
    </button>
    <button class="btn btn-ghost" onclick="goBack()">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>
      Kitap Değiştir
    </button>
  </div>
  <div class="analysis-right">
    <div class="response-banner" id="responseBanner">
      <svg viewBox="0 0 52 52" width="28" height="28" aria-hidden="true">
        <circle class="checkmark-circle" cx="26" cy="26" r="25"></circle>
        <path class="checkmark-check" d="M14 27.5l8 8 16-18"></path>
      </svg>
      <span class="response-banner-text">Cevap tamamlandı</span>
    </div>
    <div class="chat-flow" id="chatFlow">
      <div class="chat-empty" id="chatEmpty">
        <div class="prompt-heading">Ne üzerinde çalışıyorsun?</div>
        <div class="quick-chips" id="quickChips">
          <button class="chip" onclick="setPrompt('Bu kitabın konularını özetle.')">Konuları özetle</button>
          <button class="chip" onclick="setPrompt('Bu kitaptaki anahtar kavramları listele.')">Anahtar kavramlar</button>
          <button class="chip" onclick="setPrompt('Bu kitap için sınav soruları oluştur.')">Sınav soruları</button>
          <button class="chip" onclick="setPrompt('Bu kitabın müfredatını analiz et.')">Müfredatı analiz et</button>
        </div>
      </div>
      <div class="chat-typing" id="typingIndicator">
        <div class="typing-spinner" aria-hidden="true"></div>
        <span class="typing-label">Yazıyor...</span>
      </div>
    </div>
    <div class="chat-input-bar">
      <div class="chat-input-wrap">
        <textarea id="promptInput" placeholder="Bir soru sor ya da görev ver… (Enter ile gönder)" rows="1"></textarea>
      </div>
      <button id="analyzeBtn" onclick="analyze()" aria-label="Analiz Et" title="Analiz Et">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
        <span class="analyze-btn-label">Analiz Et</span>
      </button>
    </div>
  </div>
</div>

<div class="task-overlay" id="scanTaskOverlay">
  <div class="task-panel">
    <button class="task-dismiss-btn" id="scanTaskDismissBtn" type="button" onclick="dismissScanTaskOverlay()" aria-label="İptal et veya kapat">&#10005;</button>
    <div class="task-hero">
      <div class="task-spinner" id="scanTaskSpinner"></div>
      <svg class="task-success-icon" id="scanTaskSuccess" viewBox="0 0 52 52" aria-hidden="true">
        <circle class="checkmark-circle" cx="26" cy="26" r="25"></circle>
        <path class="checkmark-check" d="M14 27.5l8 8 16-18"></path>
      </svg>
      <div>
        <div class="task-title">Kitaplar analiz için hazırlanıyor</div>
        <div class="task-subtitle">Tarama tamamlandığında yapay zekâ cevapları daha hızlı gelecektir.</div>
      </div>
    </div>
    <div class="task-status" id="scanTaskStatus">Hazırlanıyor…</div>
    <div class="task-progress-track">
      <div class="task-progress-fill" id="scanTaskProgress"></div>
    </div>
    <div class="task-metrics">
      <div class="task-metric">
        <div class="task-metric-label">Toplam</div>
        <div class="task-metric-value" id="scanTaskTotal">0</div>
      </div>
      <div class="task-metric">
        <div class="task-metric-label">Tamamlanan</div>
        <div class="task-metric-value" id="scanTaskDone">0</div>
      </div>
      <div class="task-metric">
        <div class="task-metric-label">Hazır</div>
        <div class="task-metric-value" id="scanTaskReady">0</div>
      </div>
      <div class="task-metric">
        <div class="task-metric-label">Sorunlu</div>
        <div class="task-metric-value" id="scanTaskFailed">0</div>
      </div>
    </div>
    <div class="task-log" id="scanTaskLog"></div>
    <div class="task-actions" id="scanTaskActions">
      <div class="task-cancel-note" id="scanTaskCancelNote" hidden>İptal isteği gönderildi.</div>
      <button class="btn btn-primary task-close-btn" id="scanTaskCloseBtn" onclick="closeScanTaskOverlay()">Kapat</button>
    </div>
  </div>
</div>

<!-- ── PDF Viewer Overlay ──────────────────────────────────────────────────── -->
<div class="pdf-viewer-overlay" id="pdfViewerOverlay">
  <div class="pdf-viewer-header">
    <div class="pdf-viewer-title" id="pdfViewerTitle">PDF G&#246;r&#252;nt&#252;leyici</div>
    <div class="pdf-viewer-header-actions">
      <div class="pdf-viewer-controls">
        <button class="pdf-ctrl-btn" onclick="pdfPrevPage()" title="&#214;nceki sayfa">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15 18 9 12 15 6"/></svg>
        </button>
        <form class="pdf-page-jump" onsubmit="pdfGoToPage(event)">
          <input class="pdf-page-input" id="pdfPageInput" type="number" min="1" value="1" inputmode="numeric" title="Sayfa numaras&#305;" aria-label="Sayfa numaras&#305;" onchange="pdfGoToPage()">
          <span class="pdf-page-total" id="pdfPageTotal">/ -</span>
        </form>
        <button class="pdf-ctrl-btn" onclick="pdfNextPage()" title="Sonraki sayfa">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"/></svg>
        </button>
        <span class="pdf-ctrl-sep"></span>
        <button class="pdf-ctrl-btn" onclick="pdfZoom(-0.2)" title="K&#252;&#231;&#252;lt">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="5" y1="12" x2="19" y2="12"/></svg>
        </button>
        <span class="pdf-zoom-info" id="pdfZoomInfo">100%</span>
        <button class="pdf-ctrl-btn" onclick="pdfZoom(0.2)" title="B&#252;y&#252;t">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
        </button>
        <button class="pdf-ctrl-btn" onclick="pdfFitPage()" title="Ekrana S&#305;&#287;d&#305;r">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/></svg>
        </button>
        <button class="pdf-ctrl-btn active" id="pdfWheelZoomToggle" onclick="togglePdfWheelZoom()" title="Tekerlekle yak&#305;nla&#351;t&#305;r">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="7" y="2" width="10" height="20" rx="5"/><line x1="12" y1="6" x2="12" y2="10"/></svg>
        </button>
      </div>
      <button class="pdf-viewer-close" onclick="closePdfViewer()">&#10005;</button>
    </div>
  </div>
  <div class="pdf-viewer-body" id="pdfViewerBody" style="position:relative;">
    <div id="pdfLoadingOverlay">
      <div class="pdf-load-title" id="pdfLoadTitle"></div>
      <div class="pdf-load-ring">
        <svg viewBox="0 0 120 120">
          <circle class="pdf-load-ring-bg" cx="60" cy="60" r="54"/>
          <circle class="pdf-load-ring-fg" id="pdfLoadRing" cx="60" cy="60" r="54"/>
        </svg>
        <div class="pdf-load-pct" id="pdfLoadPct">0%</div>
      </div>
      <div class="pdf-load-label" id="pdfLoadLabel">PDF y&#252;kleniyor...</div>
      <div class="pdf-load-bytes" id="pdfLoadBytes"></div>
    </div>
    <div id="pdfCanvasWrap">
      <canvas id="pdfCanvas"></canvas>
      <iframe id="pdfFrame" title="PDF"></iframe>
    </div>
  </div>
</div>

<!-- ── Delete Overlay ─────────────────────────────────────────────────────── -->
<div class="del-overlay" id="delOverlay">
  <div class="del-panel">
    <div class="del-panel-title">Kitabı Sil</div>
    <div class="del-panel-sub">
      <span class="del-panel-name" id="delBookName"></span> kitabı kütüphaneden kaldırılacak.
      Bu işlem geri alınamaz.
    </div>
    <div class="del-actions">
      <button class="btn btn-ghost" onclick="hideDelConfirm()">Vazgeç</button>
      <button class="btn-danger" onclick="confirmDelete()">Sil</button>
    </div>
  </div>
</div>

<!-- ── Add Book Overlay ────────────────────────────────────────────────── -->
<div class="add-overlay" id="addOverlay">
  <div class="add-panel">
    <div class="add-panel-header">
      <div class="add-panel-title">+ Kitap Ekle</div>
      <button class="cfg-close-btn" onclick="closeAddBook()">&#x2715;</button>
    </div>
    <div class="add-desc">
      Google Drive&#39;da PDF dosyas&#305;n&#305; a&#231;&#305;n &#8594; adres &#231;ubu&#287;undaki URL&#39;den dosya ID&#39;sini kopyalay&#305;n:<br>
      <code style="background:rgba(37,99,235,0.15);padding:2px 6px;border-radius:4px;display:inline-block;margin-top:5px">drive.google.com/file/d/<strong>DOSYA_ID</strong>/view</code>
    </div>
    <div class="add-field">
      <div class="add-label">Drive Dosya ID</div>
      <input class="add-input" id="addFileId" type="text" placeholder="&#214;rnek: 1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms">
    </div>
    <div class="add-field">
      <div class="add-label">Kitap Ad&#305; (iste&#287;e ba&#287;l&#305;)</div>
      <input class="add-input" id="addBookName" type="text" placeholder="Bo&#351; b&#305;rak&#305;rsan&#305;z dosya ismi kullan&#305;l&#305;r">
    </div>
    <div class="add-field">
      <div class="add-label">S&#305;n&#305;f</div>
      <select class="add-grade-select" id="addGrade">
        <option value="9">9. S&#305;n&#305;f</option>
        <option value="10">10. S&#305;n&#305;f</option>
      </select>
    </div>
    <div class="add-actions">
      <button class="btn btn-ghost" onclick="closeAddBook()">&#304;ptal</button>
      <button class="btn btn-primary" id="addBookBtn" onclick="submitAddBook()">Ekle</button>
    </div>
  </div>
</div>

<!-- ── Settings Overlay ────────────────────────────────────────────────── -->
<div class="cfg-overlay" id="cfgOverlay">
  <div class="cfg-panel">
    <div class="cfg-panel-header">
      <div class="cfg-panel-title">&#9881; Cloud Klas&#246;r Ayarlar&#305;</div>
      <button class="cfg-close-btn" onclick="closeSettings()">&#x2715;</button>
    </div>
    <div class="cfg-desc">
      Her s&#305;n&#305;f&#305;n Google Drive klas&#246;r ID&#39;sini girin.<br>
      Drive&#39;da klas&#246;r&#252; a&#231;&#305;n &#8594; URL&#39;deki <code style="background:rgba(37,99,235,0.15);padding:1px 5px;border-radius:4px">/folders/<strong>ID_BURAYA</strong></code> k&#305;sm&#305;n&#305; kopyalay&#305;n.<br>
      ID girilmezse klas&#246;r ismiyle arama yap&#305;l&#305;r.
    </div>
    <div style="display:flex;flex-direction:column;gap:11px">
      <div class="cfg-grade-row">
        <div class="cfg-grade-label">9. S&#305;n&#305;f</div>
        <input class="cfg-input" id="cfgFolder9"  type="text" placeholder="Drive klas&#246;r ID&#39;si (iste&#287;e ba&#287;l&#305;)">
      </div>
      <div class="cfg-grade-row">
        <div class="cfg-grade-label">10. S&#305;n&#305;f</div>
        <input class="cfg-input" id="cfgFolder10" type="text" placeholder="Drive klas&#246;r ID&#39;si (iste&#287;e ba&#287;l&#305;)">
      </div>
    </div>
    <div class="cfg-actions">
      <button class="btn btn-ghost" onclick="closeSettings()">&#304;ptal</button>
      <button class="btn btn-primary" onclick="saveSettings()">Kaydet ve Senkronize Et</button>
    </div>
  </div>
</div>

<div class="profile-settings-overlay" id="profileSettingsOverlay" onclick="if(event.target===this)closeProfileSettings()">
  <div class="profile-settings-panel">
    <div class="profile-settings-head">
      <div>
        <div class="profile-settings-kicker">Hesap</div>
        <div class="profile-settings-title">Profil ayarları</div>
      </div>
      <button class="cfg-close-btn" type="button" onclick="closeProfileSettings()">&#x2715;</button>
    </div>
    <div class="profile-settings-grid">
      <div class="profile-photo-card" id="profilePhotoCard">
        <div class="profile-photo-preview-wrap">
          <div class="profile-photo-preview" id="profilePhotoPreview">R</div>
        </div>
        <input class="profile-photo-input" id="profilePhotoInput" type="file" accept="image/png,image/jpeg,image/webp" onchange="handleProfilePhotoChange(event)">
        <button class="account-menu-btn" type="button" onclick="document.getElementById('profilePhotoInput').click()">Fotoğraf seç</button>
        <button class="account-menu-btn" type="button" onclick="clearProfilePhoto()">Kaldır</button>
      </div>
      <div class="settings-form">
        <label class="settings-field">
          <span class="settings-label">Görünen ad</span>
          <input class="settings-input" id="settingsDisplayName" type="text" maxlength="40" placeholder="Görünen ad">
        </label>
        <div class="settings-field">
          <div class="settings-label-row">
            <span class="settings-label">E-posta</span>
            <span class="settings-email-state" id="settingsEmailState">Doğrulama bekliyor</span>
          </div>
          <div class="settings-email-control">
            <input class="settings-input" id="settingsEmail" type="email" maxlength="254" placeholder="eposta@example.com" oninput="updateVerificationPanel()">
            <button class="email-verify-btn" id="settingsEmailVerifyBtn" type="button" onclick="handleEmailVerifyButton()">Doğrula</button>
          </div>
          <div class="settings-hint" id="settingsEmailHint">E-posta doğrulaması hesap güvenliği için kullanılır.</div>
        </div>
        <button class="account-menu-btn settings-password-btn" type="button" onclick="openPasswordChangeModal()">Şifreyi değiştir</button>
      </div>
    </div>
    <div class="settings-actions">
      <button class="btn btn-ghost" type="button" onclick="closeProfileSettings()">İptal</button>
      <button class="btn btn-primary" type="button" onclick="saveProfileSettings()">Kaydet</button>
    </div>
  </div>
</div>

<div class="avatar-crop-overlay" id="avatarCropOverlay" onclick="if(event.target===this)closeAvatarCropModal()">
  <div class="avatar-crop-panel">
    <div class="profile-settings-head">
      <div>
        <div class="profile-settings-kicker">Profil fotoğrafı</div>
        <div class="profile-settings-title">512 x 512 kırp</div>
      </div>
      <button class="cfg-close-btn" type="button" onclick="closeAvatarCropModal()">&#x2715;</button>
    </div>
    <canvas class="avatar-crop-canvas" id="avatarCropCanvas" width="512" height="512"></canvas>
    <div class="avatar-crop-controls">
      <label class="settings-field">
        <span class="settings-label">Yakınlık</span>
        <input class="settings-range" id="avatarCropZoom" type="range" min="100" max="300" value="100" oninput="renderAvatarCropPreview()">
      </label>
      <label class="settings-field">
        <span class="settings-label">Yatay konum</span>
        <input class="settings-range" id="avatarCropX" type="range" min="0" max="100" value="50" oninput="renderAvatarCropPreview()">
      </label>
      <label class="settings-field">
        <span class="settings-label">Dikey konum</span>
        <input class="settings-range" id="avatarCropY" type="range" min="0" max="100" value="50" oninput="renderAvatarCropPreview()">
      </label>
    </div>
    <div class="settings-actions">
      <button class="btn btn-ghost" type="button" onclick="closeAvatarCropModal()">İptal</button>
      <button class="btn btn-primary" type="button" onclick="applyAvatarCrop()">Kırp ve kullan</button>
    </div>
  </div>
</div>

<div class="password-change-overlay" id="passwordChangeOverlay" onclick="if(event.target===this)closePasswordChangeModal()">
  <div class="password-change-panel">
    <button class="email-code-back" type="button" onclick="closePasswordChangeModal()">‹ Geri</button>
    <div class="email-code-kicker" id="passwordChangeKicker">Şifre güvenliği</div>
    <div class="email-code-title" id="passwordChangeTitle">Şifreyi değiştir</div>
    <div class="email-code-lead" id="passwordChangeLead">Önce e-postana gönderilen 6 haneli kodu onayla.</div>
    <div class="email-code-grid password-code-grid" id="passwordCodeGrid" onclick="focusFirstEmptyPasswordCodeCell()">
      <input class="email-code-cell password-code-cell" inputmode="numeric" autocomplete="one-time-code" maxlength="1" aria-label="Şifre kod hanesi 1">
      <input class="email-code-cell password-code-cell" inputmode="numeric" maxlength="1" aria-label="Şifre kod hanesi 2">
      <input class="email-code-cell password-code-cell" inputmode="numeric" maxlength="1" aria-label="Şifre kod hanesi 3">
      <input class="email-code-cell password-code-cell" inputmode="numeric" maxlength="1" aria-label="Şifre kod hanesi 4">
      <input class="email-code-cell password-code-cell" inputmode="numeric" maxlength="1" aria-label="Şifre kod hanesi 5">
      <input class="email-code-cell password-code-cell" inputmode="numeric" maxlength="1" aria-label="Şifre kod hanesi 6">
    </div>
    <div class="password-new-fields" id="passwordNewFields">
      <label class="settings-field">
        <span class="settings-label">Yeni şifre</span>
        <input class="settings-input" id="passwordNewInput" type="password" autocomplete="new-password" minlength="8" maxlength="128" placeholder="En az 8 karakter">
      </label>
      <label class="settings-field">
        <span class="settings-label">Yeni şifre tekrar</span>
        <input class="settings-input" id="passwordNewConfirmInput" type="password" autocomplete="new-password" minlength="8" maxlength="128" placeholder="Tekrar gir">
      </label>
    </div>
    <div class="email-code-status" id="passwordChangeStatus"></div>
    <div class="email-code-actions">
      <button class="account-menu-btn" id="passwordResendBtn" type="button" onclick="sendPasswordChangeCode()">Kodu yeniden gönder</button>
      <button class="btn btn-primary" id="passwordSubmitBtn" type="button" onclick="confirmPasswordCodeOrSave()">Kodu onayla</button>
    </div>
  </div>
</div>

<div class="existing-account-overlay" id="existingAccountOverlay" onclick="if(event.target===this)closeExistingAccountModal()">
  <div class="existing-account-panel" role="dialog" aria-modal="true" aria-labelledby="existingAccountTitle">
    <button class="email-code-back" type="button" onclick="closeExistingAccountModal()">‹ Geri</button>
    <div class="email-code-kicker">Hesap bulundu</div>
    <div class="email-code-title" id="existingAccountTitle">Zaten böyle bir hesap var</div>
    <div class="email-code-lead">
      <span id="existingAccountEmail">Bu e-posta</span> ile kayıtlı bir hesap var. Giriş yapmak istiyor musunuz?
    </div>
    <div class="existing-account-actions">
      <button class="account-menu-btn" type="button" onclick="closeExistingAccountModal()">Vazgeç</button>
      <button class="btn btn-primary" type="button" onclick="goToLoginFromExistingAccount()">Girişe geç</button>
    </div>
  </div>
</div>

<div class="email-code-overlay" id="emailCodeOverlay" onclick="if(event.target===this)closeEmailCodeModal()">
  <div class="email-code-panel" role="dialog" aria-modal="true" aria-labelledby="emailCodeTitle">
    <button class="email-code-back" type="button" onclick="closeEmailCodeModal()">‹ Geri</button>
    <div class="email-code-kicker">Güvenlik kodu</div>
    <div class="email-code-title" id="emailCodeTitle">E-postanı doğrula</div>
    <div class="email-code-lead" id="emailCodeLead">E-postana gönderdiğimiz 6 haneli kodu gir.</div>
    <div class="email-code-target" id="emailCodeTarget"></div>
    <div class="email-code-grid" id="emailCodeGrid" onclick="focusFirstEmptyEmailCodeCell()">
      <input class="email-code-cell" inputmode="numeric" autocomplete="one-time-code" maxlength="1" aria-label="Kod hanesi 1">
      <input class="email-code-cell" inputmode="numeric" maxlength="1" aria-label="Kod hanesi 2">
      <input class="email-code-cell" inputmode="numeric" maxlength="1" aria-label="Kod hanesi 3">
      <input class="email-code-cell" inputmode="numeric" maxlength="1" aria-label="Kod hanesi 4">
      <input class="email-code-cell" inputmode="numeric" maxlength="1" aria-label="Kod hanesi 5">
      <input class="email-code-cell" inputmode="numeric" maxlength="1" aria-label="Kod hanesi 6">
    </div>
    <div class="email-code-status" id="emailCodeStatus"></div>
    <div class="email-code-actions">
      <button class="account-menu-btn" id="emailCodeResendBtn" type="button" onclick="resendEmailCode()">Kodu yeniden gönder</button>
      <button class="btn btn-primary" id="emailCodeSubmitBtn" type="button" onclick="confirmEmailCodeFromModal()">Onayla</button>
    </div>
  </div>
</div>

<div class="verify-required-overlay" id="verifyRequiredOverlay" onclick="if(event.target===this)closeVerifyRequiredModal()">
  <div class="verify-required-panel">
    <div class="email-code-kicker">E-posta gerekli</div>
    <div class="email-code-title">AI için e-postanı doğrula</div>
    <div class="email-code-lead">Kitap analizlerine erişmeden önce hesabındaki e-posta adresini gönderdiğimiz güvenlik koduyla doğrulaman gerekiyor.</div>
    <div class="email-code-actions">
      <button class="btn btn-ghost" type="button" onclick="closeVerifyRequiredModal()">Sonra</button>
      <button class="btn btn-primary" type="button" onclick="startVerifyRequiredFlow()">E-postayı doğrula</button>
    </div>
  </div>
</div>

<div class="admin-tools-overlay" id="adminToolsOverlay" onclick="if(event.target===this)closeAdminTools()">
  <div class="admin-tools-panel">
    <div class="admin-tools-head">
      <div>
        <div class="admin-tools-kicker">Admin</div>
        <div class="admin-tools-title">Hesap ve güvenlik araçları</div>
      </div>
      <button class="cfg-close-btn" type="button" onclick="closeAdminTools()">&#x2715;</button>
    </div>
    <div class="admin-stats" id="adminStats"></div>
    <div class="admin-sensitive-actions">
      <button class="account-menu-btn" type="button" onclick="unlockAdminSensitive()">Kilitli detayları aç</button>
      <div class="admin-sensitive-note">Şifreler düz metin olarak saklanmaz; güvenli hash özeti ve cihaz oturumları gösterilir.</div>
    </div>
    <div class="admin-sensitive-list" id="adminSensitiveList"></div>
    <div class="admin-account-list" id="adminAccountsList"></div>
  </div>
</div>

<div class="dm-overlay" id="dmOverlay" onclick="if(event.target===this)closeDmOverlay()">
  <div class="dm-panel" role="dialog" aria-modal="true" aria-label="ReylAI mesajlar">
    <aside class="dm-people">
      <div class="dm-head">
        <div>
          <div class="dm-kicker">ReylAI DM</div>
          <div class="dm-title">Mesajlar</div>
        </div>
        <button class="dm-close" type="button" onclick="closeDmOverlay()" aria-label="Mesajları kapat">×</button>
      </div>
      <input class="dm-search" id="dmSearch" type="search" placeholder="Kişi ara..." oninput="renderDmThreads()">
      <div class="dm-thread-list" id="dmThreadList"></div>
    </aside>
    <section class="dm-chat">
      <div class="dm-chat-head">
        <button class="dm-back" type="button" onclick="showDmPeople()" aria-label="Kişilere dön">‹</button>
        <div class="dm-chat-user" id="dmChatUser">
          <div class="dm-avatar">R</div>
          <div>
            <div class="dm-chat-title">Bir konuşma seç</div>
            <div class="dm-chat-subtitle">Kayıtlı hesaplar burada görünür.</div>
          </div>
        </div>
        <button class="dm-close" type="button" onclick="closeDmOverlay()" aria-label="Mesajları kapat">×</button>
      </div>
      <div class="dm-message-list" id="dmMessageList">
        <div class="dm-empty-state">Mesajlaşmak için soldan bir hesap seç.</div>
      </div>
      <div class="dm-composer">
        <div class="dm-pending" id="dmPendingBar">
          <span id="dmPendingText"></span>
          <button class="dm-close" type="button" onclick="clearDmPending()" aria-label="Eki kaldır">×</button>
        </div>
        <div class="dm-compose-row">
          <button class="dm-tool-btn" type="button" onclick="document.getElementById('dmFileInput').click()" aria-label="Dosya ekle" title="Dosya ekle">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round"><path d="M21.44 11.05 12.2 20.29a6 6 0 0 1-8.49-8.49l9.24-9.24a4 4 0 0 1 5.66 5.66l-9.25 9.24a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
          </button>
          <textarea class="dm-input" id="dmTextInput" rows="1" maxlength="4000" placeholder="Mesaj yaz..." oninput="autoResizeDmInput()"></textarea>
          <button class="dm-send-btn" id="dmSendBtn" type="button" onclick="sendDmMessage()" aria-label="Mesaj gönder">
            <svg viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
          </button>
        </div>
        <input id="dmFileInput" type="file" hidden onchange="handleDmFileSelect(this.files && this.files[0]); this.value='';">
      </div>
    </section>
  </div>
</div>

<div id="toastContainer"></div>

<!-- Auth Modal -->
<div class="auth-overlay" id="authOverlay" onclick="if(event.target===this)closeAuth()">
  <div class="auth-panel">
    <div class="auth-lock-icon">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
    </div>
    <div class="auth-title">Cloudflare Doğrulaması</div>
    <div class="auth-desc">Bu yönetici işlemi için bot doğrulamasını tamamla.</div>
    <div class="turnstile-wrap admin-turnstile-wrap">
      <div id="adminTurnstile"></div>
      <div class="turnstile-note" id="adminTurnstileNote">Cloudflare doğrulaması hazırlanıyor...</div>
    </div>
    <div class="auth-error" id="authError"></div>
    <div class="auth-actions">
      <button class="btn btn-ghost" onclick="closeAuth()">&#304;ptal</button>
      <button class="btn btn-primary" id="adminVerifyBtn" onclick="submitAuth()">Doğrula</button>
    </div>
  </div>
</div>

<!-- Rename Modal -->
<div class="rename-overlay" id="renameOverlay" onclick="if(event.target===this)closeRename()">
  <div class="rename-panel">
    <div class="rename-title">&#128221; Kitap D&#252;zenle</div>
    <div class="rename-section">
      <label class="rename-label" for="renameInput">Kitap Ba&#351;l&#305;&#287;&#305;</label>
      <input class="rename-input" id="renameInput" type="text" placeholder="Yeni isim" onkeydown="if(event.key==='Enter')submitRename()">
    </div>
    <div class="rename-section">
      <div class="rename-label">Thumbnail</div>
      <div class="rename-cover-row">
        <div class="rename-cover-preview" id="renameCoverPreview">&#128196;</div>
        <div class="rename-cover-controls">
          <button class="rename-cover-btn" type="button" onclick="openRenameCoverPicker()">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>
            Thumbnail Seç
          </button>
          <div class="rename-cover-note" id="renameCoverNote">JPG, PNG veya WebP dosyası seçebilirsin.</div>
        </div>
      </div>
      <input class="rename-cover-file" id="renameCoverInput" type="file" accept="image/png,image/jpeg,image/webp" onchange="handleRenameCoverChange(event)">
    </div>
    <div class="rename-actions">
      <button class="btn btn-ghost" onclick="closeRename()">&#304;ptal</button>
      <button class="btn btn-primary" onclick="submitRename()">Kaydet</button>
    </div>
  </div>
</div>

<script>
let selectedBook  = null;
let selectedGrade = '9';
let _pendingDeleteInfo = null;
let _renameBookId = '';
let _renameCoverFile = null;
const APP_AUTH_TOKEN_KEY = 'reylai.accountToken.v1';
const APP_AUTH_SESSION_KEY = 'reylai.accountSessionToken.v1';
const CHAT_HISTORY_BASE_KEY = 'reylai.chatHistory.v1';
let _accountAuthMode = 'login';
let _accountUser = null;
let _turnstileSiteKey = '';
let _turnstileWidgetId = null;
let _turnstileToken = '';
let _turnstileReady = false;
let _adminTurnstileWidgetId = null;
let _adminTurnstileToken = '';
let _adminTurnstileReady = false;
let _appStarted = false;
let _pendingProfileAvatarDataUrl = '';
let _profileCropImage = null;
let _profileCropSrc = '';
let _emailCodeMode = 'verify';
const PRESENCE_IDLE_MS = 5 * 60 * 1000;
let _presenceIdleTimer = null;
let _presenceAutoIdle = false;
let _presenceRestorePending = false;
let _presenceActivityBound = false;
let _emailCodeTargetEmail = '';
let _emailCodeSubmitting = false;
let _emailCodeAutoTimer = null;
let _passwordChangeToken = '';
let _passwordCodeSubmitting = false;
let _passwordCodeAutoTimer = null;
let _passwordFlowMode = 'account';
let _passwordResetEmail = '';
let _existingAccountEmail = '';
let _chatStore = { chats: [] };
let _chatStoreLoaded = false;
let _chatStoreLoadPromise = null;
let _chatStoreSaveTimer = null;
let _activeChatId = '';
let _libraryBookCache = {};
let _activeAnalyzeController = null;
let _analysisStopRequested = false;
let _editingMessageId = '';
let _dmUsers = [];
let _dmThreads = [];
let _dmActiveUserId = '';
let _dmMessages = [];
let _dmPendingAttachment = null;
let _dmPendingForward = null;
let _dmSending = false;
let _dmPollTimer = null;
let _dmKnownLatestIds = {};
let _dmInitialPollDone = false;
let _dmConversationSeq = 0;
let _dmPanelLoadSeq = 0;
let _dmMessagesUserId = '';
let _dmMessageAbortController = null;
const BOOKS_REMOTE_BASE_URL = {{ books_remote_base_url|tojson }};

const SEND_ICON = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>';
const STOP_ICON = '<svg width="17" height="17" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2.5"/></svg>';

let _loadingStatusTimer = null;

function setLoadingStatus(message) {
  const el = document.getElementById('loadingStatusText');
  if (el) el.textContent = message;
}

function startLoadingStatusCycle(message) {
  if (message) setLoadingStatus(message);
  clearInterval(_loadingStatusTimer);
  _loadingStatusTimer = null;
}

function showLoadingOverlay(message) {
  const overlay = document.getElementById('appLoadingOverlay');
  if (!overlay) return;
  document.body.classList.add('app-loading-active');
  document.body.classList.remove('app-ready');
  overlay.classList.remove('done');
  startLoadingStatusCycle(message || '');
}

function hideLoadingOverlay(revealApp) {
  clearInterval(_loadingStatusTimer);
  const overlay = document.getElementById('appLoadingOverlay');
  if (overlay) overlay.classList.add('done');
  document.body.classList.remove('app-loading-active');
  document.body.classList.toggle('app-ready', !!revealApp);
}

function getAppAuthToken() {
  return localStorage.getItem(APP_AUTH_TOKEN_KEY) || sessionStorage.getItem(APP_AUTH_SESSION_KEY) || '';
}

function setAppAuthToken(token, rememberDevice) {
  clearAppAuthToken();
  if (rememberDevice) localStorage.setItem(APP_AUTH_TOKEN_KEY, token);
  else sessionStorage.setItem(APP_AUTH_SESSION_KEY, token);
}

function clearAppAuthToken() {
  localStorage.removeItem(APP_AUTH_TOKEN_KEY);
  sessionStorage.removeItem(APP_AUTH_SESSION_KEY);
}

function resetAccountScopedState() {
  _chatStore = { chats: [] };
  _chatStoreLoaded = false;
  _chatStoreLoadPromise = null;
  _activeChatId = '';
  _dmUsers = [];
  _dmThreads = [];
  _dmActiveUserId = '';
  _dmMessages = [];
  _dmPendingAttachment = null;
  _dmPendingForward = null;
  setDmSendingState(false);
  _dmKnownLatestIds = {};
  _dmInitialPollDone = false;
  _dmConversationSeq++;
  _dmPanelLoadSeq++;
  _dmMessagesUserId = '';
  cancelDmMessageLoad();
  selectedBook = null;
  stopDmPolling();
  resetPresenceAutomation();
  const dmOverlay = document.getElementById('dmOverlay');
  if (dmOverlay) dmOverlay.classList.remove('active', 'chat-open');
  const dmThreads = document.getElementById('dmThreadList');
  if (dmThreads) dmThreads.innerHTML = '';
  const dmMessages = document.getElementById('dmMessageList');
  if (dmMessages) dmMessages.innerHTML = '<div class="dm-empty-state">Mesajlaşmak için soldan bir hesap seç.</div>';
  renderDmHeader(null);
  updateDmPendingBar();
  clearChat();
  renderChatHistory();
}

function apiFetch(url, options) {
  options = options || {};
  const headers = new Headers(options.headers || {});
  const token = getAppAuthToken();
  if (token) headers.set('Authorization', 'Bearer ' + token);
  return fetch(url, Object.assign({}, options, { headers: headers })).then(function(res) {
    if (res.status === 401) {
      clearAppAuthToken();
      resetAccountScopedState();
      _accountUser = null;
      updateAccountUI();
      showAccountAuth();
      throw new Error('Oturum süresi doldu.');
    }
    return res;
  });
}

function showAccountAuth() {
  const screen = document.getElementById('accountAuthScreen');
  document.body.classList.add('account-auth-visible');
  document.body.classList.remove('app-ready');
  if (screen) screen.classList.add('active');
  setAccountAuthMode(_accountAuthMode || 'login');
  renderAccountTurnstile();
}

function hideAccountAuth() {
  const screen = document.getElementById('accountAuthScreen');
  if (screen) screen.classList.remove('active');
  document.body.classList.remove('account-auth-visible');
}

function setAccountAuthError(message) {
  const el = document.getElementById('accountAuthError');
  if (el) el.textContent = message || '';
}

function setAccountAuthMode(mode) {
  _accountAuthMode = mode === 'signup' ? 'signup' : 'login';
  const signup = _accountAuthMode === 'signup';
  const display = document.getElementById('displayNameField');
  const displayInput = document.getElementById('accountDisplayName');
  const loginTab = document.getElementById('loginTabBtn');
  const signupTab = document.getElementById('signupTabBtn');
  const password = document.getElementById('accountPassword');
  const submit = document.getElementById('accountSubmitBtn');
  const switchText = document.getElementById('accountSwitchText');
  const switchBtn = document.getElementById('accountSwitchBtn');
  const screen = document.getElementById('accountAuthScreen');
  const title = document.getElementById('accountAuthTitle');
  const subtitle = document.getElementById('accountAuthSubtitle');
  const kicker = document.getElementById('authModeKicker');
  const panelTitle = document.getElementById('authPanelTitle');
  const panelLead = document.getElementById('authPanelLead');
  const passwordHint = document.getElementById('accountPasswordHint');
  const forgotBtn = document.getElementById('forgotPasswordBtn');
  if (display) display.classList.toggle('active', signup);
  if (screen) screen.classList.toggle('signup-mode', signup);
  if (displayInput) displayInput.required = signup;
  if (loginTab) loginTab.classList.toggle('active', !signup);
  if (signupTab) signupTab.classList.toggle('active', signup);
  if (password) password.autocomplete = signup ? 'new-password' : 'current-password';
  if (submit) submit.textContent = signup ? 'Hesap oluştur' : 'Giriş yap';
  if (title) title.textContent = signup ? "ReylAI hesabını oluştur." : "ReylAI'ye hoş geldin.";
  if (subtitle) subtitle.textContent = signup ? 'Görünen adını seç, e-posta ve şifreyle güvenli çalışma alanını aç.' : 'Kitapların, sohbet geçmişin ve çalışma alanın hesabına bağlı şekilde açılır.';
  if (kicker) kicker.textContent = signup ? 'Yeni hesap' : 'Güvenli oturum';
  if (panelTitle) panelTitle.textContent = signup ? 'Kayıt ol' : 'Giriş yap';
  if (panelLead) panelLead.textContent = signup ? 'E-posta, şifre ve görünen ad ile ReylAI alanını oluştur.' : 'Kayıtlı e-posta ve şifrenle devam et.';
  if (passwordHint) passwordHint.textContent = signup ? 'En az 8 karakter kullan; şifreler güvenli biçimde korunur.' : 'Şifren korunan oturum doğrulaması için kullanılır.';
  if (switchText) switchText.textContent = signup ? 'Zaten hesabın var mı?' : 'Hesabın yok mu?';
  if (forgotBtn) forgotBtn.classList.toggle('hidden', signup);
  if (switchBtn) {
    switchBtn.textContent = signup ? 'Giriş yap' : 'Kayıt ol';
    switchBtn.onclick = function() { setAccountAuthMode(signup ? 'login' : 'signup'); };
  }
  setAccountAuthError('');
  resetAccountTurnstile();
}

function updateAccountSubmitState() {
  const btn = document.getElementById('accountSubmitBtn');
  if (!btn) return;
  btn.disabled = !_turnstileSiteKey || !_turnstileReady;
}

function renderAccountTurnstile() {
  const target = document.getElementById('accountTurnstile');
  const note = document.getElementById('turnstileNote');
  if (!target) return;
  if (!_turnstileSiteKey) {
    _turnstileReady = false;
    if (note) note.textContent = 'Cloudflare doğrulaması için production site key bekleniyor.';
    updateAccountSubmitState();
    return;
  }
  if (!window.turnstile || typeof window.turnstile.render !== 'function') {
    if (note) note.textContent = 'Cloudflare doğrulaması yükleniyor...';
    setTimeout(renderAccountTurnstile, 220);
    return;
  }
  target.innerHTML = '';
  _turnstileToken = '';
  _turnstileReady = false;
  _turnstileWidgetId = window.turnstile.render(target, {
    sitekey: _turnstileSiteKey,
    action: 'turnstile-spin-v1',
    theme: 'dark',
    callback: function(token) {
      _turnstileToken = token || '';
      _turnstileReady = !!_turnstileToken;
      if (note) note.textContent = _turnstileReady ? 'Cloudflare doğrulaması tamam.' : 'Cloudflare doğrulaması bekleniyor...';
      updateAccountSubmitState();
    },
    'expired-callback': function() {
      _turnstileToken = '';
      _turnstileReady = false;
      if (note) note.textContent = 'Doğrulama süresi doldu. Yenileniyor...';
      updateAccountSubmitState();
      resetAccountTurnstile();
    },
    'error-callback': function() {
      _turnstileToken = '';
      _turnstileReady = false;
      if (note) note.textContent = 'Cloudflare doğrulaması tekrar deneniyor...';
      updateAccountSubmitState();
    }
  });
  if (note) note.textContent = 'Cloudflare doğrulaması bekleniyor...';
  updateAccountSubmitState();
}

function resetAccountTurnstile() {
  _turnstileToken = '';
  _turnstileReady = false;
  if (window.turnstile && _turnstileWidgetId !== null) {
    try { window.turnstile.reset(_turnstileWidgetId); } catch(e) { renderAccountTurnstile(); }
  } else {
    renderAccountTurnstile();
  }
  updateAccountSubmitState();
}

function updateAdminVerifyState() {
  const btn = document.getElementById('adminVerifyBtn');
  if (btn) btn.disabled = !_adminTurnstileReady || _authSubmitting;
}

function renderAdminTurnstile() {
  const target = document.getElementById('adminTurnstile');
  const note = document.getElementById('adminTurnstileNote');
  if (!target) return;
  if (!_turnstileSiteKey) {
    _adminTurnstileReady = false;
    if (note) note.textContent = 'Cloudflare doğrulaması için production site key bekleniyor.';
    updateAdminVerifyState();
    return;
  }
  if (!window.turnstile || typeof window.turnstile.render !== 'function') {
    if (note) note.textContent = 'Cloudflare doğrulaması yükleniyor...';
    setTimeout(renderAdminTurnstile, 220);
    return;
  }
  target.innerHTML = '';
  _adminTurnstileToken = '';
  _adminTurnstileReady = false;
  _adminTurnstileWidgetId = window.turnstile.render(target, {
    sitekey: _turnstileSiteKey,
    action: 'admin-action-v1',
    theme: 'dark',
    callback: function(token) {
      _adminTurnstileToken = token || '';
      _adminTurnstileReady = !!_adminTurnstileToken;
      if (note) note.textContent = _adminTurnstileReady ? 'Cloudflare doğrulaması tamam.' : 'Cloudflare doğrulaması bekleniyor...';
      updateAdminVerifyState();
    },
    'expired-callback': function() {
      _adminTurnstileToken = '';
      _adminTurnstileReady = false;
      if (note) note.textContent = 'Doğrulama süresi doldu. Yenileniyor...';
      updateAdminVerifyState();
      resetAdminTurnstile();
    },
    'error-callback': function() {
      _adminTurnstileToken = '';
      _adminTurnstileReady = false;
      if (note) note.textContent = 'Cloudflare doğrulaması tekrar deneniyor...';
      updateAdminVerifyState();
    }
  });
  if (note) note.textContent = 'Cloudflare doğrulaması bekleniyor...';
  updateAdminVerifyState();
}

function resetAdminTurnstile() {
  _adminTurnstileToken = '';
  _adminTurnstileReady = false;
  if (window.turnstile && _adminTurnstileWidgetId !== null) {
    try { window.turnstile.reset(_adminTurnstileWidgetId); } catch(e) { renderAdminTurnstile(); }
  } else {
    renderAdminTurnstile();
  }
  updateAdminVerifyState();
}

async function loadAccountAuthConfig() {
  try {
    const res = await fetch('/api/auth/config', { cache: 'no-store' });
    const data = await res.json();
    _turnstileSiteKey = data.turnstile_site_key || '';
  } catch(e) {
    _turnstileSiteKey = '';
  }
  renderAccountTurnstile();
}

function showExistingAccountModal(email) {
  _existingAccountEmail = String(email || '').trim();
  const overlay = document.getElementById('existingAccountOverlay');
  const emailEl = document.getElementById('existingAccountEmail');
  if (emailEl) emailEl.textContent = _existingAccountEmail || 'Bu e-posta';
  if (overlay) overlay.classList.add('active');
}

function closeExistingAccountModal() {
  const overlay = document.getElementById('existingAccountOverlay');
  if (overlay) overlay.classList.remove('active');
}

function goToLoginFromExistingAccount() {
  const email = _existingAccountEmail || (document.getElementById('accountEmail') || {}).value || '';
  closeExistingAccountModal();
  setAccountAuthMode('login');
  const emailInput = document.getElementById('accountEmail');
  const passwordInput = document.getElementById('accountPassword');
  if (emailInput) emailInput.value = email;
  if (passwordInput) {
    passwordInput.value = '';
    setTimeout(function(){ passwordInput.focus(); }, 80);
  }
  setAccountAuthError('');
}

function startForgotPassword() {
  const email = String((document.getElementById('accountEmail') || {}).value || '').trim();
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    setAccountAuthError('Şifre sıfırlama için önce e-postanı yaz.');
    return;
  }
  if (!_turnstileToken) {
    setAccountAuthError('Şifre sıfırlamak için Cloudflare doğrulamasını tamamlayın.');
    return;
  }
  setAccountAuthError('');
  _passwordFlowMode = 'reset';
  _passwordResetEmail = email;
  _passwordChangeToken = '';
  _passwordCodeSubmitting = false;
  clearPasswordCodeCells();
  setPasswordStage(false);
  const newInput = document.getElementById('passwordNewInput');
  const confirmInput = document.getElementById('passwordNewConfirmInput');
  if (newInput) newInput.value = '';
  if (confirmInput) confirmInput.value = '';
  const overlay = document.getElementById('passwordChangeOverlay');
  if (overlay) overlay.classList.add('active');
  sendPasswordChangeCode();
  setTimeout(function(){ focusFirstEmptyPasswordCodeCell(); }, 140);
}

async function submitAccountAuth(event) {
  if (event) event.preventDefault();
  const btn = document.getElementById('accountSubmitBtn');
  if (!_turnstileToken) {
    setAccountAuthError('Cloudflare bot doğrulamasını tamamlayın.');
    return;
  }
  const email = document.getElementById('accountEmail').value.trim();
  const password = document.getElementById('accountPassword').value;
  const displayName = document.getElementById('accountDisplayName').value.trim();
  const remember = document.getElementById('rememberDevice').checked;
  const endpoint = _accountAuthMode === 'signup' ? '/api/auth/signup' : '/api/auth/login';
  const payload = {
    email: email,
    password: password,
    display_name: displayName,
    remember_device: remember,
    turnstile_token: _turnstileToken
  };
  setAccountAuthError('');
  if (btn) {
    btn.disabled = true;
    btn.textContent = _accountAuthMode === 'signup' ? 'Hesap oluşturuluyor...' : 'Giriş yapılıyor...';
  }
  try {
    const res = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (_accountAuthMode === 'signup' && (res.status === 409 || data.code === 'account_exists')) {
      showExistingAccountModal(data.login_email || email);
      resetAccountTurnstile();
      return;
    }
    if (!res.ok || !data.success || !data.token) {
      throw new Error(data.error || 'Giriş tamamlanamadı.');
    }
    setAppAuthToken(data.token, remember);
    _accountUser = data.user || null;
    resetAccountScopedState();
    updateAccountUI();
    startPresenceAutomation();
    showLoadingOverlay('Kişisel alan açılıyor...');
    hideAccountAuth();
    await startApp();
    hideLoadingOverlay(true);
    if (_accountAuthMode === 'signup' && _accountUser && !_accountUser.email_verified) {
      if (data.email_delivery_configured === false) {
        showToast('warning', 'Kod gönderimi bekliyor', 'Hesabın açıldı; e-postana kod göndermek için servis hazırlanıyor.', 6500);
      } else if (data.verification_email_sent === false) {
        showToast('warning', 'Kod gönderilemedi', emailDeliveryFailureMessage(data, 'Hesabın açıldı ama doğrulama kodu e-postana gönderilemedi.'), 8200);
      } else {
        showToast('warning', 'E-postanı doğrula', 'E-postana gönderdiğimiz kodu açılan ekranda onayla.', 6200);
        setTimeout(function(){ openEmailCodeModal('verify', { email: _accountUser.email || '', send: false }); }, 850);
      }
    } else {
      showToast('success', 'Hoş geldin', 'ReylAI hesabın hazır.', 2800);
    }
  } catch(e) {
    hideLoadingOverlay(false);
    showAccountAuth();
    setAccountAuthError(e.message || 'Bağlantı hatası.');
    resetAccountTurnstile();
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = _accountAuthMode === 'signup' ? 'Hesap oluştur' : 'Giriş yap';
      updateAccountSubmitState();
    }
  }
}

function updateAccountUI() {
  const user = _accountUser || {};
  const name = user.display_name || 'Hesap';
  const email = user.email || '';
  const chip = document.getElementById('accountChip');
  const avatar = document.getElementById('accountAvatar');
  const chipName = document.getElementById('accountChipName');
  const menuName = document.getElementById('accountMenuName');
  const menuEmail = document.getElementById('accountMenuEmail');
  const profileInput = document.getElementById('profileDisplayName');
  if (chip) chip.classList.toggle('visible', !!_accountUser);
  if (avatar) avatar.textContent = name.trim().charAt(0).toUpperCase() || 'R';
  if (chipName) chipName.textContent = name;
  if (menuName) menuName.textContent = name;
  if (menuEmail) menuEmail.textContent = email;
  if (profileInput) profileInput.value = _accountUser ? name : '';
}

function mountAccountMenu() {
  const menu = document.getElementById('accountMenu');
  if (menu && menu.parentElement !== document.body) {
    document.body.appendChild(menu);
  }
}

function mountLibraryBottomMenu() {
  const menu = document.getElementById('libraryBottomMenu');
  if (menu && menu.parentElement !== document.body) {
    document.body.appendChild(menu);
  }
}

function toggleAccountMenu(event) {
  if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
  mountAccountMenu();
  const menu = document.getElementById('accountMenu');
  const chip = document.getElementById('accountChip');
  if (menu) {
    const nextOpen = !menu.classList.contains('active');
    menu.classList.toggle('active', nextOpen);
    document.body.classList.toggle('account-menu-open', nextOpen);
    if (chip) chip.setAttribute('aria-expanded', nextOpen ? 'true' : 'false');
    if (!nextOpen) closeProfilePresencePicker();
  }
}

function closeAccountMenu() {
  const menu = document.getElementById('accountMenu');
  if (menu) menu.classList.remove('active');
  document.body.classList.remove('account-menu-open');
  const chip = document.getElementById('accountChip');
  if (chip) chip.setAttribute('aria-expanded', 'false');
  closeProfilePresencePicker();
}

async function saveAccountProfile() {
  const input = document.getElementById('profileDisplayName');
  const displayName = input ? input.value.trim() : '';
  if (displayName.length < 2) {
    showToast('warning', 'Ad kısa', 'Görünen ad en az 2 karakter olmalı.', 3000);
    return;
  }
  try {
    const res = await apiFetch('/api/auth/profile', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ display_name: displayName })
    });
    const data = await res.json();
    if (!res.ok || !data.success) throw new Error(data.error || 'Profil kaydedilemedi.');
    _accountUser = data.user;
    updateAccountUI();
    closeAccountMenu();
    showToast('success', 'Profil güncellendi', 'Görünen ad kaydedildi.', 2600);
  } catch(e) {
    showToast('error', 'Profil Kaydedilemedi', e.message || 'Bağlantı hatası.', 4500);
  }
}

function accountIsAdmin() {
  return !!(_accountUser && (_accountUser.is_admin || _accountUser.role === 'admin'));
}

function accountInitial(user) {
  const name = (user && (user.display_name || user.email)) || 'R';
  return String(name).trim().charAt(0).toUpperCase() || 'R';
}

function setAccountAvatarElement(el, user, sizeClass) {
  if (!el) return;
  const avatar = user && user.avatar_data_url;
  el.textContent = avatar ? '' : accountInitial(user);
  el.style.backgroundImage = avatar ? 'url("' + avatar.replace(/"/g, '%22') + '")' : '';
}

function roleIconText(icon) {
  if (icon === 'shield') return '&lt;/&gt;';
  if (icon === 'sparkles') return '✦';
  return '●';
}

function renderAccountBadges(roles) {
  roles = Array.isArray(roles) && roles.length ? roles : [{ label: 'Member', icon: 'user' }];
  return roles.map(function(role) {
    const label = String(role.label || 'Member');
    const cls = label.toLowerCase() === 'admin' ? ' admin' : (label.toLowerCase() === 'staff' ? ' staff' : '');
    return '<span class="account-role-badge' + cls + '">' + roleIconText(role.icon) + ' ' + escHtml(label) + '</span>';
  }).join('');
}

function normalizePresenceStatus(status) {
  status = String(status || 'online').toLowerCase();
  return ['online', 'idle', 'dnd', 'invisible'].indexOf(status) !== -1 ? status : 'online';
}

function presenceLabel(status) {
  status = normalizePresenceStatus(status);
  if (status === 'online') return 'Çevrimiçi';
  if (status === 'idle') return 'Boşta';
  if (status === 'dnd') return 'Rahatsız etmeyin';
  if (status === 'invisible') return 'Görünmez';
  return 'Çevrimiçi';
}

function renderPresencePicker(status) {
  const active = normalizePresenceStatus(status);
  document.querySelectorAll('#presencePicker .presence-btn').forEach(function(btn) {
    btn.classList.toggle('active', btn.dataset.presence === active);
  });
}

function updateProfileStatusToggle(status) {
  const btn = document.getElementById('profileStatusToggle');
  if (!btn) return;
  const active = normalizePresenceStatus(status);
  btn.dataset.presence = active;
  btn.classList.toggle('active', !!(document.getElementById('profilePresencePopover') || {}).classList?.contains('active'));
  btn.title = 'Durum: ' + presenceLabel(active);
  btn.setAttribute('aria-label', 'Durum değiştir: ' + presenceLabel(active));
}

function toggleProfilePresencePicker(event) {
  if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
  const popover = document.getElementById('profilePresencePopover');
  if (!popover) return;
  const open = !popover.classList.contains('active');
  popover.classList.toggle('active', open);
  const btn = document.getElementById('profileStatusToggle');
  if (btn) btn.classList.toggle('active', open);
}

function closeProfilePresencePicker() {
  const popover = document.getElementById('profilePresencePopover');
  if (popover) popover.classList.remove('active');
  const btn = document.getElementById('profileStatusToggle');
  if (btn) btn.classList.remove('active');
}

function bindPresenceActivityTracking() {
  if (_presenceActivityBound) return;
  _presenceActivityBound = true;
  ['mousemove', 'mousedown', 'keydown', 'touchstart', 'scroll', 'focus'].forEach(function(eventName) {
    window.addEventListener(eventName, handlePresenceActivity, { passive: true });
  });
}

function stopPresenceIdleTimer() {
  clearTimeout(_presenceIdleTimer);
  _presenceIdleTimer = null;
}

function schedulePresenceIdleTimer() {
  stopPresenceIdleTimer();
  if (!_accountUser || normalizePresenceStatus(_accountUser.presence_status) !== 'online') return;
  _presenceIdleTimer = setTimeout(function() {
    if (_accountUser && normalizePresenceStatus(_accountUser.presence_status) === 'online') {
      setPresenceStatus('idle', { auto: true, silent: true }).catch(function() {});
    }
  }, PRESENCE_IDLE_MS);
}

function handlePresenceActivity() {
  if (!_accountUser) return;
  if (_presenceAutoIdle && normalizePresenceStatus(_accountUser.presence_status) === 'idle') {
    if (_presenceRestorePending) return;
    _presenceRestorePending = true;
    setPresenceStatus('online', { auto: true, silent: true }).catch(function() {}).finally(function() {
      _presenceRestorePending = false;
    });
    return;
  }
  if (normalizePresenceStatus(_accountUser.presence_status) === 'online') {
    schedulePresenceIdleTimer();
  }
}

function startPresenceAutomation() {
  bindPresenceActivityTracking();
  schedulePresenceIdleTimer();
}

function resetPresenceAutomation() {
  stopPresenceIdleTimer();
  _presenceAutoIdle = false;
  _presenceRestorePending = false;
}

async function setPresenceStatus(status, options) {
  options = options || {};
  status = normalizePresenceStatus(status);
  if (!_accountUser) return;
  if (!options.auto) _presenceAutoIdle = false;
  try {
    const res = await apiFetch('/api/auth/presence', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: status })
    });
    const data = await res.json();
    if (!res.ok || !data.success) throw new Error(data.error || 'Durum kaydedilemedi.');
    _accountUser = data.user || Object.assign({}, _accountUser, { presence_status: status });
    _presenceAutoIdle = !!options.auto && status === 'idle';
    updateAccountUI();
    if (!options.auto) closeProfilePresencePicker();
    schedulePresenceIdleTimer();
    fetchDmThreads().then(renderDmThreads).catch(function() {});
  } catch(e) {
    if (options.silent) throw e;
    showToast('error', 'Durum kaydedilemedi', e.message || 'Bağlantı hatası.', 4200);
  }
}

updateAccountUI = function() {
  const user = _accountUser || {};
  const name = user.display_name || 'Hesap';
  const email = user.email || '';
  const chip = document.getElementById('accountChip');
  const avatar = document.getElementById('accountAvatar');
  const menuAvatar = document.getElementById('accountMenuAvatar');
  const chipName = document.getElementById('accountChipName');
  const chipRole = document.getElementById('accountChipRole');
  const menuName = document.getElementById('accountMenuName');
  const menuEmail = document.getElementById('accountMenuEmail');
  const badges = document.getElementById('accountRoleBadges');
  const verify = document.getElementById('accountVerifyState');
  const adminBtn = document.getElementById('adminToolsMenuBtn');
  if (chip) chip.classList.toggle('visible', !!_accountUser);
  setAccountAvatarElement(avatar, user);
  setAccountAvatarElement(menuAvatar, user);
  if (chipName) chipName.textContent = name;
  const presence = normalizePresenceStatus(user.presence_status || 'online');
  if (menuName) menuName.textContent = name;
  if (menuEmail) menuEmail.textContent = email;
  if (badges) badges.innerHTML = renderAccountBadges(user.roles);
  if (chipRole) {
    const admin = accountIsAdmin();
    chipRole.textContent = admin ? '</> Admin' : '';
    chipRole.classList.toggle('visible', admin);
  }
  if (adminBtn) adminBtn.style.display = accountIsAdmin() ? 'inline-flex' : 'none';
  if (verify) {
    const ok = !!user.email_verified;
    verify.classList.toggle('verified', ok);
    verify.textContent = ok ? '✓ E-posta doğrulandı' : 'E-posta doğrulaması bekliyor';
  }
  renderPresencePicker(user.presence_status || 'online');
  updateProfileStatusToggle(user.presence_status || 'online');
  updateVerificationPanel();
};

saveAccountProfile = async function() {
  openProfileSettings();
};

function openProfileSettings() {
  if (!_accountUser) return;
  closeAccountMenu();
  _pendingProfileAvatarDataUrl = _accountUser.avatar_data_url || '';
  const name = document.getElementById('settingsDisplayName');
  const email = document.getElementById('settingsEmail');
  if (name) name.value = _accountUser.display_name || '';
  if (email) email.value = _accountUser.email || '';
  closeProfilePresencePicker();
  updateProfilePhotoPreview();
  updateVerificationPanel();
  document.getElementById('profileSettingsOverlay').classList.add('active');
}

function closeProfileSettings() {
  const overlay = document.getElementById('profileSettingsOverlay');
  if (overlay) overlay.classList.remove('active');
  closeProfilePresencePicker();
}

function updateProfilePhotoPreview() {
  const preview = document.getElementById('profilePhotoPreview');
  if (!preview) return;
  const user = Object.assign({}, _accountUser || {}, { avatar_data_url: _pendingProfileAvatarDataUrl });
  setAccountAvatarElement(preview, user);
}

function updateVerificationPanel() {
  const state = document.getElementById('settingsEmailState');
  const btn = document.getElementById('settingsEmailVerifyBtn');
  const hint = document.getElementById('settingsEmailHint');
  const input = document.getElementById('settingsEmail');
  if (!state || !btn || !hint || !input || !_accountUser) return;
  const currentEmail = String(_accountUser.email || '').trim().toLowerCase();
  const typedEmail = String(input.value || '').trim().toLowerCase();
  const changed = typedEmail && typedEmail !== currentEmail;
  state.classList.remove('verified', 'changed', 'pending');
  btn.style.display = '';
  btn.disabled = false;
  if (changed) {
    state.textContent = 'Değişiklik bekliyor';
    state.classList.add('changed');
    btn.textContent = 'Kaydet & doğrula';
    hint.textContent = 'Yeni e-posta önce kaydedilir, sonra o adrese gelen 6 haneli kodla onaylanır.';
    return;
  }
  if (_accountUser.email_verified) {
    state.textContent = 'Doğrulandı';
    state.classList.add('verified');
    btn.textContent = '';
    btn.disabled = true;
    btn.style.display = 'none';
    hint.textContent = 'Bu e-posta adresi doğrulanmış durumda.';
    return;
  }
  state.textContent = 'Doğrulama bekliyor';
  state.classList.add('pending');
  btn.textContent = 'Doğrula';
  hint.textContent = 'E-postana gönderdiğimiz kodla adresini doğrulayabilirsin.';
}

function accountEmailVerified() {
  return !!(_accountUser && _accountUser.email_verified);
}

function showVerifyRequiredModal(message) {
  const overlay = document.getElementById('verifyRequiredOverlay');
  const lead = overlay ? overlay.querySelector('.email-code-lead') : null;
  if (lead) {
    lead.textContent = message || 'Kitap analizlerine erişmeden önce hesabındaki e-posta adresini gönderdiğimiz güvenlik koduyla doğrulaman gerekiyor.';
  }
  if (overlay) overlay.classList.add('active');
}

function closeVerifyRequiredModal() {
  const overlay = document.getElementById('verifyRequiredOverlay');
  if (overlay) overlay.classList.remove('active');
}

function startVerifyRequiredFlow() {
  closeVerifyRequiredModal();
  if (!_accountUser) {
    showAccountAuth();
    return;
  }
  openEmailCodeModal('verify', { email: _accountUser.email || '', send: true });
}

function ensureEmailVerifiedForAI() {
  if (accountEmailVerified()) return true;
  showVerifyRequiredModal();
  return false;
}

function handleProfilePhotoChange(event) {
  const file = event && event.target && event.target.files ? event.target.files[0] : null;
  if (!file) return;
  if (!/^image\/(png|jpeg|webp)$/i.test(file.type || '')) {
    showToast('warning', 'Görsel desteklenmiyor', 'PNG, JPG veya WebP formatında bir profil fotoğrafı seç.', 4200);
    return;
  }
  if (file.size > 5 * 1024 * 1024) {
    showToast('warning', 'Fotoğraf büyük', 'Profil fotoğrafı için 5 MB altında bir görsel seç.', 4200);
    return;
  }
  const reader = new FileReader();
  reader.onload = function() {
    openAvatarCropModal(String(reader.result || ''));
  };
  reader.onerror = function() {
    showToast('error', 'Fotoğraf okunamadı', 'Görsel dosyasını tekrar seç.', 4200);
  };
  reader.readAsDataURL(file);
}

function openAvatarCropModal(src) {
  _profileCropSrc = src || '';
  _profileCropImage = new Image();
  _profileCropImage.onload = function() {
    const zoom = document.getElementById('avatarCropZoom');
    const x = document.getElementById('avatarCropX');
    const y = document.getElementById('avatarCropY');
    if (zoom) zoom.value = '100';
    if (x) x.value = '50';
    if (y) y.value = '50';
    const overlay = document.getElementById('avatarCropOverlay');
    if (overlay) overlay.classList.add('active');
    renderAvatarCropPreview();
  };
  _profileCropImage.onerror = function() {
    showToast('error', 'Fotoğraf açılamadı', 'Görseli tekrar seç.', 4200);
  };
  _profileCropImage.src = _profileCropSrc;
}

function closeAvatarCropModal() {
  const overlay = document.getElementById('avatarCropOverlay');
  if (overlay) overlay.classList.remove('active');
  const input = document.getElementById('profilePhotoInput');
  if (input) input.value = '';
}

function getAvatarCropRect() {
  if (!_profileCropImage) return null;
  const img = _profileCropImage;
  const zoom = Number((document.getElementById('avatarCropZoom') || {}).value || 100) / 100;
  const xVal = Number((document.getElementById('avatarCropX') || {}).value || 50) / 100;
  const yVal = Number((document.getElementById('avatarCropY') || {}).value || 50) / 100;
  const baseSide = Math.min(img.naturalWidth || img.width, img.naturalHeight || img.height);
  const side = Math.max(1, baseSide / Math.max(1, zoom));
  const maxX = Math.max(0, (img.naturalWidth || img.width) - side);
  const maxY = Math.max(0, (img.naturalHeight || img.height) - side);
  return { sx: maxX * xVal, sy: maxY * yVal, side: side };
}

function renderAvatarCropPreview() {
  const canvas = document.getElementById('avatarCropCanvas');
  if (!canvas || !_profileCropImage || !_profileCropImage.complete) return;
  const rect = getAvatarCropRect();
  if (!rect) return;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, 512, 512);
  ctx.fillStyle = '#06101f';
  ctx.fillRect(0, 0, 512, 512);
  ctx.drawImage(_profileCropImage, rect.sx, rect.sy, rect.side, rect.side, 0, 0, 512, 512);
}

function applyAvatarCrop() {
  renderAvatarCropPreview();
  const canvas = document.getElementById('avatarCropCanvas');
  if (!canvas) return;
  _pendingProfileAvatarDataUrl = canvas.toDataURL('image/jpeg', 0.86);
  updateProfilePhotoPreview();
  closeAvatarCropModal();
}

function clearProfilePhoto() {
  _pendingProfileAvatarDataUrl = '';
  const input = document.getElementById('profilePhotoInput');
  if (input) input.value = '';
  updateProfilePhotoPreview();
}

async function saveProfileSettings(options) {
  options = options || {};
  if (!_accountUser) return;
  const displayName = (document.getElementById('settingsDisplayName') || {}).value || '';
  const email = (document.getElementById('settingsEmail') || {}).value || '';
  const payload = {
    display_name: displayName.trim(),
    email: email.trim(),
    avatar_data_url: _pendingProfileAvatarDataUrl || ''
  };
  const changingEmail = payload.email.toLowerCase() !== String(_accountUser.email || '').toLowerCase();
  try {
    const res = await apiFetch('/api/auth/profile', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!res.ok || !data.success) throw new Error(data.error || 'Profil kaydedilemedi.');
    _accountUser = data.user;
    _pendingProfileAvatarDataUrl = _accountUser.avatar_data_url || '';
    updateAccountUI();
    updateVerificationPanel();
    if (data.email_change_pending) {
      const targetEmail = data.pending_email || payload.email;
      if (data.email_delivery_configured === false) {
        showToast('warning', 'Kod gönderimi bekliyor', 'Değişiklik beklemeye alındı; e-postana kod göndermek için servis hazırlanıyor.', 6200);
        return;
      }
      if (data.verification_email_sent === false) {
        showToast('warning', 'Kod gönderilemedi', emailDeliveryFailureMessage(data, 'Yeni e-postana doğrulama kodu gönderilemedi.'), 8200);
        return;
      }
      showToast('warning', 'E-postanı onayla', 'Yeni adresine gelen 6 haneli kodu gir.', 5200);
      openEmailCodeModal('email-change', { email: targetEmail, send: false });
      return;
    }
    closeProfileSettings();
    if (data.email_delivery_configured === false) {
      showToast('warning', 'Kod gönderimi bekliyor', 'Profil kaydedildi; e-postana kod göndermek için servis hazırlanıyor.', 6200);
    } else if (changingEmail && !_accountUser.email_verified) {
      showToast('warning', 'E-postanı doğrula', 'Yeni adresine gelen 6 haneli kodu ayarlardan onayla.', 6200);
    } else {
      showToast('success', 'Profil güncellendi', 'Hesap ayarların kaydedildi.', 3000);
    }
  } catch(e) {
    showToast('error', 'Profil kaydedilemedi', e.message || 'Bağlantı hatası.', 5200);
  }
}

function handleEmailVerifyButton() {
  if (!_accountUser) return;
  const input = document.getElementById('settingsEmail');
  const typedEmail = input ? String(input.value || '').trim().toLowerCase() : '';
  const currentEmail = String(_accountUser.email || '').trim().toLowerCase();
  if (typedEmail && typedEmail !== currentEmail) {
    saveProfileSettings({ openEmailVerification: true });
    return;
  }
  openEmailCodeModal('verify', { email: _accountUser.email || '', send: true });
}

function getEmailCodeCells() {
  return Array.prototype.slice.call(document.querySelectorAll('#emailCodeGrid .email-code-cell'));
}

function setupEmailCodeInputs() {
  getEmailCodeCells().forEach(function(cell, index, cells) {
    cell.addEventListener('input', function() {
      const digits = String(cell.value || '').replace(/\D+/g, '');
      if (digits.length > 1) {
        fillEmailCodeCells(digits);
        return;
      }
      cell.value = digits;
      cell.classList.toggle('filled', !!digits);
      if (digits && cells[index + 1]) cells[index + 1].focus();
      maybeAutoConfirmEmailCode();
    });
    cell.addEventListener('keydown', function(e) {
      if (e.key === 'Backspace' && !cell.value && cells[index - 1]) {
        cells[index - 1].focus();
        cells[index - 1].value = '';
        cells[index - 1].classList.remove('filled');
        e.preventDefault();
      } else if (e.key === 'ArrowLeft' && cells[index - 1]) {
        cells[index - 1].focus();
        e.preventDefault();
      } else if (e.key === 'ArrowRight' && cells[index + 1]) {
        cells[index + 1].focus();
        e.preventDefault();
      }
    });
    cell.addEventListener('paste', function(e) {
      const text = (e.clipboardData || window.clipboardData || {}).getData ? (e.clipboardData || window.clipboardData).getData('text') : '';
      const digits = String(text || '').replace(/\D+/g, '').slice(0, 6);
      if (digits) {
        e.preventDefault();
        fillEmailCodeCells(digits);
      }
    });
  });
}

function getPasswordCodeCells() {
  return Array.prototype.slice.call(document.querySelectorAll('.password-code-cell'));
}

function setupPasswordCodeInputs() {
  getPasswordCodeCells().forEach(function(cell, index, cells) {
    cell.addEventListener('input', function() {
      const digits = String(cell.value || '').replace(/\D+/g, '');
      if (digits.length > 1) {
        fillPasswordCodeCells(digits);
        return;
      }
      cell.value = digits;
      cell.classList.toggle('filled', !!digits);
      if (digits && cells[index + 1]) cells[index + 1].focus();
      maybeAutoConfirmPasswordCode();
    });
    cell.addEventListener('keydown', function(e) {
      if (e.key === 'Backspace' && !cell.value && cells[index - 1]) {
        cells[index - 1].focus();
        cells[index - 1].value = '';
        cells[index - 1].classList.remove('filled');
        e.preventDefault();
      }
    });
    cell.addEventListener('paste', function(e) {
      const text = (e.clipboardData || window.clipboardData || {}).getData ? (e.clipboardData || window.clipboardData).getData('text') : '';
      const digits = String(text || '').replace(/\D+/g, '').slice(0, 6);
      if (digits) {
        e.preventDefault();
        fillPasswordCodeCells(digits);
      }
    });
  });
}

function clearPasswordCodeCells() {
  getPasswordCodeCells().forEach(function(cell) {
    cell.value = '';
    cell.classList.remove('filled');
  });
  if (_passwordCodeAutoTimer) clearTimeout(_passwordCodeAutoTimer);
}

function fillPasswordCodeCells(value) {
  const digits = String(value || '').replace(/\D+/g, '').slice(0, 6);
  const cells = getPasswordCodeCells();
  cells.forEach(function(cell, index) {
    cell.value = digits[index] || '';
    cell.classList.toggle('filled', !!cell.value);
  });
  if (digits.length < 6 && cells[digits.length]) cells[digits.length].focus();
  maybeAutoConfirmPasswordCode();
}

function getPasswordCodeValue() {
  return getPasswordCodeCells().map(function(cell){ return cell.value || ''; }).join('').replace(/\D+/g, '').slice(0, 6);
}

function focusFirstEmptyPasswordCodeCell() {
  const cells = getPasswordCodeCells();
  const empty = cells.find(function(cell){ return !cell.value; });
  (empty || cells[0] || {}).focus && (empty || cells[0]).focus();
}

function maybeAutoConfirmPasswordCode() {
  const code = getPasswordCodeValue();
  if (code.length !== 6 || _passwordCodeSubmitting || _passwordChangeToken) return;
  if (_passwordCodeAutoTimer) clearTimeout(_passwordCodeAutoTimer);
  _passwordCodeAutoTimer = setTimeout(function(){ confirmPasswordCodeOrSave(); }, 220);
}

function setPasswordChangeStatus(message, type) {
  const status = document.getElementById('passwordChangeStatus');
  if (!status) return;
  status.textContent = message || '';
  status.className = 'email-code-status' + (type ? ' ' + type : '');
}

function setPasswordStage(ready) {
  const panel = document.querySelector('.password-change-panel');
  if (panel) panel.classList.toggle('password-ready', !!ready);
  const resetMode = _passwordFlowMode === 'reset';
  const kicker = document.getElementById('passwordChangeKicker');
  const title = document.getElementById('passwordChangeTitle');
  const submit = document.getElementById('passwordSubmitBtn');
  if (submit) submit.textContent = ready ? 'Şifreyi kaydet' : 'Kodu onayla';
  const lead = document.getElementById('passwordChangeLead');
  if (kicker) kicker.textContent = resetMode ? 'Hesap kurtarma' : 'Åifre gÃ¼venliÄŸi';
  if (title) title.textContent = resetMode ? 'Åifreni sÄ±fÄ±rla' : 'Åifreyi deÄŸiÅŸtir';
  if (lead) lead.textContent = ready
    ? 'Kod onaylandı. Şimdi yeni şifreni belirle.'
    : 'Önce e-postana gönderilen 6 haneli kodu onayla.';
}

setPasswordStage = function(ready) {
  const panel = document.querySelector('.password-change-panel');
  if (panel) panel.classList.toggle('password-ready', !!ready);
  const resetMode = _passwordFlowMode === 'reset';
  const kicker = document.getElementById('passwordChangeKicker');
  const title = document.getElementById('passwordChangeTitle');
  const submit = document.getElementById('passwordSubmitBtn');
  const resend = document.getElementById('passwordResendBtn');
  if (submit) submit.textContent = ready ? '\u015Eifreyi kaydet' : 'Kodu onayla';
  if (resend) {
    resend.textContent = resetMode ? 'Yeni kod i\u00E7in geri d\u00F6n' : 'Kodu yeniden g\u00F6nder';
    resend.onclick = resetMode ? closePasswordChangeModal : sendPasswordChangeCode;
  }
  const lead = document.getElementById('passwordChangeLead');
  if (kicker) kicker.textContent = resetMode ? 'Hesap kurtarma' : '\u015Eifre g\u00FCvenli\u011Fi';
  if (title) title.textContent = resetMode ? '\u015Eifreni s\u0131f\u0131rla' : '\u015Eifreyi de\u011Fi\u015Ftir';
  if (lead) lead.textContent = ready
    ? 'Kod onayland\u0131. \u015Eimdi yeni \u015Fifreni belirle.'
    : (resetMode ? (_passwordResetEmail + ' adresine g\u00F6nderilen 6 haneli kodu onayla.') : '\u00D6nce e-postana g\u00F6nderilen 6 haneli kodu onayla.');
};

function openPasswordChangeModal() {
  if (!_accountUser) return;
  if (!_accountUser.email_verified) {
    showVerifyRequiredModal();
    return;
  }
  _passwordFlowMode = 'account';
  _passwordResetEmail = '';
  _passwordChangeToken = '';
  _passwordCodeSubmitting = false;
  clearPasswordCodeCells();
  setPasswordStage(false);
  const newInput = document.getElementById('passwordNewInput');
  const confirmInput = document.getElementById('passwordNewConfirmInput');
  if (newInput) newInput.value = '';
  if (confirmInput) confirmInput.value = '';
  const overlay = document.getElementById('passwordChangeOverlay');
  if (overlay) overlay.classList.add('active');
  sendPasswordChangeCode();
  setTimeout(function(){ focusFirstEmptyPasswordCodeCell(); }, 140);
}

function closePasswordChangeModal() {
  const overlay = document.getElementById('passwordChangeOverlay');
  if (overlay) overlay.classList.remove('active');
  _passwordChangeToken = '';
  _passwordCodeSubmitting = false;
  _passwordFlowMode = 'account';
  _passwordResetEmail = '';
}

async function sendPasswordChangeCode() {
  try {
    setPasswordChangeStatus('Kod gönderiliyor...', '');
    const resetMode = _passwordFlowMode === 'reset';
    const res = resetMode
      ? await fetch('/api/auth/password-reset/send', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email: _passwordResetEmail, turnstile_token: _turnstileToken })
        })
      : await apiFetch('/api/auth/password-change/send', { method: 'POST' });
    if (resetMode) resetAccountTurnstile();
    const data = await res.json();
    if (!res.ok || !data.success) throw new Error(data.error || 'Kod gönderilemedi.');
    clearPasswordCodeCells();
    setPasswordStage(false);
    setPasswordChangeStatus('Kod gönderildi. E-postandaki 6 haneyi gir veya yapıştır.', 'success');
    focusFirstEmptyPasswordCodeCell();
  } catch(e) {
    setPasswordChangeStatus(e.message || 'Kod gönderilemedi.', 'error');
    showToast('warning', 'Kod gönderilemedi', e.message || 'E-postana kod gönderemedik.', 5600);
  }
}

async function confirmPasswordCodeOrSave() {
  if (_passwordChangeToken) {
    await completePasswordChange();
    return;
  }
  const code = getPasswordCodeValue();
  if (!/^\d{6}$/.test(code)) {
    setPasswordChangeStatus('6 haneli güvenlik kodunu tamamla.', 'error');
    return;
  }
  if (_passwordCodeSubmitting) return;
  _passwordCodeSubmitting = true;
  setPasswordChangeStatus('Kod doğrulanıyor...', '');
  try {
    const resetMode = _passwordFlowMode === 'reset';
    const res = resetMode ? await fetch('/api/auth/password-reset/confirm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: _passwordResetEmail, code: code })
    }) : await apiFetch('/api/auth/password-change/confirm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: code })
    });
    const data = await res.json();
    if (!res.ok || !data.success || !data.token) throw new Error(data.error || 'Kod doğrulanamadı.');
    _passwordChangeToken = data.token;
    setPasswordStage(true);
    setPasswordChangeStatus('Kod onaylandı. Yeni şifreni yaz.', 'success');
    setTimeout(function(){ const input = document.getElementById('passwordNewInput'); if (input) input.focus(); }, 120);
  } catch(e) {
    clearPasswordCodeCells();
    setPasswordChangeStatus(e.message || 'Kod hatalı veya süresi dolmuş.', 'error');
    focusFirstEmptyPasswordCodeCell();
  } finally {
    _passwordCodeSubmitting = false;
  }
}

async function completePasswordChange() {
  const next = (document.getElementById('passwordNewInput') || {}).value || '';
  const confirm = (document.getElementById('passwordNewConfirmInput') || {}).value || '';
  if (next.length < 8 || next.length > 128) {
    setPasswordChangeStatus('Yeni şifre 8-128 karakter olmalı.', 'error');
    return;
  }
  if (next !== confirm) {
    setPasswordChangeStatus('Şifre tekrar alanı eşleşmiyor.', 'error');
    return;
  }
  try {
    setPasswordChangeStatus('Şifre kaydediliyor...', '');
    const resetMode = _passwordFlowMode === 'reset';
    const res = resetMode ? await fetch('/api/auth/password-reset/complete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: _passwordResetEmail, token: _passwordChangeToken, new_password: next })
    }) : await apiFetch('/api/auth/password-change/complete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: _passwordChangeToken, new_password: next })
    });
    const data = await res.json();
    if (!res.ok || !data.success) throw new Error(data.error || 'Şifre değiştirilemedi.');
    if (resetMode) {
      const resetEmail = _passwordResetEmail;
      closePasswordChangeModal();
      setAccountAuthMode('login');
      const emailInput = document.getElementById('accountEmail');
      const passwordInput = document.getElementById('accountPassword');
      if (emailInput) emailInput.value = resetEmail;
      if (passwordInput) {
        passwordInput.value = '';
        passwordInput.focus();
      }
      showToast('success', '\u015Eifre s\u0131f\u0131rland\u0131', 'Yeni \u015Fifrenle giri\u015F yapabilirsin.', 4200);
      return;
    }
    _accountUser = data.user || _accountUser;
    updateAccountUI();
    closePasswordChangeModal();
    showToast('success', 'Şifre değiştirildi', 'Hesap şifren güncellendi.', 3600);
  } catch(e) {
    setPasswordChangeStatus(e.message || 'Bağlantı hatası.', 'error');
  }
}

function openEmailCodeModal(mode, options) {
  options = options || {};
  _emailCodeMode = mode || 'verify';
  _emailCodeTargetEmail = String(options.email || (_accountUser && _accountUser.email) || '').trim();
  _emailCodeSubmitting = false;
  clearEmailCodeCells();
  const overlay = document.getElementById('emailCodeOverlay');
  const title = document.getElementById('emailCodeTitle');
  const lead = document.getElementById('emailCodeLead');
  const target = document.getElementById('emailCodeTarget');
  if (title) title.textContent = _emailCodeMode === 'email-change' ? 'Yeni e-postanı onayla' : 'E-postanı doğrula';
  if (lead) lead.textContent = _emailCodeMode === 'email-change'
    ? 'Yeni adresine gönderilen 6 haneli kodu gir. Kod tamamlanınca e-posta değişimi otomatik onaylanır.'
    : 'E-postana gönderdiğimiz 6 haneli kodu gir. Kodu yapıştırırsan kutucuklar otomatik dolar.';
  if (target) target.textContent = _emailCodeTargetEmail ? 'Hedef: ' + _emailCodeTargetEmail : '';
  setEmailCodeStatus(options.send ? 'Kod gönderiliyor...' : 'Kodu bekliyorum.', '');
  if (overlay) overlay.classList.add('active');
  setTimeout(function(){ focusFirstEmptyEmailCodeCell(); }, 120);
  if (options.send) sendVerificationCode({ quiet: true });
}

function closeEmailCodeModal() {
  const overlay = document.getElementById('emailCodeOverlay');
  if (overlay) overlay.classList.remove('active');
  _emailCodeSubmitting = false;
  if (_emailCodeAutoTimer) clearTimeout(_emailCodeAutoTimer);
}

function focusFirstEmptyEmailCodeCell() {
  const cells = getEmailCodeCells();
  const empty = cells.find(function(cell){ return !cell.value; });
  (empty || cells[0] || {}).focus && (empty || cells[0]).focus();
}

function clearEmailCodeCells() {
  getEmailCodeCells().forEach(function(cell) {
    cell.value = '';
    cell.classList.remove('filled');
  });
  if (_emailCodeAutoTimer) clearTimeout(_emailCodeAutoTimer);
}

function fillEmailCodeCells(value) {
  const digits = String(value || '').replace(/\D+/g, '').slice(0, 6);
  const cells = getEmailCodeCells();
  cells.forEach(function(cell, index) {
    cell.value = digits[index] || '';
    cell.classList.toggle('filled', !!cell.value);
  });
  if (digits.length < 6 && cells[digits.length]) cells[digits.length].focus();
  maybeAutoConfirmEmailCode();
}

function getEmailCodeValue() {
  return getEmailCodeCells().map(function(cell){ return cell.value || ''; }).join('').replace(/\D+/g, '').slice(0, 6);
}

function maybeAutoConfirmEmailCode() {
  const code = getEmailCodeValue();
  if (code.length !== 6 || _emailCodeSubmitting) return;
  if (_emailCodeAutoTimer) clearTimeout(_emailCodeAutoTimer);
  _emailCodeAutoTimer = setTimeout(function(){ confirmEmailCodeFromModal(); }, 220);
}

function setEmailCodeStatus(message, type) {
  const status = document.getElementById('emailCodeStatus');
  if (!status) return;
  status.textContent = message || '';
  status.className = 'email-code-status' + (type ? ' ' + type : '');
}

function emailDeliveryFailureMessage(data, fallback) {
  data = data || {};
  if (data.email_delivery_error || data.error) return data.email_delivery_error || data.error;
  if (data.email_error_code === 'E_RECIPIENT_NOT_ALLOWED') {
    return 'Cloudflare bu alıcıya e-posta göndermeye izin vermiyor. Email Sending için Workers Paid planı açın veya alıcıyı Cloudflare hesabında doğrulayın.';
  }
  if (data.email_error_code === 'E_SENDER_NOT_VERIFIED' || data.email_error_code === 'E_SENDER_DOMAIN_NOT_AVAILABLE') {
    return 'Gönderici alan adı Cloudflare Email Service için hazır değil. reyliar.xyz Email Sending kurulumunu ve no-reply adresini kontrol edin.';
  }
  return fallback || 'E-posta kodu gönderilemedi.';
}

async function sendVerificationCode(options) {
  options = options || {};
  try {
    const res = await apiFetch('/api/auth/verification/send', { method: 'POST' });
    const data = await res.json();
    if (!res.ok || !data.success) throw new Error(emailDeliveryFailureMessage(data, 'Kod gönderilemedi.'));
    setEmailCodeStatus('Kod gönderildi. E-postandaki 6 haneyi gir veya yapıştır.', 'success');
    if (!options.quiet) showToast('success', 'Kod gönderildi', 'E-postana gönderdiğimiz 6 haneli kodu gir.', 5200);
    return true;
  } catch(e) {
    setEmailCodeStatus(e.message || 'Kod gönderilemedi.', 'error');
    if (!options.quiet) showToast('warning', 'Kod gönderilemedi', e.message || 'E-postana kod gönderemedik.', 5600);
    return false;
  }
}

async function resendEmailCode() {
  if (_emailCodeMode === 'email-change') {
    try {
      setEmailCodeStatus('Yeni kod gönderiliyor...', '');
      const res = await apiFetch('/api/auth/email-change/send', { method: 'POST' });
      const data = await res.json();
      if (!res.ok || !data.success) throw new Error(emailDeliveryFailureMessage(data, 'Kod gönderilemedi.'));
      _emailCodeTargetEmail = data.pending_email || _emailCodeTargetEmail;
      const target = document.getElementById('emailCodeTarget');
      if (target) target.textContent = _emailCodeTargetEmail ? 'Hedef: ' + _emailCodeTargetEmail : '';
      clearEmailCodeCells();
      setEmailCodeStatus('Yeni kod gönderildi. 6 haneyi gir veya yapıştır.', 'success');
      focusFirstEmptyEmailCodeCell();
    } catch(e) {
      setEmailCodeStatus(e.message || 'Kod gönderilemedi.', 'error');
      showToast('warning', 'Kod gönderilemedi', e.message || 'E-postana kod gönderemedik.', 5600);
    }
    return;
  }
  clearEmailCodeCells();
  await sendVerificationCode({ quiet: false });
  focusFirstEmptyEmailCodeCell();
}

async function confirmEmailCodeFromModal() {
  const code = getEmailCodeValue();
  if (!/^\d{6}$/.test(code)) {
    setEmailCodeStatus('6 haneli güvenlik kodunu tamamla.', 'error');
    return;
  }
  if (_emailCodeSubmitting) return;
  _emailCodeSubmitting = true;
  const submit = document.getElementById('emailCodeSubmitBtn');
  if (submit) submit.disabled = true;
  setEmailCodeStatus('Kod doğrulanıyor...', '');
  try {
    const endpoint = _emailCodeMode === 'email-change' ? '/api/auth/email-change/confirm' : '/api/auth/verification/confirm';
    const res = await apiFetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: code })
    });
    const data = await res.json();
    if (!res.ok || !data.success) throw new Error(data.error || 'Doğrulama tamamlanamadı.');
    _accountUser = data.user || _accountUser;
    _pendingProfileAvatarDataUrl = _accountUser.avatar_data_url || '';
    const emailInput = document.getElementById('settingsEmail');
    if (emailInput) emailInput.value = _accountUser.email || '';
    updateAccountUI();
    updateVerificationPanel();
    setEmailCodeStatus('Onaylandı. Hesap bilgilerin güncellendi.', 'success');
    showToast('success', _emailCodeMode === 'email-change' ? 'E-posta değiştirildi' : 'E-posta doğrulandı', 'Hesabın güvenlik doğrulaması tamamlandı.', 3600);
    setTimeout(function() {
      closeEmailCodeModal();
      closeProfileSettings();
    }, 420);
  } catch(e) {
    _emailCodeSubmitting = false;
    clearEmailCodeCells();
    setEmailCodeStatus(e.message || 'Kod hatalı veya süresi dolmuş.', 'error');
    focusFirstEmptyEmailCodeCell();
  } finally {
    if (submit) submit.disabled = false;
  }
}

function openAdminTools() {
  if (!accountIsAdmin()) {
    showToast('warning', 'Yönetici hesabı gerekli', 'Bu işlem sadece yönetici hesabı ile yapılabilir.', 4500);
    return;
  }
  closeAccountMenu();
  const overlay = document.getElementById('adminToolsOverlay');
  if (overlay) overlay.classList.add('active');
  renderAdminTools({ loading: true });
  loadAdminTools();
}

function closeAdminTools() {
  const overlay = document.getElementById('adminToolsOverlay');
  if (overlay) overlay.classList.remove('active');
}

async function loadAdminTools() {
  try {
    const res = await apiFetch('/api/admin/accounts', { cache: 'no-store' });
    const data = await res.json();
    if (!res.ok || !data.success) throw new Error(data.error || 'Admin araçları yüklenemedi.');
    renderAdminTools(data);
  } catch(e) {
    renderAdminTools({ error: e.message || 'Bağlantı hatası.' });
  }
}

function renderAdminTools(data) {
  const statsEl = document.getElementById('adminStats');
  const listEl = document.getElementById('adminAccountsList');
  const sensitiveEl = document.getElementById('adminSensitiveList');
  if (!statsEl || !listEl) return;
  if (sensitiveEl) sensitiveEl.innerHTML = '';
  if (data && data.loading) {
    statsEl.innerHTML = '<div class="admin-stat"><div class="admin-stat-value">...</div><div class="admin-stat-label">Yükleniyor</div></div>';
    listEl.innerHTML = '';
    return;
  }
  if (data && data.error) {
    statsEl.innerHTML = '';
    listEl.innerHTML = '<div class="admin-account-row"><div class="admin-account-main"><div class="admin-account-name">Admin araçları açılamadı</div><div class="admin-account-meta">' + escHtml(data.error) + '</div></div></div>';
    return;
  }
  const stats = data.stats || {};
  const statItems = [
    ['Toplam hesap', stats.total_accounts || 0],
    ['Doğrulanmış', stats.verified_accounts || 0],
    ['Aktif oturum', stats.active_sessions || 0],
    ['Admin', stats.admin_accounts || 0]
  ];
  statsEl.innerHTML = statItems.map(function(item) {
    return '<div class="admin-stat"><div class="admin-stat-value">' + escHtml(item[1]) + '</div><div class="admin-stat-label">' + escHtml(item[0]) + '</div></div>';
  }).join('');
  const accounts = Array.isArray(data.accounts) ? data.accounts : [];
  listEl.innerHTML = accounts.map(function(account) {
    const roles = renderAccountBadges(account.roles).replace(/account-role-badge/g, 'admin-mini-badge');
    const verified = account.email_verified ? '<span class="admin-mini-badge good">Doğrulandı</span>' : '<span class="admin-mini-badge warn">Doğrulanmadı</span>';
    const lastLogin = account.last_login_at ? formatAdminDate(account.last_login_at) : 'Yok';
    const lastSeen = account.last_seen_at ? formatAdminDate(account.last_seen_at) : 'Yok';
    const ip = account.last_login_ip || account.session_ip || 'Bilinmiyor';
    return '<div class="admin-account-row">' +
      '<div class="admin-account-main">' +
        '<div class="admin-account-name">' + escHtml(account.display_name || 'Hesap') + '</div>' +
        '<div class="admin-account-meta">' + escHtml(account.email || '') + '<br>IP: ' + escHtml(ip) + ' · Son giriş: ' + escHtml(lastLogin) + ' · Son görülme: ' + escHtml(lastSeen) + '</div>' +
      '</div>' +
      '<div class="admin-account-flags">' + roles + verified + '<span class="admin-mini-badge">' + escHtml(account.session_count || 0) + ' oturum</span></div>' +
    '</div>';
  }).join('') || '<div class="admin-account-row"><div class="admin-account-main"><div class="admin-account-name">Hesap yok</div><div class="admin-account-meta">Henüz listelenecek kullanıcı bulunamadı.</div></div></div>';
}

function unlockAdminSensitive() {
  requireAuth(function(turnstileToken) {
    loadAdminSensitive(turnstileToken);
  }, true, { directToken: true });
}

async function loadAdminSensitive(turnstileToken) {
  const target = document.getElementById('adminSensitiveList');
  if (!target) return;
  target.innerHTML = '<div class="admin-sensitive-card"><div class="admin-sensitive-title">Gizli detaylar yükleniyor...</div></div>';
  try {
    const res = await apiFetch('/api/admin/accounts/sensitive', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ turnstile_token: turnstileToken || '' })
    });
    const data = await res.json();
    if (!res.ok || !data.success) throw new Error(data.error || 'Gizli detaylar açılamadı.');
    renderAdminSensitive(data.accounts || []);
  } catch(e) {
    target.innerHTML = '<div class="admin-sensitive-card"><div class="admin-sensitive-title">Gizli detaylar açılamadı</div><div class="admin-account-meta">' + escHtml(e.message || 'Bağlantı hatası.') + '</div></div>';
  } finally {}
}

function renderAdminSensitive(accounts) {
  const target = document.getElementById('adminSensitiveList');
  if (!target) return;
  target.innerHTML = (accounts || []).map(function(account) {
    const storage = account.password_storage || {};
    const sessions = Array.isArray(account.sessions) ? account.sessions : [];
    const sessionHtml = sessions.slice(0, 8).map(function(session) {
      const device = describeDevice(session.user_agent || '');
      const status = session.active ? 'Aktif' : 'Süresi dolmuş';
      return '<div class="admin-session-row">' +
        '<strong>' + escHtml(status) + '</strong> · IP: ' + escHtml(session.ip_address || 'Bilinmiyor') +
        '<br>' + escHtml(device) +
        '<br>Son görülme: ' + escHtml(session.last_seen_at ? formatAdminDate(session.last_seen_at) : 'Yok') +
        ' · Oluşturulma: ' + escHtml(session.created_at ? formatAdminDate(session.created_at) : 'Yok') +
      '</div>';
    }).join('') || '<div class="admin-session-row">Kayıtlı cihaz oturumu yok.</div>';
    return '<div class="admin-sensitive-card">' +
      '<div class="admin-sensitive-title">' + escHtml(account.display_name || 'Hesap') + ' · ' + escHtml(account.email || '') + '</div>' +
      '<div class="admin-sensitive-grid">' +
        sensitiveField('Şifre', storage.note || 'Şifreler korunuyor.') +
        sensitiveField('Hash algoritması', (storage.algorithm || 'unknown') + ' · ' + escHtml(storage.iterations || 0) + ' iterasyon') +
        sensitiveField('Hash parmak izi', storage.fingerprint || 'Yok') +
        sensitiveField('Şifre güncelleme', account.password_updated_at ? formatAdminDate(account.password_updated_at) : 'Yok') +
        sensitiveField('Son giriş IP', account.last_login_ip || 'Bilinmiyor') +
        sensitiveField('Son giriş', account.last_login_at ? formatAdminDate(account.last_login_at) : 'Yok') +
      '</div>' +
      '<div class="admin-session-list">' + sessionHtml + '</div>' +
    '</div>';
  }).join('') || '<div class="admin-sensitive-card"><div class="admin-sensitive-title">Gizli detay yok</div></div>';
}

function sensitiveField(label, value) {
  return '<div class="admin-sensitive-field"><div class="admin-sensitive-label">' + escHtml(label) + '</div><div class="admin-sensitive-value">' + escHtml(value) + '</div></div>';
}

function describeDevice(userAgent) {
  const ua = String(userAgent || '');
  if (!ua) return 'User-Agent yok';
  const os = /Windows/i.test(ua) ? 'Windows' : (/Mac OS|Macintosh/i.test(ua) ? 'macOS' : (/Android/i.test(ua) ? 'Android' : (/iPhone|iPad/i.test(ua) ? 'iOS' : (/Linux/i.test(ua) ? 'Linux' : 'Bilinmeyen OS'))));
  const browser = /Edg\//i.test(ua) ? 'Edge' : (/Chrome\//i.test(ua) ? 'Chrome' : (/Firefox\//i.test(ua) ? 'Firefox' : (/Safari\//i.test(ua) ? 'Safari' : 'Bilinmeyen tarayıcı')));
  return browser + ' · ' + os + ' · ' + ua.slice(0, 180);
}

function formatAdminDate(value) {
  try {
    return new Date(value).toLocaleString('tr-TR', { dateStyle: 'short', timeStyle: 'short' });
  } catch(e) {
    return String(value || '');
  }
}

async function logoutAccount() {
  const token = getAppAuthToken();
  document.body.classList.remove('app-ready');
  closeAccountMenu();
  resetAccountScopedState();
  try {
    if (token) {
      await fetch('/api/auth/logout', {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + token }
      });
    }
  } catch(e) {}
  clearAppAuthToken();
  _accountUser = null;
  _appStarted = false;
  updateAccountUI();
  showAccountAuth();
  showToast('info', 'Çıkış yapıldı', 'Bu cihazdaki oturum kapatıldı.', 2800);
}

async function bootApp() {
  showLoadingOverlay('Güvenli oturum kontrol ediliyor...');
  await loadAccountAuthConfig();
  const token = getAppAuthToken();
  if (!token) {
    showAccountAuth();
    hideLoadingOverlay(false);
    return;
  }
  try {
    const res = await apiFetch('/api/auth/me', { cache: 'no-store' });
    const data = await res.json();
    if (!res.ok || !data.success || !data.user) throw new Error(data.error || 'Oturum doğrulanamadı.');
    _accountUser = data.user;
    updateAccountUI();
    setLoadingStatus('Kütüphane ve sohbet geçmişi yükleniyor...');
    startPresenceAutomation();
    await startApp();
    hideLoadingOverlay(true);
  } catch(e) {
    clearAppAuthToken();
    _accountUser = null;
    resetPresenceAutomation();
    updateAccountUI();
    showAccountAuth();
    hideLoadingOverlay(false);
  }
}

function makeClientId(prefix) {
  if (window.crypto && typeof window.crypto.randomUUID === 'function') {
    return prefix + '-' + window.crypto.randomUUID();
  }
  return prefix + '-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
}

function getBookKey(book) {
  return (book && (book.book_id || book.drive_id)) || '';
}

function getBookTitle(book) {
  return (book && (book.title || book.name)) || 'Kitap';
}

function registerLibraryBooks(books) {
  if (!Array.isArray(books)) return;
  books.forEach(function(book) {
    if (!book) return;
    [book.book_id, book.drive_id].forEach(function(id) {
      if (id) _libraryBookCache[id] = book;
    });
  });
}

function getCoverUrlForBook(book) {
  if (book && book.cover_data_url) return book.cover_data_url;
  if (book && book.cover_url) return book.cover_url;
  const bookId = getBookKey(book);
  if (!bookId) return '';
  const version = encodeURIComponent(book.cover_updated_at || book.updated_at || book.added_at || '');
  return '/api/cover/' + encodeURIComponent(bookId) + (version ? '?v=' + version : '');
}

function normalizeClientChatStore(rawStore) {
  const chats = rawStore && Array.isArray(rawStore.chats) ? rawStore.chats : [];
  return {
    chats: chats.filter(function(chat) { return chat && chat.id; }).map(function(chat) {
      chat.messages = Array.isArray(chat.messages) ? chat.messages.filter(function(message) {
        return message && (message.role === 'user' || message.role === 'ai') && String(message.text || '').trim();
      }).map(function(message) {
        message.id = message.id || makeClientId('msg');
        message.text = String(message.text || '');
        message.created_at = message.created_at || new Date().toISOString();
        return message;
      }) : [];
      chat.created_at = chat.created_at || new Date().toISOString();
      chat.updated_at = chat.updated_at || chat.created_at;
      return chat;
    })
  };
}

function getChatHistoryKey() {
  const owner = _accountUser && (_accountUser.id || _accountUser.email);
  return CHAT_HISTORY_BASE_KEY + ':' + (owner || 'guest');
}

function getLocalChatStore() {
  try {
    const raw = localStorage.getItem(getChatHistoryKey());
    const parsed = raw ? JSON.parse(raw) : null;
    return normalizeClientChatStore(parsed);
  } catch(e) {
    return { chats: [] };
  }
}

function sortChatStore() {
  _chatStore.chats.sort(function(a, b) {
    return String(b.updated_at || '').localeCompare(String(a.updated_at || ''));
  });
}

function writeLocalChatStore() {
  try {
    localStorage.setItem(getChatHistoryKey(), JSON.stringify(_chatStore));
  } catch(e) {}
}

function mergeChatStores(primaryStore, secondaryStore) {
  const byId = {};
  [primaryStore, secondaryStore].forEach(function(store) {
    normalizeClientChatStore(store).chats.forEach(function(chat) {
      const existing = byId[chat.id];
      if (!existing || String(chat.updated_at || '').localeCompare(String(existing.updated_at || '')) >= 0) {
        byId[chat.id] = chat;
      }
    });
  });
  _chatStore = { chats: Object.keys(byId).map(function(id) { return byId[id]; }) };
  sortChatStore();
  return _chatStore;
}

function loadChatStore() {
  if (!_chatStoreLoaded) {
    _chatStore = getLocalChatStore();
    _chatStoreLoaded = true;
  }
  sortChatStore();
  return _chatStore;
}

async function loadChatStoreFromServer() {
  if (_chatStoreLoadPromise) return _chatStoreLoadPromise;
  const localStore = getLocalChatStore();
  _chatStoreLoadPromise = (async function() {
    try {
      const res = await apiFetch('/api/chat_history', { cache: 'no-store' });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const serverStore = normalizeClientChatStore(await res.json());
      const beforeMerge = JSON.stringify(serverStore);
      mergeChatStores(serverStore, localStore);
      _chatStoreLoaded = true;
      writeLocalChatStore();
      if (JSON.stringify(_chatStore) !== beforeMerge) {
        saveChatStore({ immediate: true });
      }
    } catch(e) {
      if (!_chatStoreLoaded || localStore.chats.length) {
        _chatStore = localStore;
      }
      _chatStoreLoaded = true;
    } finally {
      _chatStoreLoadPromise = null;
    }
    return _chatStore;
  })();
  return _chatStoreLoadPromise;
}

function persistChatStoreToServer() {
  const payload = JSON.stringify(_chatStore);
  apiFetch('/api/chat_history', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: payload
  }).catch(function() {});
}

function saveChatStore(options) {
  options = options || {};
  sortChatStore();
  writeLocalChatStore();
  if (options.skipRemote) return;
  clearTimeout(_chatStoreSaveTimer);
  if (options.immediate) {
    persistChatStoreToServer();
    return;
  }
  try {
    _chatStoreSaveTimer = setTimeout(persistChatStoreToServer, 220);
  } catch(e) {}
}

function getActiveChat() {
  return _chatStore.chats.find(function(chat) { return chat.id === _activeChatId; }) || null;
}

function makeFallbackChatTitle(prompt) {
  const clean = String(prompt || '')
    .replace(/[`*_>#\[\]()]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  if (!clean) return 'Yeni sohbet';
  return clean.length > 54 ? clean.slice(0, 51).trim() + '...' : clean;
}

function normalizeChatTitle(title, prompt) {
  const clean = String(title || '')
    .replace(/[`*_>#\[\]()"]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  return clean || makeFallbackChatTitle(prompt);
}

function createChatForBook(book) {
  const now = new Date().toISOString();
  const chat = {
    id: makeClientId('chat'),
    book_id: getBookKey(book),
    book_title: getBookTitle(book),
    book_cover: book && (book.cover_data_url || book.cover_url || book.cover_path) ? (book.cover_data_url || book.cover_url || book.cover_path) : '',
    drive_id: (book && book.drive_id) || '',
    book_grade: (book && book.grade) || selectedGrade || '',
    title: 'Yeni sohbet',
    messages: [],
    created_at: now,
    updated_at: now
  };
  _chatStore.chats.unshift(chat);
  saveChatStore();
  return chat;
}

function getLatestChatForBook(book) {
  const bookId = getBookKey(book);
  if (!bookId) return null;
  return _chatStore.chats
    .filter(function(chat) { return chat.book_id === bookId; })
    .sort(function(a, b) { return String(b.updated_at || '').localeCompare(String(a.updated_at || '')); })[0] || null;
}

function ensureActiveChatForSelectedBook() {
  loadChatStore();
  let chat = getActiveChat();
  const currentBookId = getBookKey(selectedBook);
  if (chat && (!currentBookId || chat.book_id === currentBookId)) return chat;
  chat = getLatestChatForBook(selectedBook) || createChatForBook(selectedBook);
  _activeChatId = chat.id;
  saveChatStore();
  return chat;
}

function getChatHistoryForApi(chat, options) {
  if (!chat || !Array.isArray(chat.messages)) return [];
  options = options || {};
  let messages = chat.messages.slice();
  if (options.beforeMessageId) {
    const index = messages.findIndex(function(message) { return message.id === options.beforeMessageId; });
    if (index >= 0) messages = messages.slice(0, index);
  }
  return messages.slice(-10).map(function(message) {
    return { role: message.role, text: message.text };
  });
}

function addChatMessage(role, text, options) {
  options = options || {};
  const chat = ensureActiveChatForSelectedBook();
  const message = {
    id: options.id || makeClientId('msg'),
    role: role,
    text: String(text || ''),
    created_at: options.created_at || new Date().toISOString()
  };
  chat.messages.push(message);
  chat.updated_at = new Date().toISOString();
  if ((!chat.title || chat.title === 'Yeni sohbet') && role === 'user') {
    chat.title = makeFallbackChatTitle(text);
  }
  saveChatStore({ immediate: true });
  renderChatHistory();
  return message;
}

function updateActiveChatTitle(title, prompt) {
  const chat = getActiveChat();
  if (!chat) return;
  chat.title = normalizeChatTitle(title, prompt);
  chat.updated_at = new Date().toISOString();
  saveChatStore({ immediate: true });
  renderChatHistory();
}
let _libraryLoadSeq = 0;
let _gradeSwitchTimer = null;

// ── Auth ────────────────────────────────────────────────────────────────────────
function getAdminAuthToken() { return sessionStorage.getItem('admin_auth_token') || ''; }
function isAdminAuthed() { return !!getAdminAuthToken(); }

var _authCallback = null;
var _authSubmitting = false;
var _authPassTokenDirect = false;
function requireAuth(callback, forcePrompt, options) {
  options = options || {};
  if (!accountIsAdmin()) {
    showToast('warning', 'Yönetici hesabı gerekli', 'Bu işlem sadece yönetici hesabı ile yapılabilir.', 4500);
    return;
  }
  if (!options.directToken && !forcePrompt && isAdminAuthed()) { callback(); return; }
  _abortPrefetches();
  _authCallback = callback;
  _authSubmitting = false;
  _authPassTokenDirect = !!options.directToken;
  document.getElementById('authError').textContent = '';
  document.getElementById('authOverlay').classList.add('active');
  renderAdminTurnstile();
}
function closeAuth() {
  document.getElementById('authOverlay').classList.remove('active');
  _authCallback = null;
  _authSubmitting = false;
  _authPassTokenDirect = false;
  resetAdminTurnstile();
}
function runAuthedCallback(callback, value) {
  if (typeof callback !== 'function') return;
  setTimeout(function() {
    try {
      callback(value);
    } catch(e) {
      console.error('Auth callback failed:', e);
      showToast('error', 'İşlem Açılamadı', 'Doğrulama tamamlandı ama işlem başlatılamadı. Tekrar deneyin.', 5000);
    }
  }, 0);
}
async function submitAuth() {
  if (!_adminTurnstileToken || _authSubmitting) return;
  _authSubmitting = true;
  var errEl = document.getElementById('authError');
  errEl.textContent = '';
  updateAdminVerifyState();
  try {
    var cb = _authCallback;
    var token = _adminTurnstileToken;
    if (_authPassTokenDirect) {
      closeAuth();
      runAuthedCallback(cb, token);
      return;
    }
    var res = await fetch('/api/verify_password', {
      method: 'POST',
      headers: Object.assign({ 'Content-Type': 'application/json' }, authHeaders()),
      body: JSON.stringify({ turnstile_token: token })
    });
    var data = await res.json();
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    if (data.success && data.token) {
      sessionStorage.setItem('admin_auth_token', data.token);
      closeAuth();
      runAuthedCallback(cb);
    } else {
      errEl.textContent = data.error || 'Cloudflare doğrulaması tamamlanamadı.';
      resetAdminTurnstile();
    }
  } catch(e) {
    errEl.textContent = e.message || 'Ba\u011flant\u0131 hatas\u0131.';
    resetAdminTurnstile();
  } finally {
    _authSubmitting = false;
    updateAdminVerifyState();
  }
}
function authHeaders() {
  var headers = { 'X-Auth-Token': getAdminAuthToken() };
  var token = getAppAuthToken();
  if (token) headers.Authorization = 'Bearer ' + token;
  return headers;
}
function authFormHeaders() {
  return getAdminAuthToken();
}

// ── Rename ──────────────────────────────────────────────────────────────────────
function setRenameCoverPreview(src) {
  const preview = document.getElementById('renameCoverPreview');
  if (!preview) return;
  preview.innerHTML = '';
  if (src) {
    const img = document.createElement('img');
    img.src = src;
    img.alt = 'kapak';
    img.onerror = function() { preview.innerHTML = '&#128196;'; };
    preview.appendChild(img);
  } else {
    preview.innerHTML = '&#128196;';
  }
}

function openRename(bookId, currentName) {
  requireAuth(function() {
    _renameBookId = bookId;
    _renameCoverFile = null;
    document.getElementById('renameInput').value = currentName;
    document.getElementById('renameCoverInput').value = '';
    document.getElementById('renameCoverNote').textContent = 'JPG, PNG veya WebP dosyası seçebilirsin.';
    setRenameCoverPreview(bookId ? ('/api/cover/' + encodeURIComponent(bookId) + '?t=' + Date.now()) : '');
    document.getElementById('renameOverlay').classList.add('active');
    setTimeout(function(){ document.getElementById('renameInput').focus(); }, 100);
  });
}
function closeRename() {
  document.getElementById('renameOverlay').classList.remove('active');
  _renameBookId = '';
  _renameCoverFile = null;
  var input = document.getElementById('renameCoverInput');
  if (input) input.value = '';
}

function openRenameCoverPicker() {
  var input = document.getElementById('renameCoverInput');
  if (input) input.click();
}

function handleRenameCoverChange(event) {
  var file = (event.target.files || [])[0];
  if (!file) return;
  if (!/^image\/(jpeg|png|webp)$/.test(file.type)) {
    showToast('warning', 'Dosya Desteklenmiyor', 'JPG, PNG veya WebP formatında bir görsel seçin.', 4500);
    event.target.value = '';
    return;
  }
  _renameCoverFile = file;
  document.getElementById('renameCoverNote').textContent = file.name;
  var reader = new FileReader();
  reader.onload = function(e) { setRenameCoverPreview(e.target.result); };
  reader.readAsDataURL(file);
}

function imageFileToElement(file) {
  return new Promise(function(resolve, reject) {
    var url = URL.createObjectURL(file);
    var img = new Image();
    img.onload = function() {
      URL.revokeObjectURL(url);
      resolve(img);
    };
    img.onerror = function() {
      URL.revokeObjectURL(url);
      reject(new Error('Kapak görseli okunamadı.'));
    };
    img.src = url;
  });
}

function canvasToBlob(canvas, type, quality) {
  return new Promise(function(resolve, reject) {
    canvas.toBlob(function(blob) {
      if (blob) resolve(blob);
      else reject(new Error('Kapak görseli hazırlanamadı.'));
    }, type, quality);
  });
}

async function prepareRenameCoverUpload(file) {
  if (!file) return null;
  if (file.type === 'image/jpeg' && file.size <= 640 * 1024) return file;
  var img = await imageFileToElement(file);
  var variants = [
    { maxW: 1100, maxH: 1500, quality: 0.82 },
    { maxW: 900, maxH: 1200, quality: 0.78 },
    { maxW: 720, maxH: 960, quality: 0.72 }
  ];
  var lastBlob = null;
  for (var i = 0; i < variants.length; i++) {
    var item = variants[i];
    var scale = Math.min(1, item.maxW / img.naturalWidth, item.maxH / img.naturalHeight);
    var width = Math.max(1, Math.round(img.naturalWidth * scale));
    var height = Math.max(1, Math.round(img.naturalHeight * scale));
    var canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    var ctx = canvas.getContext('2d');
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, width, height);
    ctx.drawImage(img, 0, 0, width, height);
    lastBlob = await canvasToBlob(canvas, 'image/jpeg', item.quality);
    if (lastBlob.size <= 640 * 1024) break;
  }
  if (!lastBlob) return file;
  var baseName = String(file.name || 'cover').replace(/\.[^.]+$/, '') || 'cover';
  return new File([lastBlob], baseName + '.jpg', { type: 'image/jpeg' });
}

async function uploadRenameCover() {
  if (!_renameCoverFile || !_renameBookId) return true;
  var fd = new FormData();
  var uploadFile = await prepareRenameCoverUpload(_renameCoverFile);
  fd.append('book_id', _renameBookId);
  fd.append('cover', uploadFile || _renameCoverFile);
  var res = await fetch('/api/update_cover', {
    method: 'POST',
    headers: authHeaders(),
    body: fd
  });
  var data = await res.json();
  if (!data.success) {
    if (data.auth === false) {
      sessionStorage.removeItem('admin_auth_token');
    }
    throw new Error(data.error || 'Thumbnail güncellenemedi.');
  }
  return true;
}
async function submitRename() {
  var name = document.getElementById('renameInput').value.trim();
  if (!name && !_renameCoverFile) return;
  try {
    if (name) {
      var res = await fetch('/api/rename_book', {
        method: 'POST',
        headers: Object.assign({ 'Content-Type': 'application/json' }, authHeaders()),
        body: JSON.stringify({ book_id: _renameBookId, name: name })
      });
      var data = await res.json();
      if (!data.success) {
        if (data.auth === false) {
          sessionStorage.removeItem('admin_auth_token');
          var savedId = _renameBookId;
          closeRename();
          openRename(savedId, name);
          return;
        }
        throw new Error(data.error || 'Bir hata oluştu.');
      }
    }
    await uploadRenameCover();
    closeRename();
    loadLibrary();
    showToast('info', 'Kitap Güncellendi', 'Kitap bilgileri başarıyla değiştirildi.', 3000);
  } catch(e) {
    if (e.message === 'Yetkilendirme gerekli.') {
      var savedId2 = _renameBookId;
      closeRename();
      openRename(savedId2, name);
      return;
    }
    showToast('error', 'Hata', e.message || 'Bağlantı hatası.', 5000);
  }
}

// ── Merkez bildirimleri ───────────────────────────────────────────────────────
let _scanTaskPollTimer = null;
let _typingVisibleSince = 0;
let _typingHideTimer = null;
let _analysisStatusPollTimer = null;
let _activeAnalysisId = '';

function dmInitials(user) {
  const source = String((user && (user.display_name || user.email)) || 'R').trim();
  return (source[0] || 'R').toLocaleUpperCase('tr-TR');
}

function dmAvatarHtml(user) {
  const src = user && user.avatar_data_url;
  if (src) return '<img src="' + escHtml(src) + '" alt="">';
  return escHtml(dmInitials(user));
}

function dmUserName(user) {
  return String((user && user.display_name) || 'Hesap');
}

function dmPresenceStatus(user) {
  const status = String((user && (user.effective_presence || user.presence_status)) || 'offline').toLowerCase();
  return ['online', 'idle', 'dnd', 'invisible', 'offline'].indexOf(status) !== -1 ? status : 'offline';
}

function dmPresenceLabel(user) {
  const status = dmPresenceStatus(user);
  if (status === 'online') return 'Çevrimiçi';
  if (status === 'idle') return 'Boşta';
  if (status === 'dnd') return 'Rahatsız etmeyin';
  if (status === 'invisible') return 'Görünmez';
  return 'Çevrimdışı';
}

function dmAvatarWithPresenceHtml(user) {
  const status = dmPresenceStatus(user);
  return '<div class="dm-avatar-wrap"><div class="dm-avatar">' + dmAvatarHtml(user || {}) + '</div><span class="dm-presence-dot ' + escHtml(status) + '"></span></div>';
}

function dmSnippet(message) {
  if (!message) return 'Henüz mesaj yok.';
  if (message.forward) return 'İletilen AI mesajı';
  if (message.attachment) return message.attachment.name || 'Dosya';
  return String(message.body || '').replace(/\s+/g, ' ').trim() || 'Mesaj';
}

function dmThreadForUser(userId) {
  const targetId = String(userId || '');
  return _dmThreads.find(function(thread) { return thread.user && String(thread.user.id || '') === targetId; }) || null;
}

function dmUserById(userId) {
  const targetId = String(userId || '');
  const thread = dmThreadForUser(userId);
  if (thread && thread.user) return thread.user;
  return _dmUsers.find(function(user) { return String(user.id || '') === targetId; }) || null;
}

function setDmBadge(count) {
  const badge = document.getElementById('dmHeaderBadge');
  if (badge) badge.textContent = count > 0 ? String(Math.min(count, 99)) : '';
}

function cancelDmMessageLoad() {
  if (_dmMessageAbortController) {
    try { _dmMessageAbortController.abort(); } catch(e) {}
    _dmMessageAbortController = null;
  }
}

function isCurrentDmConversation(userId, seq) {
  const expectedUserId = String(userId || '');
  return !!expectedUserId &&
    _dmActiveUserId === expectedUserId &&
    (!seq || seq === _dmConversationSeq);
}

async function fetchDmThreads(options) {
  options = options || {};
  const res = await apiFetch('/api/dm/threads', { cache: 'no-store' });
  const data = await res.json();
  if (!res.ok || !data.success) throw new Error(data.error || 'Mesajlar yüklenemedi.');
  _dmThreads = Array.isArray(data.threads) ? data.threads : [];
  const unread = _dmThreads.reduce(function(sum, thread) { return sum + Number(thread.unread_count || 0); }, 0);
  setDmBadge(unread);
  if (options.seed) {
    _dmKnownLatestIds = {};
    _dmThreads.forEach(function(thread) {
      if (thread.user && thread.latest_message) _dmKnownLatestIds[thread.user.id] = thread.latest_message.id;
    });
    _dmInitialPollDone = true;
  }
  return _dmThreads;
}

async function loadDmUsers() {
  const res = await apiFetch('/api/dm/users', { cache: 'no-store' });
  const data = await res.json();
  if (!res.ok || !data.success) throw new Error(data.error || 'Kişi listesi yüklenemedi.');
  _dmUsers = Array.isArray(data.users) ? data.users : [];
  return _dmUsers;
}

async function loadDmPanelData(options) {
  options = options || {};
  await Promise.all([loadDmUsers(), fetchDmThreads(options)]);
  renderDmThreads();
}

function openDmOverlay(options) {
  options = options || {};
  if (!_accountUser) {
    showAccountAuth();
    return;
  }
  closeAccountMenu();
  const overlay = document.getElementById('dmOverlay');
  if (!overlay) return;
  if (options.forward) {
    _dmPendingForward = options.forward;
    updateDmPendingBar();
  }
  overlay.classList.add('active');
  if (!_dmActiveUserId) overlay.classList.remove('chat-open');
  const panelSeq = ++_dmPanelLoadSeq;
  loadDmPanelData().then(function() {
    if (panelSeq !== _dmPanelLoadSeq || !overlay.classList.contains('active')) return;
    if (options.user_id) openDmConversation(options.user_id);
  }).catch(function(e) {
    if (panelSeq !== _dmPanelLoadSeq) return;
    showToast('error', 'Mesajlar açılamadı', e.message || 'Bağlantı hatası.', 5200);
  });
  setTimeout(function() {
    const search = document.getElementById('dmSearch');
    if (search && !_dmActiveUserId) search.focus();
  }, 120);
}

function closeDmOverlay() {
  const overlay = document.getElementById('dmOverlay');
  if (overlay) overlay.classList.remove('active', 'chat-open');
  _dmConversationSeq++;
  _dmPanelLoadSeq++;
  cancelDmMessageLoad();
}

function showDmPeople() {
  const overlay = document.getElementById('dmOverlay');
  if (overlay) overlay.classList.remove('chat-open');
  _dmConversationSeq++;
  cancelDmMessageLoad();
}

function renderDmThreads() {
  const list = document.getElementById('dmThreadList');
  if (!list) return;
  const query = String((document.getElementById('dmSearch') || {}).value || '').toLocaleLowerCase('tr-TR').trim();
  const seen = {};
  const rows = [];
  _dmThreads.forEach(function(thread) {
    if (thread.user && !seen[thread.user.id]) {
      seen[thread.user.id] = true;
      rows.push({ user: thread.user, thread: thread });
    }
  });
  _dmUsers.forEach(function(user) {
    if (user && !seen[user.id]) {
      seen[user.id] = true;
      rows.push({ user: user, thread: null });
    }
  });
  const filtered = rows.filter(function(row) {
    if (!query) return true;
    return (dmUserName(row.user) + ' ' + (row.user.email || '')).toLocaleLowerCase('tr-TR').indexOf(query) !== -1;
  });
  list.innerHTML = '';
  if (!filtered.length) {
    list.innerHTML = '<div class="dm-empty-state">Eşleşen hesap bulunamadı.</div>';
    return;
  }
  const fragment = document.createDocumentFragment();
  filtered.forEach(function(row) {
    const thread = row.thread || {};
    const latest = thread.latest_message || null;
    const rowUserId = String(row.user.id || '');
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'dm-thread' + (rowUserId === _dmActiveUserId ? ' active' : '');
    btn.onclick = function() { openDmConversation(rowUserId); };
    btn.innerHTML =
      dmAvatarWithPresenceHtml(row.user) +
      '<div style="min-width:0">' +
        '<div class="dm-thread-name">' + escHtml(dmUserName(row.user)) + '</div>' +
        '<div class="dm-thread-snippet">' + escHtml(dmSnippet(latest)) + '</div>' +
        '<div class="dm-chat-subtitle">' + escHtml(dmPresenceLabel(row.user)) + '</div>' +
      '</div>' +
      (thread.unread_count ? '<span class="dm-unread">' + escHtml(Math.min(Number(thread.unread_count || 0), 99)) + '</span>' : '<span class="dm-time">' + escHtml(latest ? formatChatTime(latest.created_at) : '') + '</span>');
    fragment.appendChild(btn);
  });
  list.appendChild(fragment);
}

async function openDmConversation(userId) {
  const targetUserId = String(userId || '');
  if (!targetUserId) return;
  const conversationSeq = ++_dmConversationSeq;
  _dmActiveUserId = targetUserId;
  _dmMessages = [];
  _dmMessagesUserId = targetUserId;
  const overlay = document.getElementById('dmOverlay');
  if (overlay) overlay.classList.add('chat-open');
  const chat = document.querySelector('.dm-chat');
  if (chat) {
    chat.classList.remove('switching');
    void chat.offsetWidth;
    chat.classList.add('switching');
    setTimeout(function(){ chat.classList.remove('switching'); }, 340);
  }
  renderDmThreads();
  const user = dmUserById(targetUserId);
  renderDmHeader(user);
  await loadDmMessages(targetUserId, { seq: conversationSeq });
  if (!isCurrentDmConversation(targetUserId, conversationSeq)) return;
  const input = document.getElementById('dmTextInput');
  if (input) setTimeout(function(){
    if (isCurrentDmConversation(targetUserId, conversationSeq)) input.focus();
  }, 80);
}

function renderDmHeader(user) {
  const host = document.getElementById('dmChatUser');
  if (!host) return;
  host.innerHTML =
    dmAvatarWithPresenceHtml(user || {}) +
    '<div style="min-width:0">' +
      '<div class="dm-chat-title">' + escHtml(user ? dmUserName(user) : 'Bir konuşma seç') + '</div>' +
      '<div class="dm-chat-subtitle">' + escHtml(user ? dmPresenceLabel(user) : 'Kayıtlı hesaplar burada görünür.') + '</div>' +
    '</div>';
}

async function loadDmMessages(userId, options) {
  options = options || {};
  const targetUserId = String(userId || '');
  if (!targetUserId) return false;
  const seq = options.seq || ++_dmConversationSeq;
  if (!isCurrentDmConversation(targetUserId, seq)) return false;
  cancelDmMessageLoad();
  const controller = new AbortController();
  _dmMessageAbortController = controller;
  const list = document.getElementById('dmMessageList');
  if (list && !options.silent) list.innerHTML = '<div class="dm-empty-state">Mesajlar yükleniyor...</div>';
  try {
    const res = await apiFetch('/api/dm/messages?user_id=' + encodeURIComponent(targetUserId), { cache: 'no-store', signal: controller.signal });
    const data = await res.json();
    if (!res.ok || !data.success) throw new Error(data.error || 'Mesajlar yüklenemedi.');
    if (!isCurrentDmConversation(targetUserId, seq) || controller.signal.aborted) return false;
    _dmMessages = Array.isArray(data.messages) ? data.messages : [];
    _dmMessagesUserId = targetUserId;
    renderDmMessages(targetUserId);
    await fetchDmThreads();
    if (isCurrentDmConversation(targetUserId, seq)) renderDmThreads();
    return true;
  } catch(e) {
    if (e && e.name === 'AbortError') return false;
    if (!isCurrentDmConversation(targetUserId, seq)) return false;
    if (list) list.innerHTML = '<div class="dm-empty-state">Mesajlar yüklenemedi.</div>';
    showToast('error', 'Mesajlar yüklenemedi', e.message || 'Bağlantı hatası.', 5200);
    return false;
  } finally {
    if (_dmMessageAbortController === controller) _dmMessageAbortController = null;
  }
}

function renderDmMessages(userId) {
  const expectedUserId = String(userId || _dmMessagesUserId || _dmActiveUserId || '');
  if (expectedUserId && _dmActiveUserId && expectedUserId !== _dmActiveUserId) return;
  const list = document.getElementById('dmMessageList');
  if (!list) return;
  list.innerHTML = '';
  if (!_dmMessages.length) {
    list.innerHTML = '<div class="dm-empty-state">İlk mesajı gönder.</div>';
    return;
  }
  const fragment = document.createDocumentFragment();
  _dmMessages.forEach(function(message) {
    fragment.appendChild(renderDmMessage(message));
  });
  list.appendChild(fragment);
  list.scrollTop = list.scrollHeight;
}

function renderDmMessage(message) {
  const wrap = document.createElement('div');
  wrap.className = 'dm-message ' + (message.outgoing ? 'out' : 'in');
  const bubble = document.createElement('div');
  bubble.className = 'dm-bubble';
  const body = String(message.body || '').trim();
  if (body) {
    const text = document.createElement('div');
    text.textContent = body;
    bubble.appendChild(text);
  }
  if (message.forward) bubble.appendChild(renderDmForward(message.forward));
  if (message.attachment) bubble.appendChild(renderDmAttachment(message));
  if (!body && !message.forward && !message.attachment) bubble.textContent = 'Mesaj';
  const time = document.createElement('div');
  time.className = 'dm-time';
  time.textContent = formatChatTime(message.created_at || '');
  wrap.appendChild(bubble);
  wrap.appendChild(time);
  return wrap;
}

function renderDmForward(forward) {
  const card = document.createElement('div');
  card.className = 'dm-forward-card';
  const label = document.createElement('div');
  label.className = 'dm-forward-label';
  label.textContent = 'AI mesajı iletildi' + (forward.book_title ? ' · ' + String(forward.book_title) : '');
  const text = document.createElement('div');
  text.className = 'dm-forward-text message-body';
  renderMarkdown(text, String(forward.text || '').slice(0, 9000));
  card.appendChild(label);
  card.appendChild(text);
  return card;
}

function renderDmAttachment(message) {
  const attachment = message.attachment || {};
  const card = document.createElement('div');
  card.className = 'dm-attachment-card';
  const mime = String(attachment.mime_type || '');
  if (mime.indexOf('image/') === 0) {
    card.innerHTML = '<img src="' + escHtml(attachment.data_url || '') + '" alt="' + escHtml(attachment.name || 'görsel') + '">';
    return card;
  }
  if (mime.indexOf('audio/') === 0) {
    card.innerHTML = '<div class="dm-attachment-name">Ses dosyası</div><audio controls src="' + escHtml(attachment.data_url || '') + '"></audio>';
    return card;
  }
  card.innerHTML =
    '<div class="dm-attachment-name">' + escHtml(attachment.name || 'Dosya') + '</div>' +
    '<div class="dm-attachment-meta">' + escHtml(formatBytes(Number(attachment.size || 0))) + '</div>' +
    '<a class="account-menu-btn" download="' + escHtml(attachment.name || 'dosya') + '" href="' + escHtml(attachment.data_url || '') + '">İndir</a>';
  return card;
}

function updateDmPendingBar() {
  const bar = document.getElementById('dmPendingBar');
  const text = document.getElementById('dmPendingText');
  if (!bar || !text) return;
  const parts = [];
  if (_dmPendingForward) parts.push('AI mesajı iletilecek');
  if (_dmPendingAttachment) parts.push((_dmPendingAttachment.name || 'Dosya') + ' hazır');
  text.textContent = parts.join(' · ');
  bar.classList.toggle('active', parts.length > 0);
}

function clearDmPending() {
  _dmPendingAttachment = null;
  _dmPendingForward = null;
  updateDmPendingBar();
}

function autoResizeDmInput() {
  const input = document.getElementById('dmTextInput');
  if (!input) return;
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 120) + 'px';
}

function fileToDataUrl(file) {
  return new Promise(function(resolve, reject) {
    const reader = new FileReader();
    reader.onload = function() { resolve(String(reader.result || '')); };
    reader.onerror = function() { reject(new Error('Dosya okunamadı.')); };
    reader.readAsDataURL(file);
  });
}

async function handleDmFileSelect(file) {
  if (!file) return;
  if (file.size > 650000) {
    showToast('warning', 'Dosya büyük', 'DM ekleri için 650 KB altında bir dosya seç.', 4200);
    return;
  }
  try {
    const dataUrl = await fileToDataUrl(file);
    _dmPendingAttachment = {
      data_url: dataUrl,
      name: file.name || 'dosya',
      mime_type: file.type || 'application/octet-stream',
      size: file.size || 0,
      kind: 'file'
    };
    updateDmPendingBar();
  } catch(e) {
    showToast('error', 'Dosya okunamadı', e.message || 'Dosyayı tekrar seç.', 4200);
  }
}

function setDmSendingState(sending) {
  _dmSending = !!sending;
  const sendBtn = document.getElementById('dmSendBtn');
  const fileBtn = document.querySelector('.dm-tool-btn');
  const input = document.getElementById('dmTextInput');
  if (sendBtn) {
    sendBtn.disabled = _dmSending;
    sendBtn.classList.toggle('sending', _dmSending);
    sendBtn.setAttribute('aria-busy', _dmSending ? 'true' : 'false');
  }
  if (fileBtn) fileBtn.disabled = _dmSending;
  if (input) input.setAttribute('aria-busy', _dmSending ? 'true' : 'false');
}

async function sendDmMessage() {
  if (_dmSending) return;
  const recipientId = String(_dmActiveUserId || '');
  if (!recipientId) {
    showToast('warning', 'Kişi seç', 'Mesaj göndermek için önce bir hesap seç.', 3200);
    return;
  }
  const input = document.getElementById('dmTextInput');
  const body = String(input && input.value || '').trim();
  if (!body && !_dmPendingAttachment && !_dmPendingForward) {
    showToast('warning', 'Mesaj boş', 'Bir metin, dosya ya da iletilen AI mesajı ekle.', 3400);
    return;
  }
  const payload = {
    recipient_id: recipientId,
    body: body,
    attachment: _dmPendingAttachment,
    forward: _dmPendingForward,
    client_id: makeClientId('dm')
  };
  setDmSendingState(true);
  try {
    const res = await apiFetch('/api/dm/messages', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!res.ok || !data.success) throw new Error(data.error || 'Mesaj gönderilemedi.');
    if (input) {
      input.value = '';
      autoResizeDmInput();
    }
    clearDmPending();
    if (data.message && _dmActiveUserId === recipientId) {
      _dmMessages.push(data.message);
      _dmMessagesUserId = recipientId;
      renderDmMessages(recipientId);
    }
    fetchDmThreads().then(renderDmThreads).catch(function() {});
  } catch(e) {
    showToast('error', 'Mesaj gönderilemedi', e.message || 'Bağlantı hatası.', 5200);
  } finally {
    setDmSendingState(false);
  }
}

function forwardMessageToDm(messageId) {
  const found = findActiveChatMessage(messageId);
  if (!found || !found.message) return;
  _dmPendingForward = {
    source_role: found.message.role || 'ai',
    text: found.message.text || '',
    book_title: selectedBook ? getBookTitle(selectedBook) : '',
    created_at: found.message.created_at || new Date().toISOString()
  };
  updateDmPendingBar();
  openDmOverlay({ forward: _dmPendingForward });
  showToast('info', 'DM’ye ilet', 'Bir kişi seçip gönder düğmesine bas.', 3600);
}

async function pollDmNotifications() {
  if (!_accountUser) return;
  try {
    const previous = Object.assign({}, _dmKnownLatestIds);
    await fetchDmThreads();
    let newIncoming = null;
    _dmThreads.forEach(function(thread) {
      if (!thread.user || !thread.latest_message) return;
      const userId = thread.user.id;
      const latest = thread.latest_message;
      if (_dmInitialPollDone && previous[userId] && previous[userId] !== latest.id && !latest.outgoing) {
        newIncoming = { thread: thread, latest: latest };
      }
      _dmKnownLatestIds[userId] = latest.id;
    });
    _dmInitialPollDone = true;
    renderDmThreads();
    if (newIncoming) {
      const activeOverlay = document.getElementById('dmOverlay');
      const sameOpen = activeOverlay && activeOverlay.classList.contains('active') && _dmActiveUserId === newIncoming.thread.user.id;
      if (sameOpen) loadDmMessages(_dmActiveUserId);
      else showToast('info', 'Yeni mesaj', dmUserName(newIncoming.thread.user) + ': ' + dmSnippet(newIncoming.latest), 5200);
    }
  } catch(e) {}
}

function startDmPolling() {
  clearInterval(_dmPollTimer);
  _dmInitialPollDone = false;
  fetchDmThreads({ seed: true }).catch(function() {});
  _dmPollTimer = setInterval(pollDmNotifications, 12000);
}

function stopDmPolling() {
  clearInterval(_dmPollTimer);
  _dmPollTimer = null;
  _dmKnownLatestIds = {};
  _dmInitialPollDone = false;
  setDmBadge(0);
}

function buildToastIcon(type) {
  if (type === 'success') {
    return '<span class="toast-icon success"><svg viewBox="0 0 52 52" aria-hidden="true"><circle class="checkmark-circle" cx="26" cy="26" r="25"></circle><path class="checkmark-check" d="M14 27.5l8 8 16-18"></path></svg></span>';
  }
  if (type === 'warning') {
    return '<span class="toast-icon warning"><svg viewBox="0 0 52 52" aria-hidden="true"><circle cx="26" cy="26" r="25" fill="rgba(251,191,36,0.12)" stroke="rgba(251,191,36,0.36)" stroke-width="2"></circle><path d="M26 14v16" stroke="currentColor" stroke-width="3.2" stroke-linecap="round"/><circle cx="26" cy="37" r="2.4" fill="currentColor"/></svg></span>';
  }
  if (type === 'error') {
    return '<span class="toast-icon error"><svg viewBox="0 0 52 52" aria-hidden="true"><circle cx="26" cy="26" r="25" fill="rgba(248,113,113,0.12)" stroke="rgba(248,113,113,0.36)" stroke-width="2"></circle><path d="M18 18l16 16M34 18L18 34" stroke="currentColor" stroke-width="3.2" stroke-linecap="round"/></svg></span>';
  }
  return '<span class="toast-icon info"><svg viewBox="0 0 52 52" aria-hidden="true"><circle cx="26" cy="26" r="25" fill="rgba(37,99,235,0.12)" stroke="rgba(96,165,250,0.36)" stroke-width="2"></circle><path d="M26 23v12" stroke="currentColor" stroke-width="3.2" stroke-linecap="round"/><circle cx="26" cy="16" r="2.5" fill="currentColor"/></svg></span>';
}

function showToast(type, title, message, duration) {
  duration = duration || 4200;
  const c = document.getElementById('toastContainer');
  const t = document.createElement('div');
  t.className = 'toast ' + type;
  t.innerHTML =
    buildToastIcon(type) +
    '<div class="toast-body"><div class="toast-title">' + escHtml(title) + '</div>' +
    '<div class="toast-msg">' + escHtml(message) + '</div></div>' +
    '<button class="toast-close" onclick="dismissToast(this.parentElement)">\u2715</button>' +
    '<div class="toast-progress" style="animation-duration:' + duration + 'ms"></div>';
  c.appendChild(t);
  setTimeout(function(){ dismissToast(t); }, duration);
}

function dismissToast(el) {
  if (!el || el.classList.contains('leaving')) return;
  el.classList.add('leaving');
  setTimeout(function(){ el.remove(); }, 300);
}

function showResponseBanner() {
  const banner = document.getElementById('responseBanner');
  if (!banner) return;
  banner.classList.remove('active');
  void banner.offsetWidth;
  banner.classList.add('active');
}

function openScanTaskOverlay() {
  const overlay = document.getElementById('scanTaskOverlay');
  overlay.classList.remove('done');
  overlay.classList.add('active');
}

function closeScanTaskOverlay() {
  clearTimeout(_scanTaskPollTimer);
  const overlay = document.getElementById('scanTaskOverlay');
  if (overlay.classList.contains('done')) {
    overlay.classList.remove('active');
  }
}

async function cancelMissingScans() {
  try {
    const res = await fetch('/api/scan_missing_books_cancel', { method: 'POST' });
    const data = await res.json();
    if (!data.success) {
      throw new Error(data.error || 'İptal isteği gönderilemedi.');
    }
    renderScanTask(data.job || {});
    if ((data.job || {}).running) {
      pollMissingScans();
    }
    showToast('info', 'İptal Ediliyor', 'Mevcut kitap tamamlandıktan sonra tarama duracak.', 3600);
  } catch (e) {
    showToast('error', 'İptal Edilemedi', e.message, 4200);
  }
}

function dismissScanTaskOverlay() {
  const overlay = document.getElementById('scanTaskOverlay');
  if (overlay.classList.contains('done')) {
    closeScanTaskOverlay();
    return;
  }
  cancelMissingScans();
}

function renderScanTask(job) {
  const total = Number(job.total || 0);
  const processed = Number(job.processed || 0);
  const success = Number(job.success || 0);
  const failed = Number(job.failed || 0);
  const alreadyReady = Number(job.already_ready || 0);
  const progress = total > 0 ? Math.min(100, Math.round(processed / total * 100)) : (job.completed ? 100 : 0);
  const running = !!job.running;
  const cancelRequested = !!job.cancel_requested;
  const cancelled = !!job.cancelled;

  document.getElementById('scanTaskStatus').textContent = job.current_message || 'Hazırlanıyor…';
  document.getElementById('scanTaskProgress').style.width = progress + '%';
  document.getElementById('scanTaskTotal').textContent = String(total);
  document.getElementById('scanTaskDone').textContent = String(processed);
  document.getElementById('scanTaskReady').textContent = String(success + alreadyReady);
  document.getElementById('scanTaskFailed').textContent = String(failed);

  const log = document.getElementById('scanTaskLog');
  log.innerHTML = '';
  (job.logs || []).slice().reverse().forEach(function(item) {
    const row = document.createElement('div');
    row.className = 'task-log-item';
    row.textContent = item;
    log.appendChild(row);
  });

  const overlay = document.getElementById('scanTaskOverlay');
  const scanBtn = document.getElementById('scanAllBtn');
  const dismissBtn = document.getElementById('scanTaskDismissBtn');
  const cancelNote = document.getElementById('scanTaskCancelNote');
  const actions = document.getElementById('scanTaskActions');
  if (scanBtn) scanBtn.disabled = !!job.running;
  if (dismissBtn) {
    dismissBtn.disabled = running && cancelRequested;
    dismissBtn.title = running ? (cancelRequested ? 'İptal bekleniyor' : 'Taramayı iptal et') : 'Pencereyi kapat';
    dismissBtn.setAttribute('aria-label', dismissBtn.title);
  }
  if (cancelNote) cancelNote.hidden = !running || !cancelRequested;
  if (actions) actions.classList.toggle('cancel-pending', !!running && !!cancelRequested);

  if (job.completed) {
    overlay.classList.add('done');
  } else {
    overlay.classList.remove('done');
  }
  overlay.classList.toggle('cancelled', cancelled);
}

async function pollMissingScans() {
  clearTimeout(_scanTaskPollTimer);
  try {
    const res = await fetch('/api/scan_missing_books_status');
    const job = await res.json();
    renderScanTask(job);
    if (job.running) {
      _scanTaskPollTimer = setTimeout(pollMissingScans, 900);
      return;
    }
    if (job.completed) {
      loadLibrary();
      const failed = Number(job.failed || 0);
      const cancelled = !!job.cancelled;
      showToast(
        cancelled ? 'info' : (failed ? 'warning' : 'success'),
        cancelled ? 'Tarama İptal Edildi' : (failed ? 'Tarama tamamlandı' : 'Kitaplar hazır'),
        job.current_message || (
          cancelled
            ? 'Tarama kullanıcı isteğiyle durduruldu.'
            : (failed ? 'Bazı kitaplarda sorun oluştu.' : 'Tüm uygun kitaplar analiz için hazır.')
        ),
        4200
      );
    }
  } catch(e) {
    renderScanTask({
      total: 0,
      processed: 0,
      success: 0,
      failed: 1,
      already_ready: 0,
      current_message: 'Tarama durumu alınamadı.',
      logs: ['Tarama durumu alınamadı: ' + e.message],
      completed: true
    });
    showToast('error', 'Tarama Hatası', 'Tarama durumu alınamadı: ' + e.message, 5000);
  }
}

async function startMissingScans() {
  openScanTaskOverlay();
  renderScanTask({
    total: 0,
    processed: 0,
    success: 0,
    failed: 0,
    already_ready: 0,
    current_message: 'Eksik taramalar bulunuyor…',
    logs: ['Eksik taramalar kontrol ediliyor…'],
    completed: false,
    running: true,
    cancel_requested: false,
    cancelled: false
  });
  try {
    const res = await fetch('/api/scan_missing_books', { method: 'POST' });
    const data = await res.json();
    if (!data.success) {
      throw new Error(data.error || 'Tarama işi başlatılamadı.');
    }
    renderScanTask(data.job || {});
    pollMissingScans();
  } catch(e) {
    renderScanTask({
      total: 0,
      processed: 0,
      success: 0,
      failed: 1,
      already_ready: 0,
      current_message: 'Tarama başlatılamadı.',
      logs: ['Tarama başlatılamadı: ' + e.message],
      completed: true,
      running: false
    });
    showToast('error', 'Tarama Başlatılamadı', e.message, 5200);
  }
}

// ── Grade selector ─────────────────────────────────────────────────────────────
function selectGrade(grade) {
  const grid = document.getElementById('bookGrid');
  if (grade === selectedGrade) {
    if (grid && (grid.classList.contains('fading') || grid.children.length === 0)) {
      clearTimeout(_gradeSwitchTimer);
      loadLibrary(true);
    }
    return;
  }
  selectedGrade = grade;
  document.querySelectorAll('.grade-btn').forEach(function(b) {
    b.classList.toggle('active', b.dataset.grade === grade);
  });
  var searchEl = document.getElementById('bookSearch');
  if (searchEl) searchEl.value = '';
  clearLibraryTransientMessages();
  if (grid) grid.classList.add('fading');
  setLibStatus(grade + '. sınıf yükleniyor...', 'green');
  _abortPrefetches();
  clearTimeout(_gradeSwitchTimer);
  _gradeSwitchTimer = setTimeout(function(){ loadLibrary(true); }, 180);
}

// ── Library ────────────────────────────────────────────────────────────────────
function clearLibraryTransientMessages() {
  var searchMsg = document.getElementById('searchEmpty');
  if (searchMsg) searchMsg.remove();
}

async function loadLibrary(fromFade) {
  const loadSeq = ++_libraryLoadSeq;
  const grade = selectedGrade;
  const grid  = document.getElementById('bookGrid');
  if (!grid) return;
  clearLibraryTransientMessages();
  try {
    const res = await fetch('/api/library?grade=' + encodeURIComponent(grade), { cache: 'no-store' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const books = await res.json();
    if (loadSeq !== _libraryLoadSeq || grade !== selectedGrade) return;
    if (!Array.isArray(books)) throw new Error('Kütüphane verisi okunamadı.');
    registerLibraryBooks(books);

    grid.innerHTML = '';

    if (books.length === 0) {
      grid.innerHTML =
        '<div class="empty-state">' +
        '<img src="{{ books_stack_src }}" class="empty-books-img" alt="Kitaplar">' +
        '<h3>' + grade + '. S\u0131n\u0131f i\u00e7in hen\u00fcz kitap yok</h3>' +
        '<p>PDF kitap eklemek i\u00e7in a\u015fa\u011f\u0131daki butonu kullan\u0131n.</p>' +
        '<button class="sync-btn-empty" id="emptySync" onclick="openPdfPicker()">' +
        '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>' +
        'Kitap Yükle' +
        '</button>' +
        '</div>';
      setLibStatus('Hazır', 'green');
      return;
    }

    const fragment = document.createDocumentFragment();

    books.forEach(function(book, i) {
      const card = document.createElement('div');
      card.className = 'book-card';
      card.dataset.driveId = book.drive_id || '';
      card.dataset.bookId  = book.book_id || book.drive_id || '';
      card.setAttribute('role', 'button');
      card.tabIndex = 0;

      const rawDisplayName = book.title || book.name || 'Kitap';
      const displayName = escHtml(rawDisplayName);
      card.setAttribute('aria-label', rawDisplayName + ' kitabını aç');

      let coverHtml;
      var bookKeyId = book.book_id || book.drive_id || '';
      if (bookKeyId) {
        var cardCoverUrl = getCoverUrlForBook(book);
        coverHtml = '<div class="card-cover" style="position:relative;font-size:48px">📄' +
          '<img src="' + cardCoverUrl + '" alt="" onerror="this.remove()" ' +
          'loading="lazy" decoding="async" ' +
          'style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover;border-radius:inherit"></div>';
      } else {
        coverHtml = '<div class="card-cover" style="font-size:48px">📄</div>';
      }

      const delBtn = document.createElement('button');
      delBtn.className = 'card-del-btn';
      delBtn.title = 'Sil';
      delBtn.dataset.bookId = book.book_id || book.drive_id || '';
      delBtn.dataset.name   = book.title || book.name || '';
      delBtn.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4h6v2"/></svg>';

      var syncBadge = '';
      if (book.pdf_url) {
        syncBadge = '<div class="sync-badge-local" title="Kitap deposundan">' +
          '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M4 4.5A2.5 2.5 0 0 1 6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5z"/></svg>' +
          'Ders Kitabı</div>';
      } else if (!book.drive_id) {
        syncBadge = '<div class="sync-badge-local" title="Sadece cihazda">' +
          '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>' +
          'Yerel</div>';
      }

      const editBtn = document.createElement('button');
      editBtn.className = 'card-edit-btn';
      editBtn.title = '\u0130smi d\u00fczenle';
      editBtn.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>';

      card.innerHTML = coverHtml +
        '<div class="card-name">' + displayName + '</div>' +
        '<div class="card-meta-row">' + syncBadge + scanBadgeHtml(book) + '</div>';
      card.appendChild(delBtn);
      card.appendChild(editBtn);

      card.addEventListener('click', function(e) {
        if (e.target.closest('.card-del-btn') || e.target.closest('.card-edit-btn')) return;
        openAnalysis(book);
      });
      card.addEventListener('keydown', function(e) {
        if (e.target.closest('.card-del-btn') || e.target.closest('.card-edit-btn')) return;
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          openAnalysis(book);
        }
      });
      delBtn.addEventListener('click', function(e) {
        e.stopPropagation();
        requireAuth(function(){ showDelConfirm(delBtn.dataset.bookId, delBtn.dataset.name); });
      });
      editBtn.addEventListener('click', function(e) {
        e.stopPropagation();
        openRename(bookKeyId, book.title || book.name || '');
      });

      setTimeout(function(){ card.classList.add('visible'); }, Math.min(i * 28, 360));
      fragment.appendChild(card);
    });
    grid.appendChild(fragment);

    setLibStatus('Hazır', 'green');
    scheduleScanPoll(books);
    prefetchPdfs(books);
  } catch(e) {
    console.error('loadLibrary fetch failed:', e);
    if (loadSeq === _libraryLoadSeq) {
      grid.innerHTML =
        '<div class="empty-state">' +
        '<img src="{{ books_stack_src }}" class="empty-books-img" alt="Kitaplar">' +
        '<h3>K\u00fct\u00fcphane y\u00fcklenemedi</h3>' +
        '<p>S\u0131n\u0131f de\u011fi\u015fiminde veri al\u0131namad\u0131. Tekrar deneyin.</p>' +
        '<button class="sync-btn-empty" onclick="loadLibrary(true)">Tekrar Dene</button>' +
        '</div>';
      setLibStatus('Kütüphane yüklenemedi.', 'red');
    }
  } finally {
    if (loadSeq === _libraryLoadSeq) {
      requestAnimationFrame(function(){ grid.classList.remove('fading'); });
    }
  }
}

var _prefetchCache = {};
var _prefetchControllers = [];
var _prefetchToken = 0;
function _isPdfViewerActive() {
  var overlay = document.getElementById('pdfViewerOverlay');
  return !!(overlay && overlay.classList.contains('active'));
}
function prefetchPdfs(books) {
  return;
}
function _abortPrefetches() {
  _prefetchToken++;
  _prefetchControllers.forEach(function(c) { try { c.abort(); } catch(e){} });
  _prefetchControllers = [];
}

function prefetchAllGrades() {
  return;
}

var _filterBooksFrame = 0;
var _filterBooksQuery = '';
function filterBooks(query) {
  _filterBooksQuery = String(query || '');
  if (_filterBooksFrame) cancelAnimationFrame(_filterBooksFrame);
  _filterBooksFrame = requestAnimationFrame(applyBookFilter);
}

function applyBookFilter() {
  _filterBooksFrame = 0;
  var q = _filterBooksQuery.toLowerCase().trim();
  var cards = document.querySelectorAll('.book-card');
  var visible = 0;
  cards.forEach(function(card) {
    var match = name_of(card).indexOf(q) !== -1;
    card.style.display = match ? '' : 'none';
    if (match) visible++;
  });
  var msg = document.getElementById('searchEmpty');
  if (!q || visible > 0) {
    if (msg) msg.classList.remove('show');
  } else {
    if (!msg) {
      var d = document.createElement('div');
      d.id = 'searchEmpty';
      d.className = 'search-empty';
      document.getElementById('bookGrid').parentNode.appendChild(d);
      msg = d;
    }
    msg.innerHTML = '<svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" style="opacity:0.5"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="8" y1="8" x2="14" y2="14"/><line x1="14" y1="8" x2="8" y2="14"/></svg>' +
      '<span>\u201c' + escHtml(query) + '\u201d ile e\u015fle\u015fen kitap bulunamad\u0131.</span>';
    requestAnimationFrame(function(){ msg.classList.add('show'); });
  }
}
function name_of(card) {
  return ((card.querySelector('.card-name') || {}).textContent || '').toLowerCase();
}

function scanBadgeHtml(book) {
  const s = book.scan_status;
  if (!s || s === 'unavailable') return '';
  if (s === 'pending') return '<div class="scan-badge pending"><div class="scan-dot spin"></div>Taranıyor</div>';
  if (s === 'done') {
    const pg = book.scan_pages ? book.scan_pages + ' sayfa' : 'Tarand\u0131';
    const isBasic = book.scan_extractor === 'basic';
    const extractorTitle = isBasic ? 'Yerel hızlı tarama ile tarandı' : 'Yerel PDF metin çıkarma ile tarandı';
    return '<div class="scan-badge done" title="' + extractorTitle + '">' +
      '<div class="scan-dot"></div>' + pg + '</div>';
  }
  if (s === 'failed') return '<div class="scan-badge failed"><div class="scan-dot"></div>Tarama ba\u015far\u0131s\u0131z</div>';
  return '';
}

let _scanPollTimer = null;
function scheduleScanPoll(books) {
  clearTimeout(_scanPollTimer);
  const pending = books.filter(function(b){ return b.scan_status === 'pending' && (b.book_id || b.drive_id); });
  if (!pending.length) return;
  _scanPollTimer = setTimeout(async function() {
    for (const book of pending) {
      try {
        const bid = book.book_id || book.drive_id;
        const r = await fetch('/api/scan_status/' + bid);
        const d = await r.json();
        if (d.scan_status !== 'pending') {
          book.scan_status = d.scan_status;
          book.scan_pages  = d.scan_pages;
          book.scan_extractor = d.scan_extractor || book.scan_extractor || '';
          const bookId = book.book_id || book.drive_id || '';
          const card = document.querySelector('.book-card[data-book-id="' + bookId + '"]');
          if (card) {
            const old = card.querySelector('.scan-badge');
            if (old) old.remove();
            card.insertAdjacentHTML('beforeend', scanBadgeHtml(book));
          }
        }
      } catch(e) {}
    }
    const stillPending = pending.filter(function(b){ return b.scan_status === 'pending'; });
    if (stillPending.length) scheduleScanPoll(stillPending);
  }, 4000);
}

// ── Sync ───────────────────────────────────────────────────────────────────────
async function syncManual() {
  if (!navigator.onLine) {
    showToast('warning', '\u00c7evrimi\u00e7i De\u011fil',
      'Cloud\\u2019a aktar\u0131m i\u00e7in internet ba\u011flant\u0131s\u0131 gerekli.', 6000);
    return;
  }
  const btn       = document.getElementById('syncBtn');
  const emptySync = document.getElementById('emptySync');
  [btn, emptySync].forEach(function(b){ if (b) { b.disabled = true; } });
  if (btn) btn.classList.add('syncing');
  setLibStatus('Cloud\\u2019a aktar\u0131l\u0131yor...', 'amber');
  try {
    const res  = await fetch('/api/sync_cloud', { method: 'POST', headers: authHeaders() });
    const data = await res.json();
    if (data.success) {
      const n = data.uploaded || 0;
      if (n > 0) {
        setLibStatus(n + ' kitap Cloud\\u2019a aktar\u0131ld\u0131.', 'green');
        showToast('success', 'Aktar\u0131m Tamamland\u0131',
          n + ' kitap ba\u015far\u0131yla Cloud\\u2019a y\u00fcklendi.', 5000);
        loadLibrary();
      } else {
        setLibStatus('T\u00fcm kitaplar zaten Cloud\\u2019da.', 'green');
      }
      if (data.errors && data.errors.length > 0) {
        showToast('warning', 'Baz\u0131 Hatalar', data.errors.slice(0,3).join('; '), 7000);
      }
    } else if (data.auth === false) {
      sessionStorage.removeItem('admin_auth_token');
      setLibStatus('Yetkilendirme gerekli.', 'amber');
      requireAuth(function(){ syncManual(); });
    } else if (data.skipped) {
      setLibStatus('Cloud ba\u011flant\u0131s\u0131 yap\u0131land\u0131r\u0131lmam\u0131\u015f.', 'amber');
      showToast('warning', 'Cloud Yap\u0131land\u0131r\u0131lmam\u0131\u015f',
        'GAS_WEB_APP_URL ortam de\u011fi\u015fkeni hen\u00fcz ayarlanmam\u0131\u015f.', 7000);
    } else {
      setLibStatus('Aktar\u0131m hatas\u0131.', 'red');
      showToast('error', 'Aktar\u0131m Ba\u015far\u0131s\u0131z', data.error || '', 6000);
    }
  } catch(e) {
    setLibStatus('Ba\u011flant\u0131 hatas\u0131.', 'red');
    showToast('error', 'Ba\u011flant\u0131 Hatas\u0131', e.message, 6000);
  }
  if (btn) { btn.disabled = false; btn.classList.remove('syncing'); }
  if (emptySync) emptySync.disabled = false;
}

async function syncSilent() {
  // No-op in local-first mode — sync is manual push only
}

// ── Local PDF upload handler ──────────────────────────────────────────────────
function formatBytes(bytes) {
  if (bytes < 1024)        return bytes + ' B';
  if (bytes < 1048576)     return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1048576).toFixed(2) + ' MB';
}

function formatSpeed(bps) {
  if (bps < 1024)        return Math.round(bps) + ' B/s';
  if (bps < 1048576)     return Math.round(bps / 1024) + ' KB/s';
  return (bps / 1048576).toFixed(1) + ' MB/s';
}

function showUploadOverlay() {
  document.getElementById('uploadOverlay').classList.add('active');
}
function hideUploadOverlay() {
  document.getElementById('uploadOverlay').classList.remove('active');
}
function setUploadUI(counter, filename, pct, speed, loaded, total, overallPct) {
  document.getElementById('uploadCounter').textContent  = counter;
  document.getElementById('uploadFilename').textContent = filename;
  document.getElementById('uploadBarFill').style.width  = pct + '%';
  document.getElementById('uploadPct').textContent      = pct + '%';
  document.getElementById('uploadSpeed').textContent    = speed;
  document.getElementById('uploadSize').textContent     = loaded ? (formatBytes(loaded) + ' / ' + formatBytes(total)) : '';
  document.getElementById('uploadOverallFill').style.width = overallPct + '%';
  document.getElementById('uploadOverallLabel').textContent = 'Genel: ' + Math.round(overallPct) + '%';
}

function uploadFileXHR(file, grade, onProgress) {
  return new Promise(function(resolve, reject) {
    var xhr    = new XMLHttpRequest();
    var t0     = Date.now();
    var last   = { loaded: 0, time: t0 };
    var smooth = 0;

    xhr.upload.onprogress = function(e) {
      if (!e.lengthComputable) return;
      var now  = Date.now();
      var dt   = (now - last.time) / 1000;
      var dL   = e.loaded - last.loaded;
      if (dt > 0.05) {
        var inst = dL / dt;
        smooth   = smooth === 0 ? inst : smooth * 0.7 + inst * 0.3;
        last     = { loaded: e.loaded, time: now };
      }
      var pct = Math.round(e.loaded / e.total * 100);
      onProgress(pct, smooth, e.loaded, e.total);
    };

    xhr.onload = function() {
      if (xhr.status === 200) {
        try { resolve(JSON.parse(xhr.responseText)); }
        catch(ex) { reject(new Error('Geçersiz yanıt')); }
      } else {
        reject(new Error('HTTP ' + xhr.status));
      }
    };
    xhr.onerror = function() { reject(new Error('Ağ hatası')); };

    var fd = new FormData();
    fd.append('file', file);
    fd.append('grade', grade);
    xhr.open('POST', '/api/upload');
    xhr.setRequestHeader('X-Auth-Token', getAdminAuthToken());
    xhr.send(fd);
  });
}

function openPdfPicker() {
  var fileInput = document.getElementById('pdfFileInput');
  if (fileInput) fileInput.click();
}

async function uploadSelectedFiles(allFiles, fileInput) {
  var files    = allFiles.filter(function(f){ return f.name.toLowerCase().endsWith('.pdf'); });
  var skipped  = allFiles.length - files.length;
  if (skipped > 0) {
    showToast('warning', 'Atlandı', skipped + ' dosya PDF değil, atlandı.', 4000);
  }
  if (!files.length) return;

  var grade        = selectedGrade || '9';
  var total        = files.length;
  var successCount = 0;
  var errors       = [];

  showUploadOverlay();
  setLibStatus('Yükleniyor...', 'amber');

  for (var i = 0; i < total; i++) {
    var file = files[i];
    var idx  = i;

    setUploadUI(
      (i + 1) + ' / ' + total + ' dosya',
      file.name,
      0, '0 KB/s', 0, file.size,
      Math.round(i / total * 100)
    );

    try {
      var data = await uploadFileXHR(file, grade, function(pct, speed, loaded, fTotal) {
        var overallPct = (idx + pct / 100) / total * 100;
        setUploadUI(
          (idx + 1) + ' / ' + total + ' dosya',
          file.name,
          pct,
          formatSpeed(speed),
          loaded, fTotal,
          overallPct
        );
      });

      if (data && data.success) {
        successCount++;
      } else {
        errors.push(file.name + ': ' + ((data && data.error) || 'Hata'));
      }
    } catch(err) {
      errors.push(file.name + ': ' + err.message);
    }
  }

  hideUploadOverlay();
  if (fileInput) fileInput.value = '';

  if (successCount > 0) {
    setLibStatus(successCount + ' kitap eklendi.', 'green');
    loadLibrary();
  } else {
    setLibStatus('Hazır', 'green');
  }
  if (errors.length > 0) {
    showToast('error', 'Yükleme Hatası', errors.slice(0, 3).join(' | '), 6000);
  }
}

document.addEventListener('DOMContentLoaded', function() {
  var fileInput = document.getElementById('pdfFileInput');
  if (!fileInput) return;
  fileInput.addEventListener('change', function(e) {
    var allFiles = Array.from(e.target.files || []);
    fileInput.value = '';
    if (!allFiles.length) return;
    requireAuth(function(){ uploadSelectedFiles(allFiles, fileInput); });
  });
});

// ── Network status ─────────────────────────────────────────────────────────────
var NET_ICONS = {
  wifi: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.55a11 11 0 0 1 14.08 0"/><path d="M1.42 9a16 16 0 0 1 21.16 0"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><line x1="12" y1="20" x2="12.01" y2="20"/></svg>',
  ethernet: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="8" width="20" height="8" rx="2"/><path d="M6 8V6"/><path d="M10 8V6"/><path d="M14 8V6"/><path d="M18 8V6"/><path d="M6 16v2"/><path d="M18 16v2"/></svg>',
  online: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>',
  offline: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="1" y1="1" x2="23" y2="23"/><path d="M16.72 11.06A10.94 10.94 0 0 1 19 12.55"/><path d="M5 12.55a10.94 10.94 0 0 1 5.17-2.39"/><path d="M10.71 5.05A16 16 0 0 1 22.56 9"/><path d="M1.42 9a15.91 15.91 0 0 1 4.7-2.88"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><line x1="12" y1="20" x2="12.01" y2="20"/></svg>'
};

function scrollToFooter() {
  var footer = document.querySelector('.lib-footer') || document.querySelector('footer');
  if (footer) footer.scrollIntoView({ behavior: 'smooth', block: 'end' });
}

function updateNetworkStatus() {
  var el   = document.getElementById('netIndicator');
  if (!el) return;
  var conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
  var type = conn ? (conn.type || '') : '';

  var dot = '<span class="net-dot"></span>';

  if (!navigator.onLine) {
    el.className  = 'net-indicator offline';
    el.innerHTML  = NET_ICONS.offline + dot;
    el.title      = '\u00c7evrimi\u015fi';
    el.onclick    = function() {
      showToast('warning', '\u0130nternet Yok',
        'Cloud klas\u00f6r\u00fcnden senkronizasyon i\u00e7in internet ba\u011flant\u0131s\u0131 gerekli.', 7000);
    };
    return;
  }

  if (type === 'ethernet') {
    el.className = 'net-indicator ethernet';
    el.innerHTML = NET_ICONS.ethernet + dot;
    el.title     = 'Ethernet';
  } else if (type === 'wifi') {
    el.className = 'net-indicator wifi';
    el.innerHTML = NET_ICONS.wifi + dot;
    el.title     = 'Wi-Fi';
  } else {
    el.className = 'net-indicator online';
    el.innerHTML = NET_ICONS.online + dot;
    el.title     = '\u00c7evrimi\u00e7i';
  }
  el.onclick = null;
}

window.addEventListener('online',  function(){ updateNetworkStatus(); });
window.addEventListener('offline', function(){
  updateNetworkStatus();
  showToast('warning', '\u0130nternet Ba\u011flant\u0131s\u0131 Kesildi',
    'Cloud senkronizasyonu \u00e7evrimd\u0131\u015f\u0131 oldu\u011funuz i\u00e7in \u015fu an m\u00fcmk\u00fcn de\u011fil.', 8000);
});
if (navigator.connection) {
  navigator.connection.addEventListener('change', updateNetworkStatus);
}

// ── Analysis Screen ────────────────────────────────────────────────────────────
function clearChat(options) {
  options = options || {};
  if (!options.keepEdit) resetEditState();
  const flow   = document.getElementById('chatFlow');
  const empty  = document.getElementById('chatEmpty');
  const typing = document.getElementById('typingIndicator');
  const banner = document.getElementById('responseBanner');
  flow.querySelectorAll('.chat-msg').forEach(function(el){ el.remove(); });
  if (empty)  empty.style.display = '';
  clearTimeout(_typingHideTimer);
  _typingVisibleSince = 0;
  if (typing) { typing.classList.remove('active'); flow.appendChild(typing); }
  if (banner) banner.classList.remove('active');
  const chips = document.getElementById('quickChips');
  if (chips) chips.classList.remove('hidden');
  if (!options.keepPrompt) {
    document.getElementById('promptInput').value = '';
    autoResizeTA(document.getElementById('promptInput'));
  }
}

function updateSelectedBookPanel(book) {
  const cover = document.getElementById('selectedCover');
  var coverUrl = '';
  if (book && book.book_id) {
    coverUrl = getCoverUrlForBook(book);
  } else if (book && book.drive_id) {
    coverUrl = getCoverUrlForBook(book) || ('https://drive.google.com/thumbnail?id=' + book.drive_id + '&sz=w400');
  }
  if (coverUrl) {
    var img = document.createElement('img');
    img.src = coverUrl;
    img.alt = 'kapak';
    img.style.cssText = 'width:100%;height:100%;object-fit:cover;border-radius:inherit';
    img.onerror = function() { cover.innerHTML = '<span style="font-size:52px">&#128196;</span>'; };
    cover.innerHTML = '';
    cover.appendChild(img);
  } else {
    cover.innerHTML = '<span style="font-size:52px">&#128196;</span>';
  }

  document.getElementById('selectedTitle').textContent = getBookTitle(book);
  var hasLocal = !!(book && (book.book_id || book.local_path || book.drive_id));
  document.getElementById('readBtn').style.display = hasLocal ? 'flex' : 'none';
  setAnalysisStatus('Hazır', 'green');
}

function formatChatTime(iso) {
  try {
    return new Date(iso || Date.now()).toLocaleString('tr-TR', {
      day: '2-digit',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit'
    });
  } catch(e) {
    return '';
  }
}

function renderChatHistory() {
  const list = document.getElementById('chatHistoryList');
  if (!list) return;
  loadChatStore();
  list.innerHTML = '';
  if (!_chatStore.chats.length) {
    const empty = document.createElement('div');
    empty.className = 'chat-history-empty';
    empty.textContent = 'Henüz kayıtlı sohbet yok. Bir soru sorduğunda burada kitap adıyla birlikte görünecek.';
    list.appendChild(empty);
    return;
  }

  _chatStore.chats.forEach(function(chat, index) {
    const last = (chat.messages || []).slice().reverse().find(function(msg) { return msg.text; });
    const item = document.createElement('div');
    item.setAttribute('role', 'button');
    item.tabIndex = 0;
    item.className = 'chat-history-item' + (chat.id === _activeChatId ? ' active' : '');
    item.style.animationDelay = Math.min(index * 28, 220) + 'ms';
    item.onclick = function() { openChatFromHistory(chat.id); };
    item.onkeydown = function(e) {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        openChatFromHistory(chat.id);
      }
    };

    const main = document.createElement('div');
    main.className = 'chat-history-main';

    const title = document.createElement('div');
    title.className = 'chat-history-title';
    title.textContent = chat.title || 'Yeni sohbet';

    const book = document.createElement('div');
    book.className = 'chat-history-book';
    book.textContent = chat.book_title || 'Kitap';

    const snippet = document.createElement('div');
    snippet.className = 'chat-history-snippet';
    snippet.textContent = last ? last.text : 'Konuşmaya devam et';

    const time = document.createElement('div');
    time.className = 'chat-history-time';
    time.textContent = formatChatTime(chat.updated_at || chat.created_at);

    main.appendChild(title);
    main.appendChild(book);
    main.appendChild(snippet);
    main.appendChild(time);

    const actions = document.createElement('div');
    actions.className = 'chat-history-actions';
    const cont = document.createElement('span');
    cont.className = 'chat-history-continue';
    cont.textContent = 'Devam';
    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'chat-history-delete';
    del.title = 'Sohbeti sil';
    del.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/></svg>';
    del.onclick = function(e) {
      e.stopPropagation();
      deleteChat(chat.id);
    };
    actions.appendChild(cont);
    actions.appendChild(del);

    item.appendChild(main);
    item.appendChild(actions);
    list.appendChild(item);
  });
}

function renderChatMessages(chat) {
  clearChat();
  if (!chat || !Array.isArray(chat.messages) || !chat.messages.length) return;
  const empty = document.getElementById('chatEmpty');
  const chips = document.getElementById('quickChips');
  if (empty) empty.style.display = 'none';
  if (chips) chips.classList.add('hidden');
  chat.messages.forEach(function(message) {
    if (!message.id) message.id = makeClientId('msg');
    if (message.role === 'user') appendUserMsg(message.text, { animate: false, messageId: message.id });
    else appendAiMsg(message.text, { animate: false, messageId: message.id });
  });
}

async function findLibraryBookForChat(chat) {
  const ids = [chat && chat.book_id, chat && chat.drive_id].filter(Boolean);
  for (const id of ids) {
    if (_libraryBookCache[id]) return _libraryBookCache[id];
  }

  const grades = [];
  if (chat && chat.book_grade) grades.push(chat.book_grade);
  ['9', '10'].forEach(function(grade) {
    if (grades.indexOf(grade) === -1) grades.push(grade);
  });

  for (const grade of grades) {
    try {
      const res = await fetch('/api/library?grade=' + encodeURIComponent(grade), { cache: 'no-store' });
      if (!res.ok) continue;
      const books = await res.json();
      registerLibraryBooks(books);
      for (const id of ids) {
        if (_libraryBookCache[id]) return _libraryBookCache[id];
      }
    } catch(e) {}
  }

  return {
    book_id: (chat && chat.book_id) || '',
    drive_id: (chat && chat.drive_id) || '',
    title: (chat && chat.book_title) || 'Kitap',
    name: (chat && chat.book_title) || 'Kitap',
    grade: (chat && chat.book_grade) || selectedGrade || ''
  };
}

async function openChatFromHistory(chatId) {
  loadChatStore();
  const chat = _chatStore.chats.find(function(item) { return item.id === chatId; });
  if (!chat) return;
  closeChatSidebar();
  const book = await findLibraryBookForChat(chat);
  chat.book_id = getBookKey(book) || chat.book_id || '';
  chat.drive_id = (book && book.drive_id) || chat.drive_id || '';
  chat.book_title = getBookTitle(book);
  chat.book_grade = (book && book.grade) || chat.book_grade || '';
  chat.updated_at = chat.updated_at || new Date().toISOString();
  saveChatStore({ immediate: true });
  await openAnalysis(book, chat.id);
}

function activateChat(chatId) {
  openChatFromHistory(chatId);
}

function startNewChat() {
  if (!selectedBook) return;
  resetEditState();
  loadChatStore();
  const chat = createChatForBook(selectedBook);
  _activeChatId = chat.id;
  clearChat();
  renderChatHistory();
}

function deleteChat(chatId) {
  loadChatStore();
  _chatStore.chats = _chatStore.chats.filter(function(chat) { return chat.id !== chatId; });
  if (_activeChatId === chatId) {
    _activeChatId = '';
    clearChat();
  }
  saveChatStore({ immediate: true });
  renderChatHistory();
}

function openChatSidebar() {
  const sidebar = document.getElementById('chatSidebar');
  if (!sidebar) return;
  renderChatHistory();
  loadChatStoreFromServer().then(renderChatHistory);
  sidebar.classList.remove('collapsed');
  const backdrop = document.getElementById('chatHistoryBackdrop');
  if (backdrop) backdrop.classList.add('active');
}

function closeChatSidebar() {
  const sidebar = document.getElementById('chatSidebar');
  if (sidebar) sidebar.classList.add('collapsed');
  const backdrop = document.getElementById('chatHistoryBackdrop');
  if (backdrop) backdrop.classList.remove('active');
}

function toggleChatSidebar() {
  const sidebar = document.getElementById('chatSidebar');
  if (!sidebar) return;
  if (sidebar.classList.contains('collapsed')) openChatSidebar();
  else closeChatSidebar();
}

async function openAnalysis(book, preferredChatId) {
  if (!ensureEmailVerifiedForAI()) return;
  resetEditState();
  selectedBook = book;
  registerLibraryBooks([book]);
  await loadChatStoreFromServer();
  closeChatSidebar();
  updateSelectedBookPanel(book);

  const lib  = document.getElementById('libraryScreen');
  const anal = document.getElementById('analysisScreen');
  document.body.classList.add('analysis-mode');
  lib.classList.add('hidden');
  anal.classList.remove('hidden');
  let chat = preferredChatId ? _chatStore.chats.find(function(item) { return item.id === preferredChatId; }) : null;
  if (!chat) chat = getLatestChatForBook(book) || createChatForBook(book);
  _activeChatId = chat.id;
  renderChatMessages(chat);
  renderChatHistory();
  requestAnimationFrame(function(){ anal.classList.add('active'); });
}

function goBack() {
  stopCurrentAnalysis();
  resetEditState();
  closePdfViewer();
  const lib  = document.getElementById('libraryScreen');
  const anal = document.getElementById('analysisScreen');
  anal.classList.remove('active');
  setTimeout(function() {
    anal.classList.add('hidden');
    lib.classList.remove('hidden');
    document.body.classList.remove('analysis-mode');
    clearChat();
    selectedBook = null;
    _activeChatId = '';
    loadLibrary();
  }, 380);
}

// ── PDF Viewer ─────────────────────────────────────────────────────────────────
var _pdfDoc    = null;
var _pdfPage   = 0;
var _pdfTotal  = 0;
var _pdfScale  = 1;
var _pdfRender = false;
var _pdfPendingRender = false;
var _pdfPendingResetScroll = false;
var _pdfFitMode = 'page';
var _pdfWheelZoom = true;
var _pdfAfterRender = [];
var _pdfRenderedScale = 1;
var _pdfRenderedPage = 0;

if (window.pdfjsLib) {
  pdfjsLib.GlobalWorkerOptions.workerSrc = {{ pdfjs_worker_url|tojson }};
}

function _updatePdfUI() {
  var input = document.getElementById('pdfPageInput');
  var total = document.getElementById('pdfPageTotal');
  var zoom = document.getElementById('pdfZoomInfo');
  var wheelBtn = document.getElementById('pdfWheelZoomToggle');
  if (input) {
    input.max = _pdfTotal || 1;
    if (document.activeElement !== input) {
      input.value = _pdfPage || '';
    }
  }
  if (total) total.textContent = '/ ' + (_pdfTotal || '-');
  if (zoom) zoom.textContent = _pdfDoc ? Math.round(_pdfScale * 100) + '%' : '100%';
  if (wheelBtn) {
    wheelBtn.classList.toggle('active', _pdfWheelZoom);
    wheelBtn.title = _pdfWheelZoom ? 'Tekerlekle yakınlaştır açık' : 'Tekerlekle yakınlaştır kapalı';
  }
}

function _pdfAvailableSize() {
  var wrap = document.getElementById('pdfCanvasWrap');
  if (!wrap) return { width: 0, height: 0 };
  var style = window.getComputedStyle(wrap);
  var padX = parseFloat(style.paddingLeft || '0') + parseFloat(style.paddingRight || '0');
  var padY = parseFloat(style.paddingTop || '0') + parseFloat(style.paddingBottom || '0');
  return {
    width: Math.max(120, wrap.clientWidth - padX),
    height: Math.max(120, wrap.clientHeight - padY)
  };
}

function _resetPdfScroll() {
  var wrap = document.getElementById('pdfCanvasWrap');
  if (!wrap) return;
  wrap.scrollLeft = 0;
  wrap.scrollTop = 0;
}

function _updatePdfWrapMode() {
  var wrap = document.getElementById('pdfCanvasWrap');
  var canvas = document.getElementById('pdfCanvas');
  if (!wrap || !canvas) return;
  var size = _pdfAvailableSize();
  var needsPan = canvas.width > size.width + 2 || canvas.height > size.height + 2;
  wrap.classList.toggle('pdf-pannable', needsPan);
}

function _afterPdfRender(fn) {
  if (typeof fn === 'function') _pdfAfterRender.push(fn);
}

function _replacePdfAfterRender(fn) {
  _pdfAfterRender = [];
  _afterPdfRender(fn);
}

function _renderPage(resetScroll) {
  if (!_pdfDoc || !_pdfPage) return Promise.resolve();
  if (_pdfRender) {
    _pdfPendingRender = true;
    _pdfPendingResetScroll = _pdfPendingResetScroll || !!resetScroll;
    return Promise.resolve();
  }
  _pdfRender = true;
  var renderPageNo = _pdfPage;
  var renderScale = _pdfScale;
  _updatePdfUI();
  return _pdfDoc.getPage(renderPageNo).then(function(page) {
    var viewport = page.getViewport({ scale: renderScale });
    var bufferCanvas = document.createElement('canvas');
    var bufferCtx = bufferCanvas.getContext('2d');
    bufferCanvas.width = Math.max(1, Math.ceil(viewport.width));
    bufferCanvas.height = Math.max(1, Math.ceil(viewport.height));
    bufferCtx.fillStyle = '#fff';
    bufferCtx.fillRect(0, 0, bufferCanvas.width, bufferCanvas.height);
    return page.render({ canvasContext: bufferCtx, viewport: viewport }).promise.then(function() {
      return {
        canvas: bufferCanvas,
        width: bufferCanvas.width,
        height: bufferCanvas.height,
        pageNo: renderPageNo,
        scale: renderScale
      };
    });
  }).then(function(rendered) {
    _pdfRender = false;
    var isStale = rendered.pageNo !== _pdfPage || Math.abs(rendered.scale - _pdfScale) > 0.001;
    if (!isStale) {
      var canvas = document.getElementById('pdfCanvas');
      if (canvas) {
        var ctx = canvas.getContext('2d');
        canvas.width = rendered.width;
        canvas.height = rendered.height;
        canvas.style.width = rendered.width + 'px';
        canvas.style.height = rendered.height + 'px';
        ctx.drawImage(rendered.canvas, 0, 0);
        _pdfRenderedScale = rendered.scale;
        _pdfRenderedPage = rendered.pageNo;
      }
      _updatePdfWrapMode();
      _updatePdfUI();
      if (resetScroll) _resetPdfScroll();
      var callbacks = _pdfAfterRender.splice(0);
      callbacks.forEach(function(fn) {
        try { fn(); } catch(e) {}
      });
    }
    if (_pdfPendingRender) {
      var nextResetScroll = _pdfPendingResetScroll || resetScroll;
      _pdfPendingRender = false;
      _pdfPendingResetScroll = false;
      return _renderPage(nextResetScroll);
    }
  }).catch(function(err) {
    _pdfRender = false;
    if (_pdfPendingRender) {
      var nextResetScroll = _pdfPendingResetScroll || resetScroll;
      _pdfPendingRender = false;
      _pdfPendingResetScroll = false;
      return _renderPage(nextResetScroll);
    }
    console.error('Sayfa render hatasi:', err);
  });
}

function pdfSetPage(pageNo) {
  if (!_pdfDoc || !_pdfTotal) return;
  var nextPage = Math.max(1, Math.min(_pdfTotal, parseInt(pageNo, 10) || 1));
  if (nextPage === _pdfPage) {
    _updatePdfUI();
    return;
  }
  _pdfPage = nextPage;
  if (_pdfFitMode === 'page') {
    pdfFitPage(true);
  } else if (_pdfFitMode === 'width') {
    pdfFitWidth(true);
  } else {
    _renderPage(true);
  }
}

function pdfGoToPage(event) {
  if (event) event.preventDefault();
  var input = document.getElementById('pdfPageInput');
  if (!input) return;
  pdfSetPage(input.value);
}

function pdfPrevPage() {
  pdfSetPage(_pdfPage - 1);
}

function pdfNextPage() {
  pdfSetPage(_pdfPage + 1);
}

function pdfZoom(delta, anchorEvent) {
  if (!_pdfDoc) return;
  var oldScale = _pdfScale;
  var newScale = Math.max(0.35, Math.min(5, oldScale + delta));
  if (Math.abs(newScale - oldScale) < 0.001) return;
  var wrap = document.getElementById('pdfCanvasWrap');
  var rect = wrap ? wrap.getBoundingClientRect() : null;
  var anchorX = rect && anchorEvent ? (anchorEvent.clientX - rect.left) : (wrap ? wrap.clientWidth / 2 : 0);
  var anchorY = rect && anchorEvent ? (anchorEvent.clientY - rect.top) : (wrap ? wrap.clientHeight / 2 : 0);
  var oldLeft = wrap ? wrap.scrollLeft : 0;
  var oldTop = wrap ? wrap.scrollTop : 0;
  _pdfFitMode = 'custom';
  _pdfScale = newScale;
  _updatePdfUI();
  if (wrap) {
    _replacePdfAfterRender(function() {
      var baseScale = _pdfRenderedPage === _pdfPage ? _pdfRenderedScale : oldScale;
      var ratio = newScale / Math.max(baseScale || oldScale || 1, 0.001);
      wrap.scrollLeft = (oldLeft + anchorX) * ratio - anchorX;
      wrap.scrollTop = (oldTop + anchorY) * ratio - anchorY;
    });
  }
  _renderPage(false);
}

function pdfFitPage(resetScroll) {
  if (!_pdfDoc) return Promise.resolve();
  _pdfFitMode = 'page';
  return _pdfDoc.getPage(_pdfPage).then(function(page) {
    var vp = page.getViewport({ scale: 1 });
    var size = _pdfAvailableSize();
    _pdfScale = Math.max(0.35, Math.min(5, Math.min(size.width / vp.width, size.height / vp.height)));
    return _renderPage(resetScroll !== false);
  });
}

function pdfFitWidth(resetScroll) {
  if (!_pdfDoc) return Promise.resolve();
  _pdfFitMode = 'width';
  return _pdfDoc.getPage(_pdfPage).then(function(page) {
    var vp = page.getViewport({ scale: 1 });
    var size = _pdfAvailableSize();
    _pdfScale = Math.max(0.35, Math.min(5, size.width / vp.width));
    return _renderPage(resetScroll !== false);
  });
}

function togglePdfWheelZoom() {
  _pdfWheelZoom = !_pdfWheelZoom;
  _updatePdfUI();
}

function _handlePdfWheel(e) {
  if (!_pdfWheelZoom || !_pdfDoc) return;
  e.preventDefault();
  var step = e.deltaY < 0 ? 0.12 : -0.12;
  if (Math.abs(e.deltaY) > 250) step *= 1.35;
  pdfZoom(step, e);
}

var _pdfWheelTarget = document.getElementById('pdfCanvasWrap');
if (_pdfWheelTarget) {
  _pdfWheelTarget.addEventListener('wheel', _handlePdfWheel, { passive: false });
}

window.addEventListener('resize', function() {
  var overlay = document.getElementById('pdfViewerOverlay');
  if (!overlay || !overlay.classList.contains('active') || !_pdfDoc) return;
  if (_pdfFitMode === 'page') pdfFitPage(false);
  else if (_pdfFitMode === 'width') pdfFitWidth(false);
});

function _isValidPdfHttpUrl(url) {
  url = String(url || '').trim();
  return /^https?:\/\/.+\.pdf(?:[?#].*)?$/i.test(url);
}

function _remotePdfUrlForBook(book) {
  book = book || {};
  var fields = ['pdf_url', 'source_url', 'remote_url'];
  for (var i = 0; i < fields.length; i++) {
    var url = String(book[fields[i]] || '').trim();
    if (_isValidPdfHttpUrl(url)) return url;
  }
  var bookId = String(book.book_id || '').trim();
  if (book.pdf_source === 'book_archive' && BOOKS_REMOTE_BASE_URL && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(bookId)) {
    return BOOKS_REMOTE_BASE_URL.replace(/\/?$/, '/') + encodeURIComponent(bookId + '.pdf');
  }
  return '';
}

function _pdfIframeUrl(url) {
  url = String(url || '').trim();
  if (!url) return '';
  if (url.indexOf('#') !== -1) return url;
  return url + '#toolbar=1&navpanes=0&view=FitH';
}

function openPdfViewer() {
  if (!selectedBook) return;
  var bookId  = selectedBook.book_id || selectedBook.drive_id || '';
  if (!bookId) return;
  _abortPrefetches();
  var overlay  = document.getElementById('pdfViewerOverlay');
  var title    = document.getElementById('pdfViewerTitle');
  var bookName = selectedBook.title || selectedBook.name || 'PDF';
  title.textContent = bookName;
  overlay.classList.add('active');

  var proxyPdfUrl = '/api/serve_pdf/' + encodeURIComponent(bookId) + '?t=' + Date.now();
  var remotePdfUrl = _remotePdfUrlForBook(selectedBook);
  var pdfUrl = remotePdfUrl || proxyPdfUrl;

  if (_pdfDoc) { try { _pdfDoc.destroy(); } catch(e){} _pdfDoc = null; }
  if (_pdfLoadTask) { try { _pdfLoadTask.destroy(); } catch(e){} _pdfLoadTask = null; }
  if (_pdfLoadAbort) { try { _pdfLoadAbort.abort(); } catch(e){} }
  _pdfLoadAbort = new AbortController();
  _pdfRender = false;
  _pdfPendingRender = false;
  _pdfPendingResetScroll = false;
  _pdfFitMode = 'page';
  _pdfScale = 1;
  _pdfRenderedScale = 1;
  _pdfRenderedPage = 0;

  var canvas = document.getElementById('pdfCanvas');
  canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
  canvas.width = 0;
  canvas.height = 0;
  canvas.style.width = '';
  canvas.style.height = '';
  var pageInput = document.getElementById('pdfPageInput');
  if (pageInput) pageInput.value = '';
  var pageTotal = document.getElementById('pdfPageTotal');
  if (pageTotal) pageTotal.textContent = '/ -';
  _updatePdfUI();

  var loadOverlay = document.getElementById('pdfLoadingOverlay');
  var loadRing    = document.getElementById('pdfLoadRing');
  var loadPct     = document.getElementById('pdfLoadPct');
  var loadLabel   = document.getElementById('pdfLoadLabel');
  var loadBytes   = document.getElementById('pdfLoadBytes');
  var loadTitle   = document.getElementById('pdfLoadTitle');
  loadOverlay.classList.remove('hidden', 'pdf-load-indeterminate');
  loadPct.textContent  = '0%';
  loadLabel.textContent = 'PDF y\u00fckleniyor...';
  loadBytes.textContent = '';
  loadTitle.textContent = bookName;
  loadRing.style.strokeDashoffset = '339.292';
  var circumference = 339.292;

  var frame = document.getElementById('pdfFrame');
  if (frame) {
    overlay.classList.add('iframe-mode');
    loadLabel.textContent = 'PDF iframe i\u00e7inde a\u00e7\u0131l\u0131yor...';
    loadPct.textContent = '';
    loadBytes.textContent = remotePdfUrl ? 'GitHub PDF kayna\u011f\u0131 kullan\u0131l\u0131yor.' : 'PDF kayna\u011f\u0131 haz\u0131rlan\u0131yor.';
    frame.onload = function() {
      if (overlay.classList.contains('active')) {
        loadOverlay.classList.add('hidden');
      }
    };
    frame.src = _pdfIframeUrl(pdfUrl);
    setTimeout(function() {
      if (overlay.classList.contains('active') && frame.src) {
        loadOverlay.classList.add('hidden');
      }
    }, 2500);
    return;
  }

  if (!window.pdfjsLib) {
    console.error('PDF.js y\u00fcklenemedi');
    loadLabel.textContent = 'PDF g\u00f6r\u00fcnt\u00fcleyici y\u00fcklenemedi!';
    loadPct.textContent = '';
    loadBytes.textContent = 'Uygulama i\u00e7i okuyucu dosyalar\u0131 y\u00fcklenemedi.';
    return;
  }

  function _formatBytes(b) {
    if (b < 1024) return b + ' B';
    if (b < 1048576) return (b / 1024).toFixed(1) + ' KB';
    return (b / 1048576).toFixed(1) + ' MB';
  }

  function _showProgress(loaded, total) {
    if (total > 0) {
      var pct = Math.min(Math.round((loaded / total) * 100), 100);
      loadPct.textContent = pct + '%';
      loadRing.style.strokeDashoffset = String(circumference * (1 - pct / 100));
      loadBytes.textContent = _formatBytes(loaded) + ' / ' + _formatBytes(total);
    } else {
      loadOverlay.classList.add('pdf-load-indeterminate');
      loadPct.textContent = '';
      loadBytes.textContent = _formatBytes(loaded);
    }
  }

  function _startPdfLoad(activePdfUrl, fallbackPdfUrl) {
    var loadingTask = pdfjsLib.getDocument({
      url: activePdfUrl,
      rangeChunkSize: 524288,
      disableStream: true,
      disableRange: false,
      disableAutoFetch: true
    });
    _pdfLoadTask = loadingTask;
    loadingTask.onProgress = function(progress) {
      _showProgress(progress.loaded || 0, progress.total || 0);
    };
    _pdfLoadAbort.signal.addEventListener('abort', function() {
      try { loadingTask.destroy(); } catch(e) {}
    });

    loadingTask.promise
      .then(function(doc) {
        if (_pdfLoadTask !== loadingTask) {
          try { doc.destroy(); } catch(e) {}
          return;
        }
        _pdfDoc   = doc;
        _pdfTotal = doc.numPages;
        _pdfPage  = 1;
        loadLabel.textContent = 'PDF haz\u0131rlan\u0131yor...';
        _updatePdfUI();
        pdfFitPage(true).then(function() {
          if (_pdfDoc === doc) loadOverlay.classList.add('hidden');
        }).catch(function(err) {
          console.error('Sayfa render hatasi:', err);
          if (_pdfDoc === doc) {
            loadOverlay.classList.remove('hidden');
            loadLabel.textContent = 'PDF okunamad\u0131!';
            loadPct.textContent = '';
          }
        });
      })
      .catch(function(err) {
        if (String(err && err.name || '') === 'AbortException') return;
        if (fallbackPdfUrl) {
          console.warn('GitHub PDF yuklenemedi, yerel gecis deneniyor:', err);
          loadLabel.textContent = 'GitHub ba\u011flant\u0131s\u0131 denenemedi, yerel ge\u00e7i\u015f kullan\u0131l\u0131yor...';
          loadPct.textContent = '';
          loadBytes.textContent = '';
          _startPdfLoad(fallbackPdfUrl, '');
          return;
        }
        console.error('PDF yukleme hatasi:', err);
        loadOverlay.classList.remove('hidden');
        loadLabel.textContent = 'PDF y\u00fcklenemedi!';
        loadPct.textContent = '';
        loadBytes.textContent = 'Ba\u011flant\u0131y\u0131 veya PDF kayna\u011f\u0131n\u0131 kontrol edin.';
      });
  }

  _startPdfLoad(pdfUrl, remotePdfUrl ? proxyPdfUrl : '');
}

var _pdfLoadAbort = null;
var _pdfLoadTask = null;

function closePdfViewer() {
  var overlay = document.getElementById('pdfViewerOverlay');
  overlay.classList.remove('active');
  overlay.classList.remove('iframe-mode');
  var frame = document.getElementById('pdfFrame');
  if (frame) {
    frame.onload = null;
    frame.src = 'about:blank';
  }
  if (_pdfLoadTask) { try { _pdfLoadTask.destroy(); } catch(e){} _pdfLoadTask = null; }
  if (_pdfLoadAbort) { try { _pdfLoadAbort.abort(); } catch(e){} _pdfLoadAbort = null; }
  setTimeout(function(){
    if (_pdfDoc) { try { _pdfDoc.destroy(); } catch(e){} }
    _pdfDoc   = null;
    _pdfPage  = 0;
    _pdfTotal = 0;
    _pdfScale = 1;
    _pdfFitMode = 'page';
    _pdfRender = false;
    _pdfPendingRender = false;
    _pdfPendingResetScroll = false;
    _pdfAfterRender = [];
    _pdfRenderedScale = 1;
    _pdfRenderedPage = 0;
    var canvas = document.getElementById('pdfCanvas');
    if (canvas) {
      canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
      canvas.width = 0;
      canvas.height = 0;
      canvas.style.width = '';
      canvas.style.height = '';
    }
    var wrap = document.getElementById('pdfCanvasWrap');
    if (wrap) wrap.classList.remove('pdf-pannable');
    _updatePdfUI();
    document.getElementById('pdfLoadingOverlay').classList.add('hidden');
  }, 350);
}

// ── Chat helpers ───────────────────────────────────────────────────────────────
function normalizeMessageText(text) {
  return String(text || '').replace(/\\r\\n/g, '\\n').replace(/\\r/g, '\\n');
}

let _markdownRenderCounter = 0;

function normalizeMarkdownLabel(value) {
  return normalizeMessageText(value).trim().replace(/\s+/g, ' ').toLowerCase();
}

function createMarkdownState(references, footnotes) {
  _markdownRenderCounter += 1;
  return {
    references: references || Object.create(null),
    footnotes: footnotes || Object.create(null),
    usedFootnotes: [],
    lastPageReference: 0,
    idPrefix: 'chat-md-' + _markdownRenderCounter + '-'
  };
}

function decodeMarkdownEntities(text) {
  if (String(text).indexOf('&') === -1) return text;
  const textarea = decodeMarkdownEntities._textarea || (decodeMarkdownEntities._textarea = document.createElement('textarea'));
  textarea.innerHTML = text;
  return textarea.value;
}

function applyInlineMarks(element, marks) {
  marks = marks || {};
  if (marks.bold) element.classList.add('chat-inline-strong');
  if (marks.italic) element.classList.add('chat-inline-em');
  if (marks.del) element.classList.add('chat-inline-del');
}

function appendMarkedText(container, text, marks) {
  if (!text) return;
  const value = decodeMarkdownEntities(text);
  marks = marks || {};
  if (!marks.bold && !marks.italic && !marks.del) {
    container.appendChild(document.createTextNode(value));
    return;
  }
  const span = document.createElement('span');
  applyInlineMarks(span, marks);
  span.textContent = value;
  container.appendChild(span);
}

function withInlineMarks(marks, nextMarks) {
  return Object.assign({}, marks || {}, nextMarks || {});
}

function isEscapableMarkdownChar(ch) {
  return /[\\\\`*_\{\}\[\]\(\)#\+\-.!|>~]/.test(ch || '');
}

function findClosingDelimiter(source, delimiter, start) {
  let index = start;
  while (index < source.length) {
    index = source.indexOf(delimiter, index);
    if (index === -1) return -1;
    if (source[index - 1] !== '\\\\') return index;
    index += delimiter.length;
  }
  return -1;
}

function findClosingBracket(source, openIndex) {
  let depth = 0;
  for (let index = openIndex; index < source.length; index += 1) {
    const ch = source[index];
    if (ch === '\\\\') {
      index += 1;
      continue;
    }
    if (ch === '[') depth += 1;
    if (ch === ']') {
      depth -= 1;
      if (depth === 0) return index;
    }
  }
  return -1;
}

function findClosingParen(source, openIndex) {
  let depth = 0;
  for (let index = openIndex; index < source.length; index += 1) {
    const ch = source[index];
    if (ch === '\\\\') {
      index += 1;
      continue;
    }
    if (ch === '(') depth += 1;
    if (ch === ')') {
      depth -= 1;
      if (depth === 0) return index;
    }
  }
  return -1;
}

function splitLinkTarget(rawTarget) {
  const raw = String(rawTarget || '').trim();
  if (!raw) return null;
  let href = '';
  let rest = '';
  if (raw[0] === '<') {
    const close = raw.indexOf('>');
    if (close === -1) return null;
    href = raw.slice(1, close);
    rest = raw.slice(close + 1).trim();
  } else {
    const match = /^(\S+)(?:\s+([\s\S]+))?$/.exec(raw);
    if (!match) return null;
    href = match[1];
    rest = (match[2] || '').trim();
  }
  let title = '';
  if (rest) {
    const quote = rest[0];
    if ((quote === '"' || quote === "'" || quote === '(') &&
        rest[rest.length - 1] === (quote === '(' ? ')' : quote)) {
      title = rest.slice(1, -1);
    }
  }
  return { href: href, title: title };
}

function normalizeLinkHref(rawHref) {
  const clean = decodeMarkdownEntities(String(rawHref || '').trim())
    .replace(/[\\u0000-\\u001f\\u007f]/g, '');
  if (!clean) return '';
  if (/^www\./i.test(clean)) return 'https://' + clean;
  if (/^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$/i.test(clean)) return 'mailto:' + clean;
  if (/^#/i.test(clean) || /^\/(?!\/)/.test(clean) || /^\.\.?\//.test(clean)) return clean;
  if (/^[a-z][a-z0-9+.-]*:/i.test(clean)) {
    return /^(https?|mailto|tel):/i.test(clean) ? clean : '';
  }
  return clean;
}

function getSelectedBookKey() {
  return getBookKey(selectedBook) || '';
}

function extractMarkdownPageNumber() {
  const combined = Array.prototype.slice.call(arguments)
    .map(function(value) { return String(value || ''); })
    .join(' ');
  const direct = /\\bpage\s*:\s*(\d{1,4})\\b/i.exec(combined);
  if (direct) return parseInt(direct[1], 10);
  const page = /(?:sayfa|sf|page)\s*[=:.-]?\s*(\d{1,4})/i.exec(combined);
  return page ? parseInt(page[1], 10) : 0;
}

function rememberMarkdownPageReference(text, state) {
  if (!state) return;
  const pageNo = extractMarkdownPageNumber(text);
  if (pageNo > 0) state.lastPageReference = pageNo;
}

function isPlaceholderImageHref(href) {
  const value = String(href || '').trim().toLowerCase();
  if (!value) return false;
  if (/^page\s*:\s*\d{1,4}\\b/.test(value)) return true;
  if (/^https?:\/\/(?:www\.)?example\.(?:com|org|net)\\b/.test(value)) return true;
  if (/^(?:gorsel|görsel|image|resim|placeholder)[\w.-]*\.(?:png|jpe?g|webp|gif)$/i.test(value)) return true;
  return false;
}

function pageImageSrc(pageNo) {
  const bookId = getSelectedBookKey();
  if (!bookId || !pageNo) return '';
  return '/api/page_image/' + encodeURIComponent(bookId) + '/' + encodeURIComponent(String(pageNo));
}

function normalizeImageSrc(rawHref, state, alt, title) {
  const clean = decodeMarkdownEntities(String(rawHref || '').trim())
    .replace(/[\\u0000-\\u001f\\u007f]/g, '');
  if (!clean) return { src: '', pageNo: 0, original: clean };

  const explicitPage = extractMarkdownPageNumber(clean);
  if (explicitPage && /^page\s*:/i.test(clean)) {
    return { src: pageImageSrc(explicitPage), pageNo: explicitPage, original: clean };
  }

  const pageNo = extractMarkdownPageNumber(alt, title, clean) || (state && state.lastPageReference) || 0;
  if (pageNo && isPlaceholderImageHref(clean)) {
    return { src: pageImageSrc(pageNo), pageNo: pageNo, original: clean };
  }

  if (/^data:image\/(?:png|jpe?g|gif|webp);base64,[a-z0-9+/=\s]+$/i.test(clean)) {
    return { src: clean.replace(/\s+/g, ''), pageNo: 0, original: clean };
  }
  if (/^\/(?!\/)/.test(clean) || /^\.\.?\//.test(clean)) {
    return { src: clean, pageNo: 0, original: clean };
  }
  if (/^[a-z][a-z0-9+.-]*:/i.test(clean)) {
    return /^(https?):/i.test(clean) ? { src: clean, pageNo: 0, original: clean } : { src: '', pageNo: 0, original: clean };
  }
  return { src: clean, pageNo: 0, original: clean };
}

function trimAutolinkPunctuation(value) {
  let text = value;
  while (/[.,;:!?]$/.test(text)) text = text.slice(0, -1);
  while (text.endsWith(')')) {
    const opens = (text.match(/\(/g) || []).length;
    const closes = (text.match(/\)/g) || []).length;
    if (closes <= opens) break;
    text = text.slice(0, -1);
  }
  return text;
}

function hasAutolinkBoundary(source, index) {
  return index === 0 || /[\s([<{]/.test(source[index - 1]);
}

function readAutolinkLiteral(source, index) {
  if (!hasAutolinkBoundary(source, index)) return null;
  const chunk = source.slice(index);
  const urlMatch = /^(https?:\/\/[^\s<]+|www\.[^\s<]+)/i.exec(chunk);
  if (urlMatch) {
    const text = trimAutolinkPunctuation(urlMatch[0]);
    return { text: text, href: normalizeLinkHref(text), length: text.length };
  }
  const emailMatch = /^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/i.exec(chunk);
  if (emailMatch) {
    const text = trimAutolinkPunctuation(emailMatch[0]);
    return { text: text, href: 'mailto:' + text, length: text.length };
  }
  return null;
}

function appendMarkdownLink(container, label, href, title, state, marks) {
  const safeHref = normalizeLinkHref(href);
  if (!safeHref) {
    appendMarkedText(container, '[' + label + '](' + href + ')', marks);
    return;
  }
  const link = document.createElement('a');
  link.className = 'chat-md-link';
  applyInlineMarks(link, marks);
  link.href = safeHref;
  if (!safeHref.startsWith('#')) {
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
  }
  if (title) link.title = decodeMarkdownEntities(title);
  const linkLabel = label || safeHref;
  if (normalizeLinkHref(linkLabel) === safeHref) appendMarkedText(link, linkLabel, marks);
  else appendInlineMarkdown(link, linkLabel, state, marks);
  container.appendChild(link);
}

function createMarkdownImageFallback(alt, href, pageNo) {
  const fallback = document.createElement('div');
  fallback.className = 'chat-md-image-fallback';
  const titleEl = document.createElement('div');
  titleEl.className = 'chat-md-image-fallback-title';
  titleEl.textContent = alt || (pageNo ? 'Sayfa ' + pageNo : 'Görsel');
  const detail = document.createElement('div');
  detail.className = 'chat-md-image-fallback-src';
  detail.textContent = pageNo
    ? 'Sayfa görseli açılamadı.'
    : 'Görsel bağlantısı açılamadı' + (href ? ': ' + href : '.');
  fallback.appendChild(titleEl);
  fallback.appendChild(detail);
  return fallback;
}

function appendMarkdownImage(container, alt, href, title, state, marks) {
  const imageInfo = normalizeImageSrc(href, state, alt, title);
  const safeHref = imageInfo.src;
  if (!safeHref) {
    appendMarkedText(container, '![' + alt + '](' + href + ')', marks);
    return;
  }
  const image = document.createElement('img');
  image.className = 'chat-md-image';
  if (imageInfo.pageNo) image.classList.add('chat-md-page-image');
  image.src = safeHref;
  image.alt = decodeMarkdownEntities(alt || '');
  image.loading = 'lazy';
  image.referrerPolicy = 'no-referrer';
  if (title) image.title = decodeMarkdownEntities(title);
  image.onerror = function() {
    image.replaceWith(createMarkdownImageFallback(image.alt, imageInfo.original || href, imageInfo.pageNo));
  };
  if (imageInfo.pageNo) {
    const link = document.createElement('a');
    link.className = 'chat-md-image-link';
    link.href = '/api/serve_pdf/' + encodeURIComponent(getSelectedBookKey()) + '#page=' + imageInfo.pageNo;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.appendChild(image);
    container.appendChild(link);
  } else {
    container.appendChild(image);
  }
}

function appendFootnoteReference(container, rawId, state) {
  const key = normalizeMarkdownLabel(rawId);
  if (!state.footnotes[key]) {
    appendMarkedText(container, '[^' + rawId + ']', {});
    return;
  }
  if (state.usedFootnotes.indexOf(key) === -1) state.usedFootnotes.push(key);
  const number = state.usedFootnotes.indexOf(key) + 1;
  const sup = document.createElement('sup');
  sup.className = 'chat-md-footnote-ref';
  const link = document.createElement('a');
  link.className = 'chat-md-link';
  link.href = '#' + makeFootnoteDomId(state, key, 'fn-');
  link.id = makeFootnoteDomId(state, key, 'ref-');
  link.textContent = String(number);
  sup.appendChild(link);
  container.appendChild(sup);
}

function parseInlineLink(source, index, state, marks, isImage) {
  const openIndex = isImage ? index + 1 : index;
  const closeIndex = findClosingBracket(source, openIndex);
  if (closeIndex === -1) return null;
  const label = source.slice(openIndex + 1, closeIndex);
  let consumed = closeIndex + 1 - index;
  let target = null;

  if (source[closeIndex + 1] === '(') {
    const closeParen = findClosingParen(source, closeIndex + 1);
    if (closeParen === -1) return null;
    target = splitLinkTarget(source.slice(closeIndex + 2, closeParen));
    consumed = closeParen + 1 - index;
  } else {
    let refLabel = label;
    if (source[closeIndex + 1] === '[') {
      const refClose = findClosingBracket(source, closeIndex + 1);
      if (refClose === -1) return null;
      refLabel = source.slice(closeIndex + 2, refClose) || label;
      consumed = refClose + 1 - index;
    }
    target = state.references[normalizeMarkdownLabel(refLabel)];
  }

  if (!target || !target.href) return null;
  return {
    consumed: consumed,
    render: function(container) {
      if (isImage) appendMarkdownImage(container, label, target.href, target.title, state, marks);
      else appendMarkdownLink(container, label, target.href, target.title, state, marks);
    }
  };
}

function parseCodeSpan(source, index) {
  const ticks = /^`+/.exec(source.slice(index));
  if (!ticks) return null;
  const delimiter = ticks[0];
  const end = source.indexOf(delimiter, index + delimiter.length);
  if (end === -1) return null;
  let code = source.slice(index + delimiter.length, end).replace(/\s+/g, ' ');
  if (/^ .+ $/.test(code)) code = code.slice(1, -1);
  return { text: code, consumed: end + delimiter.length - index };
}

const CHAT_MATH_SYMBOLS = {
  alpha: 'α', beta: 'β', gamma: 'γ', delta: 'δ', epsilon: 'ε', varepsilon: 'ε',
  zeta: 'ζ', eta: 'η', theta: 'θ', vartheta: 'ϑ', iota: 'ι', kappa: 'κ',
  lambda: 'λ', mu: 'μ', nu: 'ν', xi: 'ξ', pi: 'π', rho: 'ρ', sigma: 'σ',
  tau: 'τ', upsilon: 'υ', phi: 'φ', varphi: 'ϕ', chi: 'χ', psi: 'ψ', omega: 'ω',
  Gamma: 'Γ', Delta: 'Δ', Theta: 'Θ', Lambda: 'Λ', Xi: 'Ξ', Pi: 'Π',
  Sigma: 'Σ', Phi: 'Φ', Psi: 'Ψ', Omega: 'Ω',
  infty: '∞', partial: '∂', nabla: '∇', degree: '°'
};

const CHAT_MATH_OPERATORS = {
  cdot: '·', times: '×', div: '÷', pm: '±', mp: '∓', le: '≤', leq: '≤',
  ge: '≥', geq: '≥', neq: '≠', approx: '≈', sim: '∼', to: '→',
  rightarrow: '→', leftarrow: '←', leftrightarrow: '↔', implies: '⇒',
  sum: '∑', prod: '∏', int: '∫'
};

function isEscapedIndex(source, index) {
  let count = 0;
  for (let pos = index - 1; pos >= 0 && source[pos] === '\\\\'; pos -= 1) count += 1;
  return count % 2 === 1;
}

function findClosingMathDelimiter(source, delimiter, start) {
  let index = start;
  while (index < source.length) {
    index = source.indexOf(delimiter, index);
    if (index === -1) return -1;
    if (!isEscapedIndex(source, index)) return index;
    index += delimiter.length;
  }
  return -1;
}

function readLatexBraceGroup(source, openIndex) {
  let depth = 0;
  for (let index = openIndex; index < source.length; index += 1) {
    const ch = source[index];
    if (ch === '\\\\') {
      index += 1;
      continue;
    }
    if (ch === '{') depth += 1;
    if (ch === '}') {
      depth -= 1;
      if (depth === 0) {
        return {
          content: source.slice(openIndex + 1, index),
          nextIndex: index + 1
        };
      }
    }
  }
  return null;
}

function readLatexAtom(source, index) {
  while (index < source.length && /\s/.test(source[index])) index += 1;
  if (index >= source.length) return { content: '', nextIndex: index };
  if (source[index] === '{') {
    const group = readLatexBraceGroup(source, index);
    if (group) return group;
  }
  if (source[index] === '\\\\') {
    const command = /^\\\\([A-Za-z]+|.)/.exec(source.slice(index));
    if (command) {
      return { content: command[0], nextIndex: index + command[0].length };
    }
  }
  return { content: source[index], nextIndex: index + 1 };
}

function appendMathText(container, value, className) {
  if (!value) return;
  const span = document.createElement('span');
  if (className) span.className = className;
  span.textContent = value;
  container.appendChild(span);
}

function appendLatexNodes(container, latex) {
  const source = String(latex || '').replace(/\s+/g, ' ').trim();
  let index = 0;

  while (index < source.length) {
    const ch = source[index];

    if (/\s/.test(ch)) {
      container.appendChild(document.createTextNode(' '));
      index += 1;
      continue;
    }

    if (ch === '{') {
      const group = readLatexBraceGroup(source, index);
      if (group) {
        appendLatexNodes(container, group.content);
        index = group.nextIndex;
        continue;
      }
    }

    if (ch === '^' || ch === '_') {
      const atom = readLatexAtom(source, index + 1);
      const script = document.createElement(ch === '^' ? 'sup' : 'sub');
      appendLatexNodes(script, atom.content);
      container.appendChild(script);
      index = atom.nextIndex;
      continue;
    }

    if (ch === '\\\\') {
      const command = /^\\\\([A-Za-z]+|.)/.exec(source.slice(index));
      if (!command) {
        index += 1;
        continue;
      }
      const full = command[0];
      const name = command[1];
      let nextIndex = index + full.length;

      if (name === 'frac') {
        const numerator = readLatexAtom(source, nextIndex);
        const denominator = readLatexAtom(source, numerator.nextIndex);
        const frac = document.createElement('span');
        frac.className = 'chat-math-frac';
        const num = document.createElement('span');
        num.className = 'chat-math-num';
        const den = document.createElement('span');
        den.className = 'chat-math-den';
        appendLatexNodes(num, numerator.content);
        appendLatexNodes(den, denominator.content);
        frac.appendChild(num);
        frac.appendChild(den);
        container.appendChild(frac);
        index = denominator.nextIndex;
        continue;
      }

      if (name === 'sqrt') {
        let rootIndex = nextIndex;
        let root = null;
        while (rootIndex < source.length && /\s/.test(source[rootIndex])) rootIndex += 1;
        if (source[rootIndex] === '[') {
          const close = source.indexOf(']', rootIndex + 1);
          if (close !== -1) {
            root = source.slice(rootIndex + 1, close);
            nextIndex = close + 1;
          }
        }
        const radicand = readLatexAtom(source, nextIndex);
        const sqrt = document.createElement('span');
        sqrt.className = 'chat-math-sqrt';
        if (root) appendMathText(sqrt, root, 'chat-math-root');
        appendMathText(sqrt, '√', 'chat-math-radical');
        const body = document.createElement('span');
        body.className = 'chat-math-radicand';
        appendLatexNodes(body, radicand.content);
        sqrt.appendChild(body);
        container.appendChild(sqrt);
        index = radicand.nextIndex;
        continue;
      }

      if (name === 'text' || name === 'mathrm') {
        const group = readLatexAtom(source, nextIndex);
        appendMathText(container, group.content, 'chat-math-text');
        index = group.nextIndex;
        continue;
      }

      if (name === 'left' || name === 'right') {
        index = nextIndex;
        continue;
      }

      if (name === ',' || name === ';' || name === ':' || name === 'quad' || name === 'qquad') {
        container.appendChild(document.createTextNode(name === 'qquad' ? '  ' : ' '));
        index = nextIndex;
        continue;
      }

      if (Object.prototype.hasOwnProperty.call(CHAT_MATH_SYMBOLS, name)) {
        appendMathText(container, CHAT_MATH_SYMBOLS[name]);
        index = nextIndex;
        continue;
      }

      if (Object.prototype.hasOwnProperty.call(CHAT_MATH_OPERATORS, name)) {
        appendMathText(container, CHAT_MATH_OPERATORS[name]);
        index = nextIndex;
        continue;
      }

      appendMathText(container, name);
      index = nextIndex;
      continue;
    }

    appendMathText(container, ch);
    index += 1;
  }
}

function appendMath(container, latex, display) {
  const host = document.createElement(display ? 'div' : 'span');
  host.className = 'chat-md-math ' + (display ? 'display' : 'inline');
  appendLatexNodes(host, latex);
  container.appendChild(host);
}

function parseInlineMath(source, index) {
  const starters = [
    { open: '\\\\(', close: '\\\\)', display: false },
    { open: '\\\\[', close: '\\\\]', display: true },
    { open: '$$', close: '$$', display: true },
    { open: '$', close: '$', display: false }
  ];
  for (let startIndex = 0; startIndex < starters.length; startIndex += 1) {
    const starter = starters[startIndex];
    if (source.slice(index, index + starter.open.length) !== starter.open) continue;
    if (starter.open === '$' && source[index + 1] === '$') continue;
    const close = findClosingMathDelimiter(source, starter.close, index + starter.open.length);
    if (close === -1) continue;
    const latex = source.slice(index + starter.open.length, close);
    if (!latex.trim()) continue;
    if (starter.open === '$' && /\\n/.test(latex)) continue;
    return {
      latex: latex,
      display: starter.display,
      consumed: close + starter.close.length - index
    };
  }
  return null;
}

function appendInlineMarkdown(container, text, state, marks) {
  const source = normalizeMessageText(text);
  let index = 0;
  let buffer = '';
  marks = marks || {};

  function flushBuffer() {
    appendMarkedText(container, buffer, marks);
    buffer = '';
  }

  while (index < source.length) {
    const ch = source[index];

    if (ch === '\\n') {
      flushBuffer();
      container.appendChild(document.createElement('br'));
      index += 1;
      continue;
    }

    const parsedMath = parseInlineMath(source, index);
    if (parsedMath) {
      flushBuffer();
      appendMath(container, parsedMath.latex, parsedMath.display);
      index += parsedMath.consumed;
      continue;
    }

    if (ch === '\\\\' && isEscapableMarkdownChar(source[index + 1])) {
      buffer += source[index + 1];
      index += 2;
      continue;
    }

    if (ch === '`') {
      const codeSpan = parseCodeSpan(source, index);
      if (codeSpan) {
        flushBuffer();
        const code = document.createElement('code');
        code.className = 'chat-md-code';
        applyInlineMarks(code, marks);
        code.textContent = codeSpan.text;
        container.appendChild(code);
        index += codeSpan.consumed;
        continue;
      }
    }

    const footnote = /^\[\^([^\]]+)\]/.exec(source.slice(index));
    if (footnote) {
      flushBuffer();
      appendFootnoteReference(container, footnote[1], state);
      index += footnote[0].length;
      continue;
    }

    if (source.slice(index, index + 2) === '![') {
      const parsedImage = parseInlineLink(source, index, state, marks, true);
      if (parsedImage) {
        flushBuffer();
        parsedImage.render(container);
        index += parsedImage.consumed;
        continue;
      }
    }

    if (ch === '[') {
      const parsedLink = parseInlineLink(source, index, state, marks, false);
      if (parsedLink) {
        flushBuffer();
        parsedLink.render(container);
        index += parsedLink.consumed;
        continue;
      }
    }

    if (ch === '<') {
      const close = source.indexOf('>', index + 1);
      if (close !== -1) {
        const value = source.slice(index + 1, close).trim();
        const href = normalizeLinkHref(value);
        if (href && (/^(https?|mailto|tel):/i.test(href) || /^[^@\s]+@[^@\s]+\.[^@\s]+$/i.test(value))) {
          flushBuffer();
          appendMarkdownLink(container, value, href, '', state, marks);
          index = close + 1;
          continue;
        }
      }
    }

    const literalLink = readAutolinkLiteral(source, index);
    if (literalLink && literalLink.href) {
      flushBuffer();
      appendMarkdownLink(container, literalLink.text, literalLink.href, '', state, marks);
      index += literalLink.length;
      continue;
    }

    const markRules = [
      { delimiter: '***', marks: { bold: true, italic: true } },
      { delimiter: '___', marks: { bold: true, italic: true } },
      { delimiter: '**', marks: { bold: true } },
      { delimiter: '__', marks: { bold: true } },
      { delimiter: '~~', marks: { del: true } },
      { delimiter: '*', marks: { italic: true } },
      { delimiter: '_', marks: { italic: true } },
      { delimiter: '~', marks: { del: true } }
    ];
    let matchedRule = null;
    for (let ruleIndex = 0; ruleIndex < markRules.length; ruleIndex += 1) {
      const rule = markRules[ruleIndex];
      if (source.slice(index, index + rule.delimiter.length) === rule.delimiter) {
        matchedRule = rule;
        break;
      }
    }
    if (matchedRule) {
      const end = findClosingDelimiter(source, matchedRule.delimiter, index + matchedRule.delimiter.length);
      const inner = end !== -1 ? source.slice(index + matchedRule.delimiter.length, end) : '';
      if (end !== -1 && inner.trim()) {
        flushBuffer();
        appendInlineMarkdown(container, inner, state, withInlineMarks(marks, matchedRule.marks));
        index = end + matchedRule.delimiter.length;
        continue;
      }
    }

    buffer += ch;
    index += 1;
  }

  flushBuffer();
}

function parseMessageSegments(text, style) {
  return [{ text: normalizeMessageText(text), bold: !!(style && style.bold), italic: !!(style && style.italic) }];
}

function createMessageSpan(segment) {
  const span = document.createElement(segment.code ? 'code' : 'span');
  if (segment.code) span.classList.add('chat-md-code');
  if (segment.bold) span.classList.add('chat-inline-strong');
  if (segment.italic) span.classList.add('chat-inline-em');
  if (segment.del) span.classList.add('chat-inline-del');
  return span;
}

function renderMessageSegments(container, segments) {
  segments.forEach(function(segment) {
    if (segment.br) {
      container.appendChild(document.createElement('br'));
      return;
    }
    if (segment.href) {
      appendMarkdownLink(container, segment.text, segment.href, '', createMarkdownState(), segment);
      return;
    }
    const span = createMessageSpan(segment);
    span.textContent = segment.text;
    container.appendChild(span);
  });
}

function renderInlineMarkdown(container, text, state) {
  state = state || createMarkdownState();
  rememberMarkdownPageReference(text, state);
  appendInlineMarkdown(container, text, state, {});
}

function makeFootnoteDomId(state, id, prefix) {
  const safe = String(id || 'note').toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '') || 'note';
  return state.idPrefix + prefix + safe;
}

function isThematicBreak(line) {
  const trimmed = line.trim();
  return /^(\*\s*){3,}$/.test(trimmed) || /^(-\s*){3,}$/.test(trimmed) || /^(_\s*){3,}$/.test(trimmed);
}

function matchFence(line) {
  return /^\s{0,3}(`{3,}|~{3,})\s*([A-Za-z0-9_-]+)?\s*$/.exec(line);
}

function isFenceClose(line, marker) {
  const fence = matchFence(line);
  return !!fence && fence[1][0] === marker[0] && fence[1].length >= marker.length;
}

function matchAtxHeading(line) {
  return /^\s{0,3}(#{1,6})(?:\s+|$)(.*?)(?:\s+#+\s*)?$/.exec(line);
}

function matchSetextHeading(line) {
  return /^\s{0,3}(=+|-+)\s*$/.exec(line);
}

function matchListMarker(line) {
  const unordered = /^(\s{0,3})([-+*])\s+([\s\S]*)$/.exec(line);
  if (unordered) {
    return { ordered: false, indent: unordered[1].length, content: unordered[3], start: null };
  }
  const ordered = /^(\s{0,3})(\d{1,9})[.)]\s+([\s\S]*)$/.exec(line);
  if (ordered) {
    return { ordered: true, indent: ordered[1].length, content: ordered[3], start: parseInt(ordered[2], 10) };
  }
  return null;
}

function stripListContinuation(line, markerIndent) {
  let index = 0;
  const max = markerIndent + 2;
  while (index < line.length && index < max && line[index] === ' ') index += 1;
  if (index === 0 && line[0] === '\t') return line.slice(1);
  return line.slice(index);
}

function splitTableRow(line) {
  let text = line.trim();
  if (text.startsWith('|')) text = text.slice(1);
  if (text.endsWith('|')) text = text.slice(0, -1);
  const cells = [];
  let cell = '';
  let escaped = false;
  for (let index = 0; index < text.length; index += 1) {
    const ch = text[index];
    if (escaped) {
      cell += ch;
      escaped = false;
      continue;
    }
    if (ch === '\\\\') {
      escaped = true;
      continue;
    }
    if (ch === '|') {
      cells.push(cell.trim());
      cell = '';
      continue;
    }
    cell += ch;
  }
  cells.push(cell.trim());
  return cells;
}

function parseTableDelimiter(line) {
  const cells = splitTableRow(line);
  if (!cells.length) return null;
  const alignments = [];
  for (let index = 0; index < cells.length; index += 1) {
    const cell = cells[index].replace(/\s+/g, '');
    if (!/^:?-+:?$/.test(cell)) return null;
    const left = cell.startsWith(':');
    const right = cell.endsWith(':');
    alignments.push(left && right ? 'center' : right ? 'right' : left ? 'left' : '');
  }
  return alignments;
}

function isTableStart(lines, index) {
  return index + 1 < lines.length &&
    lines[index].indexOf('|') !== -1 &&
    !!parseTableDelimiter(lines[index + 1]);
}

function renderTable(container, lines, start, state) {
  const headerCells = splitTableRow(lines[start]);
  const alignments = parseTableDelimiter(lines[start + 1]);
  const wrap = document.createElement('div');
  wrap.className = 'chat-md-table-wrap';
  const table = document.createElement('table');
  table.className = 'chat-md-table';
  const thead = document.createElement('thead');
  const headRow = document.createElement('tr');
  headerCells.forEach(function(cell, cellIndex) {
    const th = document.createElement('th');
    if (alignments[cellIndex]) th.style.textAlign = alignments[cellIndex];
    renderInlineMarkdown(th, cell, state);
    headRow.appendChild(th);
  });
  thead.appendChild(headRow);
  table.appendChild(thead);
  const tbody = document.createElement('tbody');
  let index = start + 2;
  while (index < lines.length && lines[index].trim() && lines[index].indexOf('|') !== -1) {
    const row = document.createElement('tr');
    const cells = splitTableRow(lines[index]);
    for (let cellIndex = 0; cellIndex < headerCells.length; cellIndex += 1) {
      const td = document.createElement('td');
      if (alignments[cellIndex]) td.style.textAlign = alignments[cellIndex];
      renderInlineMarkdown(td, cells[cellIndex] || '', state);
      row.appendChild(td);
    }
    tbody.appendChild(row);
    index += 1;
  }
  table.appendChild(tbody);
  wrap.appendChild(table);
  container.appendChild(wrap);
  return index;
}

function appendCodeBlock(container, codeLines, language) {
  const pre = document.createElement('pre');
  pre.className = 'chat-md-pre';
  const code = document.createElement('code');
  if (language) code.className = 'language-' + language;
  code.textContent = codeLines.join('\\n');
  pre.appendChild(code);
  container.appendChild(pre);
}

function appendParagraph(container, lines, state) {
  if (!lines.length) return;
  const p = document.createElement('p');
  renderInlineMarkdown(p, lines.join('\\n'), state);
  container.appendChild(p);
}

function isMarkdownBlockStart(lines, index) {
  const line = lines[index] || '';
  return !!(matchFence(line) ||
    isMathBlockStart(lines, index) ||
    matchAtxHeading(line) ||
    matchListMarker(line) ||
    /^\s{0,3}>\s?/.test(line) ||
    isThematicBreak(line) ||
    isTableStart(lines, index) ||
    /^( {4}|\t)/.test(line));
}

function mathBlockLooksIntentional(latex) {
  return /\\\\[A-Za-z]+|[_^=+\-*/]|[A-Za-z]\s*\^|\d/.test(latex || '');
}

function parseMathBlock(lines, start) {
  const line = lines[start] || '';
  const trimmed = line.trim();
  const markers = [
    { open: '\\\\[', close: '\\\\]', bare: false },
    { open: '$$', close: '$$', bare: false }
  ];

  for (let markerIndex = 0; markerIndex < markers.length; markerIndex += 1) {
    const marker = markers[markerIndex];
    if (!trimmed.startsWith(marker.open)) continue;
    const first = trimmed.slice(marker.open.length);
    const sameLineClose = first.indexOf(marker.close);
    if (sameLineClose !== -1) {
      return {
        latex: first.slice(0, sameLineClose),
        nextIndex: start + 1
      };
    }
    const content = [];
    if (first.trim()) content.push(first);
    let index = start + 1;
    while (index < lines.length) {
      const current = lines[index];
      const close = current.indexOf(marker.close);
      if (close !== -1) {
        content.push(current.slice(0, close));
        return {
          latex: content.join('\\n'),
          nextIndex: index + 1
        };
      }
      content.push(current);
      index += 1;
    }
  }

  if (trimmed === '[') {
    const content = [];
    let index = start + 1;
    while (index < lines.length) {
      if ((lines[index] || '').trim() === ']') {
        const latex = content.join('\\n');
        if (mathBlockLooksIntentional(latex)) {
          return { latex: latex, nextIndex: index + 1 };
        }
        return null;
      }
      content.push(lines[index]);
      index += 1;
    }
  }

  return null;
}

function isMathBlockStart(lines, index) {
  return !!parseMathBlock(lines, index);
}

function trimOuterBlankLines(lines) {
  const copy = lines.slice();
  while (copy.length && !copy[0].trim()) copy.shift();
  while (copy.length && !copy[copy.length - 1].trim()) copy.pop();
  return copy;
}

function renderListItem(li, itemLines, state) {
  const lines = trimOuterBlankLines(itemLines);
  const task = /^\[([ xX])\]\s*([\s\S]*)$/.exec(lines[0] || '');
  let host = li;
  if (task) {
    li.classList.add('chat-md-task-item');
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.disabled = true;
    checkbox.className = 'chat-md-task-checkbox';
    checkbox.checked = task[1].toLowerCase() === 'x';
    li.appendChild(checkbox);
    host = document.createElement('div');
    host.className = 'chat-md-task-content';
    li.appendChild(host);
    lines[0] = task[2];
  }
  if (!lines.length) return;
  if (lines.length === 1) renderInlineMarkdown(host, lines[0], state);
  else renderMarkdownBlocks(host, lines, state);
}

function renderListBlock(container, lines, start, state) {
  const first = matchListMarker(lines[start]);
  const list = document.createElement(first.ordered ? 'ol' : 'ul');
  if (first.ordered && first.start && first.start !== 1) list.start = first.start;
  let index = start;
  let hasTask = false;
  while (index < lines.length) {
    const marker = matchListMarker(lines[index]);
    if (!marker || marker.ordered !== first.ordered || marker.indent !== first.indent) break;
    const itemLines = [marker.content];
    index += 1;
    while (index < lines.length) {
      const next = matchListMarker(lines[index]);
      if (next && next.indent === first.indent && next.ordered === first.ordered) break;
      if (next && next.indent < first.indent) break;
      itemLines.push(stripListContinuation(lines[index], first.indent));
      index += 1;
    }
    const li = document.createElement('li');
    renderListItem(li, itemLines, state);
    if (li.classList.contains('chat-md-task-item')) hasTask = true;
    list.appendChild(li);
  }
  if (hasTask) list.classList.add('chat-md-task-list');
  container.appendChild(list);
  return index;
}

function renderMarkdownBlocks(container, lines, state) {
  let index = 0;
  let paragraph = [];

  function flushParagraph() {
    appendParagraph(container, paragraph, state);
    paragraph = [];
  }

  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      flushParagraph();
      index += 1;
      continue;
    }

    const fence = matchFence(line);
    if (fence) {
      flushParagraph();
      index += 1;
      const codeLines = [];
      while (index < lines.length && !isFenceClose(lines[index], fence[1])) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      appendCodeBlock(container, codeLines, fence[2] || '');
      continue;
    }

    if (/^( {4}|\t)/.test(line)) {
      flushParagraph();
      const codeLines = [];
      while (index < lines.length && (/^( {4}|\t)/.test(lines[index]) || !lines[index].trim())) {
        codeLines.push(lines[index].replace(/^( {4}|\t)/, ''));
        index += 1;
      }
      appendCodeBlock(container, codeLines, '');
      continue;
    }

    const mathBlock = parseMathBlock(lines, index);
    if (mathBlock) {
      flushParagraph();
      appendMath(container, mathBlock.latex, true);
      index = mathBlock.nextIndex;
      continue;
    }

    const heading = matchAtxHeading(line);
    if (heading) {
      flushParagraph();
      const h = document.createElement('div');
      h.className = 'chat-md-heading level-' + heading[1].length;
      renderInlineMarkdown(h, heading[2].trim(), state);
      container.appendChild(h);
      index += 1;
      continue;
    }

    const setext = index + 1 < lines.length ? matchSetextHeading(lines[index + 1]) : null;
    if (setext && line.trim() && !isMarkdownBlockStart(lines, index)) {
      flushParagraph();
      const h = document.createElement('div');
      h.className = 'chat-md-heading level-' + (setext[1][0] === '=' ? 1 : 2);
      renderInlineMarkdown(h, line.trim(), state);
      container.appendChild(h);
      index += 2;
      continue;
    }

    if (isThematicBreak(line)) {
      flushParagraph();
      const hr = document.createElement('hr');
      hr.className = 'chat-md-hr';
      container.appendChild(hr);
      index += 1;
      continue;
    }

    if (/^\s{0,3}>\s?/.test(line)) {
      flushParagraph();
      const quoteLines = [];
      while (index < lines.length && (/^\s{0,3}>\s?/.test(lines[index]) || !lines[index].trim())) {
        quoteLines.push(lines[index].replace(/^\s{0,3}>\s?/, ''));
        index += 1;
      }
      const quote = document.createElement('blockquote');
      quote.className = 'chat-md-quote';
      renderMarkdownBlocks(quote, quoteLines, state);
      container.appendChild(quote);
      continue;
    }

    if (isTableStart(lines, index)) {
      flushParagraph();
      index = renderTable(container, lines, index, state);
      continue;
    }

    if (matchListMarker(line)) {
      flushParagraph();
      index = renderListBlock(container, lines, index, state);
      continue;
    }

    paragraph.push(line);
    index += 1;
    if (index < lines.length && isMarkdownBlockStart(lines, index)) flushParagraph();
  }
  flushParagraph();
}

function collectMarkdownDefinitions(lines) {
  const references = Object.create(null);
  const footnotes = Object.create(null);
  const body = [];
  for (let index = 0; index < lines.length; index += 1) {
    const footnote = /^\s{0,3}\[\^([^\]]+)\]:\s*([\s\S]*)$/.exec(lines[index]);
    if (footnote) {
      const content = [footnote[2]];
      index += 1;
      while (index < lines.length && (/^( {4}|\t)/.test(lines[index]) || !lines[index].trim())) {
        content.push(lines[index].replace(/^( {4}|\t)/, ''));
        index += 1;
      }
      index -= 1;
      footnotes[normalizeMarkdownLabel(footnote[1])] = { label: footnote[1], content: content.join('\\n') };
      continue;
    }
    const reference = /^\s{0,3}\[([^\]^][^\]]*)\]:\s*([\s\S]+)$/.exec(lines[index]);
    if (reference) {
      const target = splitLinkTarget(reference[2]);
      if (target && normalizeLinkHref(target.href)) {
        references[normalizeMarkdownLabel(reference[1])] = target;
        continue;
      }
    }
    body.push(lines[index]);
  }
  return { lines: body, references: references, footnotes: footnotes };
}

function appendFootnotes(container, state) {
  if (!state.usedFootnotes.length) return;
  const section = document.createElement('section');
  section.className = 'chat-md-footnotes';
  const ol = document.createElement('ol');
  state.usedFootnotes.forEach(function(key) {
    const note = state.footnotes[key];
    if (!note) return;
    const li = document.createElement('li');
    li.id = makeFootnoteDomId(state, key, 'fn-');
    renderMarkdownBlocks(li, normalizeMessageText(note.content).split('\\n'), state);
    const back = document.createElement('a');
    back.className = 'chat-md-footnote-backref';
    back.href = '#' + makeFootnoteDomId(state, key, 'ref-');
    back.textContent = '↩';
    li.appendChild(back);
    ol.appendChild(li);
  });
  section.appendChild(ol);
  container.appendChild(section);
}

function renderMarkdown(container, text) {
  container.innerHTML = '';
  const collected = collectMarkdownDefinitions(normalizeMessageText(text).split('\\n'));
  const state = createMarkdownState(collected.references, collected.footnotes);
  renderMarkdownBlocks(container, collected.lines, state);
  appendFootnotes(container, state);
}

function createMessageBody(panelClass) {
  const panel = document.createElement('div');
  panel.className = panelClass;
  const body = document.createElement('div');
  body.className = 'message-body';
  panel.appendChild(body);
  return { panel: panel, body: body };
}

function copyTextFallback(text) {
  const helper = document.createElement('textarea');
  helper.value = text;
  helper.setAttribute('readonly', '');
  helper.style.position = 'fixed';
  helper.style.left = '-9999px';
  document.body.appendChild(helper);
  helper.select();
  try { document.execCommand('copy'); } catch(e) {}
  helper.remove();
}

async function copyMessageText(text) {
  const value = String(text || '');
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(value);
    } else {
      copyTextFallback(value);
    }
    showToast('success', 'Kopyalandı', 'Mesaj panoya kopyalandı.', 2200);
  } catch(e) {
    copyTextFallback(value);
    showToast('success', 'Kopyalandı', 'Mesaj panoya kopyalandı.', 2200);
  }
}

function findActiveChatMessage(messageId) {
  const chat = getActiveChat();
  if (!chat || !Array.isArray(chat.messages)) return null;
  const index = chat.messages.findIndex(function(message) { return message.id === messageId; });
  if (index < 0) return null;
  return { chat: chat, index: index, message: chat.messages[index] };
}

function resetEditState(options) {
  options = options || {};
  _editingMessageId = '';
  const wrap = document.querySelector('.chat-input-wrap');
  if (wrap) wrap.classList.remove('editing');
  if (!options.keepButton) setAnalyzeButtonMode('idle');
}

function editUserMessage(messageId) {
  const found = findActiveChatMessage(messageId);
  if (!found || !found.message || found.message.role !== 'user') return;
  _editingMessageId = messageId;
  setPrompt(found.message.text);
  const wrap = document.querySelector('.chat-input-wrap');
  if (wrap) wrap.classList.add('editing');
  setAnalyzeButtonMode('edit');
  setAnalysisStatus('Mesaj düzenleniyor...', 'amber');
  showToast('info', 'Mesaj Düzenleniyor', 'Yeni metni gönderince cevap bu mesajdan yeniden oluşturulacak.', 4200);
}

function createMessageActions(text, role, messageId) {
  const actions = document.createElement('div');
  actions.className = 'message-actions';

  const copyBtn = document.createElement('button');
  copyBtn.type = 'button';
  copyBtn.className = 'message-action-btn';
  copyBtn.title = 'Mesajı kopyala';
  copyBtn.setAttribute('aria-label', 'Mesajı kopyala');
  copyBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
  copyBtn.onclick = function(e) {
    e.stopPropagation();
    copyMessageText(text);
  };
  actions.appendChild(copyBtn);

  if (role === 'ai') {
    const forwardBtn = document.createElement('button');
    forwardBtn.type = 'button';
    forwardBtn.className = 'message-action-btn';
    forwardBtn.title = 'DM’ye ilet';
    forwardBtn.setAttribute('aria-label', 'DM’ye ilet');
    forwardBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2 11 13"/><path d="m22 2-7 20-4-9-9-4 20-7Z"/></svg>';
    forwardBtn.onclick = function(e) {
      e.stopPropagation();
      forwardMessageToDm(messageId);
    };
    actions.appendChild(forwardBtn);
  }

  if (role === 'user') {
    const editBtn = document.createElement('button');
    editBtn.type = 'button';
    editBtn.className = 'message-action-btn';
    editBtn.title = 'Mesajı düzenle';
    editBtn.setAttribute('aria-label', 'Mesajı düzenle');
    editBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/></svg>';
    editBtn.onclick = function(e) {
      e.stopPropagation();
      editUserMessage(messageId);
    };
    actions.appendChild(editBtn);
  }

  return actions;
}

function scrollChatToBottom() {
  const flow = document.getElementById('chatFlow');
  flow.scrollTop = flow.scrollHeight;
}

function appendUserMsg(text, options) {
  options = options || {};
  const flow  = document.getElementById('chatFlow');
  const empty = document.getElementById('chatEmpty');
  const chips = document.getElementById('quickChips');
  if (empty) empty.style.display = 'none';
  if (chips) chips.classList.add('hidden');
  const div = document.createElement('div');
  div.className = 'chat-msg user';
  const messageId = options.messageId || makeClientId('msg');
  div.dataset.messageId = messageId;
  const parts = createMessageBody('chat-bubble');
  div.appendChild(parts.panel);
  div.appendChild(createMessageActions(text, 'user', messageId));
  flow.insertBefore(div, document.getElementById('typingIndicator'));
  if (options.animate === false) {
    renderMarkdown(parts.body, text);
    scrollChatToBottom();
    return Promise.resolve();
  }
  return typewriteMessage(parts.body, text);
}

function setTypingStatus(text) {
  const label = document.querySelector('#typingIndicator .typing-label');
  if (label) label.textContent = text || 'Yazıyor...';
}

function showTyping(message) {
  const flow   = document.getElementById('chatFlow');
  const typing = document.getElementById('typingIndicator');
  clearTimeout(_typingHideTimer);
  _typingVisibleSince = Date.now();
  setTypingStatus(message || 'Yazıyor...');
  typing.classList.add('active');
  flow.appendChild(typing);
  scrollChatToBottom();
}

function hideTyping() {
  const typing = document.getElementById('typingIndicator');
  if (!typing.classList.contains('active')) return Promise.resolve();
  clearTimeout(_typingHideTimer);
  const elapsed = Date.now() - _typingVisibleSince;
  const minVisibleMs = 650;
  return new Promise(function(resolve) {
    const finalize = function() {
      typing.classList.remove('active');
      _typingVisibleSince = 0;
      setTypingStatus('Yazıyor...');
      resolve();
    };
    if (elapsed >= minVisibleMs) {
      finalize();
    } else {
      _typingHideTimer = setTimeout(finalize, minVisibleMs - elapsed);
    }
  });
}

function makeAnalysisId() {
  if (window.crypto && typeof window.crypto.randomUUID === 'function') {
    return window.crypto.randomUUID();
  }
  return 'analysis-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
}

function stopAnalysisStatusPoll() {
  clearInterval(_analysisStatusPollTimer);
  _analysisStatusPollTimer = null;
  _activeAnalysisId = '';
}

function setAnalyzeButtonMode(mode) {
  const btn = document.getElementById('analyzeBtn');
  if (!btn) return;
  const label = btn.querySelector('.analyze-btn-label');
  btn.classList.toggle('stop-mode', mode === 'stop');
  btn.classList.toggle('edit-mode', mode === 'edit');
  if (mode === 'stop') {
    btn.innerHTML = STOP_ICON + '<span class="analyze-btn-label">Durdur</span>';
    btn.setAttribute('aria-label', 'Yanıtı durdur');
    btn.title = 'Yanıtı durdur';
  } else if (mode === 'edit') {
    btn.innerHTML = SEND_ICON + '<span class="analyze-btn-label">Yeniden Başlat</span>';
    btn.setAttribute('aria-label', 'Mesajı düzenle ve yeniden başlat');
    btn.title = 'Mesajı düzenle ve yeniden başlat';
  } else {
    btn.innerHTML = SEND_ICON + '<span class="analyze-btn-label">Analiz Et</span>';
    btn.setAttribute('aria-label', 'Analiz Et');
    btn.title = 'Analiz Et';
  }
}

function stopCurrentAnalysis() {
  if (!_activeAnalyzeController) return;
  _analysisStopRequested = true;
  try { _activeAnalyzeController.abort(); } catch(e) {}
  stopAnalysisStatusPoll();
  hideTyping();
  setAnalysisStatus('Durduruldu', 'amber');
  showToast('info', 'Yanıt Durduruldu', 'Devam eden yanıt ekrana yazılmadan durduruldu.', 3200);
}

async function pollAnalysisStatus(analysisId) {
  if (!analysisId || analysisId !== _activeAnalysisId) return;
  try {
    const res = await fetch('/api/analyze_status/' + encodeURIComponent(analysisId), {
      cache: 'no-store'
    });
    const data = await res.json();
    if (analysisId !== _activeAnalysisId) return;
    if (data && data.message) setTypingStatus(data.message);
    if (data && data.done) stopAnalysisStatusPoll();
  } catch(e) {
    // Durum mesajı yardımcı bilgi; ana analiz isteğini etkilemesin.
  }
}

function startAnalysisStatusPoll(analysisId) {
  stopAnalysisStatusPoll();
  _activeAnalysisId = analysisId;
  _analysisStatusPollTimer = setInterval(function() {
    pollAnalysisStatus(analysisId);
  }, 700);
}

function waitForAnalysisResult(analysisId, timeoutMs, signal) {
  const startedAt = Date.now();
  return new Promise(function(resolve, reject) {
    let stopped = false;
    let timer = null;
    const finish = function(fn, value) {
      if (stopped) return;
      stopped = true;
      clearTimeout(timer);
      if (signal) signal.removeEventListener('abort', abortWait);
      fn(value);
    };
    const abortWait = function() {
      const err = new Error('Analiz durduruldu.');
      err.name = 'AbortError';
      finish(reject, err);
    };
    if (signal) {
      if (signal.aborted) {
        abortWait();
        return;
      }
      signal.addEventListener('abort', abortWait);
    }
    const tick = async function() {
      if (stopped) return;
      if (Date.now() - startedAt > timeoutMs) {
        const err = new Error('Analiz zaman aşımına uğradı.');
        err.name = 'AbortError';
        finish(reject, err);
        return;
      }
      try {
        const res = await fetch('/api/analyze_status/' + encodeURIComponent(analysisId), {
          cache: 'no-store',
          signal: signal
        });
        const data = await res.json();
        if (data && data.message) setTypingStatus(data.message);
        if (data && data.done) {
          finish(resolve, data);
          return;
        }
      } catch(e) {
        // Bir poll kaçarsa sonraki turda tekrar deneriz.
      }
      timer = setTimeout(tick, 700);
    };
    tick();
  });
}

function typewriteMessage(body, text) {
  const source = normalizeMessageText(text);
  const totalChars = source.length;
  const charsPerFrame =
    totalChars > 18000 ? 96 :
    totalChars > 10000 ? 72 :
    totalChars > 5000 ? 48 :
    totalChars > 1800 ? 24 :
    totalChars > 700 ? 10 : 5;

  return new Promise(function(resolve) {
    let visibleChars = 0;

    function tick() {
      visibleChars = Math.min(totalChars, visibleChars + charsPerFrame);
      renderMarkdown(body, source.slice(0, visibleChars));
      scrollChatToBottom();

      if (visibleChars < totalChars) {
        requestAnimationFrame(tick);
      } else {
        resolve();
      }
    }

    tick();
  });
}

async function appendAiMsg(text, options) {
  options = options || {};
  const flow   = document.getElementById('chatFlow');
  const typing = document.getElementById('typingIndicator');
  const div    = document.createElement('div');
  div.className = 'chat-msg ai';
  const messageId = options.messageId || makeClientId('msg');
  div.dataset.messageId = messageId;
  const parts = createMessageBody('chat-text');
  div.appendChild(parts.panel);
  div.appendChild(createMessageActions(text, 'ai', messageId));
  flow.insertBefore(div, typing);
  if (options.animate === false) {
    renderMarkdown(parts.body, text);
  } else {
    await typewriteMessage(parts.body, text);
  }
  scrollChatToBottom();
}

function getLocalSmallTalkResponse(text) {
  const clean = (text || '')
    .toLocaleLowerCase('tr-TR')
    .replace(/ı/g, 'i')
    .replace(/[!?.,;:()[\]{}"']/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  if (!clean) return '';
  const tokens = clean.split(' ').filter(Boolean);
  const tokenSet = new Set(tokens);
  const bookIntentExactTerms = [
    'kitap', 'sayfa', 'sf', 'soru', 'soruyu', 'sorular', 'cevap', 'cevabi',
    'çöz', 'coz', 'çözüm', 'cozum', 'açikla', 'acikla', 'anlat', 'özet',
    'ozet', 'analiz', 'incele', 'konu', 'ünite', 'unite', 'tema', 'metin',
    'paragraf', 'etkinlik', 'etkinli', 'aliştirma', 'alistirma', 'test', 'örnek',
    'ornek', 'pdf', 'oku', 'okuma', 'göster', 'goster', 'bul',
    'değerlendir', 'degerlendir', 'müfredat', 'mufredat', 'kazanim',
    'ders', 'ödev', 'odev', 'ödevi', 'odevi', 'egzersiz', 'exercise',
    'activity', 'yap', 'hazirla', 'hazırla', 'tamamla', 'performans', 'proje'
  ];
  const bookIntentStemTerms = [
    'kitap', 'sayfa', 'soru', 'cevap', 'çöz', 'coz', 'açikla', 'acikla',
    'özet', 'ozet', 'analiz', 'konu', 'ünite', 'unite', 'tema', 'metin',
    'paragraf', 'etkinli', 'aliştirma', 'alistirma', 'örnek', 'ornek',
    'değerlendir', 'degerlendir', 'müfredat', 'mufredat', 'kazanim',
    'ödev', 'odev', 'performans', 'proje', 'egzersiz', 'exercise',
    'activity', 'hazirla', 'hazırla', 'tamamla'
  ];
  const isBookRelated = bookIntentExactTerms.some(function(term) {
    return tokenSet.has(term);
  }) || bookIntentStemTerms.some(function(term) {
    return clean.indexOf(term) !== -1;
  }) || /\b\d{1,4}\b/.test(clean);
  if (isBookRelated) return '';

  if (clean.indexOf('tesekkur') !== -1 || clean.indexOf('teşekkür') !== -1 ||
      clean.indexOf('sag ol') !== -1 || clean.indexOf('sağ ol') !== -1 ||
      clean.indexOf('eyvallah') !== -1) {
    return 'Rica ederim. Buradayım; kitapla ilgili bir soru, sayfa veya konu yazarsan hemen yardımcı olurum.';
  }
  if (clean.indexOf('kimsin') !== -1 || clean.indexOf('sen nesin') !== -1 ||
      clean.indexOf('adin ne') !== -1 || clean.indexOf('adın ne') !== -1) {
    return 'Ben ReylAI. Ders kitaplarındaki sayfa, soru ve konuları hızlıca açıklamak için buradayım.';
  }
  if (clean.indexOf('yavaş') !== -1 || clean.indexOf('yavas') !== -1 ||
      clean.indexOf('bekliyorum') !== -1 || clean.indexOf('cevap vermiyorsun') !== -1) {
    return 'Haklısın, kısa sohbetlerde bekletmemem gerekiyor. Kitap dışı mesajlara hızlı cevap vereceğim.';
  }
  if (clean.indexOf('yardim') !== -1 || clean.indexOf('yardım') !== -1 ||
      clean.indexOf('ne yapabilirsin') !== -1 || clean.indexOf('nasil kullanilir') !== -1 ||
      clean.indexOf('nasıl kullanılır') !== -1) {
    return 'Bir sayfa numarası, soru ya da konu yazarsan seçili kitaba göre açıklama, özet veya çözüm hazırlayabilirim.';
  }
  if (clean.indexOf('naber') !== -1 || clean.indexOf('nasilsin') !== -1 ||
      clean.indexOf('nasılsın') !== -1 || clean.indexOf('ne haber') !== -1 ||
      clean.indexOf('napıyorsun') !== -1 || clean.indexOf('napiyorsun') !== -1 ||
      clean.indexOf('yapıyorsun') !== -1 || clean.indexOf('yapiyorsun') !== -1) {
    return 'İyiyim, buradayım. Kitaptan bir sayfa, soru veya konu yazarsan hemen yardımcı olurum.';
  }
  const greetingTerms = new Set(['selam', 'merhaba', 'mrb', 'slm', 'sa', 'hey', 'hi', 'hello']);
  if (tokens.some(function(token) { return greetingTerms.has(token); })) {
    return 'Merhaba, buradayım. Kitaptaki bir soru, sayfa veya konuyu yaz; hemen yardımcı olayım.';
  }
  return '';
}

function prepareEditedPrompt(prompt) {
  if (!_editingMessageId) return null;
  const found = findActiveChatMessage(_editingMessageId);
  if (!found || !found.message || found.message.role !== 'user') {
    resetEditState();
    return null;
  }

  found.message.text = String(prompt || '');
  found.message.updated_at = new Date().toISOString();
  found.chat.messages = found.chat.messages.slice(0, found.index + 1);
  found.chat.updated_at = new Date().toISOString();
  if (!found.chat.title || found.chat.title === 'Yeni sohbet') {
    found.chat.title = makeFallbackChatTitle(prompt);
  }
  saveChatStore({ immediate: true });
  renderChatMessages(found.chat);
  renderChatHistory();
  resetEditState({ keepButton: true });
  return {
    chat: found.chat,
    message: found.message,
    historyForApi: getChatHistoryForApi(found.chat, { beforeMessageId: found.message.id }),
    titleRequested: !found.chat.title || found.chat.title === 'Yeni sohbet'
  };
}

// ── Analyze ────────────────────────────────────────────────────────────────────
async function analyze() {
  if (_activeAnalyzeController) {
    stopCurrentAnalysis();
    return;
  }
  if (!selectedBook) return;
  if (!ensureEmailVerifiedForAI()) return;
  const ta     = document.getElementById('promptInput');
  const prompt = ta.value.trim();
  if (!prompt) {
    setAnalysisStatus('Lütfen bir soru veya görev yazın.', 'amber');
    showToast('warning', 'Soru gerekli', 'Analizden önce bir soru ya da görev yazın.', 3200);
    return;
  }

  const editRun = prepareEditedPrompt(prompt);
  const chat = editRun ? editRun.chat : ensureActiveChatForSelectedBook();
  const historyForApi = editRun ? editRun.historyForApi : getChatHistoryForApi(chat);
  const titleRequested = editRun
    ? editRun.titleRequested
    : (!chat.title || chat.title === 'Yeni sohbet' || (chat.messages || []).length === 0);
  if (!editRun) {
    const userMessage = addChatMessage('user', prompt);
    await appendUserMsg(prompt, { messageId: userMessage.id });
  }
  ta.value = '';
  autoResizeTA(ta);

  const localReply = getLocalSmallTalkResponse(prompt);
  if (localReply) {
    const aiMessage = addChatMessage('ai', localReply);
    await appendAiMsg(localReply, { messageId: aiMessage.id });
    if (titleRequested) updateActiveChatTitle(makeFallbackChatTitle(prompt), prompt);
    setAnalysisStatus('Hazır', 'green');
    showResponseBanner();
    setAnalyzeButtonMode('idle');
    return;
  }

  showTyping();
  setTypingStatus('Analiz başlatılıyor...');

  const btn = document.getElementById('analyzeBtn');
  btn.classList.add('loading');
  setAnalyzeButtonMode('stop');
  setAnalysisStatus('Hazır', 'green');
  const analysisId = makeAnalysisId();
  const controller = new AbortController();
  _activeAnalyzeController = controller;
  _analysisStopRequested = false;

  try {
    const res = await apiFetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: controller.signal,
      body: JSON.stringify({
        book_id: selectedBook.book_id || selectedBook.drive_id || '',
        book_name: selectedBook.title || selectedBook.name,
        prompt: prompt,
        analysis_id: analysisId,
        chat_id: chat.id,
        chat_title: chat.title || '',
        title_requested: titleRequested,
        chat_history: historyForApi
      })
    });
    const data = await res.json();
    await hideTyping();
    if (controller.signal.aborted || _analysisStopRequested) return;

    if (data.result) {
      if (data.chat_title) updateActiveChatTitle(data.chat_title, prompt);
      const aiMessage = addChatMessage('ai', data.result);
      await appendAiMsg(data.result, { messageId: aiMessage.id });
      setAnalysisStatus('Hazır', 'green');
      showResponseBanner();
    } else if (data.email_verification_required) {
      setAnalysisStatus('E-posta doğrulaması gerekli.', 'amber');
      showVerifyRequiredModal(data.error || 'AI cevaplarına erişmek için e-postanı doğrula.');
    } else if (data.rate_limit) {
      setAnalysisStatus('API kotası doldu.', 'red');
      showToast('warning', 'API kota sınırı aşıldı',
        'Mistral API istek limiti doldu. Birkaç dakika bekleyip tekrar deneyin.', 9000);
    } else if (data.temporary_unavailable) {
      setAnalysisStatus('Yapay zekâ servisi yoğun.', 'red');
      showToast('warning', 'Yapay zekâ servisi yoğun',
        'Seçili model şu anda yoğun. Biraz sonra tekrar deneyin.', 7000);
    } else {
      const errMsg = data.error || 'Bilinmeyen hata';
      setAnalysisStatus('Analiz başarısız.', 'red');
      showToast('error', 'Analiz Başarısız', errMsg, 7000);
    }
  } catch(e) {
    await hideTyping();
    if (e && e.name === 'AbortError') {
      if (_analysisStopRequested || (controller && controller.signal.aborted)) {
        setAnalysisStatus('Durduruldu', 'amber');
      } else {
        setAnalysisStatus('Yanıt süresi doldu.', 'red');
        showToast('warning', 'Yanıt süresi doldu',
          'Model uzun yanıtı zamanında tamamlayamadı. Biraz sonra tekrar deneyin.', 7000);
      }
    } else {
      setAnalysisStatus('Bağlantı hatası.', 'red');
      showToast('error', 'Bağlantı Hatası', e.message, 6000);
    }
  } finally {
    if (_activeAnalyzeController === controller) {
      _activeAnalyzeController = null;
      _analysisStopRequested = false;
      stopAnalysisStatusPoll();
      btn.classList.remove('loading');
      setAnalyzeButtonMode(_editingMessageId ? 'edit' : 'idle');
    }
  }
}

function setPrompt(text) {
  const ta = document.getElementById('promptInput');
  ta.value = text;
  ta.focus();
  autoResizeTA(ta);
}

function autoResizeTA(ta) {
  ta.style.height = 'auto';
  ta.style.height = Math.min(ta.scrollHeight, 180) + 'px';
}

// ── Delete ─────────────────────────────────────────────────────────────────────
function showDelConfirm(bookId, name) {
  _pendingDeleteInfo = { bookId: bookId, name: name };
  document.getElementById('delBookName').textContent = name;
  document.getElementById('delOverlay').classList.add('active');
}

function hideDelConfirm() {
  document.getElementById('delOverlay').classList.remove('active');
  _pendingDeleteInfo = null;
}

async function confirmDelete() {
  if (!_pendingDeleteInfo) return;
  const info = _pendingDeleteInfo;
  hideDelConfirm();
  setLibStatus('Siliniyor...', 'amber');
  try {
    const res  = await fetch('/api/delete', {
      method: 'POST',
      headers: Object.assign({ 'Content-Type': 'application/json' }, authHeaders()),
      body: JSON.stringify({ book_id: info.bookId })
    });
    const data = await res.json();
    if (data.success) {
      setLibStatus('Kitap silindi.', 'green');
    } else if (data.auth === false) {
      sessionStorage.removeItem('admin_auth_token');
      requireAuth(function(){ _pendingDeleteInfo = info; confirmDelete(); });
    } else {
      setLibStatus('Silme hatas\u0131: ' + (data.error || ''), 'red');
      showToast('error', 'Silme Ba\u015far\u0131s\u0131z', data.error || 'Bilinmeyen hata', 6000);
    }
  } catch(e) {
    setLibStatus('Bağlantı hatası.', 'red');
  }
  loadLibrary();
}

// ── Status helpers ─────────────────────────────────────────────────────────────
const colors = { green: '#60a5fa', amber: '#fbbf24', red: '#fb7185' };
function setLibStatus(msg, color) {
  color = color || 'green';
  const el   = document.getElementById('libStatusText');
  const pill = el.closest('.status-pill');
  el.textContent = msg;
  pill.style.color = colors[color] || colors.green;
}
function setAnalysisStatus(msg, color) {
  color = color || 'green';
  const el = document.getElementById('analysisStatus');
  el.textContent = msg;
  el.style.color = colors[color] || colors.green;
}

function escHtml(str) {
  if (!str) return '';
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Event listeners ────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', function() {
  mountAccountMenu();
  mountLibraryBottomMenu();
  setupEmailCodeInputs();
  setupPasswordCodeInputs();
  const ta = document.getElementById('promptInput');
  if (ta) {
    ta.addEventListener('input', function(){ autoResizeTA(ta); });
    ta.addEventListener('keydown', function(e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); analyze(); }
    });
  }
  const dmInput = document.getElementById('dmTextInput');
  if (dmInput) {
    dmInput.addEventListener('keydown', function(e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendDmMessage(); }
    });
  }
  document.getElementById('delOverlay').addEventListener('click', function(e) {
    if (e.target === this) hideDelConfirm();
  });
  document.addEventListener('click', function(e) {
    const menu = document.getElementById('accountMenu');
    const chip = document.getElementById('accountChip');
    if (!menu || !chip) return;
    if (!menu.contains(e.target) && !chip.contains(e.target)) closeAccountMenu();
  });
  document.addEventListener('click', function(e) {
    const menu = document.getElementById('accountMenu');
    const toggle = document.getElementById('profileStatusToggle');
    const popover = document.getElementById('profilePresencePopover');
    if (!popover || !popover.classList.contains('active')) return;
    if ((popover && popover.contains(e.target)) || (toggle && toggle.contains(e.target))) return;
    if (!menu || !menu.contains(e.target)) {
      closeProfilePresencePicker();
      return;
    }
    closeProfilePresencePicker();
  });
});

document.addEventListener('keydown', function(e) {
  var pdfActive = document.getElementById('pdfViewerOverlay').classList.contains('active');
  if (e.key === 'Escape') {
    if (document.getElementById('passwordChangeOverlay') && document.getElementById('passwordChangeOverlay').classList.contains('active')) { closePasswordChangeModal(); }
    else if (document.getElementById('dmOverlay') && document.getElementById('dmOverlay').classList.contains('active')) { closeDmOverlay(); }
    else if (document.getElementById('avatarCropOverlay') && document.getElementById('avatarCropOverlay').classList.contains('active')) { closeAvatarCropModal(); }
    else if (document.getElementById('verifyRequiredOverlay') && document.getElementById('verifyRequiredOverlay').classList.contains('active')) { closeVerifyRequiredModal(); }
    else if (document.getElementById('emailCodeOverlay') && document.getElementById('emailCodeOverlay').classList.contains('active')) { closeEmailCodeModal(); }
    else if (document.getElementById('accountMenu') && document.getElementById('accountMenu').classList.contains('active')) { closeAccountMenu(); }
    else if (document.getElementById('profileSettingsOverlay') && document.getElementById('profileSettingsOverlay').classList.contains('active')) { closeProfileSettings(); }
    else if (document.getElementById('adminToolsOverlay') && document.getElementById('adminToolsOverlay').classList.contains('active')) { closeAdminTools(); }
    else if (_activeAnalyzeController) { stopCurrentAnalysis(); }
    else if (pdfActive) { closePdfViewer(); }
    else { hideDelConfirm(); }
    return;
  }
  if (pdfActive) {
    if (e.key === 'ArrowLeft')  { pdfPrevPage(); e.preventDefault(); }
    if (e.key === 'ArrowRight') { pdfNextPage(); e.preventDefault(); }
    if (e.key === '+' || e.key === '=') { pdfZoom(0.25); e.preventDefault(); }
    if (e.key === '-')          { pdfZoom(-0.25); e.preventDefault(); }
    return;
  }
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') analyze();
});

// ── Add Book modal ─────────────────────────────────────────────────────────────
function openAddBook() {
  document.getElementById('addFileId').value  = '';
  document.getElementById('addBookName').value = '';
  document.getElementById('addGrade').value   = selectedGrade;
  document.getElementById('addOverlay').classList.add('active');
  setTimeout(function(){ document.getElementById('addFileId').focus(); }, 120);
}

function closeAddBook() {
  document.getElementById('addOverlay').classList.remove('active');
}

async function submitAddBook() {
  var rawInput = document.getElementById('addFileId').value.trim();
  if (!rawInput) {
    document.getElementById('addFileId').focus();
    return;
  }

  // Extract file ID from a full Drive URL if pasted
  var fileId = rawInput;
  if (rawInput.indexOf('/file/d/') !== -1) {
    var part = rawInput.split('/file/d/')[1] || '';
    fileId = part.split('/')[0].split('?')[0].split('&')[0];
  } else if (rawInput.indexOf('id=') !== -1) {
    var part2 = rawInput.split('id=')[1] || '';
    fileId = part2.split('&')[0];
  }
  fileId = fileId.trim();

  var name  = document.getElementById('addBookName').value.trim();
  var grade = document.getElementById('addGrade').value;

  var btn = document.getElementById('addBookBtn');
  btn.disabled = true;
  btn.textContent = 'Ekleniyor...';

  try {
    var res  = await fetch('/api/add_book', {
      method: 'POST',
      headers: Object.assign({ 'Content-Type': 'application/json' }, authHeaders()),
      body: JSON.stringify({ file_id: fileId, name: name || null, grade: grade })
    });
    var data = await res.json();
    if (data.success) {
      closeAddBook();
      showToast('success', 'Kitap Eklendi', (data.book.title || fileId) + ' k\u00fct\u00fcphaneye eklendi.', 5000);
      if (grade === selectedGrade) loadLibrary();
    } else if (data.auth === false) {
      sessionStorage.removeItem('admin_auth_token');
      requireAuth(function(){ submitAddBook(); });
    } else {
      showToast('error', 'Eklenemedi', data.error || 'Bilinmeyen hata', 6000);
    }
  } catch(e) {
    showToast('error', 'Ba\u011flant\u0131 Hatas\u0131', e.message, 5000);
  }

  btn.disabled = false;
  btn.textContent = 'Ekle';
}

document.getElementById('addOverlay').addEventListener('click', function(e) {
  if (e.target === this) closeAddBook();
});

document.getElementById('addFileId').addEventListener('keydown', function(e) {
  if (e.key === 'Enter') submitAddBook();
});

// ── Settings modal ─────────────────────────────────────────────────────────────
async function openSettings() {
  try {
    const res = await fetch('/api/config');
    const cfg = await res.json();
    const ids  = cfg.folder_ids || {};
    document.getElementById('cfgFolder9').value  = ids['9']  || '';
    document.getElementById('cfgFolder10').value = ids['10'] || '';
  } catch(e) {}
  document.getElementById('cfgOverlay').classList.add('active');
}

function closeSettings() {
  document.getElementById('cfgOverlay').classList.remove('active');
}

async function saveSettings() {
  const folderIds = {
    '9':  document.getElementById('cfgFolder9').value.trim(),
    '10': document.getElementById('cfgFolder10').value.trim()
  };
  try {
    await fetch('/api/config', {
      method: 'POST',
      headers: Object.assign({ 'Content-Type': 'application/json' }, authHeaders()),
      body: JSON.stringify({ folder_ids: folderIds })
    });
    closeSettings();
    showToast('success', 'Ayarlar Kaydedildi', 'Klas\u00f6r ayarlar\u0131 g\u00fcncellendi. Senkronize ediliyor...', 4000);
    syncManual();
  } catch(e) {
    showToast('error', 'Hata', e.message, 5000);
  }
}

document.getElementById('cfgOverlay').addEventListener('click', function(e) {
  if (e.target === this) closeSettings();
});

// ── Startup ────────────────────────────────────────────────────────────────────
async function startApp() {
  if (_appStarted) {
    updateNetworkStatus();
    await loadLibrary();
    startDmPolling();
    return;
  }
  loadChatStore();
  renderChatHistory();
  loadChatStoreFromServer().then(renderChatHistory).catch(function() {});
  updateNetworkStatus();
  await loadLibrary();
  syncSilent();
  setTimeout(prefetchAllGrades, 1000);
  startDmPolling();
  _appStarted = true;
}
bootApp();
</script>
</body>
</html>
"""

CONTACT_EMAIL = "contact@reyliar.xyz"

LEGAL_PAGE_TEMPLATE = """
<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ title }} · ReylAI</title>
<meta name="theme-color" content="#030712">
<link rel="icon" type="image/png" href="{{ reylai_icon_src }}">
<link rel="apple-touch-icon" href="{{ reylai_icon_src }}">
<script>
(function() {
  if (!window.history || !window.history.replaceState) return;
  var path = window.location.pathname;
  var nextPath = "";
  if (/\/index\.html$/i.test(path)) nextPath = path.replace(/index\.html$/i, "");
  else if (/\/terms\.html$/i.test(path)) nextPath = path.replace(/terms\.html$/i, "terms");
  else if (/\/privacy\.html$/i.test(path)) nextPath = path.replace(/privacy\.html$/i, "privacy");
  if (nextPath && nextPath !== path) window.history.replaceState(null, "", nextPath + window.location.search + window.location.hash);
})();
</script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800;900&family=Manrope:wght@600;700;800&display=swap" rel="stylesheet">
<style>
:root {
  color-scheme: dark;
  --bg: #030712;
  --panel: rgba(15,23,42,0.72);
  --panel-strong: rgba(15,23,42,0.94);
  --line: rgba(147,197,253,0.18);
  --text: #eef5ff;
  --muted: #a7b2c7;
  --accent: #60a5fa;
  --warm: #fbbf24;
}
* { box-sizing: border-box; }
html, body { margin: 0; min-height: 100%; background: var(--bg); color: var(--text); font-family: Inter, system-ui, sans-serif; }
body {
  background:
    radial-gradient(circle at 8% 0%, rgba(96,165,250,0.18), transparent 30%),
    radial-gradient(circle at 90% 12%, rgba(251,191,36,0.10), transparent 28%),
    linear-gradient(180deg, #030712 0%, #07101f 54%, #030712 100%);
}
.legal-shell { width: min(940px, calc(100% - 30px)); margin: 0 auto; padding: 28px 0 38px; }
.legal-nav { display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-bottom: 34px; }
.legal-brand { display: inline-flex; align-items: center; gap: 10px; color: var(--text); text-decoration: none; font-weight: 950; }
.legal-brand img { width: 36px; height: 36px; border-radius: 12px; box-shadow: 0 12px 32px rgba(0,0,0,0.28); }
.legal-back { color: var(--muted); text-decoration: none; font-weight: 800; border: 1px solid var(--line); border-radius: 999px; padding: 9px 13px; background: rgba(255,255,255,0.06); }
.legal-back:hover, .legal-link:hover { color: #fff; border-color: rgba(191,219,254,0.42); }
.legal-hero { padding: 8px 0 24px; border-bottom: 1px solid var(--line); margin-bottom: 20px; }
.legal-kicker { color: var(--accent); font-size: 12px; font-weight: 950; letter-spacing: .16em; text-transform: uppercase; }
h1 { font-family: Manrope, Inter, sans-serif; font-size: clamp(34px, 7vw, 70px); line-height: 0.96; letter-spacing: 0; margin: 10px 0 14px; }
.legal-lead { max-width: 720px; color: var(--muted); font-size: 17px; line-height: 1.7; margin: 0; }
.legal-meta { display: flex; flex-wrap: wrap; gap: 9px; margin-top: 18px; }
.legal-pill { border: 1px solid var(--line); border-radius: 999px; color: var(--muted); padding: 7px 11px; background: rgba(255,255,255,0.05); font-size: 12px; font-weight: 800; }
.legal-card { border: 1px solid var(--line); border-radius: 20px; background: var(--panel); box-shadow: 0 28px 90px rgba(0,0,0,0.28), inset 0 1px rgba(255,255,255,0.08); overflow: hidden; backdrop-filter: blur(22px) saturate(1.24); }
.legal-prose { padding: clamp(20px, 4vw, 38px); }
.legal-prose h2 { margin: 28px 0 10px; font-size: 21px; line-height: 1.24; }
.legal-prose h2:first-child { margin-top: 0; }
.legal-prose p, .legal-prose li { color: var(--muted); line-height: 1.74; }
.legal-prose ul { padding-left: 22px; }
.legal-prose strong { color: var(--text); }
.legal-note { margin: 22px 0; padding: 14px 16px; border-left: 3px solid var(--warm); background: rgba(251,191,36,0.08); border-radius: 12px; color: #f8e7b0; }
.legal-link { color: #bfdbfe; font-weight: 800; text-decoration: none; border-bottom: 1px solid rgba(191,219,254,0.34); }
.legal-footer { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-top: 18px; color: rgba(167,178,199,0.82); font-size: 12px; }
@media (max-width: 640px) {
  .legal-nav { align-items: flex-start; flex-direction: column; }
  .legal-back { width: 100%; text-align: center; }
  .legal-prose { padding: 20px; }
}
</style>
</head>
<body>
  <main class="legal-shell">
    <nav class="legal-nav">
      <a class="legal-brand" href="/"><img src="{{ reylai_icon_src }}" alt="ReylAI"><span>ReylAI</span></a>
      <a class="legal-back" href="/">Ana sayfaya dön</a>
    </nav>
    <section class="legal-hero">
      <div class="legal-kicker">{{ kicker }}</div>
      <h1>{{ title }}</h1>
      <p class="legal-lead">{{ lead }}</p>
      <div class="legal-meta">
        <span class="legal-pill">Son güncelleme: 9 Haziran 2026</span>
        <span class="legal-pill">İletişim: {{ contact_email }}</span>
      </div>
    </section>
    <article class="legal-card">
      <div class="legal-prose">
{{ body|safe }}
      </div>
    </article>
    <footer class="legal-footer">
      <span>©2026 ReylAI. All Rights Reserved.</span>
      <span><a class="legal-link" href="/terms">Kullanım Şartları</a> · <a class="legal-link" href="/privacy">Gizlilik Politikası</a> · <a class="legal-link" href="mailto:{{ contact_email }}">{{ contact_email }}</a></span>
    </footer>
  </main>
</body>
</html>
"""

TERMS_BODY = """
<h2>1. Kabul</h2>
<p>ReylAI'yi kullanarak bu Kullanım Şartları'nı kabul etmiş olursun. Bu şartları kabul etmiyorsan hizmeti kullanmamalısın.</p>
<div class="legal-note"><strong>Kısa özet:</strong> ReylAI eğitim ve üretkenlik odaklı bir araçtır; çıktıları kontrol etmek ve kendi kararını vermek kullanıcı sorumluluğundadır.</div>

<h2>2. Hizmetin kapsamı</h2>
<p>ReylAI; ders kitabı içeriklerini arama, özetleme, analiz etme, sohbet geçmişi tutma ve hesaplar arasında DM gönderme gibi özellikler sağlayabilir. Özellikler zaman içinde değişebilir, geçici olarak durabilir veya geliştirilebilir.</p>

<h2>3. Kullanıcı sorumlulukları</h2>
<ul>
  <li>Hesap bilgilerini doğru ve güvenli tutmalısın.</li>
  <li>Başkasının hesabına, verisine veya cihazına izinsiz erişmeye çalışmamalısın.</li>
  <li>DM, dosya eki, profil bilgisi veya prompt alanlarında hukuka aykırı, zararlı, taciz edici ya da izinsiz kişisel veri içeren içerik paylaşmamalısın.</li>
  <li>AI yanıtlarını tek doğruluk kaynağı gibi kullanmadan önce kontrol etmelisin.</li>
</ul>

<h2>4. AI çıktıları</h2>
<p>AI yanıtları otomatik üretilir ve hata içerebilir. ReylAI, eğitim desteği sunmayı amaçlar; profesyonel, akademik, hukuki, finansal veya tıbbi danışmanlık yerine geçmez.</p>

<h2>5. İçerik ve lisans</h2>
<p>Uygulamaya gönderdiğin içeriklerin gerekli haklarına sahip olduğundan emin olmalısın. İçeriğini yalnızca hizmeti sağlamak, güvenliğini korumak, hataları gidermek ve özellikleri çalıştırmak için gerekli ölçüde işleyebiliriz.</p>

<h2>6. Hesap, güvenlik ve erişim</h2>
<p>Güvenlik, kötüye kullanım, sistem bütünlüğü veya yasal gereklilikler nedeniyle hesap erişimini sınırlayabilir, askıya alabilir veya belirli içerikleri kaldırabiliriz.</p>

<h2>7. Sorumluluk sınırı</h2>
<p>Hizmet mümkün olan en iyi şekilde sunulmaya çalışılır; ancak kesintisiz, hatasız veya her ihtiyaca uygun olacağı garanti edilmez. Yürürlükteki kanunların izin verdiği ölçüde dolaylı zararlar, veri kaybı veya kullanım kaybından sorumlu olmayız.</p>

<h2>8. Değişiklikler</h2>
<p>Bu şartlar güncellenebilir. Önemli değişikliklerde makul şekilde bildirim yapılır. Güncellenmiş şartlardan sonra hizmeti kullanmaya devam etmen yeni şartları kabul ettiğin anlamına gelir.</p>

<h2>9. İletişim</h2>
<p>Sorular, kaldırma talepleri veya güvenlik bildirimleri için bize <a class="legal-link" href="mailto:contact@reyliar.xyz">contact@reyliar.xyz</a> adresinden ulaşabilirsin.</p>
"""

PRIVACY_BODY = """
<h2>1. Topladığımız bilgiler</h2>
<p>ReylAI'yi çalıştırmak için hesap e-postası, görünen ad, profil fotoğrafı, oturum bilgileri, doğrulama durumu, sohbet geçmişi, DM içerikleri, dosya ekleri, durum bilgisi ve teknik günlükler gibi bilgiler işlenebilir.</p>

<h2>2. Bilgileri nasıl kullanırız?</h2>
<ul>
  <li>Hesap oluşturma, giriş, e-posta doğrulama ve şifre güvenliği işlemlerini yürütmek.</li>
  <li>Sohbet, kitap analizi, DM ve bildirim özelliklerini çalıştırmak.</li>
  <li>Kötüye kullanım, güvenlik açıkları ve sistem hatalarını tespit etmek.</li>
  <li>Performansı, güvenilirliği ve kullanıcı deneyimini iyileştirmek.</li>
</ul>

<h2>3. AI ve içerik işleme</h2>
<p>Promptların, seçili kitap bağlamı ve sohbet geçmişinle birlikte AI yanıtı üretmek için işlenebilir. Hassas kişisel verileri promptlara veya DM'lere eklememeni öneririz.</p>

<h2>4. DM ve iletişim verileri</h2>
<p>DM mesajları, iletilen AI yanıtları, dosya ekleri ve okunma durumları mesajlaşma özelliğini sağlamak için saklanabilir. E-posta bildirimleri, okunmamış mesajlar hakkında kısa bir özet içerebilir.</p>

<h2>5. Çerezler ve yerel depolama</h2>
<p>Oturum tokenları, cihaz hatırlama tercihleri ve arayüz durumları tarayıcıdaki localStorage/sessionStorage benzeri mekanizmalarda tutulabilir. Bu verileri tarayıcı ayarlarından silebilirsin.</p>

<h2>6. Paylaşım</h2>
<p>Veriler; barındırma, veritabanı, e-posta gönderimi, güvenlik doğrulaması ve AI yanıt üretimi gibi hizmet sağlayıcılarla yalnızca gerekli olduğu ölçüde paylaşılabilir. Verilerini satmayız.</p>

<h2>7. Saklama</h2>
<p>Hesap ve içerik verileri hizmeti sağlamak için gerekli olduğu sürece saklanır. Güvenlik veya yasal nedenlerle bazı kayıtlar daha uzun tutulabilir.</p>

<h2>8. Hakların</h2>
<p>Hesap bilgilerini güncelleme, belirli verilerin silinmesini isteme veya gizlilikle ilgili soru sorma hakkın vardır. Taleplerini <a class="legal-link" href="mailto:contact@reyliar.xyz">contact@reyliar.xyz</a> adresine gönderebilirsin.</p>

<h2>9. Güvenlik</h2>
<p>Parolalar düz metin olarak saklanmaz; oturumlar, doğrulama kodları ve yönetici işlemleri için güvenlik kontrolleri kullanılır. Yine de hiçbir sistem tamamen risksiz değildir.</p>

<h2>10. Güncellemeler</h2>
<p>Bu Gizlilik Politikası zaman zaman güncellenebilir. Önemli değişikliklerde uygun bir bildirim yöntemi kullanılabilir.</p>
"""


def render_legal_page(kind):
    pdfjs_version = _pdfjs_asset_version()
    page = {
        "terms": {
            "title": "Kullanım Şartları",
            "kicker": "ReylAI yasal",
            "lead": "ReylAI'yi kullanırken geçerli olan temel kurallar, sorumluluklar ve kullanım şartları.",
            "body": TERMS_BODY,
        },
        "privacy": {
            "title": "Gizlilik Politikası",
            "kicker": "ReylAI gizlilik",
            "lead": "Hangi verileri işlediğimiz, bunları nasıl kullandığımız ve bizimle nasıl iletişime geçebileceğin.",
            "body": PRIVACY_BODY,
        },
    }[kind]
    return render_template_string(
        LEGAL_PAGE_TEMPLATE,
        reylai_icon_src=_asset_data_url("static/reylai_icon.png", "/static/reylai_icon.png"),
        contact_email=CONTACT_EMAIL,
        pdfjs_version=pdfjs_version,
        **page,
    )


@app.route('/')
def index():
    pdfjs_version = _pdfjs_asset_version()
    return render_template_string(
        HTML,
        meb_logo_src=_asset_data_url("static/meb_logo.png", "/static/meb_logo.png"),
        reylai_icon_src=_asset_data_url("static/reylai_icon.png", "/static/reylai_icon.png"),
        books_stack_src=_asset_data_url("static/books_stack.png", "/static/books_stack.png"),
        books_remote_base_url=BOOKS_REMOTE_BASE_URL,
        pdfjs_lib_url=f"/pdfjs/pdf.min.js?v={pdfjs_version}",
        pdfjs_worker_url=f"/pdfjs/pdf.worker.min.js?v={pdfjs_version}",
    )


@app.route('/index.html')
def index_html_redirect():
    return redirect('/', code=301)


@app.route('/terms')
def terms_page():
    return render_legal_page("terms")


@app.route('/terms.html')
def terms_html_redirect():
    return redirect('/terms', code=301)


@app.route('/privacy')
def privacy_page():
    return render_legal_page("privacy")


@app.route('/privacy.html')
def privacy_html_redirect():
    return redirect('/privacy', code=301)


@app.route('/pdfjs/<path:filename>')
def api_pdfjs_asset(filename):
    asset_path = _pdfjs_asset_path(filename)
    if not asset_path:
        return ('PDF okuyucu dosyası bulunamadı', 404)
    mimetype = 'application/javascript'
    return send_file(asset_path, mimetype=mimetype, max_age=3600, conditional=True)


@app.route('/api/verify_password', methods=['POST'])
def api_verify_password():
    token = secrets.token_hex(32)
    _auth_tokens.add(token)
    return jsonify({'success': True, 'token': token})


@app.route('/api/rename_book', methods=['POST'])
def api_rename_book():
    if not _check_auth():
        return jsonify({'success': False, 'auth': False, 'error': 'Yetkilendirme gerekli.'})
    data = request.get_json() or {}
    bid = data.get('book_id', '')
    new_name = (data.get('name') or '').strip()
    if not new_name:
        return jsonify({'success': False, 'error': '\u0130sim bo\u015f olamaz.'})
    library = load_library()
    book = next((b for b in library if b.get('book_id') == bid or b.get('drive_id') == bid), None)
    if not book:
        return jsonify({'success': False, 'error': 'Kitap bulunamad\u0131.'})
    book['title'] = new_name
    if 'name' in book:
        book['name'] = new_name
    save_library(library)
    return jsonify({'success': True})


@app.route('/api/update_cover', methods=['POST'])
def api_update_cover():
    if not _check_auth():
        return jsonify({'success': False, 'auth': False, 'error': 'Yetkilendirme gerekli.'})
    if not _HAS_PIL:
        return jsonify({'success': False, 'error': 'Pillow paketi kurulu değil; thumbnail işlenemedi.'})

    bid = (request.form.get('book_id') or request.form.get('drive_id') or '').strip()
    file = request.files.get('cover')
    if not bid:
        return jsonify({'success': False, 'error': 'Kitap kimliği eksik.'})
    if not file or not file.filename:
        return jsonify({'success': False, 'error': 'Thumbnail dosyası bulunamadı.'})
    if file.mimetype not in {'image/jpeg', 'image/png', 'image/webp'}:
        return jsonify({'success': False, 'error': 'Sadece JPG, PNG veya WebP görsel yüklenebilir.'})

    library = load_library()
    book = _find_library_book_for_selection(library, bid)
    if not book:
        return jsonify({'success': False, 'error': 'Kitap bulunamadı.'})

    cover_key = _book_scan_key(book)
    if not cover_key:
        return jsonify({'success': False, 'error': 'Kapak için kitap kimliği bulunamadı.'})
    cover_path = os.path.join(COVERS_DIR, cover_key + '.jpg')

    try:
        image = _PILImage.open(file.stream)
        image.thumbnail((1200, 1600))
        if image.mode in ('RGBA', 'LA') or (image.mode == 'P' and 'transparency' in image.info):
            alpha_image = image.convert('RGBA')
            canvas = _PILImage.new('RGB', alpha_image.size, (255, 255, 255))
            canvas.paste(alpha_image, mask=alpha_image.split()[-1])
            image = canvas
        else:
            image = image.convert('RGB')
        image.save(cover_path, 'JPEG', quality=88, optimize=True)
    except Exception:
        return jsonify({'success': False, 'error': 'Thumbnail görseli okunamadı.'})

    book['cover_path'] = cover_path
    book['cover_updated_at'] = _utc_now_iso()
    save_library(library)
    return jsonify({
        'success': True,
        'cover_url': '/api/cover/' + cover_key + '?v=' + str(int(time.time())),
        'cover_data_url': _file_data_url(cover_path),
    })


@app.route('/api/library')
def api_library():
    grade   = request.args.get('grade', None)
    library = load_library()
    result  = []
    for book in library:
        if grade and book.get('grade', '9') != grade:
            continue
        result.append(_public_book_payload(book))
    return jsonify(result)


@app.route('/api/chat_history', methods=['GET'])
def api_chat_history():
    return jsonify(load_chat_history())


@app.route('/api/chat_history', methods=['POST', 'PUT'])
def api_save_chat_history():
    data = request.get_json(silent=True) or {}
    store = save_chat_history(data)
    return jsonify({'success': True, 'store': store})


@app.route('/api/chat_history/<chat_id>', methods=['DELETE'])
def api_delete_chat_history(chat_id):
    chat_id = _clean_chat_string(chat_id, 140)
    store = load_chat_history()
    store['chats'] = [chat for chat in store.get('chats', []) if chat.get('id') != chat_id]
    store = save_chat_history(store)
    return jsonify({'success': True, 'store': store})


@app.route('/api/add_book', methods=['POST'])
def api_add_book():
    if not _check_auth():
        return jsonify({'success': False, 'auth': False, 'error': 'Yetkilendirme gerekli.'})

    data = request.get_json(silent=True) or {}
    file_id = (data.get('file_id') or '').strip()
    name = (data.get('name') or '').strip()
    grade = (data.get('grade') or '9').strip()

    if not file_id:
        return jsonify({'success': False, 'error': 'Drive dosya kimligi gerekli.'})
    if grade not in ['9', '10']:
        grade = '9'

    library = load_library()
    existing = next((b for b in library if b.get('drive_id') == file_id), None)
    if existing:
        return jsonify({'success': True, 'existing': True, 'book': _public_book_payload(existing)})

    display_name = name or file_id
    entry = {
        'book_id':     '',
        'name':        display_name,
        'title':       _clean_title(display_name),
        'drive_id':    file_id,
        'local_path':  '',
        'grade':       grade,
        'scan_status': 'pending',
        'scan_pages':  0,
        'added_at':    _utc_now_iso()
    }
    library.append(entry)
    save_library(library)
    start_scan(file_id, drive_id=file_id)
    return jsonify({'success': True, 'book': _public_book_payload(entry)})


@app.route('/api/upload', methods=['POST'])
def api_upload():
    if not _check_auth():
        return jsonify({'success': False, 'auth': False, 'error': 'Yetkilendirme gerekli.'})
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'Dosya bulunamad\u0131'})
    file  = request.files['file']
    grade = request.form.get('grade', '9').strip()
    name  = (request.form.get('name') or file.filename or '').strip()
    if not name.lower().endswith('.pdf'):
        return jsonify({'success': False, 'error': 'Sadece PDF y\u00fcklenebilir'})
    if grade not in ['9', '10']:
        grade = '9'

    book_id   = str(uuid.uuid4())
    grade_dir = os.path.join(BOOKS_DIR, grade)
    os.makedirs(grade_dir, exist_ok=True)
    local_path = os.path.join(grade_dir, book_id + '.pdf')
    file.save(local_path)

    entry = {
        'book_id':     book_id,
        'name':        name,
        'title':       _clean_title(name),
        'drive_id':    '',
        'local_path':  local_path,
        'grade':       grade,
        'scan_status': 'pending',
        'scan_pages':  0,
        'added_at':    _utc_now_iso()
    }
    library = load_library()
    library.append(entry)
    save_library(library)
    start_scan(book_id, local_path=local_path)

    # Extract cover image and title in background thread
    def _post_upload(bid, lpath, lib_entry):
        cover = _extract_cover(bid, lpath)
        if cover:
            title = _extract_title_from_cover(cover)
            lib2 = load_library()
            for b in lib2:
                if (b.get('book_id') or b.get('drive_id', '')) == bid:
                    b['cover_path'] = cover
                    if title:
                        b['title'] = title
                    break
            save_library(lib2)

    threading.Thread(target=_post_upload, args=(book_id, local_path, entry), daemon=True).start()
    return jsonify({'success': True, 'book': _public_book_payload(entry)})


@app.route('/api/serve_pdf/<book_id>')
def api_serve_pdf(book_id):
    library = load_library()
    for b in library:
        if b.get('book_id') == book_id or b.get('drive_id') == book_id:
            remote_url = _book_remote_pdf_url(b)
            if remote_url:
                proxied = _proxy_remote_pdf(remote_url, request.headers.get('Range', ''))
                if proxied is not None:
                    return proxied

            local_path = _resolve_app_path(b.get('local_path', ''))
            if local_path and os.path.exists(local_path):
                return _serve_local_pdf_response(local_path)

            lp = _ensure_local_pdf(b, library)
            if lp and os.path.exists(lp):
                return _serve_local_pdf_response(lp)
    return ('PDF bulunamad\u0131', 404)


@app.route('/api/page_image/<book_id>/<int:page_no>')
def api_page_image(book_id, page_no):
    library = load_library()
    book = _find_library_book_for_selection(library, book_id)
    if not book:
        return ('Kitap bulunamadı', 404)
    image_path, error = _render_pdf_page_image(book, book_id, page_no)
    if image_path and os.path.exists(image_path):
        return send_file(image_path, mimetype='image/jpeg', max_age=86400, conditional=True)
    status = 503 if not _HAS_PDF2IMAGE else 404
    return (error or 'Sayfa görseli bulunamadı', status)



@app.route('/api/cover/<book_id>')
def api_cover(book_id):
    """Serve cover image for a book, extracting it on-demand if needed."""
    # Look up local_path
    library = load_library()
    bk = next((b for b in library
                if b.get('book_id') == book_id or b.get('drive_id') == book_id), None)

    cover_path = os.path.join(COVERS_DIR, book_id + '.jpg')

    # If cover doesn't exist yet, extract it only from an already-local PDF.
    if not os.path.exists(cover_path) and bk:
        local_path = _resolve_app_path(bk.get('local_path', ''))
        if local_path and os.path.exists(local_path):
            cover_path = _extract_cover(book_id, local_path) or cover_path

    if os.path.exists(cover_path):
        return send_file(cover_path, mimetype='image/jpeg',
                         max_age=86400, conditional=True)
    return ('', 404)


@app.route('/api/config', methods=['GET', 'POST'])
def api_config():
    if request.method == 'POST':
        if not _check_auth():
            return jsonify({'success': False, 'auth': False, 'error': 'Yetkilendirme gerekli.'})
        data = request.get_json(silent=True) or {}
        cfg  = load_config()
        cfg.update(data)
        save_config(cfg)
        return jsonify({'success': True})
    return jsonify(load_config())



@app.route('/api/debug_gas')
def api_debug_gas():
    if not GAS_WEB_APP_URL or GAS_WEB_APP_URL == "YOUR_WEB_APP_URL":
        return jsonify({'error': 'GAS_WEB_APP_URL ayarlanmamış'})
    results = {}
    for grade in ['9', '10']:
        try:
            res = requests.get(GAS_WEB_APP_URL,
                               params={'action': 'list', 'grade': grade},
                               timeout=20,
                               allow_redirects=True)
            results[grade] = {'status': res.status_code, 'raw': res.text[:2000]}
        except Exception as e:
            results[grade] = {'error': str(e)}
    return jsonify(results)


@app.route('/api/sync_cloud', methods=['POST'])
def api_sync_cloud():
    if not _check_auth():
        return jsonify({'success': False, 'auth': False, 'error': 'Yetkilendirme gerekli.'})
    if not GAS_WEB_APP_URL or GAS_WEB_APP_URL == "YOUR_WEB_APP_URL":
        return jsonify({'success': False, 'skipped': True,
                        'error': 'GAS_WEB_APP_URL yap\u0131land\u0131r\u0131lmam\u0131\u015f'})

    library  = load_library()
    uploaded = 0
    skipped  = 0
    errors   = []

    for book in library:
        if book.get('drive_id'):
            skipped += 1
            continue
        local_path = _resolve_app_path(book.get('local_path', ''))
        if not local_path or not os.path.exists(local_path):
            skipped += 1
            continue
        try:
            with open(local_path, 'rb') as fh:
                encoded = base64.b64encode(fh.read()).decode('utf-8')
            file_name = book.get('name', (book.get('book_id', '') + '.pdf'))
            grade     = book.get('grade', '9')
            payload = {
                'action':   'upload',
                'fileName': file_name,
                'fileData': encoded,
                'grade':    grade
            }
            res = requests.post(GAS_WEB_APP_URL,
                                json=payload,
                                timeout=120,
                                allow_redirects=False)
            if res.status_code in (301, 302, 303, 307, 308):
                redirect_url = res.headers.get('Location', '')
                if redirect_url:
                    res = requests.post(redirect_url,
                                        json=payload,
                                        timeout=120,
                                        allow_redirects=True)
            raw = res.text.strip()
            if not raw:
                errors.append((book.get('title') or book.get('name') or '?') + ': GAS bo\u015f yan\u0131t d\u00f6nd\u00fc (HTTP ' + str(res.status_code) + ')')
                continue
            try:
                result = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                snippet = raw[:200]
                errors.append((book.get('title') or book.get('name') or '?') + ': GAS ge\u00e7ersiz yan\u0131t: ' + snippet)
                continue
            if result.get('success') and result.get('file_id'):
                book['drive_id'] = result['file_id']
                uploaded += 1
            else:
                errors.append((book.get('title') or book.get('name') or '?') + ': ' + str(result.get('error', 'Bilinmeyen hata')))
        except Exception as exc:
            errors.append(book.get('title', '?') + ': ' + str(exc))

    save_library(library)
    return jsonify({'success': True, 'uploaded': uploaded, 'skipped': skipped, 'errors': errors})


@app.route('/api/scan_status/<book_id>')
def api_scan_status(book_id):
    library = load_library()
    book = _find_library_book_for_selection(library, book_id)
    if book:
        scan_data, _scan_path, _scan_key = _load_scan_data_for_book(book, book_id)
        if scan_data:
            _sync_library_scan_status(library, book, scan_data)
            return jsonify({
                'scan_status': 'done',
                'scan_pages': scan_data.get('total_pages') or len(scan_data.get('pages') or []),
                'scan_extractor': _public_scan_extractor(scan_data.get('extractor', ''))
            })
        return jsonify({
            'scan_status': book.get('scan_status', 'unknown'),
            'scan_pages':  book.get('scan_pages', 0),
            'scan_extractor': _public_scan_extractor(book.get('scan_extractor', ''))
        })
    scan_path = os.path.join(SCANS_DIR, book_id + '.json')
    if os.path.exists(scan_path):
        try:
            with open(scan_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if _scan_data_is_usable(data):
                return jsonify({
                    'scan_status': 'done',
                    'scan_pages': data.get('total_pages') or len(data.get('pages') or []),
                    'scan_extractor': _public_scan_extractor(data.get('extractor', ''))
                })
        except Exception:
            pass
    return jsonify({'scan_status': 'failed', 'scan_pages': 0})


@app.route('/api/scan_missing_books', methods=['POST'])
def api_scan_missing_books():
    snapshot = _batch_scan_snapshot()
    if snapshot.get('running'):
        return jsonify({'success': True, 'started': False, 'job': snapshot})

    next_job_id = int(snapshot.get('job_id', 0)) + 1
    _batch_scan_update(
        job_id=next_job_id,
        running=True,
        completed=False,
        cancel_requested=False,
        cancelled=False,
        total=0,
        processed=0,
        success=0,
        failed=0,
        already_ready=0,
        current_title='',
        current_message='Tarama işi başlatılıyor…',
        logs=['Tarama işi başlatılıyor…'],
        started_at=_utc_now_iso(),
        finished_at='',
    )
    threading.Thread(target=_run_batch_scan_job, args=(next_job_id,), daemon=True).start()
    return jsonify({'success': True, 'started': True, 'job': _batch_scan_snapshot()})


@app.route('/api/scan_missing_books_status')
def api_scan_missing_books_status():
    return jsonify(_batch_scan_snapshot())


@app.route('/api/scan_missing_books_cancel', methods=['POST'])
def api_scan_missing_books_cancel():
    snapshot = _batch_scan_snapshot()
    if not snapshot.get('running'):
        return jsonify({'success': True, 'job': snapshot, 'cancelled': False})

    if not snapshot.get('cancel_requested'):
        _batch_scan_log('İptal isteği alındı. Mevcut kitap tamamlanınca tarama duracak.')
    _batch_scan_update(
        cancel_requested=True,
        current_message='İptal isteği alındı. Mevcut kitap tamamlanıyor…',
    )
    return jsonify({'success': True, 'job': _batch_scan_snapshot(), 'cancelled': True})


@app.route('/api/delete', methods=['POST'])
def api_delete():
    if not _check_auth():
        return jsonify({'success': False, 'auth': False, 'error': 'Yetkilendirme gerekli.'})
    data    = request.get_json() or {}
    bid     = data.get('book_id') or data.get('drive_id', '')
    library = load_library()

    book = next((b for b in library
                 if b.get('book_id') == bid or b.get('drive_id') == bid), None)
    if not book:
        return jsonify({'success': False, 'error': 'Kitap bulunamad\u0131'})

    # Delete local PDF file
    lp = _resolve_app_path(book.get('local_path', ''))
    if lp and os.path.exists(lp):
        try:
            os.remove(lp)
        except Exception:
            pass

    # Remove cached scan file
    scan_key = book.get('book_id') or book.get('drive_id', '')
    scan_path = os.path.join(SCANS_DIR, scan_key + '.json')
    if os.path.exists(scan_path):
        try:
            os.remove(scan_path)
        except Exception:
            pass

    # Delete from Cloud via GAS (best-effort)
    drive_id = book.get('drive_id', '')
    if drive_id and GAS_WEB_APP_URL and GAS_WEB_APP_URL != "YOUR_WEB_APP_URL":
        try:
            del_payload = {'action': 'delete', 'file_id': drive_id}
            dr = requests.post(GAS_WEB_APP_URL, json=del_payload, timeout=15, allow_redirects=False)
            if dr.status_code in (301, 302, 303, 307, 308):
                rurl = dr.headers.get('Location', '')
                if rurl:
                    requests.post(rurl, json=del_payload, timeout=15, allow_redirects=True)
        except Exception:
            pass

    library = [b for b in library if b is not book]
    save_library(library)
    return jsonify({'success': True})


@app.route('/api/analyze_status/<analysis_id>')
def api_analyze_status(analysis_id):
    status = _analysis_status_snapshot(analysis_id)
    if not status:
        return jsonify({'message': 'Yazıyor...', 'stage': 'pending', 'done': False})
    status.pop('updated_ts', None)
    return jsonify(status)


def _run_analysis_background(data, analysis_id):
    try:
        data = dict(data or {})
        data['analysis_id'] = analysis_id
        with app.test_request_context('/api/analyze', method='POST', json=data):
            response = api_analyze()
            payload = response.get_json(silent=True) or {}

        if payload.get('result'):
            _analysis_status_update(
                analysis_id,
                'Yanıt hazır.',
                'done',
                True,
                result=payload.get('result', ''),
                local=bool(payload.get('local')),
                chat_title=payload.get('chat_title', ''),
            )
        else:
            message = payload.get('error') or 'Analiz başarısız.'
            _analysis_status_update(
                analysis_id,
                message,
                'error',
                True,
                error=message,
                rate_limit=bool(payload.get('rate_limit')),
                temporary_unavailable=bool(payload.get('temporary_unavailable')),
            )
    except Exception as exc:
        _analysis_status_update(
            analysis_id,
            'Analiz beklenmedik şekilde durdu.',
            'error',
            True,
            error=str(exc),
        )


@app.route('/api/analyze_start', methods=['POST'])
def api_analyze_start():
    data = request.get_json(silent=True) or {}
    analysis_id = _clean_analysis_id(data.get('analysis_id')) or uuid.uuid4().hex
    data['analysis_id'] = analysis_id
    _analysis_status_update(analysis_id, 'Analiz başlatılıyor...', 'queued')
    threading.Thread(
        target=_run_analysis_background,
        args=(data, analysis_id),
        daemon=True,
    ).start()
    return jsonify({'success': True, 'analysis_id': analysis_id})


@app.route('/api/analyze', methods=['POST'])
def api_analyze():
    if not _is_configured(MISTRAL_API_KEY, "YOUR_MISTRAL_API_KEY"):
        return jsonify({'error': 'MISTRAL_API_KEY yapılandırılmamış. Proje kökündeki .env dosyasına ekleyin.'})

    data        = request.get_json(silent=True) or {}
    book_id     = data.get('book_id') or data.get('drive_id', '')
    book_name   = data.get('book_name', 'Kitap')
    prompt_text = data.get('prompt')
    analysis_id = _clean_analysis_id(data.get('analysis_id'))
    chat_history = _sanitize_chat_history(data.get('chat_history'))
    title_requested = bool(data.get('title_requested'))

    if not prompt_text:
        _analysis_status_update(analysis_id, 'Prompt eksik.', 'error', True)
        return jsonify({'error': 'Prompt eksik'})
    if not book_id:
        _analysis_status_update(analysis_id, 'Kitap seçimi eksik.', 'error', True)
        return jsonify({'error': 'book_id eksik'})
    if _is_small_talk_prompt(prompt_text):
        result = _small_talk_response(prompt_text)
        chat_title = _fallback_chat_title(prompt_text) if title_requested else ''
        _analysis_status_update(
            analysis_id,
            'Kısa cevap hazırlanıyor...',
            'local',
            True,
            result=result,
            local=True,
            chat_title=chat_title,
        )
        return jsonify({'result': result, 'local': True, 'chat_title': chat_title})

    _analysis_status_update(analysis_id, 'Kitap bilgileri kontrol ediliyor...', 'book')
    library = load_library()
    bk = _find_library_book_for_selection(library, book_id)
    if not bk:
        msg = 'Seçili kitap kütüphanede bulunamadı. Kütüphaneye dönüp kitabı yeniden seçin.'
        _analysis_status_update(analysis_id, msg, 'error', True, error=msg)
        return jsonify({'error': msg, 'missing_book': True})
    _analysis_status_update(analysis_id, 'Hazır tarama metni kontrol ediliyor...', 'context')
    solution_request = _is_solution_request(prompt_text) and not _is_list_only_request(prompt_text)
    expanded_work_request = _is_expanded_work_request(prompt_text) and not _is_list_only_request(prompt_text)
    requested_pages = _extract_page_numbers(prompt_text)

    system_msg = (
        'Sen ReylAI adli bir yapay zeka asistansin. '
        'MEB ders kitaplarini analiz eder, ogrencilere ve ogretmenlere yardimci olursun. '
        'Amacin secili kitaptaki sayfa, soru, konu, kazanim ve odevleri hizli ve guvenilir bicimde aciklamaktir.\n\n'
        'TEMEL DAVRANIS KURALLARI:\n'
        '1. Yalnizca hazir tarama metnine dayan; kitapta olmayan bilgi uydurma.\n'
        '2. Baglam yeterli degilse bunu acikca soyle ve kullanicidan sayfa, soru numarasi veya konu adi iste.\n'
        '3. Yaniti istegin kapsamına gore belirle; sabit karakter ya da cumle limiti uygulama.\n'
        '4. Soru cozuyorsan once yontemi, sonra sonucu ver; etkinlik ve odevlerde gerekli adimlari atlama.\n'
        '5. Mumkunse kaynak sayfayi [Sayfa X] formatinda belirt.\n'
        '6. Kitap disi isteklere sapma; secili kitapla baglantisini kur veya kisa bir netlestirme sorusu sor.\n'
        '7. Alinti gerekiyorsa kisa alinti yap, aciklamayi Turkce ve sade yaz.\n'
        '8. Kullanici "yap", "hazirla", "tamamla", "etkinlik", "performans odevi" veya "proje" derse bunu '
        'gercek bir gorev olarak ele al; uygun basliklar, maddeler, ornek cevaplar ve gerekiyorsa taslak metinle ozenli tamamla.\n'
        '9. Gorsel eklemen gerekiyorsa sahte URL uydurma. Kitaptaki bir sayfayi gorsel olarak gostermek icin '
        'Markdown biciminde ![kisa aciklama](page:190) gibi ilgili sayfa numarasini kullan; ornek.com/example.com '
        'gibi gecici baglantilar yazma. Matematiksel ifadeleri LaTeX ile \\(...\\) veya \\[...\\] biciminde yaz.'
    )
    if expanded_work_request:
        system_msg += (
            '\n\nBU ISTEK ETKINLIK/ODEV URETIMI ODAKLIDIR. '
            'Kisa varsayilan cevap verme. Secili kitap baglamina dayanarak calismayi tamamla; '
            'gerekiyorsa amac, adimlar, cevap/taslak ve kontrol listesi gibi bolumlerle ayrintilandir.'
        )
    elif solution_request:
        system_msg += (
            '\n\nBU ISTEK COZUM ODAKLIDIR. '
            'Cozumu anlasilir adimlarla ver ve en sonda sonucu yaz. Eksik veri varsa netlestirme sor.'
        )
    else:
        system_msg += (
            '\n\nKullanici kisa bir bilgi istiyorsa dogrudan cevap ver; daha genis bir is istiyorsa yeterli ayrintiyi ver.'
        )
    if requested_pages:
        system_msg += '\n\nKullanici ozellikle su sayfa(lar)a odaklaniyor: ' + ', '.join(str(p) for p in requested_pages) + '.'
    history_context = _build_chat_history_context(chat_history)
    if history_context:
        system_msg += (
            '\n\nOnceki konusma ozeti asagidadir. Kullanici devam sorusu soruyorsa bu baglami dikkate al; '
            'ancak nihai cevap yine secili kitabin tarama metnine dayansin:\n'
            + history_context
        )

    context_text = ''
    scan_pages = []
    scan_data, _scan_path, _scan_key = _load_scan_data_for_book(bk, book_id)
    if scan_data:
        _sync_library_scan_status(library, bk, scan_data)
        _analysis_status_update(analysis_id, 'Seçili kitabın hazır tarama metni okunuyor...', 'cache')
        scan_pages = scan_data.get('pages', [])
        _analysis_status_update(analysis_id, 'İlgili sayfalar seçiliyor...', 'context')
        context_text = _build_context_excerpt(scan_pages, prompt_text)
    remote_scan_url = _book_remote_pdf_url(bk)
    if not context_text and remote_scan_url:
        scan_key = _book_scan_key(bk)
        _analysis_status_update(analysis_id, 'PDF deposundan tarama metni hazırlanıyor...', 'scan')
        if scan_key:
            _do_scan(scan_key, remote_url=remote_scan_url)
            library = load_library()
            bk = _find_library_book_for_selection(library, book_id) or bk
            scan_data, _scan_path, _scan_key = _load_scan_data_for_book(bk, book_id)
            if scan_data:
                _sync_library_scan_status(library, bk, scan_data)
                scan_pages = scan_data.get('pages', [])
                _analysis_status_update(analysis_id, 'İlgili sayfalar seçiliyor...', 'context')
                context_text = _build_context_excerpt(scan_pages, prompt_text)
    if not context_text:
        msg = (
            'Seçili kitap için hazır tarama metni bulunamadı. '
            'reylai_assets/scans klasöründe kitap kimliğiyle eşleşen .json dosyası olmalı.'
        )
        _analysis_status_update(analysis_id, msg, 'missing_scan', True, error=msg)
        return jsonify({'error': msg, 'missing_scan': True})

    if context_text:
        _analysis_status_update(analysis_id, 'Kitap bağlamı AI için hazırlanıyor...', 'context')
        system_msg += (
            '\n\nKitabin ilgili bolumleri asagidadir. Once bu alintilara dayanarak yanit ver:\n\n'
            + context_text
        )

    try:
        _analysis_status_update(analysis_id, 'AI yanıt hazırlıyor...', 'ai')
        requested_pages_text = ', '.join(str(p) for p in requested_pages) if requested_pages else 'belirtilmedi'
        user_prompt = 'Kitap adi: ' + book_name + '\nIstenen sayfalar: ' + requested_pages_text + '\n\nKullanici sorusu: ' + prompt_text
        messages = [
            {'role': 'system', 'content': system_msg},
            {'role': 'user', 'content': user_prompt},
        ]

        last_error = None
        for attempt in range(ANALYSIS_RETRY_COUNT):
            try:
                _analysis_status_update(analysis_id, 'AI yanıt hazırlıyor...', 'ai')
                response = _mistral_chat_complete(messages, temperature=0.2)
                response_text = _mistral_response_text(response)
                chat_title = ''
                if title_requested:
                    _analysis_status_update(analysis_id, 'Sohbet başlığı hazırlanıyor...', 'title')
                    chat_title = _generate_chat_title(book_name, prompt_text, response_text)
                _analysis_status_update(
                    analysis_id,
                    'Yanıt hazır.',
                    'done',
                    True,
                    result=response_text,
                    chat_title=chat_title,
                )
                return jsonify({'result': response_text, 'chat_title': chat_title})
            except Exception as inner_exc:
                last_error = inner_exc
                err_text = str(inner_exc).lower()
                retryable = any(k in err_text for k in ['503', 'unavailable', 'high demand', 'deadline exceeded', 'timeout'])
                if attempt < ANALYSIS_RETRY_COUNT - 1 and retryable:
                    _analysis_status_update(analysis_id, 'AI servisi yoğun, tekrar deneniyor...', 'retry')
                    time.sleep(0.75 * (attempt + 1))
                    continue
                raise last_error
    except Exception as e:
        err_str = str(e).lower()
        is_quota = any(k in err_str for k in ['429', 'quota', 'rate limit', 'too many requests',
                                              'resource_exhausted', 'resourceexhausted'])
        is_unavailable = any(k in err_str for k in ['503', 'unavailable', 'high demand'])
        status_message = 'AI servisi yoğun.' if is_unavailable else 'Analiz başarısız.'
        if is_quota:
            status_message = 'API kotası doldu.'
        _analysis_status_update(analysis_id, status_message, 'error', True)
        return jsonify({'error': str(e), 'rate_limit': is_quota, 'temporary_unavailable': is_unavailable})


# ── Extract covers for existing local books on startup ─────────────────────────
def _init_covers():
    """Background: extract covers (and titles) for local books that don't have one yet."""
    try:
        lib = load_library()
        changed = False
        for bk in lib:
            bid  = bk.get('book_id') or bk.get('drive_id', '')
            lp   = _resolve_app_path(bk.get('local_path', ''))
            if not bid or not lp or not os.path.exists(lp):
                continue
            cover_path = os.path.join(COVERS_DIR, bid + '.jpg')
            if os.path.exists(cover_path):
                continue
            cover = _extract_cover(bid, lp)
            if cover:
                bk['cover_path'] = cover
                raw_name = bk.get('name', '')
                if raw_name.lower().endswith('.pdf') or raw_name.startswith('cfac') or len(raw_name) > 40:
                    title = _extract_title_from_cover(cover)
                    if title:
                        bk['title'] = title
                changed = True
        if changed:
            save_library(lib)
    except Exception:
        pass

threading.Thread(target=_init_covers, daemon=True).start()


def _find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_server(url, timeout=WEBVIEW_START_TIMEOUT):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = requests.get(url, timeout=1)
            if response.ok:
                return True
        except Exception:
            time.sleep(0.2)
    return False


def _run_flask_server(host, port):
    app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)


def _run_with_webview():
    host = "127.0.0.1"
    port = _find_free_port()
    url = f"http://{host}:{port}"

    server_thread = threading.Thread(
        target=_run_flask_server,
        args=(host, port),
        daemon=True,
    )
    server_thread.start()

    if not _wait_for_server(url):
        raise RuntimeError("Flask sunucusu webview icin baslatilamadi.")

    _webview.create_window(
        WEBVIEW_WINDOW_TITLE,
        url,
        width=1440,
        height=920,
        min_size=(1100, 720),
    )
    _webview.start()


if __name__ == '__main__':
    if os.environ.get("REYLAI_SERVER_ONLY") == "1":
        app.run(
            host=os.environ.get("REYLAI_FLASK_HOST", "127.0.0.1"),
            port=int(os.environ.get("REYLAI_FLASK_PORT", "5000")),
            debug=False,
            threaded=True,
            use_reloader=False,
        )
    elif _HAS_WEBVIEW:
        _run_with_webview()
    else:
        app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
