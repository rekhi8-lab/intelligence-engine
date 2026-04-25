import os
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