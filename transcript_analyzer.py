"""
Transcript Analyzer
───────────────────
Drop a video transcript → get a full creative package:

  • 8 Midjourney / DALL-E / Stable Diffusion image prompts
  • 10 title options with emotional triggers
  • 3 SEO descriptions (different positioning angles)
  • 10 thumbnail text phrases (3–6 words)
  • 30 YouTube tags
  • Hook / opening line suggestions
  • Chapter structure suggestions

Cross-references accumulated intelligence.json so every output
is grounded in what is actually trending and resonating in your niche.

Usage:
  python transcript_analyzer.py                    # reads transcript.txt
  python transcript_analyzer.py my_transcript.txt  # reads named file
  python transcript_analyzer.py --paste            # type / paste in terminal
"""

import sys
import os
import json
import argparse
from datetime import datetime
from dotenv import load_dotenv
import anthropic

load_dotenv(override=True)
client = anthropic.Anthropic(api_key=os.getenv("CLAUDE_API_KEY"))

OUTPUT_DIR = "output"
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ─────────────────────────────────────────────────────────────
# LOAD INPUTS
# ─────────────────────────────────────────────────────────────

def load_transcript(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def paste_transcript() -> str:
    print("\nPaste your transcript below.")
    print("When done, press Enter then type END on a new line and press Enter:\n")
    lines = []
    while True:
        line = input()
        if line.strip().upper() == "END":
            break
        lines.append(line)
    return "\n".join(lines)


def load_intelligence() -> dict:
    if os.path.exists("intelligence.json"):
        with open("intelligence.json", "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


# ─────────────────────────────────────────────────────────────
# BUILD NICHE CONTEXT from intelligence.json
# ─────────────────────────────────────────────────────────────

def build_niche_context(intel: dict) -> str:
    if not intel:
        return "No accumulated intelligence available — analysing transcript in isolation."

    lines = []

    trending = intel.get("trending_topics", [])
    if trending:
        lines.append("CURRENTLY TRENDING IN THIS NICHE:")
        for t in trending[:8]:
            lines.append(f"  • {t}")

    gaps = intel.get("content_gaps", [])
    if gaps:
        lines.append("\nCONTENT GAPS (what audiences want but can't find):")
        for g in gaps[:3]:
            lines.append(f"  • {g}")

    keywords = intel.get("expanded_keywords", [])
    if keywords:
        lines.append(f"\nHIGH-VALUE KEYWORDS IN NICHE: {', '.join(keywords[:20])}")

    thumb_patterns = intel.get("thumbnail_patterns", [])
    if thumb_patterns:
        lines.append("\nTHUMBNAIL PATTERNS THAT WORK:")
        for p in thumb_patterns[:3]:
            lines.append(f"  • {p}")

    sample = intel.get("data_sample", [])
    comments = [d for d in sample if d["source"] == "youtube_comment"][:8]
    reddit   = [d for d in sample if "reddit" in d["source"]][:5]
    if comments or reddit:
        lines.append("\nRAW AUDIENCE VOICE (top community signals):")
        for d in comments:
            lines.append(f'  [comment] "{d["text"][:150]}"')
        for d in reddit:
            lines.append(f'  [reddit]  "{d["text"][:150]}"')

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# AI CREATIVE PACKAGE PROMPT
# ─────────────────────────────────────────────────────────────

def generate_creative_package(transcript: str, intel: dict) -> dict:
    niche_context = build_niche_context(intel)

    # Truncate very long transcripts to ~6000 words to stay within context
    words = transcript.split()
    if len(words) > 6000:
        transcript_block = " ".join(words[:6000]) + "\n\n[... transcript truncated at 6000 words ...]"
        print(f"  Note: transcript truncated from {len(words)} to 6000 words for AI context")
    else:
        transcript_block = transcript

    prompt = f"""You are an elite YouTube creative strategist specialising in women's health content (menopause, ADHD, PCOS, endometriosis, hormonal health).

You have two inputs:
1. A VIDEO TRANSCRIPT — the raw content of a YouTube video
2. NICHE INTELLIGENCE — accumulated data on what is trending, what audiences are actually saying, and what content gaps exist

Your job is to produce a complete, ready-to-use creative package for this video. Everything must be specific, emotionally grounded in the transcript, and calibrated against the niche intelligence.

━━━ NICHE INTELLIGENCE (accumulated from Reddit, YouTube comments, Google Trends) ━━━
{niche_context}

━━━ VIDEO TRANSCRIPT ━━━
{transcript_block}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Generate ALL of the following. Be specific to this transcript — not generic.

─── 1. IMAGE GENERATION PROMPTS (8 prompts) ───
For Midjourney / DALL-E 3 / Stable Diffusion.
Each prompt must:
  - Capture the core emotional hook or key insight from the transcript
  - Be visually specific: subject, expression, setting, lighting, composition, style
  - Be formatted for immediate use in an image generator
  - Include style suffix: "--ar 16:9 --style raw --v 6" for Midjourney, or equivalent instruction for DALL-E
  - Vary in approach: close-up portrait, symbolic/metaphorical, documentary style, split-frame, text-overlay suggestion, etc.
  - Trigger the specific emotion the transcript evokes (confusion, relief, anger at being dismissed, hope, urgency)

─── 2. VIDEO TITLES (10 options) ───
  - Directly rooted in the transcript's main revelation or emotional core
  - Use formats: "Why I...", "Nobody Told Me...", "The Real Reason...", "What Your Doctor Won't Say About...", "I Was Wrong About...", numbered lists, question hooks
  - Optimised for YouTube search using keywords from niche intelligence
  - Each title should work as a standalone hook that makes someone stop scrolling

─── 3. SEO DESCRIPTIONS (3 versions) ───
Each ~150–200 words. First 2–3 lines are crucial (shown in search before "show more").
  Version A: Search-optimised — keyword-heavy, informational, targets discovery
  Version B: Community-voice — personal, empathetic, mirrors the audience's language from Reddit/comments
  Version C: Authority — positions the creator as the expert, cites the core insight of the video
Include in each: primary topic, 3–5 related keywords, soft call to action, 2–3 relevant hashtags at the end.

─── 4. THUMBNAIL TEXT OPTIONS (10 phrases) ───
  - 3–6 words maximum
  - Curiosity gap or emotional trigger
  - Must make sense at thumbnail scale (large, readable, impactful)
  - Directly tied to a specific moment or claim in the transcript
  - Avoid vague generic phrases — be specific to the video's core argument

─── 5. YOUTUBE TAGS (30 tags) ───
  Mix of: exact match (specific to this video), broad match (niche-wide), long-tail (specific questions/symptoms)
  Format: comma-separated list

─── 6. HOOK / OPENING LINE SUGGESTIONS (5 options) ───
  First 15 seconds determine whether the viewer stays.
  Each hook should: state a pain point, make a bold claim, or ask a question the viewer is silently asking.
  Write as spoken lines, not titles.

─── 7. CHAPTER STRUCTURE SUGGESTIONS ───
  Based on the transcript content, suggest 5–8 timestamp chapters with descriptive names.
  Format: 00:00 - Chapter Name

Return ONLY valid JSON in this exact format:
{{
  "image_prompts": [
    {{"id": 1, "style": "close-up portrait", "prompt": "...", "midjourney_suffix": "--ar 16:9 --style raw --v 6"}},
    ...
  ],
  "titles": [],
  "seo_descriptions": {{
    "version_a_search": "",
    "version_b_community": "",
    "version_c_authority": ""
  }},
  "thumbnail_text": [],
  "youtube_tags": "",
  "hooks": [],
  "chapters": []
}}"""

    try:
        print("  Sending to Claude (claude-sonnet-4-6)...")
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=5000,
            temperature=0.8,
            messages=[{"role": "user", "content": prompt}]
        )

        raw = response.content[0].text
        result = safe_json_parse(raw)
        if not result:
            print("  [!] Could not parse JSON. Saving raw AI response.")
            result = {"raw_response": raw}
        return result

    except Exception as e:
        print(f"  [AI] Error: {e}")
        return {}


# ─────────────────────────────────────────────────────────────
# JSON PARSER
# ─────────────────────────────────────────────────────────────

def safe_json_parse(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        try:
            start = text.find("{")
            end   = text.rfind("}") + 1
            return json.loads(text[start:end])
        except Exception:
            return {}


# ─────────────────────────────────────────────────────────────
# FORMAT REPORT
# ─────────────────────────────────────────────────────────────

def format_report(package: dict, transcript_path: str) -> str:
    def sec(title): return f"\n{'─' * 60}\n  {title}\n{'─' * 60}"

    lines = []
    lines.append("=" * 60)
    lines.append("  CREATIVE PACKAGE")
    lines.append("=" * 60)
    lines.append(f"  Source:    {transcript_path}")
    lines.append(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # ── Image prompts ─────────────────────────────────────────
    lines.append(sec("IMAGE GENERATION PROMPTS (for Midjourney / DALL-E 3)"))
    for p in package.get("image_prompts", []):
        lines.append(f"\n  PROMPT {p.get('id', '?')} — {p.get('style', '').upper()}")
        full = p.get("prompt", "")
        suffix = p.get("midjourney_suffix", "")
        lines.append(f"  {full}")
        if suffix:
            lines.append(f"  {suffix}")

    # ── Titles ────────────────────────────────────────────────
    lines.append(sec("VIDEO TITLES (10 options)"))
    for i, t in enumerate(package.get("titles", []), 1):
        lines.append(f"  {i:2}. {t}")

    # ── SEO Descriptions ──────────────────────────────────────
    lines.append(sec("SEO DESCRIPTIONS"))
    descs = package.get("seo_descriptions", {})
    if descs.get("version_a_search"):
        lines.append("\n  VERSION A — Search Optimised:")
        lines.append(f"  {descs['version_a_search']}")
    if descs.get("version_b_community"):
        lines.append("\n  VERSION B — Community Voice:")
        lines.append(f"  {descs['version_b_community']}")
    if descs.get("version_c_authority"):
        lines.append("\n  VERSION C — Authority Positioning:")
        lines.append(f"  {descs['version_c_authority']}")

    # ── Thumbnail text ────────────────────────────────────────
    lines.append(sec("THUMBNAIL TEXT (3–6 words each)"))
    for t in package.get("thumbnail_text", []):
        lines.append(f"  ▸ {t}")

    # ── Tags ──────────────────────────────────────────────────
    lines.append(sec("YOUTUBE TAGS (30)"))
    tags = package.get("youtube_tags", "")
    if isinstance(tags, list):
        tags = ", ".join(tags)
    lines.append(f"  {tags}")

    # ── Hooks ─────────────────────────────────────────────────
    lines.append(sec("OPENING HOOKS (first 15 seconds)"))
    for i, h in enumerate(package.get("hooks", []), 1):
        lines.append(f"  {i}. \"{h}\"")

    # ── Chapters ──────────────────────────────────────────────
    lines.append(sec("SUGGESTED CHAPTERS"))
    for c in package.get("chapters", []):
        lines.append(f"  {c}")

    lines.append("\n" + "=" * 60)
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Transcript → Creative Package")
    parser.add_argument("transcript_file", nargs="?", default=None, help="Path to transcript .txt file")
    parser.add_argument("--paste", action="store_true", help="Paste transcript in terminal")
    args = parser.parse_args()

    # ── Load transcript ───────────────────────────────────────
    if args.paste:
        transcript = paste_transcript()
        transcript_path = "pasted"
    elif args.transcript_file:
        if not os.path.exists(args.transcript_file):
            print(f"File not found: {args.transcript_file}")
            sys.exit(1)
        transcript = load_transcript(args.transcript_file)
        transcript_path = args.transcript_file
    elif os.path.exists("transcript.txt"):
        transcript = load_transcript("transcript.txt")
        transcript_path = "transcript.txt"
        print(f"  Reading from transcript.txt ({len(transcript.split())} words)")
    else:
        print("No transcript found.")
        print("Options:")
        print("  1. Save transcript as 'transcript.txt' in this folder")
        print("  2. Run: python transcript_analyzer.py my_file.txt")
        print("  3. Run: python transcript_analyzer.py --paste")
        sys.exit(1)

    if not transcript.strip():
        print("Transcript is empty.")
        sys.exit(1)

    print(f"\n  Transcript loaded: {len(transcript.split())} words")

    # ── Load niche intelligence ───────────────────────────────
    intel = load_intelligence()
    if intel:
        print(f"  Intelligence loaded: {len(intel.get('trending_topics', []))} trending topics, "
              f"{len(intel.get('data_sample', []))} raw data points")
    else:
        print("  No intelligence.json found — run listener_brain.py first for best results")

    # ── Generate creative package ─────────────────────────────
    print("\n" + "━" * 52)
    print("  Generating creative package...")
    print("━" * 52)

    package = generate_creative_package(transcript, intel)

    if not package:
        print("  Failed to generate package.")
        sys.exit(1)

    # ── Save JSON ─────────────────────────────────────────────
    ts       = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    json_out = os.path.join(OUTPUT_DIR, f"creative_package_{ts}.json")
    txt_out  = os.path.join(OUTPUT_DIR, f"creative_package_{ts}.txt")

    with open(json_out, "w", encoding="utf-8") as f:
        json.dump(package, f, indent=2, ensure_ascii=False)

    # ── Save formatted report ─────────────────────────────────
    report = format_report(package, transcript_path)
    with open(txt_out, "w", encoding="utf-8") as f:
        f.write(report)

    # ── Print report ──────────────────────────────────────────
    print(report)
    print(f"\n  JSON saved:   {json_out}")
    print(f"  Report saved: {txt_out}")


if __name__ == "__main__":
    main()
