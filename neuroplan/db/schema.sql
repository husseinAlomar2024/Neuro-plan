-- NeuroPlan schema v1. Applied idempotently on every startup.
CREATE TABLE IF NOT EXISTS subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL DEFAULT 'HEAVY' CHECK (category IN ('HEAVY','CODING','MEMORY')),
    difficulty INTEGER NOT NULL DEFAULT 3 CHECK (difficulty BETWEEN 1 AND 5),
    mastery INTEGER NOT NULL DEFAULT 3 CHECK (mastery BETWEEN 1 AND 5),
    minutes_per_unit INTEGER NOT NULL DEFAULT 60,
    expected_units_by_now INTEGER NOT NULL DEFAULT 0,
    color_hex TEXT NOT NULL DEFAULT '#6C8EF5',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS units (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    number INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    minutes_logged INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'todo' CHECK (status IN ('todo','in_progress','done')),
    done_date TEXT,
    UNIQUE (subject_id, number)
);

CREATE TABLE IF NOT EXISTS assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('quiz','midterm','final','assignment','project')),
    title TEXT NOT NULL DEFAULT '',
    date TEXT NOT NULL,
    weight_pct INTEGER NOT NULL DEFAULT 0,
    unit_from INTEGER NOT NULL DEFAULT 1,
    unit_to INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS availability (
    weekday INTEGER PRIMARY KEY CHECK (weekday BETWEEN 0 AND 6),
    minutes INTEGER NOT NULL DEFAULT 0,
    start_time TEXT NOT NULL DEFAULT '18:00',
    peak_window TEXT NOT NULL DEFAULT 'night' CHECK (peak_window IN ('morning','afternoon','night'))
);

CREATE TABLE IF NOT EXISTS availability_override (
    date TEXT PRIMARY KEY,
    minutes INTEGER NOT NULL,
    start_time TEXT
);

CREATE TABLE IF NOT EXISTS study_blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_date TEXT NOT NULL,
    start_time TEXT NOT NULL DEFAULT '',
    subject_id INTEGER REFERENCES subjects(id) ON DELETE CASCADE,
    unit_number INTEGER NOT NULL DEFAULT 0,
    block_type TEXT NOT NULL CHECK (block_type IN ('CATCHUP','PROGRESS','REVIEW','PRACTICE')),
    pomodoro TEXT NOT NULL CHECK (pomodoro IN ('50/10','25/5')),
    duration_min INTEGER NOT NULL,
    task_text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','done','partial','skipped')),
    actual_min INTEGER,
    locked INTEGER NOT NULL DEFAULT 0,
    order_index INTEGER NOT NULL DEFAULT 0,
    generated_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_blocks_date ON study_blocks(plan_date);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    unit_number INTEGER NOT NULL,
    stage_index INTEGER NOT NULL DEFAULT 0,
    due_date TEXT NOT NULL,
    last_rating TEXT,
    done INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user','coach','system_event')),
    text TEXT NOT NULL DEFAULT '',
    actions_json TEXT,
    proactive INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS action_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('ui','chat_llm','chat_offline','system')),
    tool TEXT NOT NULL,
    args_json TEXT,
    inverse_json TEXT,
    undone INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
