ALTER TABLE users ADD COLUMN presence_status TEXT DEFAULT 'online';
ALTER TABLE users ADD COLUMN presence_updated_at TEXT;

CREATE INDEX IF NOT EXISTS idx_users_presence_status
  ON users(presence_status);
