import re
import unicodedata


PALI_DIACRITICS = str.maketrans(
    {
        "ā": "a",
        "ī": "i",
        "ū": "u",
        "ṅ": "n",
        "ñ": "n",
        "ṭ": "t",
        "ḍ": "d",
        "ṇ": "n",
        "ḷ": "l",
        "ṃ": "m",
        "ṁ": "m",
    }
)


def strip_vietnamese(text: str) -> str:
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.replace("đ", "d").replace("Đ", "D").lower()


def normalize_pali(text: str) -> str:
    text = text.lower().translate(PALI_DIACRITICS)
    text = re.sub(r"[^\w\s-]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def tokenize(text: str) -> list[str]:
    normalized = strip_vietnamese(text)
    tokens = re.findall(r"[a-z0-9]+", normalized)
    stopwords = {
        "tim",
        "cho",
        "toi",
        "bai",
        "kinh",
        "doan",
        "noi",
        "ve",
        "cua",
        "su",
        "la",
        "trong",
        "nhung",
        "cac",
        "mot",
        "nguoi",
    }
    return [token for token in tokens if token not in stopwords and len(token) > 1]
