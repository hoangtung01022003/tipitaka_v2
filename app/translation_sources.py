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
- CẤP BÀI KINH (`minh_chau`, `indacanda_full`): các vị chia đoạn khác bản gốc nên một
  bản ghi phủ cả bài, kèm khoảng `sort_order`. `passage_id` chỉ là đoạn neo, KHÔNG phải
  đoạn duy nhất được phủ - tra theo `passage_id` thì mọi đoạn khác trong bài đều trượt.
  Đọc bằng `_fetch_whole_sutta_translation`.

MỘT DỊCH GIẢ Ở GIAO DIỆN ≠ MỘT NGUỒN TRONG DB
----------------------------------------------
Hai kiểu gắn ở trên là chuyện lưu trữ, không phải chuyện người đọc cần biết. `indacanda`
và `indacanda_full` là cùng một bản dịch ở hai hình dạng, và khi được phơi ra thành hai
option thì sinh đúng lỗi khách báo: hai dòng cùng tên dịch giả, một bấm được, một xám
"(chưa có dữ liệu)". `TRANSLATOR_SOURCES` gộp chúng lại thành MỘT dịch giả; hình dạng
nào đang được hiển thị thì nói bằng hậu tố nhãn (`SHAPE_SUFFIXES`), tính theo dữ liệu
thật của đúng bài kinh đó chứ không phải một chuỗi cố định.

Dịch giả nào chưa có dữ liệu ở BẤT KỲ hình dạng nào thì `source_options` mới đánh dấu
`available: False` và giao diện hiện "Hiện không có bản dịch chính thức nào". Thêm nguồn
mới cần hai bước: đăng ký hàm đọc đúng kiểu ở `HUMAN_TRANSLATION_FETCHERS`, và gắn nó
vào một dịch giả trong `TRANSLATOR_SOURCES`.
"""


from typing import Callable

from .db import fetch_all, fetch_one
from .i18n import normalize_language, t

AI_SOURCE = "ai"

# ── DỊCH GIẢ (giao diện) ↔ NGUỒN (DB) ────────────────────────────────────────
# Một dịch giả ở giao diện có thể ứng với NHIỀU nguồn trong DB. Hiện chỉ có một ca:
# `indacanda` và `indacanda_full` là CÙNG một bản dịch, lưu hai hình dạng vì khoá
# `(passage_id, source)` không cho một nguồn vừa giữ bản cấp đoạn vừa giữ bản cả bài.
#
# Trước đây hai nguồn ấy được phơi thẳng ra giao diện thành hai option, nên người đọc
# thấy hai dòng cùng tên một dịch giả - một bấm được, một xám "(chưa có dữ liệu)" -
# đúng lỗi khách báo. Đó là chi tiết lưu trữ nội bộ rò ra mặt người dùng: bài kinh nào
# chưa có bản trọn bài thì option kia trông như hỏng, dù bản trích đoạn vẫn đọc tốt.
#
# Ghép ở tầng này thì `indacanda_full` thành BẢN NÂNG CẤP của `indacanda`: có dữ liệu
# trọn bài thì hiện trọn bài, chưa có thì ghép từ các đoạn - một option duy nhất, luôn
# bấm được. Hai nguồn trong DB vẫn giữ nguyên, không xoá cái nào.
TRANSLATOR_SOURCES: dict[str, tuple[str, ...]] = {
    # Thứ tự trong tuple là thứ tự ƯU TIÊN khi cùng một đoạn có cả hai hình dạng.
    "indacanda": ("indacanda_full", "indacanda"),
    "minh_chau": ("minh_chau",),
    "sujato": ("sujato",),
    "brahmali": ("brahmali",),
}
SOURCE_TO_TRANSLATOR: dict[str, str] = {
    source_id: translator_id
    for translator_id, source_ids in TRANSLATOR_SOURCES.items()
    for source_id in source_ids
}

# Tên dịch giả, KHÔNG kèm hình dạng - hình dạng do `SHAPE_SUFFIXES` ghép vào lúc hiển
# thị, vì cùng một dịch giả có bài trọn vẹn có bài chỉ có trích đoạn.
TRANSLATOR_LABELS: dict[str, dict[str, str]] = {
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

# Khách yêu cầu nhãn phải nói rõ người đọc đang xem trọn bài hay chỉ một khúc, và áp
# cho MỌI bản dịch của người dịch - cả tiếng Anh lẫn tiếng Việt - chứ không riêng
# Indacanda. Riêng AI thì giữ nguyên lối chia theo đoạn nên không mang hậu tố nào.
WHOLE_SHAPE = "whole"
EXCERPT_SHAPE = "excerpt"
SHAPE_SUFFIXES: dict[str, dict[str, str]] = {
    WHOLE_SHAPE: {
        "vi": " (toàn bộ bài kinh)",
        "en": " (whole discourse)",
        "my": " (ဒေသနာတော် အပြည့်)",
    },
    EXCERPT_SHAPE: {
        "vi": " (trích đoạn ngắn trong bài kinh)",
        "en": " (short excerpt from the discourse)",
        "my": " (ဒေသနာတော်မှ အပိုဒ်တို)",
    },
}

# Các nguồn DB gắn theo CẢ BÀI KINH (có `document_id` + khoảng `sort_order`), đối lại
# với các nguồn gắn theo từng đoạn. Quyết định phải dùng hàm đọc nào - xem docstring
# đầu file và `HUMAN_TRANSLATION_FETCHERS`.
WHOLE_SUTTA_DB_SOURCES = frozenset({"minh_chau", "indacanda_full"})

# Ngôn ngữ mà mỗi bản dịch của người dịch được viết ra.
SOURCE_LANGUAGE = {
    "minh_chau": "vi",
    "indacanda": "vi",
    "sujato": "en",
    "brahmali": "en",
}

# Thứ tự hiển thị các dịch giả. AI không nằm trong danh sách này: khách chốt khối AI
# đứng RIÊNG và đứng TRƯỚC khối bản dịch chính thức (xem `results.html`).
TRANSLATOR_ORDER = ("indacanda", "minh_chau", "sujato", "brahmali")
# Giữ tên cũ cho phần còn lại của ứng dụng; AI vẫn xếp cuối trong danh sách gộp.
SOURCE_ORDER = TRANSLATOR_ORDER + (AI_SOURCE,)


def source_label(source_id: str, language: str, shape: str | None = None) -> str:
    """Nhãn hiển thị của một dịch giả, kèm hình dạng bản dịch nếu biết.

    `shape` là `WHOLE_SHAPE`/`EXCERPT_SHAPE`, do nơi gọi xác định từ dữ liệu thật của
    đúng bài kinh đó. Không truyền thì trả về tên trần - dùng cho dropdown chọn nguồn,
    nơi chưa biết bài kinh nào.
    """
    language = normalize_language(language)
    translator_id = SOURCE_TO_TRANSLATOR.get(source_id, source_id)
    labels = TRANSLATOR_LABELS.get(translator_id, {})
    label = labels.get(language) or labels.get("vi") or translator_id
    if shape and translator_id != AI_SOURCE:
        suffixes = SHAPE_SUFFIXES.get(shape, {})
        label += suffixes.get(language) or suffixes.get("vi") or ""
    return label


def normalize_source(value: str | None) -> str:
    candidate = str(value or "").strip().lower()
    # Trang đã mở sẵn trong trình duyệt và bookmark cũ còn gửi `indacanda_full`; ánh xạ
    # về đúng dịch giả thay vì rơi về AI, nếu không người đọc bấm nút cũ sẽ thấy bản
    # dịch AI hiện ra thay cho bản của dịch giả họ chọn.
    candidate = SOURCE_TO_TRANSLATOR.get(candidate, candidate)
    return candidate if candidate in TRANSLATOR_LABELS else AI_SOURCE


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
            "value": translator_id,
            "label": source_label(translator_id, language),
            # Một dịch giả còn dùng được khi BẤT KỲ hình dạng nào của họ có dữ liệu.
            # Xét từng nguồn DB riêng lẻ chính là thứ sinh ra option Indacanda thứ hai
            # luôn xám mà khách đã báo.
            "available": translator_id == AI_SOURCE
            or any(source_id in available for source_id in TRANSLATOR_SOURCES.get(translator_id, ())),
            "language": SOURCE_LANGUAGE.get(translator_id),
        }
        for translator_id in SOURCE_ORDER
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
                "translator": SOURCE_TO_TRANSLATOR.get(str(row["source"]), str(row["source"])),
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


def _join_with_gap_markers(
    chosen: list[tuple[int, dict]], total: int, language: str
) -> tuple[str, int]:
    """Ghép các đoạn dịch rời rạc, chèn mốc tại đúng chỗ bản dịch bị hụt.

    Bản cấp đoạn khớp 70-95% số đoạn, nên nối thẳng bằng `"\\n\\n".join` sẽ dán hai
    đoạn không liền nhau lại làm một - người đọc không thấy có gì bất thường và tưởng
    mình đang đọc mạch văn liên tục. Khách báo đúng triệu chứng đó: "bấm xem toàn bộ
    thì bản dịch bị thiếu so với bản Pali gốc".

    Con số phần trăm ở tiêu đề chỉ nói THIẾU BAO NHIÊU; mốc này nói THIẾU Ở ĐÂU. Các
    đoạn hụt liền nhau gộp thành một mốc để bản dịch phủ thưa không biến thành một
    rừng mốc chen giữa từng câu.
    """
    ordered = sorted(chosen, key=lambda pair: pair[0])
    pieces: list[str] = []
    missing = 0
    previous = -1
    for index, entry in ordered:
        gap = index - previous - 1
        if gap > 0:
            missing += gap
            pieces.append(t(language, "translation.gapPassages", count=gap))
        pieces.append(entry["text"])
        previous = index
    trailing = total - 1 - previous
    if trailing > 0:
        missing += trailing
        pieces.append(t(language, "translation.gapPassages", count=trailing))
    return "\n\n".join(pieces), missing


def official_translations_merged(
    passage_ids: list[str],
    language: str,
    passage_level_only: bool = False,
    whole_sutta_excerpt_chars: int | None = None,
    covers_whole_sutta: bool = False,
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

    # Gom theo DỊCH GIẢ chứ không theo nguồn DB, rồi trong mỗi dịch giả tách theo hình
    # dạng. Có bản trọn bài thì dùng bản trọn bài và bỏ hẳn bản cấp đoạn của cùng người
    # ấy - nếu không, một dịch giả sẽ hiện ra hai lần với cùng một nội dung.
    per_translator: dict[str, dict[str, list[tuple[int, dict]]]] = {}
    for index, passage_id in enumerate(passage_ids):
        for entry in grouped.get(str(passage_id), []):
            if passage_level_only and not entry["passageLevel"]:
                continue
            shape = EXCERPT_SHAPE if entry["passageLevel"] else WHOLE_SHAPE
            per_translator.setdefault(entry["translator"], {}).setdefault(shape, []).append(
                (index, entry)
            )

    items = []
    for translator_id, shapes in per_translator.items():
        shape = WHOLE_SHAPE if WHOLE_SHAPE in shapes else EXCERPT_SHAPE
        chosen = shapes[shape]
        covered = {index for index, _ in chosen}
        positions = [
            entry["position"] for _, entry in chosen if entry.get("position") is not None
        ]

        truncated = shifted = False
        missing_passages = 0
        if shape == WHOLE_SHAPE:
            # Một bản ghi phủ cả bài, nên chỉ in nguyên văn MỘT lần dù nhiều đoạn của
            # trang kết quả cùng rơi vào khoảng của nó.
            text = chosen[0][1]["text"]
            if whole_sutta_excerpt_chars:
                # Doan trich hien thi hay trai qua vai doan Pali; ngam cua so vao GIUA
                # khoang do de phu ca cum, thay vi bam vao mot doan roi lech sang hai ben.
                centre = (min(positions) + max(positions)) / 2 if positions else None
                text, truncated, shifted = _excerpt(text, whole_sutta_excerpt_chars, centre)
        else:
            text, missing_passages = _join_with_gap_markers(chosen, len(passage_ids), language)

        coverage_count = len(covered)
        coverage_total = len(passage_ids)
        coverage_percent = round(coverage_count * 100 / coverage_total) if coverage_total else 0

        # `shape` là hình dạng LƯU TRỮ; nhãn phải nói hình dạng NGƯỜI ĐỌC NHẬN ĐƯỢC.
        # Trang đọc truyền vào TOÀN BỘ đoạn của bài, nên thứ hiện ra ở đó đã là tất cả
        # những gì tồn tại của bản dịch ấy cho bài này - gọi nó là "trích đoạn ngắn" là
        # nói ngược với chữ đang nằm trên màn hình.
        #
        # KHÔNG đòi phủ đủ 100%. Bản dịch bóc từ PDF có chỗ không lấy ra được, nên buộc
        # phải tròn 100% thì gần như bài nào cũng bị dán nhãn "trích đoạn ngắn" dù đã
        # ghép được 90-99% - đúng cái mâu thuẫn khách chỉ ra. Phủ 50%, 70%, 99% vẫn là
        # trọn bài theo nghĩa "đã lấy tối đa những gì có".
        #
        # Nhãn không vì thế mà nói dối: ngay dưới nó `section.passageOfficialHint` in
        # "ghép trong toàn bài · N/M đoạn (Z%)", và `_join_with_gap_markers` cắm mốc
        # "[… thiếu N đoạn …]" đúng chỗ hụt. Nhãn nói PHẠM VI, hai thứ kia nói ĐỘ ĐẦY.
        #
        # Trang kết quả KHÔNG bật cờ này: ở đó `passage_ids` chỉ là mấy đoạn của trích
        # dẫn, nên "trích đoạn ngắn" mới là mô tả đúng.
        label_shape = WHOLE_SHAPE if covers_whole_sutta else shape

        items.append(
            {
                "source": translator_id,
                "label": source_label(translator_id, language, label_shape),
                "shape": shape,
                "labelShape": label_shape,
                "text": text,
                "wholeSutta": shape == WHOLE_SHAPE,
                "truncated": truncated,
                "shifted": shifted,
                # Số đoạn Pali đang hiển thị mà bản dịch này không phủ. Khách báo "bản
                # dịch bị thiếu so với bản Pali gốc" nên con số phải lộ ra, và chỗ thiếu
                # phải được đánh dấu ngay trong nội dung chứ không chỉ nói tổng quát.
                "missingPassages": missing_passages,
                "coverageCount": coverage_count,
                "coverageTotal": coverage_total,
                "coveragePercent": coverage_percent,
                "complete": coverage_count == coverage_total,
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
          select id as section_id, document_id, start_sort_order,
                 coalesce(end_sort_order, start_sort_order) as end_sort_order
          from sections where id = any(%s::uuid[])
        )
        select distinct t.section_id, h.source
        from target t
        join human_translations h
          on h.document_id = t.document_id
         and h.start_sort_order is not null
         and h.end_sort_order is not null
         and h.start_sort_order <= t.end_sort_order
         and h.end_sort_order >= t.start_sort_order
        union
        select distinct t.section_id, h.source
        from target t
        join passages p
          on p.document_id = t.document_id
         and p.sort_order between t.start_sort_order and t.end_sort_order
        join human_translations h on h.passage_id = p.id
        """,
        [ids],
    )
    order = {source_id: index for index, source_id in enumerate(SOURCE_ORDER)}
    # Gộp về dịch giả: nút "Xem toàn bộ bài kinh" phải bật khi BẤT KỲ hình dạng nào của
    # dịch giả ấy có dữ liệu cho bài này. Xét riêng từng nguồn DB chính là thứ làm nút
    # Indacanda thứ hai luôn xám "(chưa có dữ liệu)" bên cạnh nút Indacanda bấm được.
    shapes_by_section: dict[str, dict[str, set[str]]] = {}
    for row in rows:
        source_id = str(row["source"])
        translator_id = SOURCE_TO_TRANSLATOR.get(source_id, source_id)
        shape = WHOLE_SHAPE if source_id in WHOLE_SUTTA_DB_SOURCES else EXCERPT_SHAPE
        shapes_by_section.setdefault(str(row["section_id"]), {}).setdefault(
            translator_id, set()
        ).add(shape)

    grouped: dict[str, list[dict]] = {}
    for section_id, translators in shapes_by_section.items():
        items = []
        for translator_id, shapes in translators.items():
            shape = WHOLE_SHAPE if WHOLE_SHAPE in shapes else EXCERPT_SHAPE
            items.append(
                {
                    "source": translator_id,
                    "shape": shape,
                    "label": source_label(translator_id, language, shape),
                }
            )
        items.sort(key=lambda item: order.get(str(item["source"]), 99))
        grouped[section_id] = items
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
    # Cùng bản dịch, hai hình dạng, nên phải hai nguồn: khoá `(passage_id, source)` không
    # cho một nguồn vừa cấp đoạn vừa cả bài. Giữ cả hai vì mỗi cái mạnh một chỗ - cấp đoạn
    # cho trích dẫn đúng đoạn ở trang kết quả, cả bài cho người đọc hết một bài kinh.
    "indacanda_full": _fetch_whole_sutta_translation("indacanda_full"),
}


def resolve_human_translation(source_id: str, passage_id: str | None, text: str, language: str) -> dict | None:
    """Bản dịch của một DỊCH GIẢ cho đoạn này, thử lần lượt các hình dạng họ có.

    `TRANSLATOR_SOURCES` xếp bản trọn bài trước bản cấp đoạn, nên đoạn nào đã có bản
    trọn bài thì người đọc nhận được trọn bài; chưa có thì rơi xuống bản cấp đoạn thay
    vì báo "chưa có dữ liệu" như trước.
    """
    for db_source in TRANSLATOR_SOURCES.get(source_id, (source_id,)):
        fetcher = HUMAN_TRANSLATION_FETCHERS.get(db_source)
        if not fetcher:
            continue
        try:
            resolved = fetcher(passage_id, text, language)
        except Exception:  # noqa: BLE001 - nguồn phụ hỏng không được làm hỏng cả request
            continue
        if resolved:
            resolved["shape"] = (
                WHOLE_SHAPE if db_source in WHOLE_SUTTA_DB_SOURCES else EXCERPT_SHAPE
            )
            resolved["label"] = source_label(source_id, language, resolved["shape"])
            return resolved
    return None
