"""Gợi ý từ khoá Pāḷi cho câu hỏi khó - tính năng ĐỘC LẬP với luồng tìm kiếm.

Khách vẫn tìm bằng tiếng Việt như cũ. Khi câu hỏi khó quá và cách tìm thường không ra,
khách bấm nút này để lấy cụm Pāḷi rồi tự dán vào ô tìm kiếm - đúng việc khách đang phải
làm tay bằng Gemini web ("hỏi Gemini lấy từ khoá rồi tìm lại").

**Để riêng một module chứ không nhét vào `search_engine`.** Đây không phải một bước của
pipeline xếp hạng: sửa phần gợi ý này không được phép làm xê dịch một kết quả tìm kiếm
nào. Chiều ngược lại cũng vậy - `search_engine` không import module này.

**Không thêm prompt AI mới.** Dùng lại đúng hai hàm mà pipeline đã dùng, và cố ý dựng lại
`clean_query` GIỐNG HỆT `search_engine._rank_candidates` (`canonicalize_query(query) or
query`) để dùng chung khoá đệm `query_ai_cache`. Nhờ vậy câu nào khách đã tìm một lần thì
bấm nút này trả lời gần như tức thì, không tốn thêm lượt gọi Gemini nào - và ngược lại,
bấm nút này trước rồi mới tìm thì lượt tìm cũng đã có sẵn đệm.

Hai hàm đó phục vụ hai vai khác nhau và ở đây cũng hiện ra thành hai phần khác nhau:
- `extract_search_keyword_with_ai` -> MỘT cụm liền mạch, chia đúng văn phạm, còn nguyên
  dấu Pāḷi. Đây là cụm để dán thẳng vào ô tìm kiếm, nên hiện thành "từ khoá chính".
- `expand_query_with_ai` -> NHIỀU thuật ngữ rời (đã bỏ dấu vì `_normalize_list`), hợp
  làm danh sách thuật ngữ liên quan để khách thử từng cái.
"""

from .glossary import analyze_query, canonicalize_query
from .i18n import DEFAULT_LANGUAGE, normalize_language
from .normalize import normalize_pali
from .query_expander import expand_query_with_ai, extract_search_keyword_with_ai

# Dùng lại bộ lọc của pipeline tìm kiếm chứ KHÔNG chép lại (một bản sao sẽ trôi khỏi bản
# gốc). AI hay bịa ra từ Pāḷi không tồn tại - đo thật: nó trả `pariyācana`, từ này có mặt
# trong ĐÚNG 0 đoạn của toàn kho. Trong tìm kiếm thì từ ma chỉ làm lệch xếp hạng; ở đây
# hậu quả nặng hơn vì khách COPY đúng từ đó đem đi tìm rồi nhận về 0 kết quả và mất tin
# vào chức năng.
from .search_engine import _existing_pali_terms

# Số thuật ngữ hiện ra cho khách. Nhiều hơn thì không ai thử hết, mà mỗi từ lại là một
# lượt kiểm tra trong kho.
MAX_TERMS = 12

# Số ứng viên tối đa đem đi kiểm tra sự tồn tại. `_existing_pali_terms` chạy một truy vấn
# cho mỗi từ (có `lru_cache`), nên phải chặn trần trước khi lọc.
MAX_TERM_CANDIDATES = 24

# Bỏ hư từ Pāḷi. Cụm AI trả về hay kèm `vā`, `ca`, `hi`, `pi`, `na` - có mặt khắp nơi
# trong kho nên qua được cửa kiểm tra tồn tại, nhưng làm từ khoá thì vô dụng.
MIN_TERM_LENGTH = 4


def _normalized_set(values: list[str]) -> set[str]:
    return {term for term in (normalize_pali(str(value)) for value in values) if term}


def _pick_terms(values: list[str], avoid: set[str]) -> list[str]:
    """Chuẩn hoá, bỏ trùng, bỏ từ quá ngắn và bỏ từ nằm trong danh sách nên tránh.

    Giữ NGUYÊN thứ tự đầu vào vì thứ tự đó chính là độ tin cậy giảm dần - xem chỗ gọi.
    """
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        term = normalize_pali(str(value))
        if not term or len(term) < MIN_TERM_LENGTH:
            continue
        if term in seen or term in avoid:
            continue
        seen.add(term)
        output.append(term)
    return output


def suggest_pali_keywords(query: str, language: str = DEFAULT_LANGUAGE) -> dict:
    """Trả cụm Pāḷi chính cho một câu hỏi.

    Không ghi `search_logs`: đây không phải một lượt tìm kiếm, và lượt tìm thật sau đó
    (khi khách bấm "Tìm ngay" hoặc tự dán) mới là thứ đáng vào lịch sử.

    TẠM THỜI chỉ trả `mainKeyword`, theo yêu cầu khách "chỉ cần lấy 1 kết quả thôi, Cụm
    từ khoá chính là được". Phần dựng danh sách "thuật ngữ liên quan" (gọi thêm
    `expand_query_with_ai`, lọc qua `_existing_pali_terms`) COMMENT lại chứ không xoá -
    xem khối bên dưới - để bật lại chỉ bằng cách bỏ comment khi khách đổi ý. Giao diện
    ([index.html] `renderKeywordPanel`) đã tự bỏ qua khối "Thuật ngữ liên quan" khi
    `terms` rỗng, nên không cần sửa gì ở template/JS.
    """
    query = str(query or "").strip()
    language = normalize_language(language)
    if not query:
        return {"ok": False, "query": "", "mainKeyword": None, "terms": []}

    clean_query = canonicalize_query(query) or query
    main_keyword = extract_search_keyword_with_ai(query, clean_query, language)
    terms: list[str] = []

    # expansion = expand_query_with_ai(query, clean_query, language) or {}
    # local = analyze_query(query, ["all"])
    #
    # avoid = _normalized_set(
    #     [*(expansion.get("avoidPali") or []), *(local.get("avoidPali") or [])]
    # )
    #
    # # Thứ tự = độ tin cậy giảm dần, và `_pick_terms` giữ nguyên thứ tự này:
    # # từng chữ của cụm chính (sát câu hỏi nhất - cụm ghép có thể không tồn tại nguyên văn
    # # trong kinh nhưng từng chữ thì có) > thuật ngữ trọng tâm của AI > thuật ngữ của bảng
    # # khái niệm tự soạn > thuật ngữ mở rộng.
    # candidates = _pick_terms(
    #     [
    #         *str(main_keyword or "").split(),
    #         *(expansion.get("paliExactTerms") or []),
    #         *(local.get("mustHavePali") or []),
    #         *(expansion.get("paliRelatedTerms") or []),
    #         *(local.get("shouldHavePali") or []),
    #         *(expansion.get("paliHints") or []),
    #     ],
    #     avoid,
    # )[:MAX_TERM_CANDIDATES]
    #
    # terms = _existing_pali_terms(candidates)[:MAX_TERMS]

    return {
        "ok": bool(main_keyword or terms),
        "query": query,
        "mainKeyword": main_keyword or None,
        "terms": terms,
    }
