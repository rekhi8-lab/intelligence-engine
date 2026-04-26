import os
import re
import requests
from dotenv import load_dotenv
from googleapiclient.discovery import build

load_dotenv(override=True)
API_KEY = os.getenv("YOUTUBE_API_KEY")

youtube = build("youtube", "v3", developerKey=API_KEY)


def get_trending_videos(query="menopause health", max_results=10):
    request = youtube.search().list(
        q=query,
        part="snippet",
        type="video",
        maxResults=max_results,
        order="viewCount"
    )
    response = request.execute()

    videos = []

    for item in response["items"]:
        video_id = item["id"]["videoId"]
        snippet = item["snippet"]

        video_data = {
            "video_id": video_id,
            "title": snippet["title"],
            "description": snippet["description"],
            "thumbnail_url": snippet["thumbnails"]["high"]["url"]
        }

        videos.append(video_data)

    return videos


def download_thumbnail(video, folder="output/thumbnails"):
    os.makedirs(folder, exist_ok=True)

    url = video["thumbnail_url"]
    path = f"{folder}/{video['video_id']}.jpg"

    img = requests.get(url).content
    with open(path, "wb") as f:
        f.write(img)

    return path


# ── Channel-specific functions ────────────────────────────────

def resolve_channel_id(channel_url_or_handle: str) -> str | None:
    """
    Convert a YouTube channel URL, @handle, or bare channel ID to a channel ID.

    Accepts:
      - https://www.youtube.com/@GlobalMenopauseCollective
      - https://www.youtube.com/channel/UCxxxxxx
      - https://www.youtube.com/c/channelname
      - https://www.youtube.com/user/username
      - @handle (bare)
      - UCxxxxxx (already a channel ID)
    """
    s = channel_url_or_handle.strip()

    # Already a channel ID
    if re.match(r"^UC[A-Za-z0-9_\-]{22}$", s):
        return s

    # Bare @handle
    if s.startswith("@") and not s.startswith("http"):
        handle = s.lstrip("@")
        try:
            r = youtube.channels().list(forHandle=handle, part="id").execute()
            items = r.get("items", [])
            if items:
                return items[0]["id"]
        except Exception:
            pass
        return None

    # youtube.com/@handle
    m = re.search(r"youtube\.com/@([\w.\-]+)", s)
    if m:
        handle = m.group(1)
        try:
            r = youtube.channels().list(forHandle=handle, part="id").execute()
            items = r.get("items", [])
            if items:
                return items[0]["id"]
        except Exception:
            pass

    # youtube.com/channel/UC...
    m = re.search(r"youtube\.com/channel/(UC[A-Za-z0-9_\-]{22})", s)
    if m:
        return m.group(1)

    # youtube.com/c/name  or  youtube.com/user/name
    m = re.search(r"youtube\.com/(?:c/|user/)([\w.\-]+)", s)
    if m:
        name = m.group(1)
        try:
            r = youtube.channels().list(forUsername=name, part="id").execute()
            items = r.get("items", [])
            if items:
                return items[0]["id"]
        except Exception:
            pass
        # Fall through to search
        s = name

    # Last resort: search for the channel name
    try:
        r = youtube.search().list(q=s, type="channel", part="snippet", maxResults=1).execute()
        items = r.get("items", [])
        if items:
            return items[0]["id"]["channelId"]
    except Exception:
        pass

    return None


def get_channel_videos(channel_id: str, max_results: int = 10) -> list[dict]:
    """
    Fetch recent videos from a YouTube channel, sorted by upload date.
    Returns basic metadata + view/like/comment stats.
    """
    try:
        search_res = youtube.search().list(
            channelId=channel_id,
            part="snippet",
            type="video",
            order="date",
            maxResults=max_results
        ).execute()

        items = search_res.get("items", [])
        if not items:
            return []

        video_ids = [item["id"]["videoId"] for item in items]

        stats_res = youtube.videos().list(
            id=",".join(video_ids),
            part="statistics,snippet,contentDetails"
        ).execute()

        videos = []
        for item in stats_res.get("items", []):
            stats   = item.get("statistics", {})
            snippet = item.get("snippet", {})
            thumbs  = snippet.get("thumbnails", {})
            thumb_url = (
                thumbs.get("maxres", thumbs.get("high", thumbs.get("default", {}))).get("url", "")
            )
            videos.append({
                "video_id":     item["id"],
                "title":        snippet.get("title", ""),
                "description":  (snippet.get("description", "") or "")[:300],
                "published_at": snippet.get("publishedAt", ""),
                "thumbnail_url": thumb_url,
                "views":        int(stats.get("viewCount",   0)),
                "likes":        int(stats.get("likeCount",   0)),
                "comments":     int(stats.get("commentCount", 0)),
            })

        videos.sort(key=lambda x: x["views"], reverse=True)
        return videos

    except Exception as e:
        print(f"  [YT] Error fetching videos for channel {channel_id}: {e}")
        return []


def get_channel_stats(channel_id: str) -> dict:
    """Return subscriber count, total views, and video count for a channel."""
    try:
        res   = youtube.channels().list(
            id=channel_id, part="statistics,snippet"
        ).execute()
        items = res.get("items", [])
        if not items:
            return {}
        item    = items[0]
        stats   = item.get("statistics", {})
        snippet = item.get("snippet", {})
        return {
            "name":        snippet.get("title", ""),
            "description": (snippet.get("description", "") or "")[:200],
            "subscribers": int(stats.get("subscriberCount", 0)),
            "total_views": int(stats.get("viewCount",       0)),
            "video_count": int(stats.get("videoCount",      0)),
        }
    except Exception as e:
        print(f"  [YT] Error fetching stats for channel {channel_id}: {e}")
        return {}