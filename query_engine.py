"""
Query Evolution Engine
----------------------
Maintains a self-expanding, scored pool of search queries.

Each run:
  - Top-scoring queries are selected for data collection
  - New AI-generated queries are added with a high initial score
  - Existing queries are scored up/down based on whether their topics
    appear in the AI's trending/keyword outputs
  - Queries that consistently underperform are retired
  - The pool never stops growing into new sub-niches

query_memory.json schema:
  active  : list of query objects (scored, ranked)
  retired : list of retired query objects (for audit trail)
  run_count         : int
  total_generated   : int
"""

import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))
import database as db

MAX_POOL   = 60   # maximum active queries kept
MIN_POOL   = 15   # never retire below this floor
RETIRE_AT  = 1.5  # retire when score drops to this
NEW_SCORE  = 9.0  # initial score for AI-generated queries
SEED_SCORE = 5.0  # initial score for hard-coded seed queries
DECAY_USED = 0.8  # score decay per run when query is used
DECAY_SKIP = 1.5  # score decay per run when query is skipped

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


# ─────────────────────────────────────────────────────────────
# LOAD / SAVE
# ─────────────────────────────────────────────────────────────

def load_pool() -> dict:
    db.init_schema()
    conn = db.get_connection()

    active_rows  = conn.execute(
        "SELECT * FROM query_pool WHERE is_retired=0 ORDER BY score DESC"
    ).fetchall()
    retired_rows = conn.execute(
        "SELECT * FROM query_pool WHERE is_retired=1"
    ).fetchall()
    run_count       = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    total_generated = conn.execute("SELECT COUNT(*) FROM query_pool").fetchone()[0]
    conn.close()

    if not active_rows:
        # First run — seed the pool into SQLite then return
        today = str(datetime.now().date())
        pool = {
            "active": [
                {
                    "query":     q,
                    "score":     SEED_SCORE,
                    "gen":       0,
                    "source":    "seed",
                    "last_used": today,
                    "use_count": 0,
                    "boost":     0
                }
                for q in SEED_QUERIES
            ],
            "retired":         [],
            "run_count":       0,
            "total_generated": len(SEED_QUERIES)
        }
        save_pool(pool)
        return pool

    def row_to_dict(row, retired=False):
        d = {
            "query":     row["query"],
            "score":     row["score"],
            "gen":       row["generation"],
            "source":    row["source"],
            "last_used": row["last_used"],
            "use_count": row["use_count"],
            "boost":     row["boost"],
        }
        if retired:
            d["retired_run"]  = row["retired_run"]
            d["retired_date"] = row["retired_date"]
        return d

    return {
        "active":          [row_to_dict(r) for r in active_rows],
        "retired":         [row_to_dict(r, retired=True) for r in retired_rows],
        "run_count":       run_count,
        "total_generated": total_generated,
    }


def save_pool(pool: dict):
    conn = db.get_connection()
    with conn:
        for q in pool["active"]:
            conn.execute(
                """INSERT INTO query_pool
                   (query, score, generation, source, last_used, use_count, boost, is_retired)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                   ON CONFLICT(query) DO UPDATE SET
                       score=excluded.score,
                       generation=excluded.generation,
                       source=excluded.source,
                       last_used=excluded.last_used,
                       use_count=excluded.use_count,
                       boost=excluded.boost,
                       is_retired=0""",
                (
                    q["query"], q["score"], q.get("gen", 0), q.get("source", "seed"),
                    q.get("last_used"), q.get("use_count", 0), q.get("boost", 0)
                )
            )
        for q in pool["retired"]:
            conn.execute(
                """INSERT INTO query_pool
                   (query, score, generation, source, last_used, use_count, boost,
                    is_retired, retired_run, retired_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                   ON CONFLICT(query) DO UPDATE SET
                       score=excluded.score,
                       is_retired=1,
                       retired_run=excluded.retired_run,
                       retired_date=excluded.retired_date""",
                (
                    q["query"], q["score"], q.get("gen", 0), q.get("source", "seed"),
                    q.get("last_used"), q.get("use_count", 0), q.get("boost", 0),
                    q.get("retired_run"), q.get("retired_date")
                )
            )
    conn.close()


# ─────────────────────────────────────────────────────────────
# SELECT — pick best queries for this run
# ─────────────────────────────────────────────────────────────

def select_queries(pool: dict, n: int = 12) -> list[str]:
    """Return top-N queries by score."""
    ranked = sorted(pool["active"], key=lambda x: x["score"], reverse=True)
    return [q["query"] for q in ranked[:n]]


# ─────────────────────────────────────────────────────────────
# EVOLVE — update pool after a run
# ─────────────────────────────────────────────────────────────

def evolve_pool(pool: dict, ai_output: dict, used_queries: list[str]) -> dict:
    """
    Score existing queries up or down, add new AI queries, retire weak ones.
    """
    today = str(datetime.now().date())

    # Build relevance signals from AI output
    trending_words = set()
    for t in ai_output.get("trending_topics", []):
        trending_words.update((t["key"] if isinstance(t, dict) else t).lower().split())

    keyword_words = set()
    for k in ai_output.get("expanded_keywords", []):
        keyword_words.update(k.lower().split())

    used_set = set(used_queries)

    # ── Update existing query scores ──────────────────────────
    for q_obj in pool["active"]:
        q_lower = q_obj["query"].lower()
        q_words  = set(q_lower.split())

        if q_obj["query"] in used_set:
            q_obj["score"]    -= DECAY_USED
            q_obj["last_used"] = today
            q_obj["use_count"] = q_obj.get("use_count", 0) + 1
        else:
            q_obj["score"] -= DECAY_SKIP  # penalise being left out

        # Boost if this query's topic surfaced in AI findings
        trend_overlap   = len(q_words & trending_words)
        keyword_overlap = len(q_words & keyword_words)

        boost = (trend_overlap * 3) + (keyword_overlap * 1.5)
        q_obj["boost"]  = round(boost, 2)
        q_obj["score"] += boost
        q_obj["score"]  = round(q_obj["score"], 2)

    # ── Add new AI-generated queries ──────────────────────────
    existing = {q["query"] for q in pool["active"]}

    for new_q in ai_output.get("next_search_queries", []):
        new_q = new_q.strip()
        if new_q and new_q not in existing:
            pool["active"].append({
                "query":     new_q,
                "score":     NEW_SCORE,
                "gen":       pool["run_count"],
                "source":    "ai_generated",
                "last_used": today,
                "use_count": 0,
                "boost":     0
            })
            pool["total_generated"] = pool.get("total_generated", 0) + 1
            existing.add(new_q)

    # ── Sort and retire ───────────────────────────────────────
    pool["active"].sort(key=lambda x: x["score"], reverse=True)

    if len(pool["active"]) > MAX_POOL:
        overflow = pool["active"][MIN_POOL:]
        to_retire = [q for q in overflow if q["score"] < RETIRE_AT]
        for q in to_retire:
            pool["retired"].append({**q, "retired_run": pool["run_count"], "retired_date": today})
            pool["active"].remove(q)

    pool["run_count"] += 1
    return pool


# ─────────────────────────────────────────────────────────────
# SUMMARY — print pool status
# ─────────────────────────────────────────────────────────────

def print_pool_status(pool: dict):
    active  = pool["active"]
    print(f"\n  Query Pool: {len(active)} active | {len(pool['retired'])} retired | {pool['total_generated']} total generated | run #{pool['run_count']}")
    print(f"  Top 5 queries by score:")
    for q in active[:5]:
        print(f"    [{q['score']:5.1f}] {q['query']}")
    if len(active) > 5:
        print(f"    ... and {len(active) - 5} more")
