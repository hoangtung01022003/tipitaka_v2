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

SUPPORTED_VOLUMES = ("sn", "dn1", "dn2", "dn3", "pts2", "pr", "pc1", "pc2", "kn1", "mv1", "mv2", "cv1", "cv2", "thag", "vvpv", "ja1", "ja2", "ja3", "bvcp")
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


@dataclass(frozen=True)
class HeadingHit:
    page: int
    line: str
    score: float


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
    }
    if volume == "sn" and stem.endswith("manavapuccha"):
        # DB gọi các mục phẩm Pārāyana là “câu hỏi của ...”, sách in gọi là “kinh ...”.
        stem = stem[: -len("manavapuccha")] + "suttam"
    return aliases.get((volume, stem), stem)


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
    if volume == "pts2":
        # PDF tập II bắt đầu từ Yuganaddhakathā (sort 1167); các Kathā trước thuộc tập I.
        return level == 5 and start >= 1167 and heading_stem(
            str(row["title"]), volume
        ).endswith("katha")
    if volume in ("mv1", "mv2", "cv1", "cv2"):
        # Đại Phẩm và Tiểu Phẩm chia theo các Khandhaka (Chương). 
        # Cấu trúc DB đặt Khandhaka ở cấp độ 3.
        return level == 3 and heading_stem(str(row["title"]), volume).endswith(
            ("khandhako", "khandhakam")
        )
    if volume in ("pr", "pc1", "pc2"):
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


def load_units(volume: str) -> list[Unit]:
    config = VOLUMES[volume]
    docs = fetch_all(
        "select id, file_name from documents where file_name = any(%s)",
        [config["docs"]],
    )
    doc_ids = [str(row["id"]) for row in docs]
    if not doc_ids:
        raise RuntimeError(f"Không tìm thấy tài liệu DB cho {volume}: {config['docs']}")
    rows = fetch_all(
        """
        select s.id, s.document_id, d.file_name, s.title, s.level, s.source_path,
               s.start_sort_order, s.end_sort_order
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
        if volume not in ("thag", "vvpv", "ja1", "ja2", "ja3", "bvcp"):
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
            continue
        found[unit.section_id] = max(pool, key=lambda hit: hit.score)
    return found


def find_last_boundary(
    volume: str,
    start_page: int,
    pages: list[str],
    page_is_vietnamese: list[bool],
    unit_stem: str | None = None,
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
    if volume == "pts2":
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
        if unit_stem and any(
            "nitthit" in line and unit_stem in line
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
        if volume not in ("thag", "vvpv", "ja1", "ja2", "ja3", "bvcp"):
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
    headings = find_headings(volume, units, pages, page_is_vietnamese)
    heading_pages = sorted(hit.page for hit in headings.values())
    if not heading_pages:
        raise RuntimeError(f"Không dò được tiêu đề nào trong PDF {config['file']}")
    audit_end, _audit_end_source = find_last_boundary(
        volume,
        heading_pages[-1],
        pages,
        page_is_vietnamese,
        heading_stem(units[-1].title, volume),
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
    vietnamese_cuts: dict[str, tuple[int, int, str]] = {}
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
        hit = headings.get(unit.section_id)
        next_hit = headings.get(units[index].section_id) if index < len(units) else None
        viet_cut = vietnamese_cuts.get(unit.section_id)
        next_viet_cut = (
            vietnamese_cuts.get(units[index].section_id) if index < len(units) else None
        )
        boundary_page: int | None = None
        boundary_source: str | None = None
        boundary_viet_page: int | None = None
        boundary_viet_offset: int | None = None
        problems: list[str] = []

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
                volume, carry_end[0], pages, page_is_vietnamese, heading_stem(unit.title, volume)
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
                        volume, hit.page, pages, page_is_vietnamese, heading_stem(unit.title, volume)
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
                volume, hit.page, pages, page_is_vietnamese, heading_stem(unit.title, volume)
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
                if paired_ratio < 0.95:
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
                if len(text) < 120:
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
