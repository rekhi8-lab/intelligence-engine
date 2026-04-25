import os
from dotenv import load_dotenv

load_dotenv(override=True)

# ------------------------
# CLAUDE API
# ------------------------

CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY")


# ------------------------
# YOUTUBE API
# ------------------------

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")


# ------------------------
# SYSTEM SETTINGS
# ------------------------

BASE_SEARCH_QUERIES = [
    "menopause anxiety",
    "perimenopause symptoms",
    "ADHD in women",
    "endometriosis pain",
    "early puberty girls",
    "PCOS symptoms",
    "women hormonal imbalance"
]


YOUTUBE_SEARCH_QUERIES = [
    "menopause symptoms",
    "perimenopause anxiety",
    "ADHD in women",
    "endometriosis pain",
    "early puberty girls"
]


# ------------------------
# FILTER SETTINGS
# ------------------------

MIN_COMMENT_LENGTH = 20

SPAM_KEYWORDS = [
    "subscribe",
    "check my channel",
    "http",
    "www",
    "buy now"
]