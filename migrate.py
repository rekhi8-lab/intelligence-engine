"""
migrate.py — ONE-TIME script
Imports intelligence.json, memory.json, and query_memory.json (if present)
into intelligence.db as the historical baseline.

Safe to re-run: checks for existing data before inserting.
"""

import json
import sys
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

import database as db

INTELLIGENCE_JSON = BASE_DIR / "intelligence.json"
MEMORY_JSON       = BASE_DIR / "memory.json"
QUERY_MEMORY_JSON = BASE_DIR / "query_memory.json"

# Seed queries mirrored from query_engine.py (used when query_memory.json absent)
SEED_QUERIES = [
    "menopause anxiety symptoms women",
    "perimenopause mental health depression",
    "ADHD in women late diagnosis",
    "endometriosis pain management",
    "early puberty girls causes",
    "PCOS symptoms hormones treatment",
    "women hormonal imbalance depression",
    "perimenopause brain fog ADHD",
    "doctor dismissed my symptoms women",
    "HRT hormone replacement therapy experience",
    "ADHD women relationships marriage",
    "endometriosis misdiagnosis stories",
    "menopause heart palpitations anxiety",
    "perimenopause rage mood swings",
    "ADHD women burnout masking"
]


def load_json(path: Path) -> dict | list | None:
    if not path.exists():
        print(f"  [skip] {path.name} not found")
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def migrate_intelligence(intel: dict) -> int:
    """Insert intelligence.json as run_id=1. Returns the run_id."""
    conn = db.get_connection()
    existing = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    conn.close()

    if existing > 0:
        print(f"  [skip] runs table already has {existing} row(s) - skipping intelligence migration")
        conn = db.get_connection()
        run_id = conn.execute("SELECT id FROM runs ORDER BY id LIMIT 1").fetchone()[0]
        conn.close()
        return run_id

    # Normalise data_sources — old format may include google_search key
    raw_sources = intel.get("data_sources", {})
    data_sources = {
        "reddit":        raw_sources.get("reddit", 0),
        "youtube":       raw_sources.get("youtube", 0),
        "google_trends": raw_sources.get("google_trends", 0),
        "total":         raw_sources.get("total", raw_sources.get("reddit", 0)),
    }

    queries_used = intel.get("query_pool", {}).get("used_this_run", [])
    engine_version = intel.get("engine_version", "2.1")

    # Override timestamp to preserve original run date
    conn = db.get_connection()
    with conn:
        cur = conn.execute(
            """INSERT INTO runs
               (timestamp, engine_version, source_reddit, source_youtube, source_trends, source_total, queries_used)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                intel.get("timestamp", str(datetime.now())),
                engine_version,
                data_sources["reddit"],
                data_sources["youtube"],
                data_sources["google_trends"],
                data_sources["total"],
                json.dumps(queries_used),
            )
        )
        run_id = cur.lastrowid

        for rank, topic in enumerate(intel.get("trending_topics", []), 1):
            conn.execute("INSERT INTO trending_topics (run_id, topic, rank) VALUES (?, ?, ?)",
                         (run_id, topic, rank))

        for kw in intel.get("expanded_keywords", []):
            conn.execute("INSERT INTO keywords (run_id, keyword) VALUES (?, ?)", (run_id, kw))

        for gap in intel.get("content_gaps", []):
            if isinstance(gap, dict):
                conn.execute(
                    "INSERT INTO content_gaps (run_id, gap, why_it_is_a_gap, emotional_signal) VALUES (?, ?, ?, ?)",
                    (run_id, gap.get("gap", ""), gap.get("why_it_is_a_gap", ""), gap.get("emotional_signal", ""))
                )
            else:
                conn.execute("INSERT INTO content_gaps (run_id, gap) VALUES (?, ?)", (run_id, str(gap)))

        for title in intel.get("youtube_titles", []):
            conn.execute("INSERT INTO youtube_titles (run_id, title) VALUES (?, ?)", (run_id, title))

        for text in intel.get("thumbnail_text_ideas", []):
            conn.execute("INSERT INTO thumbnail_ideas (run_id, text) VALUES (?, ?)", (run_id, text))

        for pattern in intel.get("thumbnail_patterns", []):
            conn.execute("INSERT INTO thumbnail_patterns (run_id, pattern) VALUES (?, ?)", (run_id, pattern))

        for series in intel.get("content_series_ideas", []):
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
                intel.get("posting_frequency_model", ""),
                intel.get("multi_channel_strategy", ""),
                intel.get("audience_funnel_strategy", ""),
            )
        )

        for item in intel.get("data_sample", []):
            conn.execute(
                "INSERT INTO raw_signals (run_id, source, text, signal) VALUES (?, ?, ?, ?)",
                (run_id, item.get("source", ""), item.get("text", "")[:300], item.get("signal", 0))
            )

    conn.close()
    print(f"  [ok] intelligence.json -> run_id={run_id}")
    return run_id


def migrate_memory_keywords(memory: dict, run_id: int):
    """Insert memory.json keywords into memory_keywords table."""
    conn = db.get_connection()
    existing = conn.execute("SELECT COUNT(*) FROM memory_keywords").fetchone()[0]
    if existing > 0:
        print(f"  [skip] memory_keywords already has {existing} row(s)")
        conn.close()
        return

    keywords = memory.get("keywords", [])
    with conn:
        for kw in keywords:
            kw = kw.strip()
            if kw:
                conn.execute(
                    """INSERT OR IGNORE INTO memory_keywords (keyword, first_seen_run, last_seen_run, frequency)
                       VALUES (?, ?, ?, 1)""",
                    (kw, run_id, run_id)
                )
    conn.close()
    print(f"  [ok] memory.json -> {len(keywords)} keywords into memory_keywords")


def migrate_query_pool(pool: dict):
    """Insert query_memory.json active + retired queries into query_pool table."""
    conn = db.get_connection()
    existing = conn.execute("SELECT COUNT(*) FROM query_pool").fetchone()[0]
    if existing > 0:
        print(f"  [skip] query_pool already has {existing} row(s)")
        conn.close()
        return

    active  = pool.get("active", [])
    retired = pool.get("retired", [])

    with conn:
        for q in active:
            conn.execute(
                """INSERT OR IGNORE INTO query_pool
                   (query, score, generation, source, last_used, use_count, boost, is_retired)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 0)""",
                (
                    q.get("query", ""),
                    q.get("score", 5.0),
                    q.get("gen", 0),
                    q.get("source", "seed"),
                    q.get("last_used"),
                    q.get("use_count", 0),
                    q.get("boost", 0),
                )
            )
        for q in retired:
            conn.execute(
                """INSERT OR IGNORE INTO query_pool
                   (query, score, generation, source, last_used, use_count, boost,
                    is_retired, retired_run, retired_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (
                    q.get("query", ""),
                    q.get("score", 0.0),
                    q.get("gen", 0),
                    q.get("source", "seed"),
                    q.get("last_used"),
                    q.get("use_count", 0),
                    q.get("boost", 0),
                    q.get("retired_run"),
                    q.get("retired_date"),
                )
            )
    conn.close()
    print(f"  [ok] query_memory.json -> {len(active)} active, {len(retired)} retired queries")


def seed_query_pool_from_defaults():
    """Seed query_pool with hardcoded seed queries when query_memory.json is absent."""
    conn = db.get_connection()
    existing = conn.execute("SELECT COUNT(*) FROM query_pool").fetchone()[0]
    if existing > 0:
        print(f"  [skip] query_pool already has {existing} row(s)")
        conn.close()
        return

    today = str(datetime.now().date())
    with conn:
        for q in SEED_QUERIES:
            conn.execute(
                """INSERT OR IGNORE INTO query_pool
                   (query, score, generation, source, last_used, use_count, boost, is_retired)
                   VALUES (?, 5.0, 0, 'seed', ?, 0, 0, 0)""",
                (q, today)
            )
    conn.close()
    print(f"  [ok] Seeded query_pool with {len(SEED_QUERIES)} default seed queries")


def verify(run_id: int):
    conn = db.get_connection()
    counts = {
        "runs":              conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
        "trending_topics":   conn.execute("SELECT COUNT(*) FROM trending_topics WHERE run_id=?", (run_id,)).fetchone()[0],
        "keywords":          conn.execute("SELECT COUNT(*) FROM keywords WHERE run_id=?", (run_id,)).fetchone()[0],
        "content_gaps":      conn.execute("SELECT COUNT(*) FROM content_gaps WHERE run_id=?", (run_id,)).fetchone()[0],
        "youtube_titles":    conn.execute("SELECT COUNT(*) FROM youtube_titles WHERE run_id=?", (run_id,)).fetchone()[0],
        "thumbnail_ideas":   conn.execute("SELECT COUNT(*) FROM thumbnail_ideas WHERE run_id=?", (run_id,)).fetchone()[0],
        "thumbnail_patterns":conn.execute("SELECT COUNT(*) FROM thumbnail_patterns WHERE run_id=?", (run_id,)).fetchone()[0],
        "content_series":    conn.execute("SELECT COUNT(*) FROM content_series WHERE run_id=?", (run_id,)).fetchone()[0],
        "raw_signals":       conn.execute("SELECT COUNT(*) FROM raw_signals WHERE run_id=?", (run_id,)).fetchone()[0],
        "memory_keywords":   conn.execute("SELECT COUNT(*) FROM memory_keywords").fetchone()[0],
        "query_pool_active": conn.execute("SELECT COUNT(*) FROM query_pool WHERE is_retired=0").fetchone()[0],
        "query_pool_retired":conn.execute("SELECT COUNT(*) FROM query_pool WHERE is_retired=1").fetchone()[0],
    }
    conn.close()

    print("\n  -- Verification -----------------------------")
    for table, count in counts.items():
        status = "ok" if count > 0 else "!!"
        print(f"  [{status}]  {table:<22} {count} rows")
    print("  ---------------------------------------------")


def main():
    print("\n" + "-" * 50)
    print("  migrate.py -- JSON -> SQLite")
    print("-" * 50)

    db.init_schema()
    print("  Schema ready.\n")

    # ── intelligence.json ─────────────────────────────
    intel = load_json(INTELLIGENCE_JSON)
    if intel:
        run_id = migrate_intelligence(intel)
    else:
        print("  [!] intelligence.json missing - cannot migrate run data")
        sys.exit(1)

    # ── memory.json ───────────────────────────────────
    memory = load_json(MEMORY_JSON)
    if memory:
        migrate_memory_keywords(memory, run_id)

    # ── query_memory.json or seed fallback ────────────
    query_pool = load_json(QUERY_MEMORY_JSON)
    if query_pool:
        migrate_query_pool(query_pool)
    else:
        print("  query_memory.json not found - seeding from defaults")
        seed_query_pool_from_defaults()

    verify(run_id)

    print("\n  Migration complete.")
    print(f"  Database: {db.DB_PATH}\n")


if __name__ == "__main__":
    main()
