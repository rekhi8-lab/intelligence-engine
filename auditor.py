"""
Auditor — Content Performance Tracker
---------------------------------------
Tracks what was posted and how it performed.
Closes the learning loop: predicted topics vs. actual engagement.

Commands:
  python auditor.py log                  # log performance for a posted piece
  python auditor.py youtube <video_url>  # fetch public stats for a YouTube video
  python auditor.py report               # weekly performance report
  python auditor.py list                 # show all logged entries
"""

import os
import sys
import re
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv(override=True)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))
import database as db


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

PLATFORM_METRICS = {
    "youtube":   ["views", "likes", "comments", "watch_time_mins", "impressions", "click_through_rate", "followers_gained"],
    "linkedin":  ["views", "likes", "comments", "shares", "impressions", "followers_gained"],
    "instagram": ["views", "likes", "comments", "shares", "impressions", "followers_gained"],
}

BRAND_LABELS = {
    "gmc":          "Global Menopause Collective",
    "endo_neutral": "Endo Neutral",
    "harmanjeet":   "Harmanjeet Rekhi",
}


def prompt_int(label: str, default: int = 0) -> int:
    while True:
        raw = input(f"  {label} [{default}]: ").strip()
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            print("  Please enter a whole number.")


def prompt_float(label: str, default: float = 0.0) -> float:
    while True:
        raw = input(f"  {label} [{default}]: ").strip()
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            print("  Please enter a number (e.g. 2.4).")


def extract_video_id(url_or_id: str) -> str:
    patterns = [
        r"youtube\.com/watch\?v=([A-Za-z0-9_-]{11})",
        r"youtu\.be/([A-Za-z0-9_-]{11})",
        r"^([A-Za-z0-9_-]{11})$",
    ]
    for p in patterns:
        m = re.search(p, url_or_id)
        if m:
            return m.group(1)
    return ""


# ─────────────────────────────────────────────────────────────
# LOG — interactive entry
# ─────────────────────────────────────────────────────────────

def cmd_log():
    db.init_schema()
    conn = db.get_connection()

    # Show recent drafts to pick from
    drafts = conn.execute(
        """SELECT cd.id, cd.brand, cd.platform, cd.created_at,
                  substr(cd.post_text, 1, 80) as preview,
                  r.timestamp as run_ts
           FROM content_drafts cd
           JOIN runs r ON cd.run_id = r.id
           ORDER BY cd.id DESC LIMIT 20"""
    ).fetchall()
    conn.close()

    print("\n  Recent content drafts:")
    print(f"  {'#':<4} {'Brand':<16} {'Platform':<12} {'Date':<12} Preview")
    print("  " + "-" * 70)
    for d in drafts:
        print(f"  {d['id']:<4} {BRAND_LABELS.get(d['brand'], d['brand']):<16} "
              f"{d['platform']:<12} {d['created_at'][:10]:<12} {d['preview']}...")

    print()
    draft_id_raw = input("  Enter draft # to log performance for (or 0 to skip): ").strip()
    draft_id = int(draft_id_raw) if draft_id_raw.isdigit() else 0

    # Look up run_id from draft
    run_id    = None
    platform  = ""
    brand     = ""
    preview   = ""
    if draft_id:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT run_id, platform, brand, post_text FROM content_drafts WHERE id=?", (draft_id,)
        ).fetchone()
        conn.close()
        if row:
            run_id   = row["run_id"]
            platform = row["platform"]
            brand    = row["brand"]
            preview  = row["post_text"][:100]
            print(f"\n  Content: {preview}...")
            print(f"  Platform: {platform}  Brand: {BRAND_LABELS.get(brand, brand)}")

    # Override/fill platform and brand if not from a draft
    if not platform:
        platform = input("\n  Platform (youtube/linkedin/instagram): ").strip().lower()
    if not brand:
        brand_input = input("  Brand (gmc/endo_neutral/harmanjeet): ").strip().lower()
        brand = brand_input or "unknown"

    posted_at_raw = input(f"  Date posted (YYYY-MM-DD) [{datetime.now().strftime('%Y-%m-%d')}]: ").strip()
    posted_at = posted_at_raw if posted_at_raw else datetime.now().strftime("%Y-%m-%d")

    content_summary = input("  Brief content summary (optional, press Enter to skip): ").strip()
    if not content_summary and preview:
        content_summary = preview[:120]

    youtube_video_id = ""
    if platform == "youtube":
        url = input("  YouTube video URL or ID (optional): ").strip()
        if url:
            youtube_video_id = extract_video_id(url)

    print(f"\n  Enter metrics for {platform.upper()} (press Enter to leave at 0):")
    metrics = {}
    for field in PLATFORM_METRICS.get(platform, ["views", "likes", "comments"]):
        if field == "click_through_rate":
            metrics[field] = prompt_float("CTR % (e.g. 2.4)", 0.0)
        else:
            metrics[field] = prompt_int(field.replace("_", " ").title())

    notes = input("\n  Notes (what worked, what didn't): ").strip()

    conn = db.get_connection()
    with conn:
        conn.execute(
            """INSERT INTO content_performance
               (draft_id, run_id, platform, brand, content_summary, posted_at,
                youtube_video_id, views, likes, comments, shares, watch_time_mins,
                impressions, click_through_rate, followers_gained, notes, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                draft_id or None,
                run_id,
                platform, brand, content_summary, posted_at,
                youtube_video_id or None,
                metrics.get("views", 0),
                metrics.get("likes", 0),
                metrics.get("comments", 0),
                metrics.get("shares", 0),
                metrics.get("watch_time_mins", 0),
                metrics.get("impressions", 0),
                metrics.get("click_through_rate", 0.0),
                metrics.get("followers_gained", 0),
                notes or None,
                str(datetime.now()),
            )
        )
    conn.close()

    print(f"\n  Performance logged for {platform.upper()} ({BRAND_LABELS.get(brand, brand)}).")


# ─────────────────────────────────────────────────────────────
# YOUTUBE — fetch public stats via YouTube Data API
# ─────────────────────────────────────────────────────────────

def cmd_youtube(url_or_id: str):
    db.init_schema()

    video_id = extract_video_id(url_or_id)
    if not video_id:
        print(f"  [!] Could not parse video ID from: {url_or_id}")
        sys.exit(1)

    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        print("  [!] YOUTUBE_API_KEY not set in .env")
        sys.exit(1)

    import requests
    url = (
        f"https://www.googleapis.com/youtube/v3/videos"
        f"?part=snippet,statistics&id={video_id}&key={api_key}"
    )
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    items = data.get("items", [])
    if not items:
        print(f"  [!] Video not found: {video_id}")
        sys.exit(1)

    item  = items[0]
    snip  = item["snippet"]
    stats = item.get("statistics", {})

    title       = snip.get("title", "")
    published   = snip.get("publishedAt", "")[:10]
    views       = int(stats.get("viewCount", 0))
    likes       = int(stats.get("likeCount", 0))
    comments    = int(stats.get("commentCount", 0))

    print(f"\n  YouTube public stats for: {title}")
    print(f"  Published : {published}")
    print(f"  Views     : {views:,}")
    print(f"  Likes     : {likes:,}")
    print(f"  Comments  : {comments:,}")

    save = input("\n  Log this to the database? (y/n): ").strip().lower()
    if save != "y":
        return

    brand_input = input("  Brand (gmc/endo_neutral/harmanjeet): ").strip().lower()
    notes = input("  Notes (optional): ").strip()

    conn = db.get_connection()
    with conn:
        conn.execute(
            """INSERT INTO content_performance
               (platform, brand, content_summary, posted_at, youtube_video_id,
                views, likes, comments, notes, recorded_at)
               VALUES ('youtube', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (brand_input, title[:200], published, video_id,
             views, likes, comments, notes or None, str(datetime.now()))
        )
    conn.close()
    print("  Saved.")


# ─────────────────────────────────────────────────────────────
# REPORT — weekly performance vs. intelligence predictions
# ─────────────────────────────────────────────────────────────

def cmd_report(days: int = 30):
    db.init_schema()
    conn = db.get_connection()

    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    entries = conn.execute(
        """SELECT cp.*, cd.post_text, r.timestamp as run_ts,
                  (SELECT GROUP_CONCAT(topic, ' | ')
                   FROM trending_topics WHERE run_id = cp.run_id LIMIT 3) as top_topics
           FROM content_performance cp
           LEFT JOIN content_drafts cd ON cp.draft_id = cd.id
           LEFT JOIN runs r ON cp.run_id = r.id
           WHERE cp.posted_at >= ?
           ORDER BY cp.posted_at DESC""",
        (since,)
    ).fetchall()

    # Engagement score: weighted sum for comparison
    def engagement_score(row):
        return (
            row["views"] * 1 +
            row["likes"] * 5 +
            row["comments"] * 10 +
            row["shares"] * 8 +
            row["followers_gained"] * 15
        )

    conn.close()

    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_path = BASE_DIR / "output" / f"performance_report_{ts}.txt"
    out_path.parent.mkdir(exist_ok=True)

    lines = []
    lines.append("=" * 60)
    lines.append("  CONTENT PERFORMANCE REPORT")
    lines.append("=" * 60)
    lines.append(f"  Period  : last {days} days (since {since})")
    lines.append(f"  Entries : {len(entries)}")
    lines.append(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")

    if not entries:
        lines.append("  No performance data yet.")
        lines.append("  Log posted content with: python auditor.py log")
    else:
        # ── By platform ───────────────────────────────────────
        lines.append("-" * 60)
        lines.append("  PERFORMANCE BY PLATFORM")
        lines.append("-" * 60)

        by_platform: dict[str, list] = {}
        for e in entries:
            by_platform.setdefault(e["platform"], []).append(e)

        for platform, rows in sorted(by_platform.items()):
            total_views    = sum(r["views"] for r in rows)
            total_likes    = sum(r["likes"] for r in rows)
            total_comments = sum(r["comments"] for r in rows)
            total_shares   = sum(r["shares"] for r in rows)
            lines.append(f"\n  {platform.upper()} ({len(rows)} posts)")
            lines.append(f"    Views: {total_views:,}  Likes: {total_likes:,}  "
                         f"Comments: {total_comments:,}  Shares: {total_shares:,}")

        # ── By brand ──────────────────────────────────────────
        lines.append("")
        lines.append("-" * 60)
        lines.append("  PERFORMANCE BY BRAND")
        lines.append("-" * 60)

        by_brand: dict[str, list] = {}
        for e in entries:
            by_brand.setdefault(e["brand"], []).append(e)

        for brand, rows in sorted(by_brand.items()):
            scores = [engagement_score(r) for r in rows]
            avg    = sum(scores) / len(scores) if scores else 0
            lines.append(f"\n  {BRAND_LABELS.get(brand, brand)}")
            lines.append(f"    Posts: {len(rows)}  Avg engagement score: {avg:.0f}")
            best = max(rows, key=engagement_score)
            summary = (best.get("content_summary") or best.get("post_text") or "")[:80]
            lines.append(f"    Best post: {summary}...")

        # ── Individual entries ────────────────────────────────
        lines.append("")
        lines.append("-" * 60)
        lines.append("  ALL LOGGED ENTRIES (newest first)")
        lines.append("-" * 60)

        for e in entries:
            score   = engagement_score(e)
            summary = (e.get("content_summary") or e.get("post_text") or "no summary")[:70]
            lines.append(f"\n  [{e['posted_at']}] {e['platform'].upper()} | {BRAND_LABELS.get(e['brand'], e['brand'])}")
            lines.append(f"  Content : {summary}...")
            lines.append(f"  Metrics : views={e['views']:,}  likes={e['likes']:,}  "
                         f"comments={e['comments']:,}  shares={e['shares']:,}")
            if e["watch_time_mins"]:
                lines.append(f"            watch_time={e['watch_time_mins']}m  "
                             f"CTR={e['click_through_rate']:.1f}%")
            if e["followers_gained"]:
                lines.append(f"            followers_gained={e['followers_gained']}")
            lines.append(f"  Eng.score: {score}")
            if e.get("top_topics"):
                lines.append(f"  Intel topics used: {e['top_topics'][:120]}")
            if e.get("notes"):
                lines.append(f"  Notes   : {e['notes']}")

        # ── Learning summary ──────────────────────────────────
        if len(entries) >= 2:
            ranked = sorted(entries, key=engagement_score, reverse=True)
            lines.append("")
            lines.append("-" * 60)
            lines.append("  LEARNING SUMMARY")
            lines.append("-" * 60)
            lines.append("")
            lines.append("  TOP PERFORMER:")
            best = ranked[0]
            lines.append(f"  {best['platform'].upper()} | {BRAND_LABELS.get(best['brand'], best['brand'])}")
            lines.append(f"  {(best.get('content_summary') or '')[:100]}")
            lines.append(f"  Engagement score: {engagement_score(best)}")
            lines.append("")
            lines.append("  LOWEST PERFORMER:")
            worst = ranked[-1]
            lines.append(f"  {worst['platform'].upper()} | {BRAND_LABELS.get(worst['brand'], worst['brand'])}")
            lines.append(f"  {(worst.get('content_summary') or '')[:100]}")
            lines.append(f"  Engagement score: {engagement_score(worst)}")

    lines.append("")
    lines.append("=" * 60)
    report = "\n".join(lines)

    print(report)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n  Report saved: {out_path}")


# ─────────────────────────────────────────────────────────────
# LIST — show all logged entries
# ─────────────────────────────────────────────────────────────

def cmd_list():
    db.init_schema()
    conn = db.get_connection()
    entries = conn.execute(
        """SELECT id, posted_at, platform, brand, views, likes, comments,
                  substr(coalesce(content_summary, '(no summary)'), 1, 60) as preview
           FROM content_performance ORDER BY id DESC LIMIT 50"""
    ).fetchall()
    conn.close()

    if not entries:
        print("\n  No performance entries yet.")
        print("  Log one with: python auditor.py log")
        return

    print(f"\n  {'ID':<4} {'Date':<12} {'Platform':<12} {'Brand':<16} "
          f"{'Views':>7} {'Likes':>6} {'Cmnts':>6}  Preview")
    print("  " + "-" * 85)
    for e in entries:
        print(f"  {e['id']:<4} {e['posted_at']:<12} {e['platform']:<12} "
              f"{BRAND_LABELS.get(e['brand'], e['brand']):<16} "
              f"{e['views']:>7,} {e['likes']:>6,} {e['comments']:>6,}  {e['preview']}...")


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Content performance auditor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("log",    help="Log performance metrics for a posted piece")
    p_yt = sub.add_parser("youtube", help="Fetch public stats for a YouTube video")
    p_yt.add_argument("url", help="YouTube video URL or 11-character video ID")
    p_rep = sub.add_parser("report", help="Generate performance report")
    p_rep.add_argument("--days", type=int, default=30, help="Days to include (default 30)")
    sub.add_parser("list",   help="List all logged performance entries")

    args = parser.parse_args()

    if args.command == "log":
        cmd_log()
    elif args.command == "youtube":
        cmd_youtube(args.url)
    elif args.command == "report":
        cmd_report(args.days)
    elif args.command == "list":
        cmd_list()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
