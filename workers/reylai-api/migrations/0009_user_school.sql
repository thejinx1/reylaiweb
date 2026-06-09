ALTER TABLE users ADD COLUMN school_id TEXT;
ALTER TABLE users ADD COLUMN school_name TEXT;
ALTER TABLE users ADD COLUMN school_province TEXT;
ALTER TABLE users ADD COLUMN school_province_code TEXT;
ALTER TABLE users ADD COLUMN school_district TEXT;
ALTER TABLE users ADD COLUMN school_district_code TEXT;
ALTER TABLE users ADD COLUMN school_type TEXT;
ALTER TABLE users ADD COLUMN school_website TEXT;
ALTER TABLE users ADD COLUMN school_selected_at TEXT;
ALTER TABLE users ADD COLUMN school_change_requested_json TEXT;
ALTER TABLE users ADD COLUMN school_change_requested_at TEXT;
ALTER TABLE users ADD COLUMN school_change_status TEXT;
ALTER TABLE users ADD COLUMN school_change_reviewed_by TEXT;
ALTER TABLE users ADD COLUMN school_change_reviewed_at TEXT;

CREATE INDEX IF NOT EXISTS idx_users_school_id ON users(school_id);
CREATE INDEX IF NOT EXISTS idx_users_school_change_status ON users(school_change_status, school_change_requested_at);
