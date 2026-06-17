-- PDF staging table: temporarily stores uploaded PDFs for text extraction
CREATE TABLE IF NOT EXISTS pdf_staging (
  id TEXT PRIMARY KEY,
  book_id TEXT NOT NULL UNIQUE,
  file_name TEXT NOT NULL DEFAULT '',
  file_data TEXT NOT NULL,  -- base64-encoded PDF content
  grade TEXT NOT NULL DEFAULT '9',
  github_url TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  expires_at TEXT NOT NULL DEFAULT (datetime('now', '+1 hour')),
  scanned INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_pdf_staging_book_id ON pdf_staging(book_id);
CREATE INDEX IF NOT EXISTS idx_pdf_staging_expires ON pdf_staging(expires_at);
