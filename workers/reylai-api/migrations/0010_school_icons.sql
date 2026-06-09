CREATE TABLE IF NOT EXISTS school_icons (
  school_key TEXT PRIMARY KEY,
  school_id TEXT,
  school_name TEXT NOT NULL,
  school_province TEXT NOT NULL,
  school_province_code TEXT,
  school_district TEXT NOT NULL,
  school_district_code TEXT,
  icon_data_url TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  updated_by TEXT
);

CREATE INDEX IF NOT EXISTS idx_school_icons_school_id ON school_icons(school_id);
CREATE INDEX IF NOT EXISTS idx_school_icons_location ON school_icons(school_province, school_district, school_name);
