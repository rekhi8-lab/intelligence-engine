import shutil
import pytesseract
from PIL import Image

TESSERACT_PATH = shutil.which("tesseract")
if TESSERACT_PATH:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH

def extract_text_from_thumbnail(image_path):
    try:
        img = Image.open(image_path)
        text = pytesseract.image_to_string(img)
        return text.strip()
    except:
        return ""