import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "intelligence.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema():
    conn = get_connection()
    with conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       TEXT NOT NULL,
                engine_version  TEXT,
                source_reddit   INTEGER DEFAULT 0,
                source_youtube  INTEGER DEFAULT 0,
                source_trends   INTEGER DEFAULT 0,
                source_total    INTEGER DEFAULT 0,
                queries_used    TEXT
            );

            CREATE TABLE IF NOT EXISTS trending_topics (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id  INTEGER REFERENCES runs(id),
                topic   TEXT NOT NULL,
                rank    INTEGER
            );

            CREATE TABLE IF NOT EXISTS keywords (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id  INTEGER REFERENCES runs(id),
                keyword TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS content_gaps (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id          INTEGER REFERENCES runs(id),
                gap             TEXT NOT NULL,
                why_it_is_a_gap TEXT,
                emotional_signal TEXT
            );

            CREATE TABLE IF NOT EXISTS youtube_titles (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id  INTEGER REFERENCES runs(id),
                title   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS thumbnail_ideas (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id  INTEGER REFERENCES runs(id),
                text    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS thumbnail_patterns (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id  INTEGER REFERENCES runs(id),
                pattern TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS content_series (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id      INTEGER REFERENCES runs(id),
                name        TEXT NOT NULL,
                description TEXT
            );

            CREATE TABLE IF NOT EXISTS run_strategy (
                run_id                   INTEGER PRIMARY KEY REFERENCES runs(id),
                posting_frequency_model  TEXT,
                multi_channel_strategy   TEXT,
                audience_funnel_strategy TEXT
            );

            CREATE TABLE IF NOT EXISTS memory_keywords (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword          TEXT UNIQUE NOT NULL,
                first_seen_run   INTEGER REFERENCES runs(id),
                last_seen_run    INTEGER REFERENCES runs(id),
                frequency        INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS raw_signals (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id  INTEGER REFERENCES runs(id),
                source  TEXT NOT NULL,
                text    TEXT NOT NULL,
                signal  REAL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS query_pool (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                query       TEXT UNIQUE NOT NULL,
                score       REAL DEFAULT 5.0,
                generation  INTEGER DEFAULT 0,
                source      TEXT DEFAULT 'seed',
                last_used   TEXT,
                use_count   INTEGER DEFAULT 0,
                boost       REAL DEFAULT 0,
                is_retired  INTEGER DEFAULT 0,
                retired_run INTEGER,
                retired_date TEXT
            );

            CREATE TABLE IF NOT EXISTS creative_packages (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp            TEXT NOT NULL,
                transcript_source    TEXT,
                transcript_word_count INTEGER,
                package_json         TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS content_drafts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id     INTEGER REFERENCES runs(id),
                brand      TEXT NOT NULL,
                platform   TEXT NOT NULL,
                post_text  TEXT NOT NULL,
                hashtags   TEXT,
                status     TEXT DEFAULT 'draft',
                created_at TEXT,
                posted_at  TEXT
            );

            CREATE TABLE IF NOT EXISTS content_performance (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                draft_id            INTEGER REFERENCES content_drafts(id),
                run_id              INTEGER REFERENCES runs(id),
                platform            TEXT NOT NULL,
                brand               TEXT NOT NULL,
                content_summary     TEXT,
                posted_at           TEXT,
                youtube_video_id    TEXT,
                views               INTEGER DEFAULT 0,
                likes               INTEGER DEFAULT 0,
                comments            INTEGER DEFAULT 0,
                shares              INTEGER DEFAULT 0,
                watch_time_mins     INTEGER DEFAULT 0,
                impressions         INTEGER DEFAULT 0,
                click_through_rate  REAL DEFAULT 0,
                followers_gained    INTEGER DEFAULT 0,
                notes               TEXT,
                recorded_at         TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS whatsapp_signals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                imported_at TEXT NOT NULL,
                source_file TEXT,
                response    TEXT NOT NULL,
                used_run_id INTEGER REFERENCES runs(id)
            );

            CREATE TABLE IF NOT EXISTS learning_signals (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                winning_topics TEXT,
                losing_topics  TEXT,
                winning_hooks  TEXT,
                losing_hooks   TEXT,
                timestamp      DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_trending_run    ON trending_topics(run_id);
            CREATE INDEX IF NOT EXISTS idx_keywords_run    ON keywords(run_id);
            CREATE INDEX IF NOT EXISTS idx_signals_run     ON raw_signals(run_id);
            CREATE INDEX IF NOT EXISTS idx_query_score     ON query_pool(score DESC);
            CREATE INDEX IF NOT EXISTS idx_query_retired   ON query_pool(is_retired);
            CREATE INDEX IF NOT EXISTS idx_wa_used_run     ON whatsapp_signals(used_run_id);
        """)
    conn.close()


def save_run(ai_output: dict, data_sources: dict, queries_used: list, engine_version: str = "3.0") -> int:
    import json
    from datetime import datetime

    conn = get_connection()
    with conn:
        cur = conn.execute(
            """INSERT INTO runs
               (timestamp, engine_version, source_reddit, source_youtube, source_trends, source_total, queries_used)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                str(datetime.now()),
                engine_version,
                data_sources.get("reddit", 0),
                data_sources.get("youtube", 0),
                data_sources.get("google_trends", 0),
                data_sources.get("total", 0),
                json.dumps(queries_used),
            )
        )
        run_id = cur.lastrowid

        for rank, topic in enumerate(ai_output.get("trending_topics", []), 1):
            conn.execute("INSERT INTO trending_topics (run_id, topic, rank) VALUES (?, ?, ?)",
                         (run_id, topic, rank))

        for kw in ai_output.get("expanded_keywords", []):
            conn.execute("INSERT INTO keywords (run_id, keyword) VALUES (?, ?)", (run_id, kw))

        for gap in ai_output.get("content_gaps", []):
            if isinstance(gap, dict):
                conn.execute(
                    "INSERT INTO content_gaps (run_id, gap, why_it_is_a_gap, emotional_signal) VALUES (?, ?, ?, ?)",
                    (run_id, gap.get("gap", ""), gap.get("why_it_is_a_gap", ""), gap.get("emotional_signal", ""))
                )
            else:
                conn.execute(
                    "INSERT INTO content_gaps (run_id, gap) VALUES (?, ?)", (run_id, str(gap))
                )

        for title in ai_output.get("youtube_titles", []):
            conn.execute("INSERT INTO youtube_titles (run_id, title) VALUES (?, ?)", (run_id, title))

        for text in ai_output.get("thumbnail_text_ideas", []):
            conn.execute("INSERT INTO thumbnail_ideas (run_id, text) VALUES (?, ?)", (run_id, text))

        for pattern in ai_output.get("thumbnail_patterns", []):
            conn.execute("INSERT INTO thumbnail_patterns (run_id, pattern) VALUES (?, ?)", (run_id, pattern))

        for series in ai_output.get("content_series_ideas", []):
            if isinstance(series, dict):
                conn.execute(
                    "INSERT INTO content_series (run_id, name, description) VALUES (?, ?, ?)",
                    (run_id, series.get("name", ""), series.get("description", ""))
                )
            else:
                conn.execute("INSERT INTO content_series (run_id, name) VALUES (?, ?)", (run_id, str(series)))

        conn.execute(
            """INSERT INTO run_strategy (run_id, posting_frequency_model, multi_channel_strategy, audience_funnel_strategy)
               VALUES (?, ?, ?, ?)""",
            (
                run_id,
                ai_output.get("posting_frequency_model", ""),
                ai_output.get("multi_channel_strategy", ""),
                ai_output.get("audience_funnel_strategy", ""),
            )
        )

        for kw in ai_output.get("memory_keywords", []):
            conn.execute(
                """INSERT INTO memory_keywords (keyword, first_seen_run, last_seen_run, frequency)
                   VALUES (?, ?, ?, 1)
                   ON CONFLICT(keyword) DO UPDATE SET
                       last_seen_run = excluded.last_seen_run,
                       frequency = frequency + 1""",
                (kw, run_id, run_id)
            )

    conn.close()
    return run_id


def save_raw_signals(run_id: int, data_sample: list):
    conn = get_connection()
    with conn:
        for item in data_sample:
            conn.execute(
                "INSERT INTO raw_signals (run_id, source, text, signal) VALUES (?, ?, ?, ?)",
                (run_id, item.get("source", ""), item.get("text", "")[:300], item.get("signal", 0))
            )
    conn.close()


def get_pending_whatsapp_signals() -> list[dict]:
    """Return expert responses not yet assigned to a run."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, response FROM whatsapp_signals WHERE used_run_id IS NULL ORDER BY imported_at"
    ).fetchall()
    conn.close()
    return [{"id": r["id"], "response": r["response"]} for r in rows]


def mark_whatsapp_signals_used(signal_ids: list[int], run_id: int):
    """Tag signals as consumed by this run."""
    if not signal_ids:
        return
    conn = get_connection()
    with conn:
        conn.execute(
            f"UPDATE whatsapp_signals SET used_run_id=? WHERE id IN ({','.join('?' * len(signal_ids))})",
            [run_id] + signal_ids
        )
    conn.close()


if __name__ == "__main__":
    init_schema()
    print(f"Database initialised at: {DB_PATH}")
    conn = get_connection()
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    print(f"Tables created: {[t['name'] for t in tables]}")
    conn.close()
