"""Hướng tìm kiếm dự phòng khi pipeline chính không ra kết quả nào.

Theo yêu cầu trong `feat_new/toiuu_timkiem.docx`: pipeline hiện tại vẫn được ưu tiên
vì chuẩn hơn, đây chỉ là giải pháp cho trường hợp không tìm thấy gì.

Ý tưởng của khách gồm hai phần:

1. Rút gọn dần từ khóa rồi tìm lại
   ("có người đi tìm rắn, chuyên tâm tìm rắn..." -> "Người đi tìm rắn" -> "tìm rắn" -> "rắn").
   Phần này đã làm trong file này.

2. Định tuyến qua các bản dịch song ngữ Pali đối chiếu (Indacanda cho tiếng Việt,
   Bhikkhu Sujato cho tiếng Anh): tìm trong bản dịch trước, lấy câu Pali đối chiếu
   ở trang bên cạnh rồi truy ngược về Tipiṭaka.
   Phần này CHƯA làm được vì các bản dịch đó hiện chỉ có ở dạng PDF trong `feat_new/`,
   chưa được bóc tách và căn chỉnh với văn bản Pali. Xem `app/translation_sources.py`
   để biết điểm mở rộng khi có dữ liệu.
"""

import re

from .normalize import strip_vietnamese

# Số bậc rút gọn tối đa. Mỗi bậc là một lượt truy vấn DB nên đừng để quá sâu.
MAX_LADDER_STEPS = 4

# Từ đệm bị bỏ trước tiên khi rút gọn - chúng không mang nội dung cần tìm.
# Viết CÓ DẤU và so khớp có dấu: bỏ dấu sẽ làm "chó" trùng với từ đệm "cho".
# Cố tình không liệt kê các từ vừa là từ đệm vừa là thuật ngữ Phật học ("tâm", "pháp").
FILLER_WORDS = {
    "có", "của", "và", "với", "khi", "đang", "người", "ấy", "một", "các", "những",
    "là", "thì", "mà", "để", "cho", "đến", "từ", "trong", "ngoài", "trên", "dưới",
    "rằng", "nên", "sẽ", "đã", "bị", "được", "vào", "ra", "này", "đó", "kia",
    "thế", "nào", "gì", "sao", "thật", "rất", "hơn", "nữa", "con", "cái", "vị",
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "and", "or", "is",
    "was", "were", "are", "be", "been", "who", "what", "when", "where", "how",
    "that", "this", "with", "from", "by", "as", "it", "he", "she", "they",
}

_TRIM_CHARS = ",.;:!?\"'“”‘’()[]"


def _words(query: str) -> list[str]:
    return [word for word in re.split(r"\s+", query.strip()) if word]


def _bare(word: str) -> str:
    return word.strip(_TRIM_CHARS).lower()


def _is_filler(word: str) -> bool:
    return _bare(word) in FILLER_WORDS


def _keyword_frequency(words: list[str]) -> list[str]:
    """Xếp các từ mang nội dung theo số lần lặp giảm dần.

    Người dùng lặp lại từ khóa chính nhiều lần trong câu dài
    ("tìm rắn, chuyên tâm tìm rắn, tìm kiếm rắn"), nên tần suất là tín hiệu tốt
    để biết đâu là từ cần giữ lại đến bậc cuối cùng.
    """
    counts: dict[str, int] = {}
    display: dict[str, str] = {}
    first_seen: dict[str, int] = {}
    for index, word in enumerate(words):
        if _is_filler(word) or len(_bare(word)) < 2:
            continue
        key = _bare(word)
        counts[key] = counts.get(key, 0) + 1
        display.setdefault(key, word.strip(_TRIM_CHARS))
        first_seen.setdefault(key, index)
    ordered = sorted(counts, key=lambda key: (-counts[key], first_seen[key]))
    return [display[key] for key in ordered]


def build_query_ladder(query: str) -> list[str]:
    """Các bậc truy vấn rút gọn dần, từ dài nhất tới ngắn nhất.

    Không bao gồm chính truy vấn gốc vì bậc đó đã chạy ở pipeline chính.
    """
    words = _words(query)
    ladder: list[str] = []

    # Bậc 1: bỏ từ đệm, giữ nguyên trật tự câu.
    content_words = [word for word in words if not _is_filler(word)]
    if content_words and len(content_words) < len(words):
        ladder.append(" ".join(content_words))

    # Các bậc sau: giữ lại dần các từ khóa lặp nhiều nhất, ngắn dần về một từ.
    keywords = _keyword_frequency(words)
    for keep in (3, 2, 1):
        if len(keywords) >= keep:
            ladder.append(" ".join(keywords[:keep]))

    deduped: list[str] = []
    normalized_query = strip_vietnamese(query).strip()
    for step in ladder:
        if not step.strip():
            continue
        if strip_vietnamese(step).strip() == normalized_query:
            continue
        if step not in deduped:
            deduped.append(step)
    return deduped[:MAX_LADDER_STEPS]


def run_fallback(
    query: str,
    corpus_types: list[str],
    pitaka_type: str | None,
    page_size: int,
    search_fn,
) -> dict | None:
    """Chạy lần lượt các bậc rút gọn, dừng ở bậc đầu tiên có kết quả.

    `search_fn(query, corpus_types, pitaka_type, page, page_size)` là hàm tìm kiếm
    của pipeline chính, truyền vào để tránh import vòng.
    """
    ladder = build_query_ladder(query)
    if not ladder:
        return None

    tried: list[str] = []
    for step in ladder:
        tried.append(step)
        try:
            result = search_fn(step, corpus_types, pitaka_type, 1, page_size)
        except Exception:  # noqa: BLE001 - fallback không được phép làm hỏng request chính
            continue
        if result.get("results"):
            return {
                "usedQuery": step,
                "triedQueries": tried,
                "ladder": ladder,
                "result": result,
            }
    return None
