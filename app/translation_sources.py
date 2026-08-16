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

`indacanda` và `indacanda_full` là CÙNG một bản dịch ở hai hình dạng, và khách chốt giữ
cả hai làm HAI OPTION riêng với nhãn cố định - một để đọc trích đoạn dưới kết quả tìm
kiếm, một để đọc hết bài.

Cái phải sửa không phải là gộp chúng lại, mà là option "toàn bộ bài kinh" đừng đòi phải
có sẵn dòng `indacanda_full`: nguồn đó mới phủ 251 bài, nên nếu chỉ đọc đúng nó thì 90%
số bài hiện "(chưa có dữ liệu)" ngay cạnh một option trích đoạn đang chạy tốt. Xem
`WHOLE_FALLBACK_SOURCE`: chưa có bản trọn bộ thì ghép từ chính các dòng cấp đoạn của
cùng dịch giả, ghép được bao nhiêu tính bấy nhiêu.

Nguồn nào chưa có dữ liệu thì `source_options` tự đánh dấu `available: False` và giao
diện hiện "Hiện không có bản dịch chính thức nào". Thêm nguồn mới chỉ cần đăng ký hàm
đọc đúng kiểu ở `HUMAN_TRANSLATION_FETCHERS`.
"""


from typing import Callable

from .db import fetch_all, fetch_one
from .i18n import normalize_language, t

AI_SOURCE = "ai"

# Nhãn của từng nguồn theo ngôn ngữ giao diện, đúng như khách liệt kê trong yêu cầu.
# CỐ ĐỊNH theo nguồn, không tính động theo dữ liệu từng bài: khách chốt giữ nguyên các
# chuỗi này. `indacanda` và `indacanda_full` là hai OPTION riêng và phải luôn hiện đủ cả
# hai - một cho trích đoạn dưới kết quả tìm kiếm, một để đọc hết bài.
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
        "vi": "Bản dịch của Tỳ Khưu Indacanda (trích đoạn ngắn)",
        "en": "Translation by Bhikkhu Indacanda (short excerpt)",
        "my": "ဘိက္ခု ဣန္ဒစန္ဒ ၏ ဘာသာပြန် (အပိုဒ်တို)",
    },
    "sujato": {
        "vi": "Bản dịch Tiếng Anh của Bhikkhu Sujato",
        "en": "English translation by Bhikkhu Sujato",
        "my": "ဘိက္ခု သုဇာတ ၏ အင်္ဂလိပ်ဘာသာပြန်",
    },
    # Cùng người dịch với `indacanda` nhưng gắn theo CẢ BÀI KINH, nên nhãn phải nói rõ
    # để người đọc biết chọn cái nào - xem chú thích ở `HUMAN_TRANSLATION_FETCHERS`.
    "indacanda_full": {
        "vi": "Bản dịch của Tỳ Khưu Indacanda (toàn bộ bài kinh)",
        "en": "Translation by Bhikkhu Indacanda (whole discourse)",
        "my": "ဘိက္ခု ဣန္ဒစန္ဒ ၏ ဘာသာပြန် (ဒေသနာတော် အပြည့်)",
    },
    # Nhãn có nói rõ "Tạng Luật": đây là nguồn duy nhất chỉ phủ một tạng, mà Ngài
    # Sujato lại không dịch Luật nên không có nguồn nào khác lấp vào chỗ đó.
    "brahmali": {
        "vi": "Bản dịch Tạng Luật Tiếng Anh của Bhikkhu Brahmali",
        "en": "English translation of the Vinaya by Bhikkhu Brahmali",
        "my": "ဘိက္ခု ဗြဟ္မာလိ ၏ ဝိနည်း အင်္ဂလိပ်ဘာသာပြန်",
    },
}

# Các nguồn DB gắn theo CẢ BÀI KINH (có `document_id` + khoảng `sort_order`), đối lại
# với các nguồn gắn theo từng đoạn. Quyết định phải dùng hàm đọc nào - xem docstring
# đầu file và `HUMAN_TRANSLATION_FETCHERS`.
WHOLE_SUTTA_DB_SOURCES = frozenset({"minh_chau", "indacanda_full"})

# ── "Toàn bộ bài kinh" KHÔNG đòi phải có sẵn bản trọn bộ ──────────────────────
# `indacanda_full` chỉ phủ 251 bài, trong khi `indacanda` cấp đoạn phủ 30.590 dòng. Nếu
# option "toàn bộ bài kinh" chỉ đọc đúng nguồn `indacanda_full` thì 90% số bài hiện
# "(chưa có dữ liệu)" ngay bên cạnh một option trích đoạn đang chạy tốt - mâu thuẫn mà
# khách chỉ ra: đã có trích đoạn thì phải đọc hết bài được chứ.
#
# Nên option này lấy theo thứ tự: có bản trọn bộ thì dùng, không thì GHÉP từ các dòng
# cấp đoạn của cùng dịch giả trên toàn bài. Ghép được 40% hay 90% đều tính là "toàn bộ
# bài kinh" - bản bóc từ PDF gần như không bao giờ tròn 100%, đòi đủ mới cho đọc thì
# chẳng khác gì không có. Mức phủ không bị giấu: giao diện in kèm tỉ lệ "N/M đoạn (Z%)".
# (Mốc "[… thiếu N đoạn …]" chen giữa nội dung đã bị bỏ theo yêu cầu của khách - lý do
# và số đo nằm ở docstring của `_join_with_gap_markers`.)
WHOLE_FALLBACK_SOURCE = {"indacanda_full": "indacanda"}

# Ngôn ngữ mà mỗi bản dịch của người dịch được viết ra.
SOURCE_LANGUAGE = {
    "minh_chau": "vi",
    "indacanda": "vi",
    "indacanda_full": "vi",
    "sujato": "en",
    "brahmali": "en",
}

# Thứ tự hiển thị do khách chốt: `indacanda` rồi `indacanda_full` đứng cạnh nhau vì cùng
# một dịch giả khác hình dạng, sau đó Minh Châu, Sujato, Brahmali. AI đứng riêng ở khối
# trên (xem `results.html`) nên xếp cuối danh sách này.
SOURCE_ORDER = ("indacanda", "indacanda_full", "minh_chau", "sujato", "brahmali", AI_SOURCE)


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
            # `indacanda_full` còn dùng được khi nguồn cấp đoạn của cùng dịch giả có dữ
            # liệu, vì lúc đó nó ghép ra bản trọn bài - xem `WHOLE_FALLBACK_SOURCE`.
            "available": source_id == AI_SOURCE
            or source_id in available
            or WHOLE_FALLBACK_SOURCE.get(source_id) in available,
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
               h.match_method,
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
                # Cách dựng ra bản dịch này. Với dòng CẢ BÀI thì đây là tín hiệu chất
                # lượng DUY NHẤT người đọc có: `coveragePercent` của dòng cả bài luôn ra
                # 100% (mọi đoạn trong khoảng đều khớp vào đúng dòng đó) nên nó không nói
                # lên điều gì - đo tay thì `6. Pāsādikasuttaṃ` kiểu `whole_unit` chỉ có
                # 71% nội dung mà vẫn báo 100%.
                "matchMethod": row["match_method"],
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


def _join_with_gap_markers(chosen: list[tuple[int, dict]], total: int) -> tuple[str, int]:
    """Ghép các đoạn dịch rời rạc và ĐẾM chỗ hụt, nhưng KHÔNG chen chữ vào mạch văn.

    Trước đây hàm này cắm mốc "[… N đoạn chưa ghép được bản dịch …]" ngay giữa nội
    dung. Khách chốt bỏ mốc đó vì nó cắt nát trải nghiệm đọc. Quyết định này đã được
    đo trước khi làm, và số đo nói rõ cái được lẫn cái mất - đừng đảo lại mà không
    đọc đoạn dưới đây.

    ĐƯỢC: mốc cũ NÓI QUÁ. Dịch giả gộp nhiều đoạn Pali vào một đoạn văn tiếng Việt,
    importer gắn cả cụm vào một `passage_id`, nên một phần chữ của đoạn "hụt" thật ra
    đã nằm sẵn ở đoạn kế bên. Đo trên nguồn `indacanda`: dòng nằm giữa mạch có tỉ lệ
    Việt/Pali trung vị 1.35, còn dòng giáp chỗ hụt là 3.16 (trước) và 3.19 (sau) -
    phình 2.34 lần, đối xứng cả hai bên. Nói "hụt N đoạn" ở những chỗ đó là sai.

    MẤT: phần hút vào KHÔNG bù đủ. Lấy nền riêng của từng tập (trung vị Việt/Pali của
    các dòng giữa mạch, để khử việc văn kệ dịch ra dài hơn văn xuôi) rồi so tổng lượng
    chữ thực có với lượng chữ đáng lẽ phải có: cả 14 tập chỉ đạt 44%, và 11/14 tập
    dưới 60% (s0103m 44%, vin02m2 42%, s0519m 21%). Nghĩa là quá nửa lượng chữ thiếu
    thật, không nằm ở đâu cả.

    Nên `missing` vẫn phải đếm và vẫn phải trả về. Nó chạy tiếp vào `missingPassages`
    và `coveragePercent`, và tiêu đề in "N/M đoạn (Z%)" - đó là thứ DUY NHẤT còn lại
    ngăn option "toàn bộ bài kinh" tự nhận là đã trọn bài. Bỏ nốt phần đếm này thì
    trang khẳng định một điều sai, chứ không còn là chuyện trải nghiệm đọc nữa.

    Cách chữa đúng nghĩa không nằm ở đây mà ở dữ liệu: bài nào có dòng `indacanda_full`
    thật (cắt liền mạch theo tiêu đề in trong PDF) thì không đi qua hàm này và không
    hụt gì cả - xem `apply_indacanda_full_preview.py`.
    """
    ordered = sorted(chosen, key=lambda pair: pair[0])
    pieces: list[str] = []
    missing = 0
    previous = -1
    for index, entry in ordered:
        missing += index - previous - 1
        pieces.append(entry["text"])
        previous = index
    missing += total - 1 - previous
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

    per_source: dict[str, list[tuple[int, dict]]] = {}
    for index, passage_id in enumerate(passage_ids):
        for entry in grouped.get(str(passage_id), []):
            if passage_level_only and not entry["passageLevel"]:
                continue
            per_source.setdefault(str(entry["source"]), []).append((index, entry))

    # Option "toàn bộ bài kinh" khi chưa có bản trọn bộ: dựng từ chính các dòng cấp đoạn
    # của cùng dịch giả. Chỉ làm ở TRANG ĐỌC (`covers_whole_sutta`), vì chỉ nơi đó mới
    # truyền vào đủ mọi đoạn của bài - ghép ở trang kết quả thì chỉ ra đúng bằng option
    # trích đoạn bên cạnh, thành hai dòng y hệt nhau.
    if covers_whole_sutta:
        for whole_source, passage_source in WHOLE_FALLBACK_SOURCE.items():
            if whole_source not in per_source and passage_source in per_source:
                per_source[whole_source] = per_source[passage_source]

    items = []
    for source_id, chosen in per_source.items():
        is_whole_row = not chosen[0][1]["passageLevel"]
        covered = {index for index, _ in chosen}
        positions = [
            entry["position"] for _, entry in chosen if entry.get("position") is not None
        ]

        truncated = shifted = False
        missing_passages = 0
        if is_whole_row:
            # Một bản ghi phủ cả bài, nên chỉ in nguyên văn MỘT lần dù nhiều đoạn của
            # trang kết quả cùng rơi vào khoảng của nó.
            text = chosen[0][1]["text"]
            if whole_sutta_excerpt_chars:
                # Doan trich hien thi hay trai qua vai doan Pali; ngam cua so vao GIUA
                # khoang do de phu ca cum, thay vi bam vao mot doan roi lech sang hai ben.
                centre = (min(positions) + max(positions)) / 2 if positions else None
                text, truncated, shifted = _excerpt(text, whole_sutta_excerpt_chars, centre)
        else:
            text, missing_passages = _join_with_gap_markers(chosen, len(passage_ids))

        coverage_count = len(covered)
        coverage_total = len(passage_ids)
        coverage_percent = round(coverage_count * 100 / coverage_total) if coverage_total else 0
        items.append(
            {
                "source": source_id,
                "label": source_label(source_id, language),
                "text": text,
                # Cờ này nói bản ghi có phải kiểu CẢ BÀI hay không, dùng để chọn lời chú
                # thích và cách cắt. Bản `indacanda_full` ghép từ đoạn thì vẫn là cấp
                # đoạn, nên nó hiện kèm tỉ lệ phủ - đúng thứ người đọc cần biết.
                "wholeSutta": is_whole_row,
                # Chỉ có nghĩa với dòng cả bài, và ở đó nó là thứ duy nhất phân biệt bản
                # cắt đúng theo tiêu đề in (`pdf_heading_boundary`) với bản ghép tự động
                # (`whole_unit`) - loại sau đo được là hụt tới 29% hoặc lẫn văn bài khác.
                "matchMethod": chosen[0][1].get("matchMethod") if is_whole_row else None,
                "truncated": truncated,
                "shifted": shifted,
                # Số đoạn Pali đang hiển thị mà bản dịch này không phủ. Khách báo "bản
                # dịch bị thiếu so với bản Pali gốc" nên con số phải lộ ra. Từ khi bỏ mốc
                # chen giữa nội dung, đây là tín hiệu DUY NHẤT còn nói lên chỗ hụt - giữ
                # nó và giữ luôn tỉ lệ phủ ở tiêu đề.
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
    by_section: dict[str, set[str]] = {}
    for row in rows:
        by_section.setdefault(str(row["section_id"]), set()).add(str(row["source"]))

    grouped: dict[str, list[dict]] = {}
    for section_id, sources in by_section.items():
        # Nút "toàn bộ bài kinh" của Indacanda phải bật cả khi bài này mới chỉ có dòng
        # cấp đoạn: bấm vào thì trang đọc ghép chúng lại thành bản trọn bài. Không có
        # dòng này thì 90% số bài hiện nút xám "(chưa có dữ liệu)" ngay cạnh một nút
        # trích đoạn đang chạy tốt - đúng mâu thuẫn khách chỉ ra.
        for whole_source, passage_source in WHOLE_FALLBACK_SOURCE.items():
            if passage_source in sources:
                sources.add(whole_source)
        items = [
            {"source": source_id, "label": source_label(source_id, language)}
            for source_id in sources
        ]
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
    """Bản dịch của một nguồn cho đúng đoạn này.

    `indacanda_full` chưa có bản trọn bộ cho bài này thì rơi xuống dòng cấp đoạn của
    cùng dịch giả, thay vì báo "chưa có dữ liệu" - cùng lý lẽ với `WHOLE_FALLBACK_SOURCE`.
    """
    for db_source in (source_id, WHOLE_FALLBACK_SOURCE.get(source_id)):
        fetcher = HUMAN_TRANSLATION_FETCHERS.get(db_source) if db_source else None
        if not fetcher:
            continue
        try:
            resolved = fetcher(passage_id, text, language)
        except Exception:  # noqa: BLE001 - nguồn phụ hỏng không được làm hỏng cả request
            continue
        if resolved:
            return resolved
    return None
