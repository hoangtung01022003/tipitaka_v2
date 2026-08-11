"""Các nguồn bản dịch hiển thị cho người đọc chọn.

Theo `feat_new/Yêu cầu.docx`: luôn hiện đủ mọi option bản dịch dù đang ở ngôn ngữ nào,
nhãn của option đổi theo ngôn ngữ đang chọn, và khi một đoạn chưa có bản dịch của
nguồn đó thì hiện "Hiện không có bản dịch chính thức nào".

TRẠNG THÁI DỮ LIỆU
------------------
- `ai`        : chạy được (Gemini).
- `sujato`    : nạp từ bilara-data của SuttaCentral bằng `import_sujato.py`.
- `indacanda` : nạp từ tamtangpaliviet.net bằng `import_indacanda.py` (hiện mới có
                Kinh Tập; các tập còn lại đã cấu hình sẵn nhưng chưa chạy).
- `minh_chau` : nạp từ SuttaCentral, gắn theo CẢ BÀI KINH.
- `brahmali`  : nạp từ bilara-data bằng `import_brahmali.py`. CHỈ có Tạng Luật, và đây
                là bản tiếng Anh DUY NHẤT của Tạng Luật - Ngài Sujato không dịch Luật,
                nên ngoài Luật ra nguồn này không trả về gì là đúng, không phải lỗi.

HAI KIỂU GẮN BẢN DỊCH - quyết định phải dùng hàm đọc nào
---------------------------------------------------------
- CẤP ĐOẠN (`sujato`, `indacanda`, `brahmali`): mỗi bản ghi ứng đúng một `passage_id`, ba cột
  `document_id/start_sort_order/end_sort_order` bỏ trống. Đọc bằng
  `_fetch_from_human_translations`.
- CẤP BÀI KINH (`minh_chau`): các vị chia đoạn khác bản gốc nên một bản ghi phủ cả
  bài, kèm khoảng `sort_order`. `passage_id` chỉ là đoạn neo, KHÔNG phải đoạn duy
  nhất được phủ - tra theo `passage_id` thì mọi đoạn khác trong bài đều trượt.
  Đọc bằng `_fetch_whole_sutta_translation`.

Nguồn nào chưa có dữ liệu thì `source_options` tự đánh dấu `available: False` và
giao diện hiện "Hiện không có bản dịch chính thức nào". Thêm nguồn mới chỉ cần
đăng ký hàm đọc đúng kiểu ở `HUMAN_TRANSLATION_FETCHERS`.
"""


from typing import Callable

from .db import fetch_all, fetch_one
from .i18n import normalize_language, t

AI_SOURCE = "ai"

# Nhãn của từng nguồn theo ngôn ngữ giao diện, đúng như khách liệt kê trong yêu cầu.
SOURCE_LABELS: dict[str, dict[str, str]] = {
    AI_SOURCE: {
        "vi": "Công cụ hỗ trợ ngôn ngữ bằng trí tuệ nhân tạo AI",
        "en": "AI-powered language support tools",
        "my": "AI ဖြင့် ဘာသာစကား အထောက်အကူ ကိရိယာ",
    },
    "minh_chau": {
        "vi": "Bản dịch của Hòa Thượng Thích Minh Châu",
        "en": "Translation by Bhikkhu Thich Minh Chau",
        "my": "အရှင် သစ်ချ် မင်းချောင် ၏ ဘာသာပြန်",
    },
    "indacanda": {
        "vi": "Bản dịch của Tỳ Khưu Indacanda",
        "en": "Translation by Bhikkhu Indacanda",
        "my": "ဘိက္ခု ဣန္ဒစန္ဒ ၏ ဘာသာပြန်",
    },
    "sujato": {
        "vi": "Bản dịch Tiếng Anh của Bhikkhu Sujato",
        "en": "English translation by Bhikkhu Sujato",
        "my": "ဘိက္ခု သုဇာတ ၏ အင်္ဂလိပ်ဘာသာပြန်",
    },
    # Nhãn có nói rõ "Tạng Luật": đây là nguồn duy nhất chỉ phủ một tạng, mà Ngài
    # Sujato lại không dịch Luật nên không có nguồn nào khác lấp vào chỗ đó.
    "brahmali": {
        "vi": "Bản dịch Tạng Luật Tiếng Anh của Bhikkhu Brahmali",
        "en": "English translation of the Vinaya by Bhikkhu Brahmali",
        "my": "ဘိက္ခု ဗြဟ္မာလိ ၏ ဝိနည်း အင်္ဂလိပ်ဘာသာပြန်",
    },
}

# Ngôn ngữ mà mỗi bản dịch của người dịch được viết ra.
SOURCE_LANGUAGE = {
    "minh_chau": "vi",
    "indacanda": "vi",
    "sujato": "en",
    "brahmali": "en",
}

SOURCE_ORDER = (AI_SOURCE, "minh_chau", "indacanda", "sujato", "brahmali")


def source_label(source_id: str, language: str) -> str:
    labels = SOURCE_LABELS.get(source_id, {})
    language = normalize_language(language)
    return labels.get(language) or labels.get("vi") or source_id


def normalize_source(value: str | None) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if candidate in SOURCE_LABELS else AI_SOURCE


def sources_with_data() -> frozenset[str]:
    """Các nguồn người dịch đã thực sự có dữ liệu trong DB.

    Cố tình KHÔNG cache: trước đây nhớ kết quả suốt đời tiến trình nên nạp xong dữ liệu
    mà không khởi động lại thì nguồn mới vẫn hiện "chưa có dữ liệu" - rất dễ tưởng hỏng.
    Bảng có index trên `source` nên truy vấn này rẻ.
    """
    try:
        rows = fetch_all("select distinct source from human_translations")
    except Exception:  # noqa: BLE001 - chưa chạy migration thì coi như chưa có nguồn nào
        return frozenset()
    return frozenset(str(row["source"]) for row in rows)


def source_options(language: str) -> list[dict[str, object]]:
    """Danh sách option cho dropdown "Bản dịch".

    Luôn trả về đủ mọi nguồn theo yêu cầu của khách; nguồn chưa có dữ liệu được
    đánh dấu `available: False` để giao diện hiện lời nhắc thay vì ẩn option đi.
    """
    language = normalize_language(language)
    available = sources_with_data()
    return [
        {
            "value": source_id,
            "label": source_label(source_id, language),
            "available": source_id == AI_SOURCE or source_id in available,
            "language": SOURCE_LANGUAGE.get(source_id),
        }
        for source_id in SOURCE_ORDER
    ]


def official_translations_for(passage_ids: list[str], language: str) -> dict[str, list[dict]]:
    """Các bản dịch của dịch giả có sẵn cho từng đoạn, tra một lượt cho cả trang kết quả.

    Có hai kiểu bản ghi và phải lấy được cả hai:
    - gắn theo TỪNG ĐOẠN (`passage_id`) - bản Sujato, vì khớp được tới từng câu;
    - gắn theo CẢ BÀI KINH (khoảng `sort_order`) - Minh Châu và các bản dịch chia đoạn
      khác bản gốc. Kiểu này phải tra theo khoảng, nếu chỉ tra theo passage_id thì kết
      quả rơi vào giữa bài sẽ không thấy bản dịch nào.
    """
    if not passage_ids:
        return {}
    rows = fetch_all(
        """
        with target as (
          select id, document_id, sort_order
          from passages where id = any(%s::uuid[])
        )
        select t.id as passage_id, h.source, h.language, h.translated_text, h.source_ref,
               (h.start_sort_order is null) as passage_level,
               t.sort_order, h.start_sort_order, h.end_sort_order
        from target t
        join human_translations h
          on h.passage_id = t.id
          or (h.document_id = t.document_id
              and t.sort_order between h.start_sort_order and h.end_sort_order)
        """,
        [list(passage_ids)],
    )
    order = {source_id: index for index, source_id in enumerate(SOURCE_ORDER)}
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(str(row["passage_id"]), []).append(
            {
                "source": row["source"],
                "label": source_label(str(row["source"]), language),
                "language": row["language"],
                "text": row["translated_text"],
                "sourceRef": row["source_ref"],
                "passageLevel": bool(row["passage_level"]),
                # Doan nay nam o dau trong bai kinh (0 = dau bai, 1 = cuoi bai). Chi co
                # nghia voi ban cap bai kinh; dung de truot cua so trich doan toi dung cho.
                "position": (
                    None
                    if row["passage_level"] or row["end_sort_order"] == row["start_sort_order"]
                    else (row["sort_order"] - row["start_sort_order"])
                    / (row["end_sort_order"] - row["start_sort_order"])
                ),
            }
        )
    for items in grouped.values():
        items.sort(key=lambda item: order.get(str(item["source"]), 99))
    return grouped


def _snap(text: str, index: int, forward: bool) -> int:
    """Đẩy vị trí cắt về ranh giới đoạn/câu gần nhất, tối đa 300 ký tự."""
    if index <= 0:
        return 0
    if index >= len(text):
        return len(text)
    window = 300
    if forward:
        for separator in ("\n\n", "\n", ". "):
            found = text.find(separator, index, index + window)
            if found != -1:
                return found + len(separator)
        return index
    for separator in ("\n\n", "\n", ". "):
        found = text.rfind(separator, max(0, index - window), index)
        if found != -1:
            return found + len(separator)
    return index


def _excerpt(text: str, limit: int, position: float | None = None) -> tuple[str, bool, bool]:
    """Lấy một cửa sổ `limit` ký tự, TRƯỢT TỚI vị trí tương đối của đoạn đang xem.

    Cắt "N ký tự đầu" là sai chỗ về mặt cấu trúc: đo trên 22.679 đoạn thì đoạn Pali đang
    hiện nằm ở trung vị 50% chiều dài bài kinh (phân vị 25 là 22%, phân vị 75 là 78%),
    trong khi cửa sổ tính từ đầu bài chỉ phủ được 16,5% đầu tiên - trúng 18,3%. Kéo dài
    gấp đôi cũng chỉ lên 29,7%, vì cửa sổ vẫn neo sai đầu.

    Ở đây `position` (0 = đầu bài, 1 = cuối bài) dời cửa sổ tới đúng khúc đó. Giả định:
    bản dịch chạy song song đều với bản gốc - đã kiểm chứng đúng trên SN 55.7 (đoạn Pali
    ở 27%, phần Việt tương ứng cũng ở 27%), nhưng CHƯA đo được sai số trên diện rộng vì
    không có dữ liệu ghép cấp đoạn để đối chiếu. Nên đây là "gần đúng", không phải "đúng",
    và giao diện phải nói rõ như vậy.

    Trả về `(đoạn trích, có bị cắt hay không, có trượt vị trí hay không)`.
    """
    if len(text) <= limit:
        return text, False, False
    if position is None:
        end = _snap(text, limit, forward=False)
        return text[:end].rstrip(), True, False

    centre = int(max(0.0, min(1.0, position)) * len(text))
    start = max(0, centre - limit // 2)
    start = min(start, len(text) - limit)
    start = _snap(text, start, forward=True)
    end = _snap(text, min(len(text), start + limit), forward=False)
    excerpt = text[start:end].strip()
    if not excerpt:
        # `_snap` lui ve ranh gioi cau co the an het mot cua so qua hep. Tra ve khoi rong
        # kem truncated=True thi giao dien in ra mot o ban dich trong - hong am tham. Tha
        # cat giua cau con hon.
        return text[start : start + limit].strip(), True, start > 0
    return excerpt, True, start > 0


def official_translations_merged(
    passage_ids: list[str],
    language: str,
    passage_level_only: bool = False,
    whole_sutta_excerpt_chars: int | None = None,
) -> list[dict]:
    """Bản dịch cho một NHÓM đoạn liền nhau, ghép lại theo đúng thứ tự đã truyền vào.

    Đoạn trích hiển thị thường đã được mở rộng ngữ cảnh, gộp vài đoạn Pali liền kề.
    Nếu chỉ lấy bản dịch của đoạn neo thì phần dịch không phủ hết phần Pali đang hiện,
    nhìn vào tưởng là ghép lệch.

    `passage_level_only=True` chỉ lấy bản dịch gắn theo TỪNG ĐOẠN. Khách muốn thấy đủ cả
    ba dịch giả ngay ở danh sách kết quả nên trang kết quả KHÔNG bật cờ này nữa; bản cấp
    bài kinh vẫn chỉ được lấy MỘT lần cho cả đoạn trích và mang cờ `wholeSutta`.

    `whole_sutta_excerpt_chars` chỉ cắt bản CẤP BÀI KINH xuống còn đoạn đầu, dùng cho
    trang kết quả: bản Minh Châu dài trung bình 3.400 và dài nhất ~147.000 ký tự, in
    trọn ra thì lấp mất phần Pali lẫn các bản dịch khác - mà ngay dưới thẻ đã có sẵn nút
    "Xem toàn bộ bài kinh" mở trang đọc. Cắt ở tầng này chứ không ở template để HTML
    khỏi phải cõng phần chữ không ai nhìn. Trang đọc gọi hàm này KHÔNG kèm tham số nên
    vẫn nhận đủ nguyên văn.
    """
    grouped = official_translations_for(passage_ids, language)
    merged: dict[str, dict] = {}
    seen_whole_sutta: set[str] = set()
    for passage_id in passage_ids:
        for entry in grouped.get(str(passage_id), []):
            if passage_level_only and not entry["passageLevel"]:
                continue
            if not entry["passageLevel"]:
                # Ban dich ca bai kinh: chi lay MOT lan du doan trich trai qua nhieu doan.
                if entry["source"] in seen_whole_sutta:
                    continue
                seen_whole_sutta.add(entry["source"])
            bucket = merged.setdefault(
                entry["source"],
                {
                    "label": entry["label"],
                    "parts": [],
                    "wholeSutta": not entry["passageLevel"],
                    "positions": [],
                },
            )
            bucket["parts"].append(entry["text"])
            if entry.get("position") is not None:
                bucket["positions"].append(entry["position"])

    items = []
    for source, value in merged.items():
        text = "\n\n".join(value["parts"])
        truncated = shifted = False
        if value["wholeSutta"] and whole_sutta_excerpt_chars:
            # Doan trich hien thi hay trai qua vai doan Pali; ngam cua so vao GIUA khoang
            # do de phu ca cum, thay vi bam vao mot doan roi lech sang hai ben.
            spots = value["positions"]
            centre = (min(spots) + max(spots)) / 2 if spots else None
            text, truncated, shifted = _excerpt(text, whole_sutta_excerpt_chars, centre)
        items.append(
            {
                "source": source,
                "label": value["label"],
                "text": text,
                "wholeSutta": value["wholeSutta"],
                "truncated": truncated,
                "shifted": shifted,
            }
        )

    order = {source_id: index for index, source_id in enumerate(SOURCE_ORDER)}
    return sorted(items, key=lambda item: order.get(str(item["source"]), 99))


def sources_for_sections(section_ids: list[str], language: str) -> dict[str, list[dict]]:
    """Những dịch giả có bản dịch cho từng mục (bài kinh), tra một lượt cho cả trang.

    Dùng để chỉ hiện nút của dịch giả THẬT SỰ có bản dịch cho bài kinh đó, thay vì
    hiện đủ nút rồi bấm vào mới biết là trống.
    """
    ids = [item for item in section_ids if item]
    if not ids:
        return {}
    rows = fetch_all(
        """
        with target as (
          select distinct section_id, document_id, sort_order
          from passages where section_id = any(%s::uuid[])
        )
        select distinct t.section_id, h.source
        from target t
        join human_translations h
          on h.document_id = t.document_id
             and t.sort_order between h.start_sort_order and h.end_sort_order
        union
        select distinct p.section_id, h.source
        from human_translations h
        join passages p on p.id = h.passage_id
        where p.section_id = any(%s::uuid[])
        """,
        [ids, ids],
    )
    order = {source_id: index for index, source_id in enumerate(SOURCE_ORDER)}
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(str(row["section_id"]), []).append(
            {"source": row["source"], "label": source_label(str(row["source"]), language)}
        )
    for items in grouped.values():
        items.sort(key=lambda item: order.get(str(item["source"]), 99))
    return grouped


def unavailable_translation(language: str) -> dict:
    return {
        "vi": None,
        "text": None,
        "fromCache": False,
        "available": False,
        "error": t(language, "translation.noOfficial"),
    }


def _fetch_from_human_translations(source_id: str):
    """Hàm đọc dùng chung cho mọi nguồn đã nạp vào bảng `human_translations`.

    Chỉ tra được khi biết `passage_id`. Với đoạn văn bản tự do (ví dụ đoạn trích đã
    ghép thêm ngữ cảnh) thì không có mã đoạn để tra, nên trả None và giao diện sẽ
    báo chưa có bản dịch chính thức - thà vậy còn hơn gán nhầm bản dịch của đoạn khác.
    """

    def fetch(passage_id: str | None, text: str, language: str) -> dict | None:
        del text, language
        if not passage_id:
            return None
        row = fetch_one(
            """
            select h.translated_text, h.language, h.source_ref
            from human_translations h
            where h.passage_id = %s and h.source = %s
            """,
            [passage_id, source_id],
        )
        if not row:
            return None
        return {
            "vi": row["translated_text"],
            "text": row["translated_text"],
            "language": row["language"],
            "sourceRef": row["source_ref"],
            "fromCache": True,
            "available": True,
        }

    return fetch


def _fetch_whole_sutta_translation(source_id: str):
    """Hàm đọc cho nguồn gắn theo CẢ BÀI KINH (Minh Châu và các bản chia đoạn khác gốc).

    Không tra theo `passage_id` được: bản ghi chỉ neo vào một đoạn nhưng phủ cả bài,
    nên mọi đoạn còn lại trong bài sẽ không thấy bản dịch nào. Phải tìm bản ghi có
    khoảng `sort_order` bao trùm đoạn đang xem.

    Trả về nguyên văn cả bài kinh - có bản dài hàng trăm nghìn ký tự - nên kèm cờ
    `wholeSutta` để giao diện thu gọn lại thay vì đổ thẳng vào thẻ kết quả.
    """

    def fetch(passage_id: str | None, text: str, language: str) -> dict | None:
        del text, language
        if not passage_id:
            return None
        row = fetch_one(
            """
            select h.translated_text, h.language, h.source_ref
            from passages p
            join human_translations h
              on h.document_id = p.document_id
             and p.sort_order between h.start_sort_order and h.end_sort_order
            where p.id = %s and h.source = %s
            order by h.end_sort_order - h.start_sort_order
            limit 1
            """,
            [passage_id, source_id],
        )
        if not row:
            return None
        return {
            "vi": row["translated_text"],
            "text": row["translated_text"],
            "language": row["language"],
            "sourceRef": row["source_ref"],
            "fromCache": True,
            "available": True,
            "wholeSutta": True,
        }

    return fetch


# Nguồn nào đã có importer thì đăng ký ở đây, đúng kiểu gắn của nguồn đó (xem docstring
# đầu file). Thêm nguồn mới chỉ cần thêm một dòng.
HUMAN_TRANSLATION_FETCHERS: dict[str, Callable[[str | None, str, str], dict | None]] = {
    "sujato": _fetch_from_human_translations("sujato"),
    "indacanda": _fetch_from_human_translations("indacanda"),
    "brahmali": _fetch_from_human_translations("brahmali"),
    "minh_chau": _fetch_whole_sutta_translation("minh_chau"),
}


def resolve_human_translation(source_id: str, passage_id: str | None, text: str, language: str) -> dict | None:
    fetcher = HUMAN_TRANSLATION_FETCHERS.get(source_id)
    if not fetcher:
        return None
    try:
        return fetcher(passage_id, text, language)
    except Exception:  # noqa: BLE001 - nguồn phụ hỏng không được làm hỏng cả request
        return None
