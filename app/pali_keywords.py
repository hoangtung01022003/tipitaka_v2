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

# Số thuật ngữ hiện ra cho khách, KHÔNG TÍNH cụm chính - khách yêu cầu "khoảng 5 kết quả
# (bao gồm cả key chính)", nên 4 + 1 cụm chính = 5.
MAX_TERMS = 4

# Số ứng viên tối đa đem đi kiểm tra sự tồn tại. `_existing_pali_terms` chạy một truy vấn
# cho mỗi từ (có `lru_cache`), nên phải chặn trần trước khi lọc - dư ra so với `MAX_TERMS`
# vì vài ứng viên đầu bảng có thể là từ AI bịa, không tồn tại trong kho.
MAX_TERM_CANDIDATES = 10

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


def _interleave(short_terms: list[str], long_terms: list[str]) -> list[str]:
    """Xen kẽ hai danh sách để kết quả có ĐỦ CẢ từ đơn ngắn lẫn cụm dài, theo đúng yêu
    cầu khách "đủ loại key ngắn dài tuỳ vào câu hỏi" - không phải danh sách toàn từ đơn
    (như trước) hay toàn cụm dài. Bắt đầu bằng một cụm dài vì `mainKeyword` hiển thị
    riêng đã "dùng" vị trí cụm dài đầu tiên, nên 4 gợi ý còn lại nên có phương án dài
    khác sớm chứ không dồn hết xuống cuối bảng.

    Nguồn nào cạn trước thì lấy tiếp nguồn còn lại, không dừng giữa chừng - một câu hỏi
    mà AI không ghép được cụm dài nào vẫn phải trả đủ số từ đơn, và ngược lại.
    """
    merged: list[str] = []
    seen: set[str] = set()
    i = j = 0
    want_long = True
    while i < len(short_terms) or j < len(long_terms):
        if want_long and j < len(long_terms):
            term = long_terms[j]
            j += 1
        elif i < len(short_terms):
            term = short_terms[i]
            i += 1
        elif j < len(long_terms):
            term = long_terms[j]
            j += 1
        else:
            break
        want_long = not want_long
        if term not in seen:
            seen.add(term)
            merged.append(term)
    return merged


def suggest_pali_keywords(query: str, language: str = DEFAULT_LANGUAGE) -> dict:
    """Trả cụm Pāḷi chính + tối đa `MAX_TERMS` gợi ý khác cho một câu hỏi - tổng khoảng 5
    kết quả (kể cả cụm chính), đủ cả từ đơn ngắn lẫn cụm dài chứ không chỉ một loại.

    Không ghi `search_logs`: đây không phải một lượt tìm kiếm, và lượt tìm thật sau đó
    (khi khách bấm "Tìm ngay" hoặc tự dán) mới là thứ đáng vào lịch sử.
    """
    query = str(query or "").strip()
    language = normalize_language(language)
    if not query:
        return {"ok": False, "query": "", "mainKeyword": None, "terms": []}

    clean_query = canonicalize_query(query) or query
    main_keyword = extract_search_keyword_with_ai(query, clean_query, language)
    expansion = expand_query_with_ai(query, clean_query, language) or {}
    local = analyze_query(query, ["all"])

    avoid = _normalized_set(
        [*(expansion.get("avoidPali") or []), *(local.get("avoidPali") or [])]
    )

    # Nguồn NGẮN: thuật ngữ đơn lẻ - thứ tự trong `_pick_terms` là độ tin cậy giảm dần:
    # thuật ngữ trọng tâm của AI > thuật ngữ của bảng khái niệm tự soạn > thuật ngữ mở
    # rộng. KHÔNG lấy từng chữ tách rời của `main_keyword` như trước nữa: giờ mainKeyword
    # đã ở NGUYÊN CỤM trong kết quả, tách rời nó ra làm từ đơn chỉ tạo thêm hàng trùng ý.
    short_pool = [
        term
        for term in _pick_terms(
            [
                *(expansion.get("paliExactTerms") or []),
                *(local.get("mustHavePali") or []),
                *(expansion.get("paliRelatedTerms") or []),
                *(local.get("shouldHavePali") or []),
                *(expansion.get("paliHints") or []),
            ],
            avoid,
        )
        if " " not in term
    ]

    # Nguồn DÀI: cụm nhiều từ AI đã ghép sẵn (`expandedQueries`) - cùng loại với cụm
    # chính nhưng là phương án khác, hợp khi cụm chính không khớp đúng cách chia trong
    # kinh. Lọc `" " not in term` phía trên / `" " in term` ở đây để hai nguồn không lẫn
    # vào nhau: một mục một-từ lọt vào `expandedQueries` (AI vẫn hay trả lẫn) thì bỏ qua
    # ở đây - nó đã có cơ hội xuất hiện qua `short_pool` rồi.
    long_pool = [
        term for term in _pick_terms(expansion.get("expandedQueries") or [], avoid) if " " in term
    ]

    candidates = _interleave(short_pool, long_pool)[:MAX_TERM_CANDIDATES]
    terms = _existing_pali_terms(candidates)[:MAX_TERMS]

    return {
        "ok": bool(main_keyword or terms),
        "query": query,
        "mainKeyword": main_keyword or None,
        "terms": terms,
    }
