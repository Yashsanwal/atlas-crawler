import sqlite3, os, json

DB_PATH = os.environ.get("ATLAS_DB", os.path.join(os.path.dirname(__file__), "..", "data", "atlas.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT,
  mode TEXT,            -- live | replay | simulated
  note TEXT, stats_json TEXT);
CREATE TABLE IF NOT EXISTS sources(
  url TEXT PRIMARY KEY, domain TEXT, source_type TEXT, is_official INTEGER, org TEXT, role TEXT,
  first_seen TEXT, last_fetched TEXT, last_status INTEGER, content_hash TEXT, fail_count INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS leads(          -- discovery leads (aggregator mentions etc.); never verified directly
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, url TEXT, found_on TEXT, found_run INTEGER,
  resolved_official_url TEXT, state TEXT, UNIQUE(name, found_on));
CREATE TABLE IF NOT EXISTS scholarships(
  id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE,
  name TEXT, provider TEXT, source_type TEXT,
  official_url TEXT, application_url TEXT,
  amount TEXT, eligibility TEXT, academic_requirements TEXT, education_level TEXT, income_criteria TEXT,
  age_criteria TEXT, gender_criteria TEXT, category_criteria TEXT, domicile TEXT, institution_requirements TEXT,
  opening_date TEXT, closing_date TEXT, documents_required TEXT, selection_process TEXT, renewal TEXT,
  num_awards TEXT, criteria_json TEXT,
  status TEXT, label TEXT, confidence REAL,
  first_seen TEXT, last_verified TEXT, last_seen_run INTEGER, first_run INTEGER, missing_runs INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS evidence(      -- Database -> Official Source -> Evidence -> Extracted Value
  id INTEGER PRIMARY KEY AUTOINCREMENT, scholarship_id INTEGER, field TEXT, value TEXT,
  quote TEXT, source_url TEXT, char_offset INTEGER, method TEXT, quote_verified INTEGER, run_id INTEGER);
CREATE TABLE IF NOT EXISTS score_breakdown(
  id INTEGER PRIMARY KEY AUTOINCREMENT, scholarship_id INTEGER, run_id INTEGER, check_name TEXT,
  weight REAL, score REAL, points REAL, detail TEXT);
CREATE TABLE IF NOT EXISTS changes(
  id INTEGER PRIMARY KEY AUTOINCREMENT, scholarship_id INTEGER, run_id INTEGER, field TEXT,
  old_value TEXT, new_value TEXT, old_quote TEXT, new_quote TEXT, source_url TEXT,
  detected_at TEXT, change_type TEXT, simulated INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_ev ON evidence(scholarship_id);
CREATE INDEX IF NOT EXISTS ix_ch ON changes(scholarship_id);
"""


def connect(path=None):
    p = path or DB_PATH
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    c = sqlite3.connect(p)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c
