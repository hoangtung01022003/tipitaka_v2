"""Tách thử bản Indacanda trọn đơn vị đọc từ từng PDF, không ghi cơ sở dữ liệu.

Mỗi PDF có cấu hình riêng nhưng dùng chung hợp đồng đầu ra: một manifest JSON và
một tệp TXT cho từng đơn vị. Chỉ mục có ``status=PASS`` mới đủ điều kiện xem xét
ở bước nạp DB sau này; chương trình này cố ý không có câu lệnh INSERT/UPDATE.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
import statistics
import sys
from typing import Iterable

from pypdf import PdfReader

from app.db import fetch_all
from app.normalize import normalize_pali, strip_vietnamese
from app.text_artifacts import clean_unicode_artifacts, unicode_artifacts
from import_indacanda import VOLUMES, download, is_vietnamese, mend_spacing, section_stem


sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

SUPPORTED_VOLUMES = ("sn", "dn1", "dn2", "dn3", "pts2", "pr", "pc1", "pc2", "kn1", "mv1", "mv2", "cv1", "cv2", "thag", "vvpv", "ja1", "ja2", "ja3", "bvcp", "mil", "par1", "par2", "ap1", "ap2", "ap3", "nidd1", "nidd2", "pts1", "net", "pet")
OUTPUT_ROOT = Path(__file__).resolve().parent / "indacanda_full_preview"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class Unit:
    section_id: str
    document_id: str
    title: str
    level: int
    start_sort_order: int
    end_sort_order: int
    source_path: list[str]
    # Phần lớn đơn vị có một section_id duy nhất. Riêng cv1 phải chia các khoảng
    # không chồng lấn theo đúng chủ sở hữu trực tiếp của từng passage; section cha
    # Kammakkhandhaka xuất hiện ở vài khoảng rời nhau nên cần khóa riêng cho mỗi khoảng.
    unit_key: str | None = None
    # Riêng cv2 dùng phần mở đầu của passage đầu tiên để tìm đúng số đoạn in trong
    # PDF. Số đoạn XML và số đoạn trong sách không cùng hệ nên không thể ghép bằng số.
    pali_anchor: str | None = None


@dataclass(frozen=True)
class HeadingHit:
    page: int
    line: str
    score: float


@dataclass(frozen=True)
class Pc2UnitStart:
    """Điểm bắt đầu một đơn vị của riêng `ttpv_03_Pc_II.pdf`."""

    pali_hit: HeadingHit
    vietnamese_page: int
    vietnamese_offset: int
    vietnamese_line: str


PC2_FIRST_BODY_VI_PAGE = 44
PC2_LAST_BODY_VI_PAGE = 424
PC2_APPENDIX_FIRST_PAGE = 425


@dataclass(frozen=True)
class Cv1UnitStart:
    """Điểm cắt song song Pāli/Việt của riêng `ttpv_06_Cv_I.pdf`."""

    pali_hit: HeadingHit
    vietnamese_page: int
    vietnamese_offset: int
    vietnamese_line: str


# `ttpv_06_Cv_I.pdf` chứa bốn Khandhaka đầu của vin02m3. Phần thân bắt đầu ở
# cặp trang 44/45, kết thúc ở cặp 476/477; trang 478 đã là trang ngăn trước phụ chú.
CV1_FIRST_BODY_PALI_PAGE = 44
CV1_LAST_BODY_PALI_PAGE = 476
CV1_APPENDIX_FIRST_PAGE = 478
CV1_BODY_LAST_SORT_ORDER = 973
CV1_EXPECTED_READER_SEGMENTS = 119

# Sáu tiêu đề in là tiểu mục nằm BÊN TRONG reader segment hiện tại, không phải điểm
# bắt đầu của `passages.section_id` kế tiếp. Khóa bằng cả trang và stem để PDF đổi nội
# dung hay dời trang sẽ làm extractor dừng, thay vì âm thầm ghép lệch toàn phần sau đó.
CV1_INTERNAL_PRINTED_HEADINGS = {
    (104, "patippassambhanam"),
    (126, "patippassambhanam"),
    (148, "patippassambhanam"),
    (194, "tassuddanam"),
    (236, "tassuddanam"),
    (372, "tassuddanam"),
}

# Hai reader segment bắt đầu bằng đoạn đánh số ngay sau một tiểu mục trước đó, không
# có nhan đề in riêng. Trang Pāli/Việt vẫn song song và cùng mở bằng "1.", nên cắt ở
# chính dòng Việt thay vì nhập đoạn ấy vào section lá đứng trước.
CV1_NUMBERED_BOUNDARY_PAGES = {
    240: (160, 161),
    272: (188, 189),
}

CV2_FIRST_BODY_PALI_PAGE = 40
CV2_LAST_BODY_PALI_PAGE = 636
CV2_BODY_END_EXCLUSIVE = 638
CV2_FIRST_SORT_ORDER = 974
CV2_LAST_SORT_ORDER = 2462
CV2_EXPECTED_READER_SEGMENTS = 73
CV2_MIN_ANCHOR_SCORE = 0.50

# Năm tập dưới đây phải theo chủ sở hữu passage sâu nhất, không theo Khandhaka/
# chương cha. Mỗi cấu hình khóa cả số đoạn và toàn bộ khoảng sort_order: nếu DB
# thay cấu trúc, extractor dừng thay vì âm thầm nạp một lớp cha quá rộng.
DEEPEST_ALIGNED_SPECS = {
    "pr": {
        "expected_count": 70,
        "first_sort_order": 0,
        "last_sort_order": 2202,
        "first_pali_page": 48,
        "body_end_exclusive": 688,
        "minimum_score": 0.50,
    },
    "mv1": {
        "expected_count": 161,
        "first_sort_order": 0,
        "last_sort_order": 1226,
        "first_pali_page": 44,
        "body_end_exclusive": 560,
        "minimum_score": 0.40,
    },
    "mv2": {
        "expected_count": 121,
        "first_sort_order": 1227,
        "last_sort_order": 2128,
        "first_pali_page": 42,
        # Trang 418 lặp lại trang Việt 417 và trang 419 đã là ngăn phụ chú.
        "body_end_exclusive": 418,
        "minimum_score": 0.50,
    },
    "mil": {
        "expected_count": 248,
        "first_sort_order": 0,
        "last_sort_order": 1922,
        "first_pali_page": 45,
        "body_end_exclusive": 753,
        "minimum_score": 0.49,
        # DB đặt Nigamanaṃ ở sort 0-15 nhưng sách in đoạn ấy ở cuối tập.
        "rotate_at_sort_order": 16,
    },
    "net": {
        "expected_count": 43,
        "first_sort_order": 0,
        "last_sort_order": 1434,
        "first_pali_page": 40,
        "body_end_exclusive": 328,
        # Câu kết sort 0 được in ở cuối trang 326/327.
        "rotate_at_sort_order": 1,
    },
    # PTS I chỉ cần hạ riêng Diṭṭhikathā xuống đúng section con. Chín chương
    # song ngữ còn lại đã có range cấp chương đúng và không được thay trong đợt này.
    # 14 run gồm phần dẫn trực tiếp 590-606 và 13 section con liên tục 607-708.
    "pts1": {
        "expected_count": 14,
        "first_sort_order": 590,
        "last_sort_order": 708,
        "first_pali_page": 290,
        "body_end_exclusive": 348,
        "minimum_score": 0.45,
        "replace_range_only": True,
    },
    # PTS II có 20 tiêu đề Kathā in song ngữ nhưng 48 run passage sâu nhất.
    # Nạp theo 20 Kathā khiến trang đọc một vāra/niddesa con nhận cả chương cha.
    # Tất cả 48 biên dưới đây đã được đối chiếu trên cặp trang Pāli/Việt thật.
    "pts2": {
        "expected_count": 48,
        "first_sort_order": 1167,
        "last_sort_order": 1759,
        "first_pali_page": 36,
        # Trang 303 là cuối phần Việt; trang 304 trắng, 305 bắt đầu phụ chú.
        "body_end_exclusive": 304,
        "minimum_score": 0.50,
    },
}

# Các section không có một đoạn đánh số đủ mạnh để neo tự động. Tất cả mốc này
# đã được đối chiếu trên đúng cặp trang Pāli/Việt; marker được so sau khi bỏ dấu
# và khoảng trắng PDF. occurrence là lần xuất hiện (1-based) trên trang Việt.
DEEPEST_FIXED_STARTS: dict[tuple[str, int], tuple[int, int, str, int]] = {
    # Pārājika: chín khối kệ tóm lược Vinītavatthu không đánh số.
    **{
        ("pr", sort_order): (pali_page, pali_page + 1, "CHUYỆN DẪN GIẢI", 1)
        for sort_order, pali_page in (
            (113, 126), (249, 172), (417, 220), (663, 282), (881, 328),
            (1047, 362), (1104, 372), (1144, 382), (1281, 418),
        )
    },
    # Mahāvagga I: đoạn Tassuddānaṃ của từng chương.
    **{
        ("mv1", sort_order): (pali_page, pali_page + 1, "TÓM LƯỢC CHƯƠNG NÀY", 1)
        for sort_order, pali_page in (
            (561, 286), (826, 384), (968, 434), (1140, 514), (1208, 558),
        )
    },
    # Milindapañha: phần mở đầu, các tiểu mục không đánh số và đoạn kết.
    ("mil", 16): (45, 46, "Kính lễ đức Thế Tôn", 1),
    ("mil", 441): (193, 194, "16. Vị trưởng lão đã nói rằng", 1),
    ("mil", 443): (195, 196, "VIỆC HỎI VÀ TRẢ LỜI", 1),
    ("mil", 449): (199, 200, "PHẦN MỞ ĐẦU CÁC CÂU HỎI ĐỐI CHỌI", 1),
    ("mil", 466): (201, 202, "4. Thưa ngài Nāgasena, có tám hạng người", 1),
    ("mil", 470): (203, 204, "6. Thưa ngài Nāgasena, chín hạng người", 1),
    ("mil", 476): (205, 206, "8. Thưa ngài Nāgasena, tánh giác tiến triển", 1),
    ("mil", 481): (205, 206, "9. Thưa ngài Nāgasena, phần lãnh thổ này", 1),
    ("mil", 483): (207, 208, "Tâu đại vương, mười đức tính cư sĩ", 1),
    ("mil", 1382): (615, 616, "2. CÂU HỎI VỀ PHÁP TỪ KHƯỚC", 1),
    ("mil", 1422): (639, 640, "CÂU HỎI GIẢNG VỀ CÁC VÍ DỤ", 1),
    ("mil", 1713): (693, 694, "10. CÂU HỎI VỀ TÍNH CHẤT CỦA VỊ CHUYỂN LUÂN", 1),
    ("mil", 1831): (725, 726, "1. CÂU HỎI VỀ TÍNH CHẤT CỦA LOÀI NHỆN", 1),
    ("mil", 1837): (727, 728, "3. CÂU HỎI VỀ TÍNH CHẤT CỦA LOÀI RÙA", 1),
    ("mil", 0): (749, 750, "ĐOẠN KẾT", 1),
    # Nettippakaraṇa: các phần mở đầu ngắn không có tiêu đề Pāli đủ dài.
    ("net", 1): (40, 41, "PHẦN TỔNG HỢP", 1),
    ("net", 11): (42, 43, "1. PHẦN TÓM LƯỢC", 1),
    # Cụm đầu bị pypdf tách “phầ”/“n ấy” thành hai dòng; stem ngắn vẫn duy
    # nhất theo occurrence và giữ đúng offset 317/934.
    ("net", 12): (42, 43, "Kệ tóm lược của phầ", 1),
    ("net", 19): (42, 43, "Kệ tóm lược của phầ", 2),
    ("net", 25): (44, 45, "Đây là tóm lược của phần ấy", 1),
    ("net", 31): (46, 47, "2. PHẦN DIỄN GIẢI", 1),
    ("net", 32): (46, 47, "1. Sự hứng thú", 1),
    ("net", 98): (50, 51, "23. Âm từ", 1),
    ("net", 681): (196, 197, "Nguồn Phát Khởi Phương Pháp", 1),
    ("net", 744): (220, 221, "Sự Hình Thành Giáo Pháp", 1),
    ("net", 0): (326, 327, "Cẩm Nang Học Phật được đầy đủ là chừng này", 1),
    # PTS I, Diṭṭhikathā: phần dẫn và hai tiêu đề có câu mở đầu quá khác DB nên
    # similarity thấp giả; cả ba mốc đều in rõ trên đúng cặp trang Pāli/Việt.
    ("pts1", 590): (290, 291, "II. GIẢNG VỀ KIẾN", 1),
    ("pts1", 675): (
        338, 339, "13. Kiến có liên hệ đến Luận Thuyết về Tự Ngã", 1
    ),
    ("pts1", 679): (
        340, 341, "15 - 16. Giải về Hữu Kiến & Phi Hữu Kiến", 1
    ),
    # PTS II không in số đoạn CST ở lề. Khóa từng run bằng trang Pāli và câu
    # Việt song song đã đối chiếu, thay vì suy đoán vị trí theo tỷ lệ ký tự.
    **{
        ("pts2", sort_order): (pali_page, pali_page + 1, marker, occurrence)
        for sort_order, pali_page, marker, occurrence in (
            (1167, 36, "I. GIẢNG VỀ SỰ KẾT HỢP CHUNG", 1),
            (1174, 38, "Tu tập minh sát có chỉ tịnh đi trước", 1),
            (1202, 52, "Tâm của vị tỳ khưu bị khuấy động bởi sự phóng dật", 1),
            (1218, 58, "II. GIẢNG VỀ CHÂN LÝ", 1),
            (1219, 58, "Khổ là chân lý theo ý nghĩa của thực thể", 1),
            (1240, 66, "Này các tỳ khưu, trước lúc Toàn Giác", 1),
            (1242, 68, "Tùy thuận vào sắc, lạc và tâm sanh lên", 1),
            (1257, 76, "III. GIẢNG VỀ GIÁC CHI", 1),
            (1264, 76, "Với ý nghĩa của nguồn gốc là các giác chi", 1),
            (1297, 96, "Có giác chi như vầy", 1),
            (1309, 102, "IV. GIẢNG VỀ TỪ ÁI", 1),
            (1314, 104, "Đối với tất cả chúng sanh, xua đi sự áp bức", 1),
            (
                1321,
                106,
                "Khi tác ý rằng Tất cả chúng sanh hãy là không thù nghịch",
                1,
            ),
            (1327, 108, "Thiết lập niệm rằng", 1),
            (1335, 110, "Nhận thức đúng đắn rằng", 1),
            (1360, 120, "V. GIẢNG VỀ LY THAM ÁI", 1),
            (1379, 130, "VI. GIẢNG VỀ SỰ PHÂN TÍCH", 1),
            (1413, 140, "Này các tỳ khưu, ta có được Pháp nhãn đã sanh khởi", 1),
            (1425, 142, "Này các tỳ khưu, ta có được Pháp nhãn đã sanh khởi", 1),
            (1437, 146, "Này các tỳ khưu, Bồ Tát Vipassī có được Pháp nhãn", 1),
            (1441, 146, "Pháp nhãn đã sanh khởi, trí đã sanh khởi", 1),
            (1444, 148, "Pháp nhãn đã sanh khởi, trí đã sanh khởi", 1),
            (1447, 148, "Pháp nhãn đã sanh khởi, trí đã sanh khởi", 2),
            (1450, 148, "Pháp nhãn đã sanh khởi", 3),
            (1453, 150, "Pháp nhãn đã sanh khởi", 1),
            (1457, 152, "VII. GIẢNG VỀ PHÁP LUÂN", 1),
            (1471, 158, "Này các tỳ khưu, ta có được Pháp nhãn đã sanh khởi", 1),
            (1477, 160, "Này các tỳ khưu, ta có được Pháp nhãn đã sanh khởi", 1),
            (1488, 164, "VIII. GIẢNG VỀ TỐI THƯỢNG Ở THẾ GIAN", 1),
            (1490, 168, "IX. GIẢNG VỀ LỰC", 1),
            (1530, 186, "X. GIẢNG VỀ KHÔNG TÁNH", 1),
            (1533, 186, "Không đối với không, không đối với các hành", 1),
            (1534, 188, "Cái gì là không đối với không", 1),
            (1561, 200, "I. GIẢNG VỀ TUỆ", 1),
            (1575, 208, "Đưa đến sự thành đạt về tuệ", 1),
            (1600, 226, "Có hai hạng người đạt được phân tích", 1),
            (1610, 230, "II. GIẢNG VỀ THẦN THÔNG", 1),
            (1616, 232, "Thần thông gì do chú nguyện", 1),
            (1638, 246, "III. GIẢNG VỀ SỰ LÃNH HỘI", 1),
            (1658, 252, "IV. GIẢNG VỀ SỰ VIỄN LY", 1),
            (1662, 254, "Đối với chánh kiến, có năm sự viễn ly", 1),
            (1674, 260, "Đối với tín quyền, có năm sự viễn ly", 1),
            (1677, 262, "V. GIẢNG VỀ HÀNH VI", 1),
            (1682, 264, "VI. GIẢNG VỀ PHÉP KỲ DIỆU", 1),
            (1696, 270, "VII. GIẢNG VỀ CÁC PHÁP ĐỨNG ĐẦU", 1),
            (1702, 274, "VIII. GIẢNG VỀ SỰ THIẾT LẬP NIỆM", 1),
            (1716, 280, "IX. GIẢNG VỀ MINH SÁT", 1),
            (1743, 296, "X. GIẢNG VỀ CÁC TIÊU ĐỀ", 1),
        )
    },
}


@dataclass(frozen=True)
class Cv2UnitStart:
    pali_hit: HeadingHit
    vietnamese_page: int
    vietnamese_offset: int
    vietnamese_line: str


@dataclass(frozen=True)
class Cv2PaliCandidate:
    page: int
    line_index: int
    printed_number: str
    line: str
    normalized_block: str
    printed_prefix: tuple[str, ...] = ()


@dataclass
class Preview:
    status: str
    reason: str
    title: str
    section_id: str
    document_id: str
    start_sort_order: int
    end_sort_order: int
    pali_start_page: int | None
    vietnamese_start_page: int | None
    boundary_page_exclusive: int | None
    boundary_source: str | None
    title_match_score: float | None
    title_line: str | None
    vietnamese_pages: list[int]
    small_text_pages: list[int]
    fallback_text_pages: list[int]
    text_audit_review_pages: list[int]
    paired_page_ratio: float
    text_characters: int
    text_sha256: str | None
    output_file: str | None
    first_text: str
    last_text: str


@dataclass
class PageTextAudit:
    page: int
    status: str
    selected_engine: str
    reason: str
    pypdf_characters: int
    pdfplumber_characters: int
    checked_words: int
    word_coverage: float
    small_words: int
    small_word_coverage: float
    minimum_font_size: float | None
    median_font_size: float | None
    image_count: int
    image_area_ratio: float
    unicode_artifacts: int
    missing_word_sample: list[str]


def heading_stem(value: str, volume: str) -> str:
    """Chuẩn hóa tiêu đề in; pts2 dùng số La Mã thay số Ả Rập trong DB."""
    if volume == "pts2":
        value = re.sub(r"^\s*[IVXLCDM]+\s*[\.\)]\s*", "", value, flags=re.I)
    stem = re.sub(r"[^a-z0-9]+", "", section_stem(value))
    # Số chú thích thường dính ngay sau tiêu đề khi pypdf bóc chữ (AJITASUTTAṂ1).
    stem = re.sub(r"\d+$", "", stem)
    aliases = {
        # Cùng đơn vị nhưng PDF/DB dùng hai nhan đề Pāli khác nhau.
        ("par1", "solasamahavaro"): "mahavibhango",
        ("par1", "antarapeyyalam"): "antarapeyyalo",
        ("par2", "khandhakapucchavaro"): "khandhakapuccha",
        ("net", "sangahavaro"): "samgahavaro",
        ("pet", "ariyasaccappakasana"): "ariyasaccappakasanapathamabhumi",
        ("pet", "sasanapatthanam"): "sasanapatthanadutiyabhumi",
        ("pet", "suttadhitthanam"): "suttadhitthanatatiyabhumi",
        ("pet", "suttavicayo"): "suttavicayacatutthabhumi",
        ("pet", "pancamabhumi"): "pancamabhumi",
        ("pet", "suttatthasamuccayo"): "suttatthasamuccayabhumi",
        ("par2", "ekuttarikanayo"): "ekuttarikam",
        ("par2", "uposathadipucchavissajjana"): "uposathadipuccha",
        ("par2", "codanakandam"): "codanakando",
        ("par2", "atthapattisamutthanam"): "samutthanam",
        ("mil", "mendakapanharambhakatha"): "mendakapanharambho",
        ("bvcp", "ratanacankamanakandam"): "ratanacankamanakando",
        ("bvcp", "sumedhapatthanakatha"): "sumedhakatha",
        ("sn", "dhammacariyasuttam"): "kapilasuttam",
        ("sn", "navasuttam"): "dhammanavasuttam",
        ("pts2", "sunnakatha"): "sunnatakatha",
        ("pts2", "mahapannakatha"): "pannakatha",
        # Dị bản tên cổ điển: sách in "11. KEVAḌḌHA SUTTAṂ", DB ghi "11. Kevaṭṭasuttaṃ".
        # Không dò được tên này thì mất HAI bài chứ không phải một - bài 10 Subha dùng
        # tiêu đề của bài 11 làm điểm kết thúc nên cũng rơi xuống REVIEW theo.
        ("dn1", "kevaddhasuttam"): "kevattasuttam",
        # `pr`: sách in đặt TIỀN TỐ VỊ TRÍ `PAṬHAMA`/`DUTIYA` lên tên gốc dùng chung cho
        # hai điều học liền nhau, còn CST/DB đặt tên trơn cho điều thứ nhất và `Dutiya-`
        # cho điều thứ nhì; hai chỗ sách in còn gọi hẳn tên khác. Không tự suy được bằng
        # khớp mờ, và khớp mờ ở đây KHÔNG PHẢI vô hại - nó gán tiêu đề của điều 11 cho
        # điều 10 rồi đẩy nội dung điều 10 vào bản dịch của điều 9 (xem chú thích ở
        # `EXACT_HEADING_SCORE`). Mỗi dòng dưới đây đã đối chiếu tay với trang PDF ghi kèm.
        #
        # Vì sao khớp mờ trượt chứ không phải vì ngưỡng quá chặt: tiêu đề ĐÚNG của điều 10
        # ở trang 494 đạt 0.8627 nhưng cách á quân chỉ 0.0479 (loạt tên `Paṭhama...
        # sikkhāpadaṃ` khác đầy trong tập), nên guard nhập nhằng loại nó; tiêu đề SAI ở
        # trang 502 đạt 0.8800 cách á quân 0.0723 nên được nhận. Nới ngưỡng chỉ làm ca sai
        # dễ vào hơn - phải khai tên, không phải nới guard.
        ("pr", "pathamasanghadiseso"): "sukkavissatthisikkhapadam",  # tr 302
        ("pr", "pathamadutthadosasikkhapadam"): "dutthadosasikkhapadam",  # tr 462
        ("pr", "pathamasanghabhedasikkhapadam"): "sanghabhedasikkhapadam",  # tr 494
        ("pr", "dutiyasanghabhedasikkhapadam"): "bhedanuvattakasikkhapadam",  # tr 502
        ("pr", "dutiyakathinasikkhapadam"): "udositasikkhapadam",  # tr 548
        ("pr", "pathamaupakkhatasikkhapadam"): "upakkhatasikkhapadam",  # tr 582
        ("nidd2", "pathamovaggo"): "pathamavaggo",
        ("nidd2", "dutiyovaggo"): "dutiyavaggo",
        ("nidd2", "tatiyovaggo"): "tatiyavaggo",
        ("nidd2", "catutthovaggo"): "catutthavaggo",
        ("nidd2", "parayanatthutigatha"): "parayananugiti",
    }
    if volume == "sn" and stem.endswith("manavapuccha"):
        # DB gọi các mục phẩm Pārāyana là “câu hỏi của ...”, sách in gọi là “kinh ...”.
        stem = stem[: -len("manavapuccha")] + "suttam"
    if volume == "nidd2" and stem.endswith("niddeso") and stem not in ("parayanavagganiddeso", "khaggavisanasuttaniddeso"):
        stem = stem[: -len("niddeso")]
    return aliases.get((volume, stem), stem)


def pc2_unit_key(unit: Unit) -> str | None:
    """Đổi đường dẫn DB thành số in `chương.phẩm.điều` của tập Pācittiya II."""
    chapter: str | None = None
    vagga: str | None = None
    for part in unit.source_path:
        match = re.match(r"^(\d+)\.\s+", str(part))
        if not match:
            continue
        if "(bhikkhunīvibhaṅgo)" in str(part).casefold():
            chapter = match.group(1)
        elif chapter == "4" and str(part).casefold().endswith("vaggo"):
            vagga = match.group(1)

    title_match = re.match(r"^([\d-]+)\.", unit.title)
    if not chapter or not title_match:
        return None
    local = title_match.group(1)
    if chapter == "4":
        if not vagga:
            return None
        # DB ghi `8-9-10`, sách gộp cùng nội dung và in rút gọn `8-10`.
        if local == "8-9-10":
            local = "8-10"
        return f"4.{vagga}.{local}"
    if chapter == "5" and local == "2":
        return "5.2-8"
    if chapter == "6":
        # Sách chỉ in đầy đủ điều sekhiya đầu và cuối; DB cũng gom thành hai đơn vị.
        return "6" if local == "1" else "6.75"
    if chapter == "7":
        return "7"
    return f"{chapter}.{local}"


def _is_unit(volume: str, row: dict) -> bool:
    level = int(row["level"])
    start = int(row["start_sort_order"])
    if volume in ("dn1", "dn2", "dn3"):
        # Cả ba tập Trường Bộ dùng chung một luật vì cấu trúc DB trùng khít - đã ĐẾM chứ
        # không suy: `s0103m` (dn3) có đúng 11 mục cấp 4, `s0101m` (dn1) có 13, và trong
        # cả hai thì 100% mục cấp 4 kết thúc bằng `suttaṃ`, còn cấp 5 (137 và 132 mục) là
        # tiểu mục bên trong bài. Với tập mới thì đếm lại, đừng mặc định nó cũng vậy:
        # `sn` cần cấp 5 kèm bảng alias, `pts2` còn phải chặn theo `sort_order`.
        return level == 4 and heading_stem(str(row["title"]), volume).endswith(
            ("sutta", "suttam")
        )
    if volume == "sn":
        # Kinh Tập có bốn phẩm dùng hậu tố sutta và phẩm cuối dùng māṇavapucchā.
        # Cấp 5 mới là đơn vị người đọc; chọn theo cấp thật trong DB, không ép hậu tố.
        stem = heading_stem(str(row["title"]), volume)
        return level == 5 and stem.endswith(
            ("sutta", "suttam", "gatha", "manavapuccha")
        )
    if volume in ("par1", "par2"):
        return level in (3, 4, 5)
    stem = heading_stem(str(row["title"]), volume)
    if volume in ("ap1", "ap2", "ap3"):
        return level == 5 and stem.endswith("apadanam")
    if volume in ("nidd1", "nidd2"):
        raw_stem = normalize_pali(str(row["title"]))
        if level in (4, 5) and raw_stem.endswith("niddeso"):
            if raw_stem in ("khaggavasanasuttaniddeso", "khaggavisanasuttaniddeso"):
                return False
            return True
        if volume == "nidd2" and level == 6 and stem.endswith("vaggo"):
            return True
        return False
    if volume == "pts1":
        return level == 5 and start < 1167 and heading_stem(
            str(row["title"]), volume
        ).endswith("katha")
    if volume == "net":
        return level == 5 or (level == 4 and stem != "patiniddesavaro")
    if volume == "pet":
        return level == 4 and stem.endswith(("bhumi", "vebhangiyam"))
    if volume == "kn1":
        file_name = row.get("file_name", "")
        stem = heading_stem(str(row["title"]), volume)
        if file_name == "s0501m.mul.xml":
            return level == 4 and (stem.endswith("suttam") or stem in ("saranattayam", "dasasikkhapadam", "dvattimsakaro", "kumarapanha"))
        if file_name == "s0502m.mul.xml":
            return level == 4 and stem.endswith("vaggo")
        if file_name == "s0503m.mul.xml":
            return level == 5 and stem.endswith("suttam")
        if file_name == "s0504m.mul.xml":
            return level == 6 and stem.endswith("suttam")
        return False
    if volume == "thag":
        file_name = row.get("file_name", "")
        stem = heading_stem(str(row["title"]), volume)
        if file_name == "s0508m.mul.xml":
            return level == 6 and stem.endswith("theragatha")
        if file_name == "s0509m.mul.xml":
            return level == 5 and stem.endswith("therigatha")
        return False
    if volume == "vvpv":
        file_name = row.get("file_name", "")
        stem = heading_stem(str(row["title"]), volume)
        if file_name == "s0506m.mul.xml":
            return level == 6 and stem.endswith("vimanavatthu")
        if file_name == "s0507m.mul.xml":
            return level == 5 and stem.endswith(("petavatthu", "petivatthu"))
        return False
    if volume in ("ja1", "ja2", "ja3"):
        stem = heading_stem(str(row["title"]), volume)
        return level == 6 and stem.endswith("jatakam")
    if volume == "bvcp":
        file_name = row.get("file_name", "")
        stem = heading_stem(str(row["title"]), volume)
        if file_name == "s0511m.mul.xml":
            return level == 4 and stem.endswith(("kandam", "kando", "katha", "buddhavamso"))
        if file_name == "s0512m.mul.xml":
            return level == 5 and stem.endswith("cariya")
        return False
    if volume == "mil":
        return level == 6
    if volume == "pts2":
        # PDF tập II bắt đầu từ Yuganaddhakathā (sort 1167); các Kathā trước thuộc tập I.
        return level == 5 and start >= 1167 and heading_stem(
            str(row["title"]), volume
        ).endswith("katha")
    if volume in ("mv1", "mv2"):
        # Đại Phẩm chia theo các Khandhaka (Chương).
        # Cấu trúc DB đặt Khandhaka ở cấp độ 3.
        return level == 3 and heading_stem(str(row["title"]), volume).endswith(
            ("khandhako", "khandhakam")
        )
    if volume == "pc2":
        # Tập Pācittiya II chỉ chứa Bhikkhunīvibhaṅga, nhưng dùng chung XML với tập I.
        # Lấy đúng section sâu nhất đang neo trực tiếp các passage: 125 đơn vị, gồm cả
        # các điều Pācittiya cấp 5 và ba phần kết. Luật cũ chỉ nhìn level 4 nên chọn nhầm
        # 96 điều tỳ-khưu của tập I và bỏ gần hết nội dung thật của PDF này.
        return bool(row.get("has_direct_passages")) and any(
            "(bhikkhunīvibhaṅgo)" in str(part).casefold()
            for part in (row.get("source_path") or [])
        )
    if volume in ("pr", "pc1"):
        # Đơn vị đọc KHÔNG đồng nhất một cấp như dn/sn - đo trực tiếp bằng
        # `_canonical_reader_section` (hàm quyết định "toàn bộ bài kinh" thật của trang
        # đọc, không phải suy diễn): 61/66 mục lá của vin01m leo đúng tier 1 (khớp tiêu
        # đề), và điểm đến LUÔN là cấp 4 mang một trong hai hậu tố:
        #   - 45 `sikkhāpadaṃ` (điều học) - phần lớn nội dung.
        #   - 4 `parajikaṃ` (chương) - chỉ khi đoạn nằm TRƯỚC điều học đầu tiên (chuyện
        #     khởi đầu chưa có số riêng), ví dụ `1. Paṭhamapārājikaṃ` phủ luôn phần mở đầu.
        # `vaggo` (3 mục) và mọi mục cấp 5 (`Vinītavatthu`...) KHÔNG bao giờ là điểm đến -
        # `_is_reader_unit_title` đòi tiêu đề bắt đầu bằng chữ số, mà các mục đó không có,
        # nên bị leo qua tự nhiên. Root `Vinayapiṭake` (phủ cả tài liệu) cũng tự loại vì
        # không mang hậu tố nào ở trên - không cần loại trừ riêng.
        return level == 4 and heading_stem(str(row["title"]), volume).endswith(
            ("sikkhapadam", "parajikam")
        )
    return False


def _direct_reader_segments(
    rows: list[dict],
    *,
    volume: str,
    expected_count: int,
    first_sort_order: int,
    last_sort_order: int,
) -> list[Unit]:
    """Gom passage thành các khoảng liên tục có cùng section sâu nhất."""
    segments: list[Unit] = []
    for row in rows:
        sort_order = int(row["sort_order"])
        section_id = str(row["section_id"])
        if (
            segments
            and segments[-1].section_id == section_id
            and segments[-1].end_sort_order + 1 == sort_order
        ):
            previous = segments[-1]
            segments[-1] = Unit(
                section_id=previous.section_id,
                document_id=previous.document_id,
                title=previous.title,
                level=previous.level,
                start_sort_order=previous.start_sort_order,
                end_sort_order=sort_order,
                source_path=previous.source_path,
                unit_key=(
                    f"{previous.section_id}:{previous.start_sort_order}:{sort_order}"
                ),
                pali_anchor=previous.pali_anchor,
            )
            continue
        segments.append(
            Unit(
                section_id=section_id,
                document_id=str(row["document_id"]),
                title=str(row["title"] or ""),
                level=int(row["level"]),
                start_sort_order=sort_order,
                end_sort_order=sort_order,
                source_path=list(row["source_path"] or []),
                unit_key=f"{section_id}:{sort_order}:{sort_order}",
                pali_anchor=str(row.get("pali_text") or "") or None,
            )
        )

    for index, unit in enumerate(segments):
        expected_start = (
            first_sort_order
            if index == 0
            else segments[index - 1].end_sort_order + 1
        )
        if unit.start_sort_order != expected_start:
            raise RuntimeError(
                f"{volume}: passage không liên tục tại sort_order "
                f"{expected_start}->{unit.start_sort_order}"
            )
    if (
        len(segments) != expected_count
        or not segments
        or segments[0].start_sort_order != first_sort_order
        or segments[-1].end_sort_order != last_sort_order
    ):
        raise RuntimeError(
            f"{volume}: cấu trúc DB không còn đúng {expected_count} reader segment "
            f"phủ {first_sort_order}-{last_sort_order}; "
            "không được tự động cắt PDF theo cấu trúc đã thay đổi"
        )
    return segments


def cv1_reader_segments(rows: list[dict]) -> list[Unit]:
    """119 khoảng cv1 không chồng lấn, kể cả các run rời của section cha."""
    return _direct_reader_segments(
        rows,
        volume="cv1",
        expected_count=CV1_EXPECTED_READER_SEGMENTS,
        first_sort_order=0,
        last_sort_order=CV1_BODY_LAST_SORT_ORDER,
    )


def cv2_reader_segments(rows: list[dict]) -> list[Unit]:
    """73 section sâu nhất phủ trọn phần thân Cullavagga II."""
    return _direct_reader_segments(
        rows,
        volume="cv2",
        expected_count=CV2_EXPECTED_READER_SEGMENTS,
        first_sort_order=CV2_FIRST_SORT_ORDER,
        last_sort_order=CV2_LAST_SORT_ORDER,
    )


def deepest_reader_segments(volume: str, rows: list[dict]) -> list[Unit]:
    """Các run trực tiếp sâu nhất, theo đúng thứ tự xuất hiện trong PDF."""
    spec = DEEPEST_ALIGNED_SPECS[volume]
    segments = _direct_reader_segments(
        rows,
        volume=volume,
        expected_count=int(spec["expected_count"]),
        first_sort_order=int(spec["first_sort_order"]),
        last_sort_order=int(spec["last_sort_order"]),
    )
    rotate_at = spec.get("rotate_at_sort_order")
    if rotate_at is None:
        return segments
    later = [unit for unit in segments if unit.start_sort_order >= int(rotate_at)]
    earlier = [unit for unit in segments if unit.start_sort_order < int(rotate_at)]
    return later + earlier


def _preview_unit_key(unit: Unit) -> str:
    return unit.unit_key or unit.section_id


def load_units(volume: str) -> list[Unit]:
    config = VOLUMES[volume]
    docs = fetch_all(
        "select id, file_name from documents where file_name = any(%s)",
        [config["docs"]],
    )
    doc_ids = [str(row["id"]) for row in docs]
    if not doc_ids:
        raise RuntimeError(f"Không tìm thấy tài liệu DB cho {volume}: {config['docs']}")
    if volume in ("cv1", "cv2") or volume in DEEPEST_ALIGNED_SPECS:
        if volume == "cv1":
            first_sort_order, last_sort_order = 0, CV1_BODY_LAST_SORT_ORDER
        elif volume == "cv2":
            first_sort_order, last_sort_order = (
                CV2_FIRST_SORT_ORDER,
                CV2_LAST_SORT_ORDER,
            )
        else:
            spec = DEEPEST_ALIGNED_SPECS[volume]
            first_sort_order = int(spec["first_sort_order"])
            last_sort_order = int(spec["last_sort_order"])
        passage_rows = fetch_all(
            """
            select p.sort_order, p.section_id, p.document_id,
                   p.pali_text, s.title, s.level, s.source_path
            from passages p
            join sections s on s.id = p.section_id
            where p.document_id = any(%s::uuid[])
              and p.sort_order between %s and %s
            order by p.sort_order
            """,
            [doc_ids, first_sort_order, last_sort_order],
        )
        if volume == "cv1":
            return cv1_reader_segments(passage_rows)
        if volume == "cv2":
            return cv2_reader_segments(passage_rows)
        return deepest_reader_segments(volume, passage_rows)
    rows = fetch_all(
        """
        select s.id, s.document_id, d.file_name, s.title, s.level, s.source_path,
               s.start_sort_order, s.end_sort_order,
               exists(select 1 from passages p where p.section_id = s.id)
                 as has_direct_passages
        from sections s
        join documents d on d.id = s.document_id
        where s.document_id = any(%s::uuid[])
        order by s.document_id, s.start_sort_order, s.level
        """,
        [doc_ids],
    )
    return [
        Unit(
            section_id=str(row["id"]),
            document_id=str(row["document_id"]),
            title=str(row["title"] or ""),
            level=int(row["level"]),
            start_sort_order=int(row["start_sort_order"]),
            end_sort_order=int(row["end_sort_order"]),
            source_path=list(row["source_path"] or []),
        )
        for row in rows
        if _is_unit(volume, row)
    ]


def _uppercase_ratio(value: str) -> float:
    letters = [char for char in value if char.isalpha()]
    if not letters:
        return 0.0
    return sum(char.isupper() for char in letters) / len(letters)


def _candidate_lines(text: str, volume: str) -> Iterable[str]:
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not 5 <= len(line) <= 110:
            continue
        if volume not in ("thag", "vvpv", "ja1", "ja2", "ja3", "bvcp", "mil", "par1", "par2", "ap1", "ap2", "ap3", "nidd1", "nidd2", "pts1", "net", "pet"):
            if _uppercase_ratio(line) < 0.55:
                continue
        elif volume == "nidd2" and "vaggo" in line.lower():
            if _uppercase_ratio(line) < 0.55:
                continue
        yield line


# Mục lục in lại đúng tiêu đề (chữ hoa, có số) của mọi đơn vị trước khi thân bài bắt đầu.
# Không loại phạm vi này thì mỗi tiêu đề in HAI lần - một ở mục lục, một ở thân bài -
# nên đếm "xuất hiện đúng N lần cho N đơn vị cùng tên" luôn sai và tiêu đề bị coi là
# "không duy nhất" dù bản thân thân bài hoàn toàn rõ ràng. Đo trực tiếp trên PDF: mục
# lục `pr` (Pārājikakaṇḍa) chiếm trang in 37-46 (1-based), thân bài bắt đầu trang 47.
HEADING_SEARCH_START_PAGE: dict[str, int] = {
    "pr": 47,
    "bvcp": 40,
    "thag": 40,
    "vvpv": 30,
    "ja1": 30,
    "ja2": 30,
    "ja3": 30,
    "mil": 10,
    "par1": 40,
    "par2": 40,
    "ap1": 40,
    "ap2": 40,
    "ap3": 40,
    "nidd1": 40,
    "nidd2": 40,
    "pts1": 33,
    "net": 35,
    "pet": 16,
}

# Ngưỡng "tiêu đề khớp TRÙNG KHÍT, không phải khớp mờ".
EXACT_HEADING_SCORE = 0.995

_PRINTED_ROMAN = {
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9,
    "x": 10, "xi": 11, "xii": 12, "xiii": 13, "xiv": 14, "xv": 15, "xvi": 16, "xvii": 17,
    "xviii": 18, "xix": 19, "xx": 20,
}


def _printed_local_number(line: str) -> int | None:
    """Số thứ tự ĐỊA PHƯƠNG in ở đầu dòng tiêu đề - số CUỐI của chuỗi số ghép nhiều cấp.

    Sách in đánh số ghép: `pr` in "5. 2. 1. KOSIYASIKKHĀPADAṂ" (chương.phẩm.điều) trong
    khi DB chỉ ghi "1. Kosiyasikkhāpadaṃ", nên chỉ số cuối mới so được với DB. `pts2` dùng
    số La Mã. Cùng phép lấy số này đã có trong `find_vietnamese_heading_offset`; cố tình
    KHÔNG gộp lại vì hàm đó so số giữa hai TRANG (Pāli với Việt) còn ở đây so số in với số
    DB - gộp sẽ buộc một trong hai bên đổi hành vi trên các tập đã nạp.
    """
    match = re.match(r"^\s*((?:\(?\d+\)?\s*\.\s*)+)", line)
    if match:
        numbers = re.findall(r"\d+", match.group(1))
        return int(numbers[-1]) if numbers else None
    match = re.match(r"^\s*([IVXLCDM]+)\s*[\.\)]", line, flags=re.I)
    return _PRINTED_ROMAN.get(match.group(1).casefold()) if match else None


def _db_local_number(title: str) -> int | None:
    match = re.match(r"^\s*(\d+)\s*[\.\-]", str(title or ""))
    return int(match.group(1)) if match else None


def _numbers_agree(printed_line: str, db_title: str) -> bool:
    """Tiêu đề in có mang ĐÚNG số của mục DB không.

    Đây là lưới chặn cho khớp mờ, và nó dùng bằng chứng có SẴN TRONG TRANG SÁCH thay vì
    một điểm tương đồng. Ca thật đã ghi DB rồi mới phát hiện (`pr`, đợt
    pdf_heading_boundary-20260817): tên DB `Saṅghabhedasikkhāpadaṃ` (điều **10**) khớp mờ
    0.88 vào tiêu đề in của điều **11** (`3. 11. DUTIYA SAṄGHABHEDASIKKHĀPADAṂ`, trang
    502) thay vì tiêu đề thật của nó ở trang 494 - và trang 494 bị guard nhập nhằng loại
    vì chỉ cách á quân 0.0479, trong khi trang 502 cách 0.0723 nên được nhận. Điều 10 vẫn
    REVIEW nên không bị ghi, nhưng điều **9** lấy hit lệch ấy làm điểm kết thúc và nuốt
    trọn nội dung điều 10: PASS mọi cổng kiểm, văn bản sạch, chỉ sai chủ sở hữu.
    Số in `11` khác số DB `10` phát hiện được ngay, còn điểm tương đồng thì không.

    Vì sao KHÔNG dùng ngưỡng điểm làm guard: đo trên 5 tập đã nạp thì dn1/dn3/sn/pts2 có 5
    hit khớp mờ hợp lệ (dị bản chính tả: `Cūḷabyūha`/`Cūḷaviyūha`...), chặn theo điểm sẽ
    phá cả bốn tập. Chặn theo số thì cả 5 hit ấy đều khớp số nên đi qua.

    Chỉ áp cho khớp mờ, không áp cho khớp trùng khít: `pts2` có một lỗi SỐ IN thật -
    `8. Lokuttarakathā` in là `VI. LOKUTTARAKATHĀ` - nhưng tên khớp tuyệt đối nên không
    cần tới lưới này. Đo toàn bộ: 153/153 hit đúng ở dn1/dn2/dn3/sn/pr đều khớp số, và
    ca lệch duy nhất trong pts2 là lỗi in đã ghi nhận.

    Thiếu số ở một trong hai bên thì coi là KHÔNG khớp: mục không đánh số hiện đều có hit
    trùng khít nên luật này không đổi gì hôm nay, và thà REVIEW hơn nhận một hit mờ không
    có gì kiểm chứng được.
    """
    printed = _printed_local_number(printed_line)
    return printed is not None and printed == _db_local_number(db_title)


def find_headings(
    volume: str, units: list[Unit], pages: list[str], page_is_vietnamese: list[bool]
) -> dict[str, HeadingHit]:
    """Tìm tiêu đề Pāli trên trang Pāli; chỉ nhận khớp rõ và duy nhất."""
    stems = [heading_stem(unit.title, volume) for unit in units]
    candidates: dict[int, list[HeadingHit]] = {index: [] for index in range(len(units))}
    exact_printed: dict[str, list[HeadingHit]] = {}
    search_start = HEADING_SEARCH_START_PAGE.get(volume, 1)
    for page_number, (text, vietnamese) in enumerate(
        zip(pages, page_is_vietnamese), start=1
    ):
        if page_number < search_start:
            continue
        if vietnamese:
            continue
        for line in _candidate_lines(text, volume):
            printed = heading_stem(line, volume)
            if len(printed) < 7:
                continue
            exact_printed.setdefault(printed, []).append(
                HeadingHit(page=page_number, line=line, score=1.0)
            )
            scored = sorted(
                (
                    SequenceMatcher(None, printed, stem).ratio(),
                    index,
                )
                for index, stem in enumerate(stems)
            )
            best_score, best_index = scored[-1]
            second_score = scored[-2][0] if len(scored) > 1 else 0.0
            if best_score < 0.84 or best_score - second_score < 0.06:
                continue
            # Khớp mờ còn phải mang ĐÚNG số của mục DB. Loại ở đây chứ không lọc lúc dùng:
            # một hit lệch không chỉ làm sai mục của nó mà còn thành ranh giới của mục
            # LIỀN TRƯỚC, nên phải chặn ngay chỗ sinh ra. Xem `_numbers_agree`.
            if best_score < EXACT_HEADING_SCORE and not _numbers_agree(
                line, units[best_index].title
            ):
                continue
            candidates[best_index].append(
                HeadingHit(page=page_number, line=line, score=best_score)
            )

    # Cùng một tên có thể xuất hiện ở hai phẩm (Tissametteyyasutta trong Kinh Tập).
    # Khi số lần in đúng bằng số đơn vị DB cùng tên, ghép tuần tự theo thứ tự sách.
    found: dict[str, HeadingHit] = {}
    indexes_by_stem: dict[str, list[int]] = {}
    for index, stem in enumerate(stems):
        indexes_by_stem.setdefault(stem, []).append(index)
    for stem, indexes in indexes_by_stem.items():
        hits = exact_printed.get(stem, [])
        unique_hits = []
        seen_pages: set[int] = set()
        for hit in sorted(hits, key=lambda item: item.page):
            if hit.page not in seen_pages:
                unique_hits.append(hit)
                seen_pages.add(hit.page)
        if len(unique_hits) != len(indexes):
            if volume == "net" and stem == "nayasamutthanam":
                unique_hits = [h for h in unique_hits if h.page == 196]
            else:
                continue
        for index, hit in zip(indexes, unique_hits):
            found[units[index].section_id] = hit

    # Một tiêu đề thật chỉ xuất hiện một lần. Nếu cùng tên bị dò ở nhiều trang (thường
    # là running header), không tự chọn để tránh cắt nhầm ranh giới.
    for index, unit in enumerate(units):
        if unit.section_id in found:
            continue
        hits = candidates[index]
        if not hits:
            continue
        exact = [hit for hit in hits if hit.score >= EXACT_HEADING_SCORE]
        pool = exact or hits
        unique_pages = sorted({hit.page for hit in pool})
        if len(unique_pages) != 1:
            if volume == "net" and stem == "nayasamutthanam":
                found[unit.section_id] = [hit for hit in pool if hit.page == 196][0]
                continue
            # Lấy trang đầu tiên xuất hiện ở phần thân bài.
            # Vì HEADING_SEARCH_START_PAGE đã loại bỏ mục lục, lần gặp đầu tiên
            # trong phần thân bài chính là tiêu đề thật (các lần sau là running header).
            first_page_hits = [hit for hit in pool if hit.page == unique_pages[0]]
            found[unit.section_id] = max(first_page_hits, key=lambda hit: hit.score)
            continue
        found[unit.section_id] = max(pool, key=lambda hit: hit.score)
    return found


def find_last_boundary(
    volume: str,
    start_page: int,
    pages: list[str],
    page_is_vietnamese: list[bool],
    unit_stems: tuple[str, ...] | None = None,
) -> tuple[int | None, str | None]:
    """Điểm kết thúc của đơn vị CUỐI tập - đơn vị duy nhất không có tiêu đề sau nó chặn lại.

    `niṭṭhita` ("đã xong") một mình KHÔNG đủ để nhận là hết bài: sách in cả mốc nội bộ.
    Ca thật ở Trường Bộ III - `11. Dasuttarasuttaṃ` có hai mốc, trang 529 ghi
    `Paṭhamabhāṇavāro niṭṭhito.` (hết tụng phẩm thứ nhất) và trang 569 ghi
    `Dasuttarasuttaṃ Niṭṭhitaṃ Ekādasamaṃ.` (hết bài). Luật cũ lấy mốc ĐẦU TIÊN nên cắt ở
    529, mất non nửa bài - Việt/Pali còn 0.78 trong khi 10 bài kia của tập nằm gọn trong
    1.45-1.71, và bản bóc vẫn PASS mọi cổng kiểm vì phần lấy được thì sạch.

    Nên ưu tiên mốc có GỌI TÊN đơn vị. Không tìm thấy thì mới rơi về mốc `niṭṭhita` đầu
    tiên, đúng hành vi cũ - nhánh dự phòng này giữ nguyên kết quả cho các tập đã chạy.

    Tên đơn vị phải nằm CÙNG MỘT DÒNG với `niṭṭhita`, không phải cùng trang. Bản sửa đầu
    tiên kiểm theo trang và không ăn thua, vì đầu trang Pāli nào của bài cũng in
    `Dīghanikāye Pāthikavaggo 11. Dasuttarasuttaṃ (34)` - xét theo trang thì trang mốc nội
    bộ cũng "gọi tên đơn vị", và luật mới thoái hoá về đúng luật cũ.
    """
    config = VOLUMES[volume]
    if "pdf_end_page" in config:
        return int(config["pdf_end_page"]) + 1, "configured_body_end"

    def _boundary_after(page_number: int) -> int | None:
        paired_vietnamese = page_number + 1
        if paired_vietnamese <= len(pages) and page_is_vietnamese[paired_vietnamese - 1]:
            return paired_vietnamese + 1
        return None

    first_marker: int | None = None
    for page_number in range(start_page, len(pages) + 1):
        if page_is_vietnamese[page_number - 1]:
            continue
        compact = normalize_pali(pages[page_number - 1])
        if "nitthit" not in compact:
            continue
        boundary = _boundary_after(page_number)
        if boundary is None:
            continue
        # Mốc gọi đúng tên đơn vị TRÊN CÙNG MỘT DÒNG là mốc hết bài; nhận ngay.
        if unit_stems and any(
            "nitthit" in line and any(stem in line for stem in unit_stems)
            for line in (
                re.sub(r"[^a-z0-9]+", "", normalize_pali(raw_line))
                for raw_line in pages[page_number - 1].splitlines()
            )
        ):
            return boundary, "printed_end_marker"
        if first_marker is None:
            first_marker = boundary
    if first_marker is not None:
        return first_marker, "printed_end_marker_unnamed"
    return None, None


def _coverage_compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", strip_vietnamese(value or ""))


def _coverage_token(value: str) -> str:
    return _coverage_compact(value)


def _text_tokens(value: str) -> set[str]:
    return {
        token
        for raw in re.findall(r"[^\W_]+", value or "", flags=re.UNICODE)
        if len(token := _coverage_token(raw)) >= 2
    }


def audit_vietnamese_page_text(
    pdf_path: Path,
    pages: list[str],
    page_numbers: list[int],
) -> tuple[dict[int, PageTextAudit], dict[int, str]]:
    """Đối chiếu pypdf với glyph/font-size của pdfplumber trên mọi trang Việt.

    Trả về audit và các trang thay thế an toàn. Chỉ dùng pdfplumber khi nó tìm được
    chữ pypdf thiếu *và* vẫn phủ toàn bộ token pypdf; nếu hai engine mâu thuẫn thì
    giữ nguyên nội dung và đánh REVIEW để không ghép/nhân đôi chữ theo phỏng đoán.
    """
    try:
        import pdfplumber
    except ModuleNotFoundError as exc:  # pragma: no cover - phụ thuộc môi trường chạy
        raise SystemExit(
            "Thiếu pdfplumber để kiểm toán chữ nhỏ. Chạy: "
            ".venv\\Scripts\\python.exe -m pip install -r requirements.txt"
        ) from exc

    audits: dict[int, PageTextAudit] = {}
    overrides: dict[int, str] = {}
    with pdfplumber.open(pdf_path) as pdf:
        for page_number in page_numbers:
            raw_pypdf = pages[page_number - 1]
            page = pdf.pages[page_number - 1]
            plumber_text = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
            words = page.extract_words(
                extra_attrs=["size"], x_tolerance=2, y_tolerance=3
            )
            sizes = [
                float(word["size"])
                for word in words
                if word.get("text", "").strip() and word.get("size") is not None
            ]
            median_size = statistics.median(sizes) if sizes else None
            minimum_size = min(sizes) if sizes else None
            checked = {
                token
                for word in words
                if len(token := _coverage_token(str(word.get("text", "")))) >= 2
            }
            small = {
                token
                for word in words
                if median_size
                and word.get("size") is not None
                and float(word["size"]) <= median_size * 0.85
                and len(token := _coverage_token(str(word.get("text", "")))) >= 2
            }
            pypdf_compact = _coverage_compact(raw_pypdf)
            plumber_compact = _coverage_compact(plumber_text)
            missing_from_pypdf = sorted(token for token in checked if token not in pypdf_compact)
            pypdf_tokens = _text_tokens(raw_pypdf)
            missing_from_plumber = sorted(
                token for token in pypdf_tokens if token not in plumber_compact
            )

            selected = raw_pypdf
            selected_engine = "pypdf"
            if missing_from_pypdf and not missing_from_plumber and plumber_text.strip():
                selected = plumber_text
                selected_engine = "pdfplumber_fallback"
                overrides[page_number] = selected
            selected_compact = _coverage_compact(selected)
            missing_selected = sorted(token for token in checked if token not in selected_compact)
            missing_small = sorted(token for token in small if token not in selected_compact)
            word_coverage = 1 - len(missing_selected) / max(1, len(checked))
            small_coverage = 1 - len(missing_small) / max(1, len(small))

            page_area = max(1.0, float(page.width) * float(page.height))
            image_area = sum(
                max(0.0, float(image.get("x1", 0)) - float(image.get("x0", 0)))
                * max(0.0, float(image.get("bottom", 0)) - float(image.get("top", 0)))
                for image in page.images
            )
            image_ratio = min(1.0, image_area / page_area)
            # Các glyph đã có ánh xạ được kiểm chứng (ví dụ m+U+F01E -> ṃ) được
            # sửa trước khi kết luận; chỉ hiện vật còn lại mới làm trang REVIEW.
            hazards = unicode_artifacts(clean_unicode_artifacts(selected))
            problems: list[str] = []
            if not checked and not selected.strip():
                problems.append("không bóc được chữ")
            if word_coverage < 0.995:
                problems.append(f"độ phủ chữ chỉ {word_coverage:.1%}")
            if small and small_coverage < 0.995:
                problems.append(f"độ phủ chữ nhỏ chỉ {small_coverage:.1%}")
            if missing_from_pypdf and missing_from_plumber:
                problems.append("hai engine cho kết quả mâu thuẫn")
            if image_ratio >= 0.20 and len(checked) < 20:
                problems.append("trang ảnh/scan cần OCR")
            if hazards:
                problems.append("còn glyph/Unicode lỗi")

            audits[page_number] = PageTextAudit(
                page=page_number,
                status="REVIEW" if problems else "PASS",
                selected_engine=selected_engine,
                reason=(
                    "; ".join(problems)
                    if problems
                    else (
                        "pdfplumber bổ sung phần pypdf thiếu và vẫn phủ đủ phần cũ"
                        if selected_engine == "pdfplumber_fallback"
                        else "pypdf phủ đủ chữ và chữ nhỏ"
                    )
                ),
                pypdf_characters=len(raw_pypdf),
                pdfplumber_characters=len(plumber_text),
                checked_words=len(checked),
                word_coverage=round(word_coverage, 4),
                small_words=len(small),
                small_word_coverage=round(small_coverage, 4),
                minimum_font_size=round(minimum_size, 3) if minimum_size is not None else None,
                median_font_size=round(median_size, 3) if median_size is not None else None,
                image_count=len(page.images),
                image_area_ratio=round(image_ratio, 4),
                unicode_artifacts=sum(hazards.values()),
                missing_word_sample=missing_selected[:12],
            )
    return audits, overrides


def _line_offsets(text: str) -> Iterable[tuple[int, str]]:
    offset = 0
    for raw_line in text.splitlines(keepends=True):
        yield offset, re.sub(r"\s+", " ", raw_line).strip()
        offset += len(raw_line)


def _cv1_heading_candidates(text: str) -> list[tuple[int, int, str]]:
    """Các dòng tiêu đề in hoa của một trang, kèm offset và chỉ số dòng."""
    candidates: list[tuple[int, int, str]] = []
    offset = 0
    for line_index, raw_line in enumerate(text.splitlines(keepends=True)):
        line = re.sub(r"\s+", " ", raw_line).strip()
        if 5 <= len(line) <= 110 and _uppercase_ratio(line) >= 0.55:
            candidates.append((offset, line_index, line))
        offset += len(raw_line)
    return candidates


def _cv1_structural_heading(line: str) -> bool:
    stem = heading_stem(line, "cv1")
    if stem in {"vinayapitake", "cullavaggapali"}:
        return True
    return bool(re.match(r"^[IVXLCDM]+\.\s+", line, flags=re.I)) and stem.endswith(
        "khandhakam"
    )


def _cv1_heading_blocks_for_pair(
    pali_page: int, pali_text: str, vietnamese_text: str
) -> list[Cv1UnitStart]:
    """Ghép các tiêu đề Pāli/Việt theo đúng thứ tự hình học trên một cặp trang.

    Hai trang đối diện in cùng số dòng tiêu đề. Một tiêu đề dài có thể xuống hai dòng;
    khi giữa hai dòng in hoa không có thân bài, chúng được xem là một mốc và lấy offset
    của dòng đầu. Dùng thứ tự song song thay vì dịch ngược nhan đề Việt sang Pāli.
    """
    pali_candidates = _cv1_heading_candidates(pali_text)
    vietnamese_candidates = _cv1_heading_candidates(vietnamese_text)
    if len(pali_candidates) != len(vietnamese_candidates):
        raise RuntimeError(
            f"cv1: cặp trang {pali_page}/{pali_page + 1} lệch số dòng tiêu đề "
            f"Pāli={len(pali_candidates)} Việt={len(vietnamese_candidates)}"
        )

    pali_lines = [re.sub(r"\s+", " ", line).strip() for line in pali_text.splitlines()]
    blocks: list[Cv1UnitStart] = []
    current: dict | None = None
    previous_line_index: int | None = None

    def flush() -> None:
        nonlocal current
        if not current:
            return
        blocks.append(
            Cv1UnitStart(
                pali_hit=HeadingHit(
                    page=pali_page,
                    line=" ".join(current["pali_lines"]),
                    score=1.0,
                ),
                vietnamese_page=pali_page + 1,
                vietnamese_offset=current["vietnamese_offset"],
                vietnamese_line=" ".join(current["vietnamese_lines"]),
            )
        )
        current = None

    for pali_candidate, vietnamese_candidate in zip(
        pali_candidates, vietnamese_candidates
    ):
        _pali_offset, line_index, pali_line = pali_candidate
        vietnamese_offset, _vietnamese_line_index, vietnamese_line = vietnamese_candidate
        if _cv1_structural_heading(pali_line):
            flush()
            previous_line_index = line_index
            continue
        body_between = (
            previous_line_index is not None
            and any(pali_lines[previous_line_index + 1 : line_index])
        )
        if current is None or body_between:
            flush()
            current = {
                "pali_lines": [pali_line],
                "vietnamese_lines": [vietnamese_line],
                "vietnamese_offset": vietnamese_offset,
            }
        else:
            current["pali_lines"].append(pali_line)
            current["vietnamese_lines"].append(vietnamese_line)
        previous_line_index = line_index
    flush()
    return blocks


def _cv1_numbered_boundary(
    pages: list[str], pali_page: int, vietnamese_page: int
) -> Cv1UnitStart:
    hits = [
        (offset, line)
        for offset, line in _line_offsets(pages[vietnamese_page - 1])
        if re.match(r"^1\.\s+Sau đó,\s+hội chúng", line, flags=re.I)
    ]
    if len(hits) != 1:
        raise RuntimeError(
            f"cv1: không dò được duy nhất mốc đoạn 1 ở trang Việt {vietnamese_page}"
        )
    offset, line = hits[0]
    return Cv1UnitStart(
        pali_hit=HeadingHit(
            page=pali_page,
            line="(ranh giới đoạn 1 không có tiêu đề riêng)",
            score=1.0,
        ),
        vietnamese_page=vietnamese_page,
        vietnamese_offset=offset,
        vietnamese_line=line,
    )


def find_cv1_vietnamese_unit_starts(
    units: list[Unit], pages: list[str], page_is_vietnamese: list[bool]
) -> dict[str, Cv1UnitStart]:
    """Dò 119 reader segment của riêng Cullavagga I theo cặp trang song song."""
    if len(units) != CV1_EXPECTED_READER_SEGMENTS:
        raise RuntimeError(
            f"cv1: cần {CV1_EXPECTED_READER_SEGMENTS} segment, nhận {len(units)}"
        )

    blocks: list[Cv1UnitStart] = []
    for pali_page in range(
        CV1_FIRST_BODY_PALI_PAGE, CV1_LAST_BODY_PALI_PAGE + 1, 2
    ):
        vietnamese_page = pali_page + 1
        if (
            pali_page > len(pages)
            or vietnamese_page > len(pages)
            or page_is_vietnamese[pali_page - 1]
            or not page_is_vietnamese[vietnamese_page - 1]
        ):
            raise RuntimeError(
                f"cv1: cặp trang {pali_page}/{vietnamese_page} không còn Pāli/Việt"
            )
        for block in _cv1_heading_blocks_for_pair(
            pali_page, pages[pali_page - 1], pages[vietnamese_page - 1]
        ):
            marker = (pali_page, heading_stem(block.pali_hit.line, "cv1"))
            if marker in CV1_INTERNAL_PRINTED_HEADINGS:
                continue
            blocks.append(block)

    special_starts = {
        sort_order: _cv1_numbered_boundary(pages, pali_page, vietnamese_page)
        for sort_order, (pali_page, vietnamese_page) in CV1_NUMBERED_BOUNDARY_PAGES.items()
    }
    ordinary_units = [
        unit for unit in units if unit.start_sort_order not in special_starts
    ]
    if len(blocks) != len(ordinary_units):
        raise RuntimeError(
            "cv1: số mốc tiêu đề không khớp reader segment "
            f"({len(blocks)} mốc/{len(ordinary_units)} segment có tiêu đề)"
        )

    starts: dict[str, Cv1UnitStart] = {}
    block_iter = iter(blocks)
    previous_position = (0, -1)
    for unit in units:
        start = special_starts.get(unit.start_sort_order) or next(block_iter)
        position = (start.vietnamese_page, start.vietnamese_offset)
        if position <= previous_position:
            raise RuntimeError(
                f"cv1: ranh giới không tăng tại sort_order {unit.start_sort_order}"
            )
        previous_position = position
        starts[_preview_unit_key(unit)] = start

    appaticchanna = next(
        unit for unit in units if unit.start_sort_order == 480 and unit.end_sort_order == 485
    )
    appaticchanna_start = starts[_preview_unit_key(appaticchanna)]
    # Bản in ghi APAṬICCHANNA- (một p) trong khi tiêu đề DB ghi
    # Appaṭicchanna- (hai p); đây là cùng mốc, đã đối chiếu trực tiếp trang 238/239.
    if "apaticchannamanattam" not in heading_stem(
        appaticchanna_start.pali_hit.line, "cv1"
    ):
        raise RuntimeError("cv1: mốc Appaṭicchannamānattaṃ không khớp PDF")
    return starts


def _cv2_squash(value: str) -> str:
    """Chuẩn hóa mạnh để so Pāli DB với font/trích chữ của đúng PDF cv2."""
    return re.sub(r"[^a-z0-9]+", "", normalize_pali(mend_spacing(value)))


def _cv2_ngrams(value: str, size: int = 5) -> set[str]:
    if len(value) < size:
        return {value} if value else set()
    return {value[index : index + size] for index in range(len(value) - size + 1)}


def _cv2_anchor_similarity(anchor: str, candidate: str) -> float:
    """Ưu tiên phần mở đầu nhưng vẫn kiểm tra thêm thân đoạn.

    Một số section DB gom nhiều tiểu mục PDF bằng dấu ``…pe…``. Nếu chỉ so cả
    đoạn dài, mốc thứ hai trong nhóm có thể thắng mốc bắt đầu thật; trọng số 70%
    cho 80 ký tự đầu giữ đúng mốc đầu tiên, còn 30% phần dài chống nhầm các công
    thức Luật tạng lặp lại.
    """

    def dice(left: str, right: str) -> float:
        left_grams = _cv2_ngrams(left)
        right_grams = _cv2_ngrams(right)
        return 2 * len(left_grams & right_grams) / max(
            1, len(left_grams) + len(right_grams)
        )

    return 0.7 * dice(anchor[:80], candidate[:80]) + 0.3 * dice(
        anchor[:220], candidate[:220]
    )


def _numbered_pali_candidates(
    pages: list[str],
    page_is_vietnamese: list[bool],
    *,
    volume: str,
    first_page: int,
    body_end_exclusive: int,
) -> list[Cv2PaliCandidate]:
    """Các đoạn Pāli đánh số trong phần thân của một PDF song ngữ."""
    candidates: list[Cv2PaliCandidate] = []
    for pali_page in range(first_page, min(len(pages) + 1, body_end_exclusive)):
        if page_is_vietnamese[pali_page - 1]:
            continue
        vietnamese_page = pali_page + 1
        if (
            vietnamese_page > len(pages)
            or vietnamese_page >= body_end_exclusive
            or not page_is_vietnamese[vietnamese_page - 1]
        ):
            raise RuntimeError(
                f"{volume}: cặp trang {pali_page}/{vietnamese_page} "
                "không còn Pāli/Việt trong phần thân"
            )

        lines = pages[pali_page - 1].splitlines()
        numbered: list[tuple[int, tuple[str, ...], str]] = []
        for line_index, raw_line in enumerate(lines):
            line = re.sub(r"\s+", " ", raw_line).strip()
            match = re.match(r"^\s*((?:\d+\s*\.\s*)+)(.+)", line)
            if not match or _uppercase_ratio(line) >= 0.55:
                continue
            prefix = tuple(re.findall(r"\d+", match.group(1)))
            if prefix:
                numbered.append((line_index, prefix, line))

        for index, (line_index, printed_prefix, line) in enumerate(numbered):
            next_line_index = (
                numbered[index + 1][0] if index + 1 < len(numbered) else len(lines)
            )
            block = " ".join(lines[line_index:next_line_index])
            candidates.append(
                Cv2PaliCandidate(
                    page=pali_page,
                    line_index=line_index,
                    printed_number=printed_prefix[-1],
                    line=line,
                    normalized_block=_cv2_squash(block),
                    printed_prefix=printed_prefix,
                )
            )
    return sorted(candidates, key=lambda item: (item.page, item.line_index))


def _cv2_pali_candidates(
    pages: list[str], page_is_vietnamese: list[bool]
) -> list[Cv2PaliCandidate]:
    return _numbered_pali_candidates(
        pages,
        page_is_vietnamese,
        volume="cv2",
        first_page=CV2_FIRST_BODY_PALI_PAGE,
        body_end_exclusive=CV2_BODY_END_EXCLUSIVE,
    )


def _cv2_monotonic_anchor_path(
    units: list[Unit],
    candidates: list[Cv2PaliCandidate],
    *,
    volume: str = "cv2",
    minimum_score: float = CV2_MIN_ANCHOR_SCORE,
) -> list[tuple[Cv2PaliCandidate, float]]:
    """Chọn 73 mốc có tổng độ khớp cao nhất với vị trí tăng nghiêm ngặt."""
    if len(candidates) < len(units):
        raise RuntimeError(f"{volume}: không đủ mốc đoạn đánh số trong PDF")

    score_rows: list[list[float]] = []
    for unit in units:
        if not unit.pali_anchor:
            raise RuntimeError(f"{volume}/{unit.title}: passage đầu không có Pāli để neo")
        anchor = re.sub(r"^\s*(?:\d+\s*\.\s*)+", "", unit.pali_anchor).strip()
        normalized_anchor = _cv2_squash(anchor)
        score_rows.append(
            [
                _cv2_anchor_similarity(normalized_anchor, candidate.normalized_block)
                for candidate in candidates
            ]
        )

    impossible = -1e9
    previous = score_rows[0][:]
    back_rows: list[list[int]] = []
    for unit_index in range(1, len(units)):
        best_score = impossible
        best_index = -1
        prefixes: list[tuple[float, int]] = []
        for candidate_index, score in enumerate(previous):
            # Lưu mốc tốt nhất NGHIÊM NGẶT trước candidate hiện tại.
            prefixes.append((best_score, best_index))
            if score > best_score:
                best_score = score
                best_index = candidate_index

        current: list[float] = []
        back: list[int] = []
        for candidate_index, score in enumerate(score_rows[unit_index]):
            prefix_score, prefix_index = prefixes[candidate_index]
            current.append(
                prefix_score + score if prefix_index >= 0 else impossible
            )
            back.append(prefix_index)
        previous = current
        back_rows.append(back)

    selected_index = max(range(len(previous)), key=lambda index: previous[index])
    selected = [selected_index]
    for back in reversed(back_rows):
        selected_index = back[selected_index]
        if selected_index < 0:
            raise RuntimeError(f"{volume}: không dựng được chuỗi mốc tăng liên tục")
        selected.append(selected_index)
    selected.reverse()

    result = [
        (candidates[candidate_index], score_rows[unit_index][candidate_index])
        for unit_index, candidate_index in enumerate(selected)
    ]
    weak = [
        (unit.title, candidate.page, candidate.printed_number, score)
        for unit, (candidate, score) in zip(units, result)
        if score < minimum_score
    ]
    if weak:
        raise RuntimeError(
            f"{volume}: còn mốc Pāli yếu, không được tự động cắt: {weak}"
        )
    return result


def _cv2_vietnamese_start(
    unit: Unit, candidate: Cv2PaliCandidate, pages: list[str]
) -> tuple[int, int, str]:
    vietnamese_page = candidate.page + 1
    page_text = pages[vietnamese_page - 1]
    indexed_lines = [
        (line_index, offset, line)
        for line_index, (offset, line) in enumerate(_line_offsets(page_text))
    ]

    if unit.start_sort_order == 2050:
        # Pāli in số 17 nhưng bản Việt bỏ số này; câu hỏi dưới đây là mốc dịch song
        # song duy nhất ngay sau phần trả lời số 16 trên trang 499.
        marker = "bach ngai, su an han se duoc xay den cho vi ty khuu khien trach khong"
        body_hits = [
            item
            for item in indexed_lines
            if marker in strip_vietnamese(item[2]).casefold()
        ]
    else:
        body_hits = []
        for item in indexed_lines:
            match = re.match(
                rf"^{re.escape(candidate.printed_number)}\s*\.\s+(.+)", item[2]
            )
            if match and _uppercase_ratio(item[2]) < 0.55:
                body_hits.append(item)
    if len(body_hits) != 1:
        raise RuntimeError(
            f"cv2/{unit.title}: trang Việt {vietnamese_page} có "
            f"{len(body_hits)} mốc thân đoạn số {candidate.printed_number}"
        )

    body_line_index, body_offset, body_line = body_hits[0]
    heading_lines: list[tuple[int, int, str]] = []
    line_index = body_line_index - 1
    while line_index >= 0:
        item = indexed_lines[line_index]
        line = item[2]
        if not line:
            line_index -= 1
            continue
        if 5 <= len(line) <= 140 and _uppercase_ratio(line) >= 0.55:
            heading_lines.append(item)
            line_index -= 1
            continue
        break
    heading_lines.reverse()
    if heading_lines:
        return vietnamese_page, heading_lines[0][1], " ".join(
            item[2] for item in heading_lines
        )
    return vietnamese_page, body_offset, body_line


def find_cv2_vietnamese_unit_starts(
    units: list[Unit], pages: list[str], page_is_vietnamese: list[bool]
) -> dict[str, Cv2UnitStart]:
    """Dò đủ 73 section sâu nhất của riêng `ttpv_07_Cv_II.pdf`."""
    if len(units) != CV2_EXPECTED_READER_SEGMENTS:
        raise RuntimeError(
            f"cv2: cần {CV2_EXPECTED_READER_SEGMENTS} segment, nhận {len(units)}"
        )
    candidates = _cv2_pali_candidates(pages, page_is_vietnamese)
    path = _cv2_monotonic_anchor_path(units, candidates)

    starts: dict[str, Cv2UnitStart] = {}
    previous_position = (0, -1)
    for unit, (candidate, score) in zip(units, path):
        vietnamese_page, vietnamese_offset, vietnamese_line = _cv2_vietnamese_start(
            unit, candidate, pages
        )
        position = (vietnamese_page, vietnamese_offset)
        if position <= previous_position:
            raise RuntimeError(
                f"cv2: ranh giới Việt không tăng tại sort_order {unit.start_sort_order}"
            )
        previous_position = position
        starts[_preview_unit_key(unit)] = Cv2UnitStart(
            pali_hit=HeadingHit(page=candidate.page, line=candidate.line, score=score),
            vietnamese_page=vietnamese_page,
            vietnamese_offset=vietnamese_offset,
            vietnamese_line=vietnamese_line,
        )

    acariya = next(unit for unit in units if unit.start_sort_order == 1775)
    acariya_start = starts[_preview_unit_key(acariya)]
    if acariya_start.vietnamese_page != 433 or not acariya_start.vietnamese_line.startswith(
        "13. PHẬN SỰ ĐỐI VỚI THẦY DẠY HỌC"
    ):
        raise RuntimeError("cv2: mốc Ācariyavattakathā không khớp trang 432/433")
    return starts


def _compact_vietnamese_marker(value: str) -> str:
    return re.sub(
        r"[^a-z0-9]+", "", strip_vietnamese(mend_spacing(value)).casefold()
    )


def _fixed_deepest_start(volume: str, unit: Unit, pages: list[str]) -> Cv2UnitStart:
    try:
        pali_page, vietnamese_page, marker, occurrence = DEEPEST_FIXED_STARTS[
            (volume, unit.start_sort_order)
        ]
    except KeyError as exc:  # pragma: no cover - lỗi lập trình/cấu hình
        raise RuntimeError(
            f"{volume}/{unit.title}: chưa khai mốc cố định"
        ) from exc
    if pali_page > len(pages) or vietnamese_page > len(pages):
        raise RuntimeError(f"{volume}/{unit.title}: mốc cố định nằm ngoài PDF")
    compact_marker = _compact_vietnamese_marker(marker)
    hits = [
        (offset, line)
        for offset, line in _line_offsets(pages[vietnamese_page - 1])
        if compact_marker in _compact_vietnamese_marker(line)
    ]
    if len(hits) < occurrence:
        raise RuntimeError(
            f"{volume}/{unit.title}: trang Việt {vietnamese_page} chỉ có "
            f"{len(hits)} mốc {marker!r}, cần lần {occurrence}"
        )
    offset, line = hits[occurrence - 1]
    return Cv2UnitStart(
        pali_hit=HeadingHit(
            page=pali_page,
            line=f"(mốc đã đối chiếu: {marker})",
            score=1.0,
        ),
        vietnamese_page=vietnamese_page,
        vietnamese_offset=offset,
        vietnamese_line=line,
    )


def _numeric_line_prefix(
    line: str, *, allow_missing_final_period: bool = False
) -> tuple[str, ...] | None:
    match = re.match(r"^\s*((?:\d+\s*\.\s*)+)", line)
    if match:
        values = tuple(re.findall(r"\d+", match.group(1)))
        return values or None
    if allow_missing_final_period:
        # ttpv_05_Mv_II.pdf, trang 59 in “54 Vào lúc bấy giờ...” (thiếu dấu
        # chấm) trong khi trang Pāli 58 in “54.”. Chỉ cho phép dạng một cấp.
        match = re.match(r"^\s*(\d+)\s+(?=\D)", line)
        if match:
            return (match.group(1),)
    return None


def _paired_vietnamese_start(
    volume: str,
    unit: Unit,
    candidate: Cv2PaliCandidate,
    candidates: list[Cv2PaliCandidate],
    pages: list[str],
) -> tuple[int, int, str]:
    """Ghép đúng lần xuất hiện của cùng chuỗi số trên hai trang đối diện."""
    vietnamese_page = candidate.page + 1
    prefix = candidate.printed_prefix or (candidate.printed_number,)
    same_pali = [
        item
        for item in candidates
        if item.page == candidate.page
        and (item.printed_prefix or (item.printed_number,)) == prefix
    ]
    try:
        occurrence = same_pali.index(candidate)
    except ValueError as exc:  # pragma: no cover - invariant nội bộ
        raise RuntimeError(f"{volume}/{unit.title}: mất mốc Pāli trong danh sách") from exc

    indexed_lines = [
        (line_index, offset, line)
        for line_index, (offset, line) in enumerate(
            _line_offsets(pages[vietnamese_page - 1])
        )
    ]
    body_hits = [
        item
        for item in indexed_lines
        if _numeric_line_prefix(
            item[2], allow_missing_final_period=(volume == "mv2")
        )
        == prefix
        and _uppercase_ratio(item[2]) < 0.55
    ]
    if occurrence >= len(body_hits):
        raise RuntimeError(
            f"{volume}/{unit.title}: trang Việt {vietnamese_page} không có lần "
            f"{occurrence + 1} của mốc số {'.'.join(prefix)}"
        )

    body_line_index, body_offset, body_line = body_hits[occurrence]
    heading_lines: list[tuple[int, int, str]] = []
    line_index = body_line_index - 1
    while line_index >= 0:
        item = indexed_lines[line_index]
        line = item[2]
        if not line:
            line_index -= 1
            continue
        if 5 <= len(line) <= 140 and _uppercase_ratio(line) >= 0.55:
            heading_lines.append(item)
            line_index -= 1
            continue
        break
    heading_lines.reverse()
    if heading_lines:
        return vietnamese_page, heading_lines[0][1], " ".join(
            item[2] for item in heading_lines
        )
    return vietnamese_page, body_offset, body_line


def find_deepest_vietnamese_unit_starts(
    volume: str,
    units: list[Unit],
    pages: list[str],
    page_is_vietnamese: list[bool],
) -> dict[str, Cv2UnitStart]:
    """Dò ranh giới tầng sâu nhất cho các PDF được nạp theo section trực tiếp."""
    spec = DEEPEST_ALIGNED_SPECS[volume]
    expected_count = int(spec["expected_count"])
    if len(units) != expected_count:
        raise RuntimeError(f"{volume}: cần {expected_count} segment, nhận {len(units)}")

    fixed_units = {
        unit.start_sort_order: unit
        for unit in units
        if (volume, unit.start_sort_order) in DEEPEST_FIXED_STARTS
    }
    starts = {
        _preview_unit_key(unit): _fixed_deepest_start(volume, unit, pages)
        for unit in fixed_units.values()
    }

    if volume == "mil":
        # Phần Opammakathā in tiêu đề Pāli/Việt song song rất rõ, nhưng câu mở
        # đầu thường dùng dị danh (gadrabha/ghorassara...) nên so nội dung cho
        # điểm thấp giả. Từ sort 1529 trở đi lấy chính tiêu đề hai trang.
        title_units = [
            unit for unit in units if unit.start_sort_order >= 1529
        ]
        heading_hits = find_headings(
            volume, title_units, pages, page_is_vietnamese
        )
        for unit in title_units:
            unit_key = _preview_unit_key(unit)
            if unit_key in starts:
                continue
            hit = heading_hits.get(unit.section_id)
            if not hit:
                raise RuntimeError(
                    f"mil/{unit.title}: không dò được tiêu đề song ngữ phần ví dụ"
                )
            vietnamese_page = hit.page + 1
            cut = find_vietnamese_heading_offset(
                volume, unit, hit, pages[vietnamese_page - 1]
            )
            if not cut:
                raise RuntimeError(
                    f"mil/{unit.title}: thiếu tiêu đề Việt trang {vietnamese_page}"
                )
            starts[unit_key] = Cv2UnitStart(
                pali_hit=hit,
                vietnamese_page=vietnamese_page,
                vietnamese_offset=cut[0],
                vietnamese_line=cut[1],
            )

    if volume == "net":
        # Ngoài tám mốc đặc biệt, tên Pāli của Nettippakaraṇa được in rõ và đã
        # có bộ dò tiêu đề chặt chẽ. Dùng nó thay vì ép các khối kệ vào số đoạn.
        heading_hits = find_headings(volume, units, pages, page_is_vietnamese)
        for unit in units:
            unit_key = _preview_unit_key(unit)
            if unit_key in starts:
                continue
            hit = heading_hits.get(unit.section_id)
            if not hit:
                raise RuntimeError(f"net/{unit.title}: không dò được tiêu đề Pāli")
            vietnamese_page = hit.page + 1
            if (
                vietnamese_page > len(pages)
                or not page_is_vietnamese[vietnamese_page - 1]
            ):
                raise RuntimeError(
                    f"net/{unit.title}: trang {vietnamese_page} không phải bản Việt"
                )
            cut = find_vietnamese_heading_offset(
                volume, unit, hit, pages[vietnamese_page - 1]
            )
            if not cut:
                raise RuntimeError(
                    f"net/{unit.title}: không dò được tiêu đề Việt trang {vietnamese_page}"
                )
            starts[unit_key] = Cv2UnitStart(
                pali_hit=hit,
                vietnamese_page=vietnamese_page,
                vietnamese_offset=cut[0],
                vietnamese_line=cut[1],
            )
    else:
        candidates = _numbered_pali_candidates(
            pages,
            page_is_vietnamese,
            volume=volume,
            first_page=int(spec["first_pali_page"]),
            body_end_exclusive=int(spec["body_end_exclusive"]),
        )
        # Chạy DP riêng giữa hai mốc cố định. Nếu bỏ ràng buộc này, đoạn con sau
        # tiêu đề Opammakathā ở Milinda có thể bị kéo ngược lên trước tiêu đề cha:
        # tổng điểm toàn cục vẫn tăng nhưng vi phạm chính cấu trúc đã đối chiếu.
        anchored_sort_orders = {
            unit.start_sort_order
            for unit in units
            if _preview_unit_key(unit) in starts
        }
        automatic_paths: list[
            tuple[Unit, Cv2PaliCandidate, float]
        ] = []
        run: list[Unit] = []
        previous_fixed_page: int | None = None
        for index in range(len(units) + 1):
            unit = units[index] if index < len(units) else None
            is_fixed = unit is None or unit.start_sort_order in anchored_sort_orders
            if not is_fixed:
                run.append(unit)
                continue
            next_fixed_page = (
                starts[_preview_unit_key(unit)].pali_hit.page
                if unit is not None
                else None
            )
            if run:
                bounded = [
                    candidate
                    for candidate in candidates
                    if (
                        previous_fixed_page is None
                        or candidate.page >= previous_fixed_page
                    )
                    and (
                        next_fixed_page is None
                        or candidate.page <= next_fixed_page
                    )
                ]
                path = _cv2_monotonic_anchor_path(
                    run,
                    bounded,
                    volume=volume,
                    minimum_score=float(spec["minimum_score"]),
                )
                automatic_paths.extend(
                    (run_unit, candidate, score)
                    for run_unit, (candidate, score) in zip(run, path)
                )
                run = []
            if unit is not None:
                previous_fixed_page = next_fixed_page

        for unit, candidate, score in automatic_paths:
            vietnamese_page, vietnamese_offset, vietnamese_line = (
                _paired_vietnamese_start(
                    volume, unit, candidate, candidates, pages
                )
            )
            starts[_preview_unit_key(unit)] = Cv2UnitStart(
                pali_hit=HeadingHit(
                    page=candidate.page, line=candidate.line, score=score
                ),
                vietnamese_page=vietnamese_page,
                vietnamese_offset=vietnamese_offset,
                vietnamese_line=vietnamese_line,
            )

    if len(starts) != len(units):
        raise RuntimeError(
            f"{volume}: chỉ dựng được {len(starts)}/{len(units)} điểm bắt đầu"
        )
    previous_position = (0, -1)
    for unit in units:
        start = starts[_preview_unit_key(unit)]
        position = (start.vietnamese_page, start.vietnamese_offset)
        if position <= previous_position:
            raise RuntimeError(
                f"{volume}: ranh giới Việt không tăng tại sort_order "
                f"{unit.start_sort_order}: {previous_position}->{position}"
            )
        previous_position = position
    return starts


def find_pc2_vietnamese_unit_starts(
    units: list[Unit], pages: list[str], page_is_vietnamese: list[bool]
) -> dict[str, Pc2UnitStart]:
    """Dò toàn bộ 125 biên bằng số in trên trang Việt của Pācittiya II.

    Phần Bhikkhunīvibhaṅga thường không in một tiêu đề Pāli riêng cho từng điều,
    nhưng trang Việt luôn đánh số cấu trúc rõ ràng (`2. 2.`, `4. 9. 8-10.`...).
    Dùng số đầy đủ từ `source_path` nên các tên lặp như `Paṭhamasikkhāpadaṃ` ở
    chín phẩm không thể bị ghép chéo. Mỗi mốc phải xuất hiện đúng một lần trong
    thân sách; mục lục và phụ chú bị loại bằng khoảng trang đã đối chiếu.
    """
    expected: dict[str, Unit] = {}
    for unit in units:
        key = pc2_unit_key(unit)
        if key is None or key in expected:
            continue
        expected[key] = unit

    candidates: dict[str, list[Pc2UnitStart]] = {key: [] for key in expected}
    last_page = min(len(pages), PC2_LAST_BODY_VI_PAGE)
    for page_number in range(PC2_FIRST_BODY_VI_PAGE, last_page + 1):
        if not page_is_vietnamese[page_number - 1]:
            continue
        lines = list(_line_offsets(pages[page_number - 1]))
        for line_index, (offset, line) in enumerate(lines):
            match = re.match(
                r"^\s*((?:\d+(?:\s*-\s*\d+)?\s*\.\s*)+)(.*)$", line
            )
            if not match:
                continue
            key = ".".join(
                token.replace(" ", "")
                for token in re.findall(r"\d+(?:\s*-\s*\d+)?", match.group(1))
            )
            if key not in expected:
                continue

            label = match.group(2).strip()
            if not label:
                label = next(
                    (
                        later_line
                        for _later_offset, later_line in lines[line_index + 1 :]
                        if later_line
                    ),
                    "",
                )
            label_upper = label.upper()
            if "ĐIỀU" not in label_upper and "PHÁP DÀN XẾP" not in label_upper:
                continue
            candidates[key].append(
                Pc2UnitStart(
                    pali_hit=HeadingHit(
                        page=page_number - 1,
                        line=line,
                        score=1.0,
                    ),
                    vietnamese_page=page_number,
                    vietnamese_offset=offset,
                    vietnamese_line=line,
                )
            )

    # Điều sekhiya cuối bắt đầu ở đầu cặp trang 421/422 nhưng sách không in lại
    # nhan đề; trang 422 mở thẳng duyên khởi và kết thúc bằng “Điều học thứ mười
    # lăm.” Mốc trang này được kiểm chứng cùng cấu trúc hai section DB của chương 6.
    if "6.75" in expected and len(pages) >= 422 and page_is_vietnamese[421]:
        candidates["6.75"] = [
            Pc2UnitStart(
                pali_hit=HeadingHit(
                    page=421,
                    line="(điều sekhiya cuối, mở đầu ở cặp trang 421/422)",
                    score=1.0,
                ),
                vietnamese_page=422,
                vietnamese_offset=0,
                vietnamese_line="(điều sekhiya cuối)",
            )
        ]

    return {
        expected[key].section_id: hits[0]
        for key, hits in candidates.items()
        if len(hits) == 1
    }


def find_vietnamese_heading_offset(
    volume: str, unit: Unit, pali_hit: HeadingHit, vietnamese_text: str
) -> tuple[int, str] | None:
    """Tìm đúng dòng nhan đề Việt trên trang đối diện để cắt được giữa trang."""
    if volume == "pts2":
        prefix_match = re.match(r"^\s*([IVXLCDM]+)\s*\.", pali_hit.line, flags=re.I)
        prefix = prefix_match.group(1).casefold() if prefix_match else None
    else:
        # Lấy số ĐỊA PHƯƠNG - số CUỐI trong chuỗi số ghép đầu dòng đã khớp Pāli - chứ
        # không lấy thẳng từ `unit.title`. Một số bản in đánh số ghép nhiều cấp trước
        # tên đơn vị: `pr` in "5. 2. 1. KOSIYASIKKHĀPADAṂ" (chương.nhóm.điều) trong khi
        # DB chỉ ghi "1. Kosiyasikkhāpadaṃ". Số cuối cùng luôn khớp đúng số DB dùng, bất
        # kể sách in ghép mấy cấp phía trước - đã kiểm bằng trang Việt đối diện thật:
        # "5. 2. 1. ĐIỀU HỌC VỀ TƠ TẰM:" cùng số ghép, số cuối vẫn là 1.
        # Với `dn`/`sn` (số đơn, không ghép cấp) phép lấy này cho kết quả y hệt cách cũ
        # vì chuỗi chỉ có một số - không đổi hành vi ở các tập đã chạy tốt.
        prefix_match = re.match(r"^\s*((?:\(?\d+\)?\s*\.\s*)+)", pali_hit.line)
        prefix_nums = re.findall(r"\d+", prefix_match.group(1)) if prefix_match else []
        prefix = prefix_nums[-1] if prefix_nums else None
        if volume == "net" and prefix is None:
            # Các heading Pāli ở phần Sampāta chỉ in tên, còn trang Việt đặt
            # số cấu trúc ở dòng ngay trước (3.2.4., 3.2.5., ...).
            db_number = _db_local_number(unit.title)
            prefix = str(db_number) if db_number is not None else None

    def _local_number(line: str) -> str | None:
        match = re.match(r"^\s*((?:\(?\d+\)?\s*\.\s*)+)", line)
        if not match:
            return None
        nums = re.findall(r"\d+", match.group(1))
        return nums[-1] if nums else None

    candidates: list[tuple[int, str]] = []
    all_candidates: list[tuple[int, str]] = []
    for offset, line in _line_offsets(vietnamese_text):
        if not 4 <= len(line) <= 120:
            continue
        if volume not in ("thag", "vvpv", "ja1", "ja2", "ja3", "bvcp", "mil", "par1", "par2", "ap1", "ap2", "ap3", "nidd1", "nidd2", "pts1", "net", "pet"):
            if _uppercase_ratio(line) < 0.50:
                continue
        all_candidates.append((offset, line))
        if prefix:
            if volume == "pts2":
                match = re.match(r"^\s*([IVXLCDM]+)\s*\.", line, flags=re.I)
                ok = bool(match) and match.group(1).casefold() == prefix
            else:
                ok = _local_number(line) == prefix
            if not ok:
                continue
        candidates.append((offset, line))

    if not candidates:
        # Một số ấn bản in sai số ở trang Việt (Mettasutta Pāli số 8 nhưng trang
        # Việt ghi số 9; Lokuttarakathā Pāli ghi VI thay vì VIII). Chỉ nhận khi
        # trang đối diện có đúng một dòng tiêu đề in hoa, nên không biến lỗi số
        # thành phép khớp mơ hồ.
        return all_candidates[0] if len(all_candidates) == 1 else None
    if prefix:
        return candidates[0]
    # Đơn vị không đánh số (Vatthugāthā, Pārāyanānugītigāthā): đầu trang có thể
    # in cả tên phẩm rồi mới tới tên đơn vị. Chọn tiêu đề in hoa cuối cùng trước
    # 800 ký tự đầu, không đoán từ bản dịch nhan đề.
    early = [candidate for candidate in candidates if candidate[0] <= 800]
    return (early or candidates)[-1]


_RUNNING_HEADS = (
    "dīghanikāya",
    "trường bộ",
    "suttanipāta",
    "kinh tập",
    "paṭisambhidāmagga",
    "phân tích đạo",
)


def clean_vietnamese_page(text: str, volume: str) -> str:
    """Giữ nội dung và chữ nhỏ; chỉ bỏ số trang và đầu trang lặp rõ ràng."""
    text = clean_unicode_artifacts(text)
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if re.fullmatch(r"\d{1,4}", line):
            continue
        folded = line.casefold()
        # Các đầu trang này là phần lặp của bố cục, không phải nội dung. Bỏ theo
        # tiền tố rõ ràng, không dựa vào cỡ chữ để khỏi làm rơi chú thích nhỏ.
        if len(line) <= 120 and any(folded.startswith(head) for head in _RUNNING_HEADS):
            continue
        if volume == "pts2" and len(line) <= 120 and re.match(
            r"^phẩm .+ - giảng", folded
        ):
            continue
        # Đầu trang lặp của riêng `ttpv_03_Pc_II.pdf`, ví dụ
        # "Phân Tích Giới Tỳ Khưu Ni Điều saṅghādisesa 03". Đây không phải nội
        # dung điều học và thường rơi đúng giữa hai đoạn khi ghép nhiều trang.
        if volume == "pc2" and len(line) <= 120 and re.match(
            r"^phân tích giới tỳ khưu\s+n\s*i\b", folded
        ) and "được chấm dứt" not in folded:
            continue
        # Đầu trang lặp của riêng `ttpv_06_Cv_I.pdf`, ví dụ
        # "Tạng Luật - Tiểu Phẩm 1 Chương ...". Tên chương ở đây chỉ là running
        # head của trang in, không thuộc đoạn đang đọc và không được chen vào giữa
        # bản dịch của một reader segment.
        if volume == "cv1" and len(line) <= 120 and folded.startswith(
            "tạng luật - tiểu phẩm 1"
        ):
            continue
        if volume == "cv2" and len(line) <= 120 and folded.startswith(
            "tạng luật - tiểu phẩm 2"
        ):
            continue
        if volume == "pr" and len(line) <= 120 and folded.startswith(
            "phân tích giới tỳ khưu 1"
        ):
            continue
        if volume == "mv1" and len(line) <= 120 and folded.startswith(
            "tạng luật - đại phẩm 1"
        ):
            continue
        if volume == "mv2" and len(line) <= 120 and folded.startswith(
            "tạng luật - đại phẩm 2"
        ):
            continue
        if volume == "mil" and len(line) <= 120 and folded.startswith(
            ("tiểu bộ kinh - milinda vấn đạo", "khuddakanikāye milindapañhapāḷi")
        ):
            continue
        if (
            volume == "net"
            and len(line) <= 120
            and folded.startswith("cẩm nang học phật")
            and "được đầy đủ" not in folded
        ):
            continue
        # Đầu trang lẻ của bộ Trường Bộ in "6. Kinh Khơi Dậy Niềm Tin (29)" - số bài kèm
        # số thứ tự toàn tạng trong ngoặc. Cả ba tập in y hệt nhau, đã thấy tận mắt khi
        # đối chiếu PDF: bỏ sót nó thì mảnh "(29)" dính vào giữa câu và cắt đứt mạch văn.
        if volume in ("dn1", "dn2", "dn3") and len(line) <= 120 and re.match(
            r"^\d+\.\s+.+\(\d+\)\s*$", line
        ):
            continue
        lines.append(line)

    # Gom dòng trong cùng đoạn, nhưng giữ các dòng trắng của PDF làm ranh giới đoạn.
    paragraphs: list[str] = []
    current: list[str] = []
    for line in lines:
        if line:
            current.append(line)
            continue
        if current:
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))
    return mend_spacing("\n\n".join(paragraphs)).strip()


def _slug(index: int, title: str) -> str:
    stem = normalize_pali(title)
    stem = re.sub(r"[^a-z0-9]+", "_", stem).strip("_")
    return f"{index:03d}_{stem[:70] or 'unit'}.txt"


def extract_preview(volume: str, output_root: Path = OUTPUT_ROOT) -> tuple[list[Preview], Path]:
    if volume not in SUPPORTED_VOLUMES:
        raise ValueError(f"Bộ chưa hỗ trợ: {volume}")
    config = VOLUMES[volume]
    units = load_units(volume)
    if not units:
        raise RuntimeError(f"Không tìm thấy đơn vị đọc trong DB cho {volume}")

    pdf_path = download(config)
    reader = PdfReader(str(pdf_path))
    pages = [page.extract_text() or "" for page in reader.pages]
    page_is_vietnamese = [is_vietnamese(text) for text in pages]
    cv1_starts = (
        find_cv1_vietnamese_unit_starts(units, pages, page_is_vietnamese)
        if volume == "cv1"
        else {}
    )
    cv2_starts = (
        find_cv2_vietnamese_unit_starts(units, pages, page_is_vietnamese)
        if volume == "cv2"
        else {}
    )
    pc2_starts = (
        find_pc2_vietnamese_unit_starts(units, pages, page_is_vietnamese)
        if volume == "pc2"
        else {}
    )
    deepest_starts = (
        find_deepest_vietnamese_unit_starts(
            volume, units, pages, page_is_vietnamese
        )
        if volume in DEEPEST_ALIGNED_SPECS
        else {}
    )
    if volume == "cv1":
        headings = {
            unit_key: start.pali_hit for unit_key, start in cv1_starts.items()
        }
    elif volume == "cv2":
        headings = {
            unit_key: start.pali_hit for unit_key, start in cv2_starts.items()
        }
    elif volume == "pc2":
        headings = {
            section_id: start.pali_hit for section_id, start in pc2_starts.items()
        }
    elif volume in DEEPEST_ALIGNED_SPECS:
        headings = {
            unit_key: start.pali_hit for unit_key, start in deepest_starts.items()
        }
    else:
        headings = find_headings(volume, units, pages, page_is_vietnamese)
    heading_pages = sorted({hit.page for hit in headings.values()})
    if not heading_pages:
        raise RuntimeError(f"Không dò được tiêu đề nào trong PDF {config['file']}")
    if volume == "cv1":
        audit_end = CV1_APPENDIX_FIRST_PAGE
    elif volume == "cv2":
        audit_end = CV2_BODY_END_EXCLUSIVE
    elif volume == "pc2":
        audit_end = PC2_APPENDIX_FIRST_PAGE
    elif volume in DEEPEST_ALIGNED_SPECS:
        audit_end = int(
            DEEPEST_ALIGNED_SPECS[volume]["body_end_exclusive"]
        )
    else:
        audit_end, _audit_end_source = find_last_boundary(
            volume,
            heading_pages[-1],
            pages,
            page_is_vietnamese,
            (heading_stem(units[-1].title, volume), re.sub(r"[^a-z0-9]+", "", section_stem(units[-1].title))),
        )
    if audit_end is None:
        audit_end = min(len(pages) + 1, heading_pages[-1] + 2)
    audit_pages = [
        page_number
        for page_number in range(heading_pages[0] + 1, audit_end)
        if page_is_vietnamese[page_number - 1]
    ]
    page_audits, page_overrides = audit_vietnamese_page_text(
        pdf_path, pages, audit_pages
    )
    for page_number, replacement in page_overrides.items():
        pages[page_number - 1] = replacement
    if page_overrides and volume in DEEPEST_ALIGNED_SPECS:
        # Offset phải được tính trên đúng engine chữ cuối cùng sẽ dùng để cắt.
        deepest_starts = find_deepest_vietnamese_unit_starts(
            volume, units, pages, page_is_vietnamese
        )
        headings = {
            unit_key: start.pali_hit for unit_key, start in deepest_starts.items()
        }
    vietnamese_cuts: dict[str, tuple[int, int, str]] = {
        unit_key: (
            start.vietnamese_page,
            start.vietnamese_offset,
            start.vietnamese_line,
        )
        for unit_key, start in cv1_starts.items()
    }
    vietnamese_cuts.update({
        unit_key: (
            start.vietnamese_page,
            start.vietnamese_offset,
            start.vietnamese_line,
        )
        for unit_key, start in cv2_starts.items()
    })
    vietnamese_cuts.update({
        section_id: (
            start.vietnamese_page,
            start.vietnamese_offset,
            start.vietnamese_line,
        )
        for section_id, start in pc2_starts.items()
    })
    vietnamese_cuts.update({
        unit_key: (
            start.vietnamese_page,
            start.vietnamese_offset,
            start.vietnamese_line,
        )
        for unit_key, start in deepest_starts.items()
    })
    if volume not in ("cv1", "cv2", "pc2") and volume not in DEEPEST_ALIGNED_SPECS:
        for unit in units:
            hit = headings.get(unit.section_id)
            if not hit:
                continue
            viet_page = hit.page + 1
            if viet_page > len(pages) or not page_is_vietnamese[viet_page - 1]:
                continue
            cut = find_vietnamese_heading_offset(volume, unit, hit, pages[viet_page - 1])
            if cut:
                vietnamese_cuts[unit.section_id] = (viet_page, cut[0], cut[1])

    volume_dir = output_root / volume
    # Do not remove the whole preview directory before a rerun.  On Windows an
    # editor, antivirus scanner, or search indexer can temporarily hold a TXT
    # file without marking it read-only, making shutil.rmtree fail with
    # WinError 5.  Every file referenced by the new manifest is overwritten
    # below, so keeping the directory is both safe and rerun-friendly.
    volume_dir.mkdir(parents=True, exist_ok=True)

    previews: list[Preview] = []
    # Trạng thái đã dựng được của đơn vị LIỀN TRƯỚC - dùng làm điểm BẮT ĐẦU khi đơn vị
    # hiện tại không có tiêu đề mở đầu riêng nhưng sách đóng khung nó bằng mốc kết thúc
    # kiểu "Dứt Phần"/`niṭṭhitaṃ`. Xem hai nhánh "dò lùi" bên dưới.
    carry_end: tuple[int, int, int] | None = None  # (trang Pāli, trang Việt, offset Việt)
    for index, unit in enumerate(units, start=1):
        unit_key = _preview_unit_key(unit)
        hit = headings.get(unit_key)
        next_hit = headings.get(_preview_unit_key(units[index])) if index < len(units) else None
        viet_cut = vietnamese_cuts.get(unit_key)
        next_viet_cut = (
            vietnamese_cuts.get(_preview_unit_key(units[index])) if index < len(units) else None
        )
        boundary_page: int | None = None
        boundary_source: str | None = None
        boundary_viet_page: int | None = None
        boundary_viet_offset: int | None = None
        problems: list[str] = []

        if volume == "cv1" and index == len(units):
            boundary_page = CV1_APPENDIX_FIRST_PAGE
            boundary_source = "cv1_body_end_before_appendix"
            boundary_viet_page = CV1_APPENDIX_FIRST_PAGE
            boundary_viet_offset = 0
        elif volume == "cv2" and index == len(units):
            # Trang 637 là trang Việt cuối; 638 trống, 639 đã mở phần phụ chú.
            boundary_page = CV2_BODY_END_EXCLUSIVE
            boundary_source = "cv2_body_end_before_appendix"
            boundary_viet_page = CV2_BODY_END_EXCLUSIVE
            boundary_viet_offset = 0
        elif volume == "pc2" and index == len(units):
            # Trang 424 kết thúc thân Bhikkhunīvibhaṅga; trang 425 mở Phần Phụ Chú.
            boundary_page = PC2_APPENDIX_FIRST_PAGE
            boundary_source = "pc2_body_end_before_appendix"
            boundary_viet_page = PC2_APPENDIX_FIRST_PAGE
            boundary_viet_offset = 0
        elif volume in DEEPEST_ALIGNED_SPECS and index == len(units):
            boundary_page = int(
                DEEPEST_ALIGNED_SPECS[volume]["body_end_exclusive"]
            )
            boundary_source = f"{volume}_body_end_before_appendix"
            boundary_viet_page = boundary_page
            boundary_viet_offset = 0

        # DÒ LÙI #1 - đơn vị không có tiêu đề MỞ ĐẦU riêng. Một số điều học không được in
        # tiêu đề đậm ở đầu, chỉ có dòng ĐÓNG "X niṭṭhitaṃ." ở cuối - cùng sách in cả hai
        # kiểu tuỳ chương (`Nissaggiya` có tiêu đề đầy đủ, `Saṅghādisesa` một số điều học
        # thì không). Khi tiêu đề riêng không tìm được nhưng đơn vị TRƯỚC đã dựng xong
        # (`carry_end`), coi đơn vị này bắt đầu ngay sau chỗ đơn vị trước kết thúc, rồi
        # tìm CHÍNH mốc kết thúc của nó bằng `find_last_boundary` - hàm vốn viết cho đơn
        # vị cuối tập, dùng lại nguyên vẹn vì cùng một việc: tìm dòng "tên đơn vị + niṭṭhita".
        #
        # CHỈ nhận mốc GỌI ĐÚNG TÊN (`marker_source == "printed_end_marker"`), không nhận
        # nhánh dự phòng "mốc đầu tiên không tên" của `find_last_boundary` - nhánh đó được
        # thiết kế cho đơn vị CUỐI TẬP, nơi không còn ứng viên nào khác để so; ở giữa tập
        # nhận một mốc không tên có thể là mốc của một đơn vị KHÁC, nuốt oan nội dung.
        if not hit and carry_end is not None:
            marker_page, marker_source = find_last_boundary(
                volume, carry_end[0], pages, page_is_vietnamese, (heading_stem(unit.title, volume), re.sub(r"[^a-z0-9]+", "", section_stem(unit.title)))
            )
            if marker_page is not None and marker_source == "printed_end_marker":
                hit = HeadingHit(
                    page=carry_end[0],
                    line='(nối tiếp từ đơn vị trước, dò bằng mốc "Dứt Phần")',
                    score=1.0,
                )
                viet_cut = (carry_end[1], carry_end[2], "")
                boundary_page = marker_page
                boundary_source = "closing_marker_backfill"
                boundary_viet_page = marker_page
                boundary_viet_offset = 0

        if not hit:
            problems.append("không dò được tiêu đề duy nhất trong PDF")
        elif not viet_cut:
            problems.append("không dò được dòng tiêu đề Việt trên trang đối diện")

        if boundary_page is not None:
            pass  # DÒ LÙI #1 đã cho đủ điểm kết thúc - khỏi cần next_hit nữa
        elif index < len(units):
            if not next_hit:
                # DÒ LÙI #2 - trước khi bó tay vì đơn vị KẾ TIẾP thiếu tiêu đề, thử chính
                # mốc kết thúc của ĐƠN VỊ NÀY. Đây là lý do "4. Catutthapārājikaṃ" từng
                # REVIEW dù bản thân nó có đủ tiêu đề mở đầu: lỗi chỉ vì đơn vị số 5 thiếu
                # tiêu đề, không phải vì đơn vị này thiếu gì - không nên bắt một đơn vị
                # lành REVIEW theo lỗi của đơn vị khác.
                # `hit.score >= EXACT_HEADING_SCORE` bắt buộc, không phải thận trọng thừa:
                # đo tay ca thật ở `pr` - "10. Saṅghabhedasikkhāpadaṃ" khớp mờ 0.88 vào
                # tiêu đề in của điều **11** thay vì tiêu đề thật của nó, nên một khớp mờ
                # SAI biến thành PASS mang nội dung mục #11 dán nhãn mục #10 - im lặng và
                # sai, còn tệ hơn REVIEW. Nguyên nhân gốc nay đã chữa bằng alias tên in;
                # guard giữ lại làm lưới cho các tập chưa khai alias.
                if hit and hit.score >= EXACT_HEADING_SCORE:
                    marker_page, marker_source = find_last_boundary(
                        volume, hit.page, pages, page_is_vietnamese, (heading_stem(unit.title, volume), re.sub(r"[^a-z0-9]+", "", section_stem(unit.title)))
                    )
                    if marker_page is not None and marker_source == "printed_end_marker":
                        boundary_page = marker_page
                        boundary_source = "own_closing_marker"
                        boundary_viet_page = marker_page
                        boundary_viet_offset = 0
                if boundary_page is None:
                    problems.append("thiếu tiêu đề của đơn vị kế tiếp nên chưa biết điểm kết thúc")
            elif not next_viet_cut:
                problems.append("thiếu dòng tiêu đề Việt của đơn vị kế tiếp")
            else:
                boundary_page = next_hit.page
                boundary_source = "next_unit_title"
                boundary_viet_page, boundary_viet_offset, _line = next_viet_cut
        elif hit:
            boundary_page, boundary_source = find_last_boundary(
                volume, hit.page, pages, page_is_vietnamese, (heading_stem(unit.title, volume), re.sub(r"[^a-z0-9]+", "", section_stem(unit.title)))
            )
            if boundary_page is None:
                problems.append("không dò được dấu kết thúc tập")
            else:
                boundary_viet_page = boundary_page
                boundary_viet_offset = 0

        # Bàn giao điểm kết thúc cho đơn vị KẾ TIẾP dùng ở DÒ LÙI #1, nếu vòng lặp sau
        # cũng thiếu tiêu đề mở đầu. Không bàn giao khi đơn vị này không xác định được
        # biên gì cả - trạng thái không chắc chắn thì không nên làm điểm neo cho đơn vị
        # sau.
        carry_end = (
            (boundary_page, boundary_viet_page, boundary_viet_offset or 0)
            if boundary_viet_page is not None
            else None
        )

        vietnamese_pages: list[int] = []
        paired_ratio = 0.0
        text = ""
        if hit and viet_cut and boundary_page and boundary_viet_page is not None:
            start_viet_page, start_viet_offset, _start_line = viet_cut
            if (boundary_viet_page, boundary_viet_offset or 0) <= (
                start_viet_page,
                start_viet_offset,
            ):
                problems.append("ranh giới dòng/trang không tăng")
            else:
                # Nếu tiêu đề kế tiếp nằm giữa trang, phần trước tiêu đề trên chính
                # trang ấy vẫn thuộc đơn vị hiện tại. Vì vậy phải lấy cả trang Pāli
                # biên và cắt trang Việt đối diện tại đúng offset tiêu đề.
                pali_stop = boundary_page + (1 if (boundary_viet_offset or 0) > 0 else 0)
                pali_pages = [
                    page_number
                    for page_number in range(hit.page, max(hit.page + 1, pali_stop))
                    if not page_is_vietnamese[page_number - 1]
                ]
                paired = 0
                for pali_page in pali_pages:
                    viet_page = pali_page + 1
                    if viet_page <= boundary_viet_page and page_is_vietnamese[viet_page - 1]:
                        paired += 1
                        vietnamese_pages.append(viet_page)
                paired_ratio = paired / max(1, len(pali_pages))
                if paired_ratio < (0.70 if volume == 'net' else 0.95):
                    problems.append(f"chỉ {paired_ratio:.0%} trang Pāli có trang Việt đi kèm")
                vietnamese_pages = sorted(set(vietnamese_pages))
                chunks: list[str] = []
                for page in vietnamese_pages:
                    begin = start_viet_offset if page == start_viet_page else 0
                    end = (
                        boundary_viet_offset
                        if page == boundary_viet_page and boundary_viet_offset is not None
                        else len(pages[page - 1])
                    )
                    if end <= begin:
                        continue
                    chunk = clean_vietnamese_page(pages[page - 1][begin:end], volume)
                    if chunk:
                        chunks.append(chunk)
                text = "\n\n".join(chunks).strip()
                # Nettippakaraṇa có section cấu trúc một-passage Niddesavāro
                # chỉ gồm đúng hai câu (76 ký tự) giữa hai mốc song ngữ chắc.
                if len(text) < (50 if volume == "net" else 120):
                    problems.append("văn bản Việt quá ngắn")

        small_text_pages = [
            page for page in vietnamese_pages if page_audits.get(page) and page_audits[page].small_words
        ]
        fallback_text_pages = [
            page
            for page in vietnamese_pages
            if page_audits.get(page)
            and page_audits[page].selected_engine == "pdfplumber_fallback"
        ]
        text_audit_review_pages = [
            page
            for page in vietnamese_pages
            if page_audits.get(page) and page_audits[page].status != "PASS"
        ]
        if text_audit_review_pages:
            problems.append(
                "trang chưa đạt kiểm toán chữ: "
                + ", ".join(str(page) for page in text_audit_review_pages)
            )

        status = "PASS" if not problems else "REVIEW"
        output_file: str | None = None
        if text:
            output_path = volume_dir / _slug(index, unit.title)
            output_path.write_text(text + "\n", encoding="utf-8")
            output_file = output_path.name
        compact_text = re.sub(r"\s+", " ", text).strip()
        previews.append(
            Preview(
                status=status,
                reason="; ".join(problems) if problems else "đủ tiêu đề đầu/cuối và cặp trang Pāli-Việt",
                title=unit.title,
                section_id=unit.section_id,
                document_id=unit.document_id,
                start_sort_order=unit.start_sort_order,
                end_sort_order=unit.end_sort_order,
                pali_start_page=hit.page if hit else None,
                vietnamese_start_page=viet_cut[0] if viet_cut else None,
                boundary_page_exclusive=boundary_page,
                boundary_source=boundary_source,
                title_match_score=round(hit.score, 4) if hit else None,
                title_line=hit.line if hit else None,
                vietnamese_pages=vietnamese_pages,
                small_text_pages=small_text_pages,
                fallback_text_pages=fallback_text_pages,
                text_audit_review_pages=text_audit_review_pages,
                paired_page_ratio=round(paired_ratio, 4),
                text_characters=len(text),
                text_sha256=(hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None),
                output_file=output_file,
                first_text=compact_text[:240],
                last_text=compact_text[-240:],
            )
        )

    manifest = {
        "schema_version": 1,
        "mode": "preview_only_no_database_writes",
        "volume": volume,
        "label": config["label"],
        "source_pdf": str(pdf_path.resolve()),
        "source_pdf_sha256": _sha256_file(pdf_path),
        "total_units": len(previews),
        "pass_units": sum(item.status == "PASS" for item in previews),
        "review_units": sum(item.status != "PASS" for item in previews),
        "audited_vietnamese_pages": len(page_audits),
        "small_text_pages": sum(bool(item.small_words) for item in page_audits.values()),
        "fallback_text_pages": sum(
            item.selected_engine == "pdfplumber_fallback" for item in page_audits.values()
        ),
        "review_text_pages": sum(item.status != "PASS" for item in page_audits.values()),
        "page_text_audit": [asdict(page_audits[page]) for page in sorted(page_audits)],
        "items": [asdict(item) for item in previews],
    }
    manifest_path = volume_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return previews, manifest_path


def run_cli(volume: str) -> None:
    parser = argparse.ArgumentParser(
        description=f"Tách thử bản Indacanda trọn đơn vị cho {volume}; không ghi DB."
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=OUTPUT_ROOT,
        help="thư mục chứa manifest và các tệp TXT xem thử",
    )
    args = parser.parse_args()
    previews, manifest_path = extract_preview(volume, args.output_root)
    passed = sum(item.status == "PASS" for item in previews)
    print(f"=== {VOLUMES[volume]['label']} ===")
    print(f"PASS {passed}/{len(previews)} | REVIEW {len(previews) - passed}")
    for item in previews:
        page_range = (
            f"trang Pāli {item.pali_start_page} -> trước {item.boundary_page_exclusive}"
            if item.pali_start_page and item.boundary_page_exclusive
            else "chưa xác định đủ ranh giới"
        )
        print(
            f"  [{item.status:<6}] {item.title} | {page_range} | "
            f"{len(item.vietnamese_pages)} trang Việt | {item.text_characters:,} ký tự"
        )
        if item.status != "PASS":
            print(f"           {item.reason}")
    print(f"\nManifest: {manifest_path.resolve()}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    print(
        "Kiểm toán chữ: "
        f"{manifest['audited_vietnamese_pages']} trang Việt | "
        f"{manifest['small_text_pages']} trang có chữ nhỏ | "
        f"{manifest['fallback_text_pages']} trang dùng fallback | "
        f"{manifest['review_text_pages']} trang REVIEW"
    )
    print("Chế độ xem thử: KHÔNG có INSERT/UPDATE/DELETE DB.")
