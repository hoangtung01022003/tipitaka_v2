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


# Ky tu vo hinh hay dinh theo khi copy tu PDF hoac trinh duyet: gach noi mem, zero-width,
# dau dinh huong, BOM. Phai XOA HAN chu khong thay bang khoang trang - `[^\w\s-]` ben duoi
# bien chung thanh dau cach, tuc la "pali" thanh "pa li", vo doi ca tu.
_INVISIBLE = re.compile(r"[­​-‏⁠﻿]")


def normalize_pali(text: str) -> str:
    """Chuan hoa chuoi Pali de so khop.

    Buoc NFC la bat buoc, khong phai lam cho du. `PALI_DIACRITICS` tra theo TUNG ma ky tu,
    nen chi bat duoc dang dung san ("ā" = 1 ma). Van ban dan tu macOS hay tu PDF thuong o
    dang tach roi ("a" + dau macron = 2 ma): bang tra truot, roi dau macron khong phai
    `\\w` nen bi thay bang khoang trang. Ket qua la
        "Sabbe sankhara aniccati" -> "sabbe san kha ra anicca ti"
    tuc moi tu vo thanh nhieu manh va truy van khong con khop gi. Gop lai NFC truoc thi
    hai dang cho ra cung mot ket qua.
    """
    text = _INVISIBLE.sub("", text)
    text = unicodedata.normalize("NFC", text)
    text = text.lower().translate(PALI_DIACRITICS)
    text = re.sub(r"[^\w\s-]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


PALI_STOPWORDS = {
    "aha", "ahosi", "api", "assa", "atha", "atthi", "ayam", "bhagava", "bhante",
    "bhikkhave", "ca", "ce", "eva", "evam", "hi", "hoti", "honti", "idam", "idha",
    "iti", "kho", "me", "na", "nam", "no", "pana", "puna", "sa", "santi",
    "seyyathidam", "so", "ta", "tam", "tassa", "tatha", "tattha", "te", "tena",
    "va", "vo", "vuccati", "vutta", "ya", "yadi", "yam", "yatha", "yena", "yo",
    # Từ chức năng tiếng Anh - loại ra để truy vấn tiếng Anh không kéo theo nhiễu.
    "the", "and", "for", "that", "this", "with", "from", "was", "were", "are",
    "have", "has", "but", "all", "his", "her", "its", "who", "what", "when",
    "where", "how", "why", "they", "them", "there", "then", "not", "you", "any",
}

# Ranh giới câu/dòng: xuống dòng và dấu kết câu. Dấu phẩy KHÔNG tách vì nằm giữa câu.
_SEGMENT_SPLIT = re.compile(r"(?:\r?\n)+|[.!?;؛।॥]+\s*")


def query_segments(text: str) -> list[str]:
    """Tách truy vấn thành từng câu/dòng.

    Cần thiết vì một câu kệ Pali thường được lưu thành nhiều dòng `passages`
    riêng biệt, nên khi người dùng dán nhiều dòng thì không dòng nào chứa đủ
    toàn bộ chuỗi.
    """
    parts = [part.strip(" \t‘’“”\"'-") for part in _SEGMENT_SPLIT.split(text or "")]
    segments = [part for part in parts if len(part.split()) >= 2]
    return segments or ([text.strip()] if text and text.strip() else [])


def pali_ratio(text: str) -> float:
    """Tỷ lệ từ viết bằng chữ Latin không dấu - dùng để đoán truy vấn có phải Pali/Anh không.

    Tiếng Việt có dấu sẽ rớt về ~0 nên không bị lấy nhầm làm từ khóa Pali.
    """
    words = normalize_pali(text).split()
    if not words:
        return 0.0
    return sum(1 for word in words if re.fullmatch(r"[a-z-]+", word)) / len(words)


def pali_content_tokens(text: str, min_length: int = 3, limit: int = 24) -> list[str]:
    """Các token Pali đặc trưng lấy thẳng từ truy vấn người dùng.

    Đây là tín hiệu tìm kiếm cuối cùng khi glossary không khớp khái niệm nào và
    Gemini cũng không trả về thuật ngữ - trước đây trường hợp đó không còn từ nào
    để truy vấn nên trả về 0 kết quả.
    """
    tokens: list[str] = []
    for token in normalize_pali(text).split():
        token = token.strip("-")
        if len(token) < min_length or not re.fullmatch(r"[a-z]+", token):
            continue
        if token in PALI_STOPWORDS or token in tokens:
            continue
        tokens.append(token)
        if len(tokens) >= limit:
            break
    return tokens


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
        "gi",
        "khai",
        "niem",
        "dinh",
        "nghia",
        "giai",
        "thich",
        "the",
        "nao",
        "trong",
        "nhung",
        "cac",
        "mot",
        "nguoi",
    }
    return [token for token in tokens if token not in stopwords and len(token) > 1]
