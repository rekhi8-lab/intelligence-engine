import subprocess
import os
from pathlib import Path
from datetime import datetime
import json
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ------------------------
# CONFIG
# ------------------------

BASE_DIR = Path(__file__).parent
LOG_DIR = BASE_DIR / "logs"
OUTPUT_DIR = BASE_DIR / "output"

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

log_file_path = LOG_DIR / f"log_{timestamp}.txt"
final_output_path = OUTPUT_DIR / f"final_output_{timestamp}.txt"

# ------------------------
# LOG FUNCTION
# ------------------------

def log(message):
    print(message)
    with open(log_file_path, "a", encoding="utf-8") as f:
        f.write(message + "\n")

# ------------------------
# RUN SCRIPT FUNCTION
# ------------------------

def run_script(script_name):
    log(f"\nRunning {script_name}...\n")

    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"

        result = subprocess.run(
            [sys.executable, script_name],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env
        )

        if result.stdout:
            log(result.stdout)

        if result.returncode != 0:
            log("Script failed with error:")
            log(result.stderr)
            raise Exception(f"{script_name} failed")

    except Exception as e:
        log(f"Critical failure while running {script_name}: {e}")
        sys.exit(1)

# ------------------------
# FINAL REPORT (all 12 fields)
# ------------------------

def section(title, char="-"):
    return f"\n{char * 55}\n  {title}\n{char * 55}"


def generate_final_output():
    intelligence_path = BASE_DIR / "intelligence.json"

    if not os.path.exists(intelligence_path):
        log("intelligence.json not found. Skipping report generation.")
        return

    with open(intelligence_path, "r", encoding="utf-8") as f:
        intel = json.load(f)

    lines = []
    lines.append("=" * 55)
    lines.append("  INTELLIGENCE ENGINE REPORT  v2.0")
    lines.append("=" * 55)
    lines.append(f"\n  Generated : {intel.get('timestamp', 'unknown')}")

    ds = intel.get("data_sources", {})
    if ds:
        lines.append(f"  Sources   : Reddit {ds.get('reddit',0)} | YouTube {ds.get('youtube',0)} | Trends {ds.get('google_trends',0)} | Total {ds.get('total',0)}")

    # 1 — Trending Topics
    lines.append(section("TOP 10 TRENDING TOPICS"))
    for i, t in enumerate(intel.get("trending_topics", []), 1):
        lines.append(f"  {i:2}. {t}")

    # 2 — Content Gaps
    lines.append(section("TOP 3 CONTENT GAPS"))
    for i, g in enumerate(intel.get("content_gaps", []), 1):
        lines.append(f"  {i}. {g}")

    # 3 — YouTube Titles
    lines.append(section("10 HIGH-PERFORMING YOUTUBE TITLES"))
    for i, t in enumerate(intel.get("youtube_titles", []), 1):
        lines.append(f"  {i:2}. {t}")

    # 4 — Thumbnail Text Ideas
    lines.append(section("THUMBNAIL TEXT IDEAS (3–6 words)"))
    for t in intel.get("thumbnail_text_ideas", []):
        lines.append(f"  > {t}")

    # 5 — Thumbnail Patterns
    lines.append(section("THUMBNAIL PATTERNS"))
    for p in intel.get("thumbnail_patterns", []):
        lines.append(f"  - {p}")

    # 6 — Content Series Ideas
    lines.append(section("CONTENT SERIES IDEAS"))
    for i, s in enumerate(intel.get("content_series_ideas", []), 1):
        lines.append(f"  {i}. {s}")

    # 7 — Creator Strategy
    lines.append(section("POSTING FREQUENCY MODEL"))
    lines.append(f"  {intel.get('posting_frequency_model', 'N/A')}")

    lines.append(section("MULTI-CHANNEL STRATEGY"))
    lines.append(f"  {intel.get('multi_channel_strategy', 'N/A')}")

    lines.append(section("AUDIENCE FUNNEL STRATEGY"))
    lines.append(f"  {intel.get('audience_funnel_strategy', 'N/A')}")

    # 8 — Expanded Keywords
    lines.append(section("EXPANDED KEYWORDS (25–30)"))
    kw = intel.get("expanded_keywords", [])
    for i, k in enumerate(kw, 1):
        lines.append(f"  {i:2}. {k}")

    # 9 — Next Search Queries
    lines.append(section("NEXT SEARCH QUERIES (for next run)"))
    for q in intel.get("next_search_queries", []):
        lines.append(f"  -> {q}")

    # 10 — Memory Keywords
    lines.append(section("MEMORY KEYWORDS (learning base)"))
    mem = intel.get("memory_keywords", [])
    lines.append("  " + " | ".join(mem))

    # Query pool status
    qp = intel.get("query_pool", {})
    if qp:
        lines.append(section("QUERY EVOLUTION ENGINE"))
        lines.append(f"  Run #{qp.get('run', '?')} | Active queries: {qp.get('active', '?')} | Retired: {qp.get('retired', '?')}")
        used = qp.get("used_this_run", [])
        if used:
            lines.append(f"\n  Queries used this run:")
            for q in used:
                lines.append(f"    -> {q}")

    lines.append(section("NEXT STEPS"))
    lines.append("  To analyse a video transcript and generate thumbnails/titles/SEO:")
    lines.append("    python transcript_analyzer.py your_transcript.txt")
    lines.append("  Or paste directly:")
    lines.append("    python transcript_analyzer.py --paste")
    lines.append("\n  Run the full pipeline again tomorrow for evolved queries:")
    lines.append("    python run_all.py")

    lines.append("\n" + "=" * 55 + "\n")

    report = "\n".join(lines)

    with open(final_output_path, "w", encoding="utf-8") as f:
        f.write(report)

    log(report)
    log(f"\n  Report saved to: {final_output_path}")

# ------------------------
# MAIN EXECUTION
# ------------------------

def main():
    log("Starting full trend engine pipeline...\n")

    run_script("listener_brain.py")
    run_script("main.py")

    generate_final_output()

    log("\nPipeline completed successfully.\n")

# ------------------------
# RUN
# ------------------------

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nFatal error: {e}")
    finally:
        try:
            input("\nPress Enter to exit...")
        except EOFError:
            pass