from textblob import TextBlob
import nltk

nltk.download('punkt')

def analyze_text(text):
    blob = TextBlob(text)

    return {
        "polarity": blob.sentiment.polarity,
        "subjectivity": blob.sentiment.subjectivity
    }


def extract_keywords(text):
    words = text.split()
    keywords = [w for w in words if len(w) > 4]
    return list(set(keywords))