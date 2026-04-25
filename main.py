import json
import pandas as pd

from youtube_scraper import get_trending_videos, download_thumbnail
from thumbnail_analyzer import extract_text_from_thumbnail
from text_analyzer import analyze_text, extract_keywords

FALLBACK_TOPICS = [
    "menopause anxiety symptoms",
    "perimenopause symptoms",
    "ADHD in women",
    "endometriosis pain",
    "early puberty girls"
]


def load_intelligence():
    try:
        with open("intelligence.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print("intelligence.json not found. Run listener_brain.py first.")
        return None


def run_pipeline():
    intelligence = load_intelligence()
    if not intelligence:
        return

    # Prefer evolved queries → trending topics → fallback
    topics = (
        intelligence.get("next_search_queries") or
        intelligence.get("trending_topics") or
        FALLBACK_TOPICS
    )
    topics = topics[:10]  # cap API usage

    all_data = []

    for topic in topics:
        print(f"\nSearching: {topic}")

        try:
            videos = get_trending_videos(query=topic, max_results=5)
        except Exception as e:
            print(f"  YouTube error: {e}")
            continue

        for video in videos:
            try:
                print(f"  Processing: {video['title']}")

                thumb_path = download_thumbnail(video)
                thumb_text = extract_text_from_thumbnail(thumb_path)
                sentiment  = analyze_text(video["title"])
                keywords   = extract_keywords(video["title"] + " " + thumb_text)

                all_data.append({
                    "topic":          topic,
                    "title":          video["title"],
                    "description":    video["description"],
                    "thumbnail_text": thumb_text,
                    "keywords":       ", ".join(keywords),
                    "polarity":       sentiment["polarity"],
                    "subjectivity":   sentiment["subjectivity"]
                })

            except Exception as e:
                print(f"    Error: {e}")
                continue

    if all_data:
        df = pd.DataFrame(all_data)
        df.to_csv("output/final_trend_analysis.csv", index=False, encoding="utf-8-sig")
        print(f"\nTrend analysis saved — {len(all_data)} videos across {len(topics)} topics")
    else:
        print("\nNo data collected.")


if __name__ == "__main__":
    run_pipeline()
