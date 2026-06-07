ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'user';
ALTER TABLE users ADD COLUMN avatar_data_url TEXT;
ALTER TABLE users ADD COLUMN email_verified_at TEXT;
ALTER TABLE users ADD COLUMN last_login_ip TEXT;
ALTER TABLE users ADD COLUMN last_login_at TEXT;
ALTER TABLE users ADD COLUMN password_updated_at TEXT;
ALTER TABLE users ADD COLUMN email_verification_code_hash TEXT;
ALTER TABLE users ADD COLUMN email_verification_expires_at TEXT;
ALTER TABLE users ADD COLUMN email_verification_sent_at TEXT;

ALTER TABLE sessions ADD COLUMN ip_address TEXT;

CREATE INDEX IF NOT EXISTS users_role_idx ON users(role);
CREATE INDEX IF NOT EXISTS users_email_verified_idx ON users(email_verified_at);
