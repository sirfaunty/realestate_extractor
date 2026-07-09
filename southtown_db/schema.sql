-- Southtown lease-abstraction warehouse schema
-- Ported from the partner's build_warehouse.py so the local re-extraction ties out
-- to the gold-standard warehouse exactly.

CREATE TABLE IF NOT EXISTS source_files(
  id INTEGER PRIMARY KEY, filename TEXT, sha256 TEXT UNIQUE, byte_size INTEGER,
  doc_type TEXT, source_msg TEXT, ingested_at TEXT);

CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY, doc_code TEXT, title TEXT, doc_class TEXT,
  source_file_id INTEGER, char_count INTEGER, notes TEXT,
  FOREIGN KEY(source_file_id) REFERENCES source_files(id));

CREATE TABLE IF NOT EXISTS provisions(
  id INTEGER PRIMARY KEY, document_id INTEGER, seq INTEGER,
  article_num INTEGER, article_roman TEXT, article_title TEXT,
  section_num TEXT, section_heading TEXT, body TEXT, char_count INTEGER,
  start_para_idx INTEGER, bucket TEXT DEFAULT 'DHOS',
  FOREIGN KEY(document_id) REFERENCES documents(id));

CREATE TABLE IF NOT EXISTS exhibits(
  id INTEGER PRIMARY KEY, document_id INTEGER, exhibit_code TEXT, title TEXT,
  body TEXT, char_count INTEGER, start_para_idx INTEGER, end_para_idx INTEGER,
  parent_exhibit TEXT, notes TEXT,
  FOREIGN KEY(document_id) REFERENCES documents(id));

-- Provision abstracts. abstract_type in ('detailed','detailed_summary','abstract_summary').
-- 'engine' records which local model produced it (or 'gold' for hand-authored reference).
CREATE TABLE IF NOT EXISTS abstracts(
  id INTEGER PRIMARY KEY, provision_id INTEGER, abstract_type TEXT,
  content TEXT, engine TEXT, created_at TEXT,
  FOREIGN KEY(provision_id) REFERENCES provisions(id));

CREATE VIRTUAL TABLE IF NOT EXISTS provisions_fts
  USING fts5(section_num, section_heading, body, content='');
