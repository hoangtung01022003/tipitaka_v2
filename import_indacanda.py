"""Nạp bản dịch tiếng Việt của Tỳ Khưu Indacanda (Tam Tạng Song Ngữ Pāli - Việt).

NGUỒN: tamtangpaliviet.net. Khách KHÔNG gửi file nào của bản này; đây là nguồn công khai
do chính nhóm dịch phát hành, phủ đúng phần đang trống: Tạng Luật và Tiểu Bộ
(Ngài Sujato không dịch, SuttaCentral cũng thiếu tiếng Việt).

CÁCH GHÉP - đây là nguồn tiếng Việt ghép được CHÍNH XÁC NHẤT
-------------------------------------------------------------
Sách in song ngữ theo trang đối diện, và đã kiểm tận mắt trên file thật:

    trang 44  PALI  câu 6,7,8,9,10,11   "Suttanipāte Uragavaggo | Uragasuttaṃ"
    trang 45  VIỆT  câu 6,7,8,9,10,11   "Kinh Tập - Phẩm Rắn    | Kinh Rắn"

Trang chẵn Pali, trang lẻ Việt, CÙNG số câu kệ. Nhờ vậy không phải đoán theo thứ tự:
- ghép cặp trang Pali <-> trang Việt kề nhau,
- trong cặp đó ghép câu kệ theo SỐ,
- rồi tìm câu Pali đó trong DB bằng chính nội dung (giống cách làm với bản Sujato).

Khác hẳn bản Minh Châu (chỉ ghép được cấp bài kinh) và bản Tịnh Sự (số đoạn lệch 19
so với bản gốc vì dịch từ ấn bản Thái).

Chạy:
    python import_indacanda.py sn          # Kinh Tập
    python import_indacanda.py --list      # xem các tập đã cấu hình
    python import_indacanda.py sn --dry-run --verbose
"""

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime
import json
import urllib.parse
import logging
import re
import sys
import urllib.request
import warnings
from pathlib import Path

# line_buffering: chay nap mat vai phut moi tap, khong bat buoc phai xem qua terminal.
# De mac dinh thi Python dem stdout khi output khong phai terminal (ghi ra file, chay nen),
# nen nhin vao chi thay file rong va tuong la treo.
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
warnings.filterwarnings("ignore")
logging.getLogger("pypdf").setLevel(logging.CRITICAL)

from pypdf import PdfReader

from app.db import execute, fetch_all
from app.normalize import normalize_pali
from app.text_artifacts import clean_unicode_artifacts
# Quy hoạch động ghép theo thứ tự, dùng chung với hai importer kia. Chỉ nhánh
# `--global-align` cần tới, nhưng import ở đây cho lỗi thiếu lộ ra ngay lúc nạp module
# chứ không phải giữa chừng lần chạy vài tiếng.
from import_sujato import align_globally

BASE = "https://www.tamtangpaliviet.net/TTPV"
CACHE = Path(__file__).resolve().parent / ".indacanda_pdf"
SOURCE_ID = "indacanda"
LANGUAGE = "vi"


def write_translation(passage_id: str, source: str, text: str, ref: str, method: str,
                      batch: str, score: float | None = None, document_id: str | None = None,
                      start: int | None = None, end: int | None = None) -> bool:
    """Ghi một dòng bản dịch, KHÔNG cho phương pháp yếu hơn đè lên phương pháp chắc hơn.

    Trả về True nếu DB THẬT SỰ đổi. Phải trả về, không được đếm số lần gọi: đợt
    `global_align` đầu tiên trên tập `net` gọi 338 lần mà bị chặn cả 338, log vẫn in
    "ghi 338" trong khi không dòng nào đổi - con số đẹp che mất kết luận thật là
    "đợt này không thêm được gì".

    Đây là chỗ khác hẳn cách cũ. Trước đây mọi importer dùng `on conflict do update` không
    điều kiện, nghĩa là đợt nạp SAU luôn thắng đợt trước bất kể ghép ẩu hơn - chạy strict
    rồi chạy global-align là global-align xoá sạch phần strict, mất đúng phần chắc nhất.

    Nhờ mệnh đề `where` ở cuối, nạp nhiều đợt trở thành cộng dồn: đợt sau chỉ lấp chỗ
    trống và nâng cấp chỗ nào phương pháp của nó đáng tin hơn, còn lại giữ nguyên. Nhờ vậy
    có thể chạy từng nhóm ca trong nhiều ngày mà không sợ hỏng phần đã xong.

    Bảng xếp hạng nằm ở `human_translation_method_rank` trong migration 004.
    """
    return bool(fetch_all(
        """
        insert into human_translations
          (passage_id, source, language, translated_text, source_ref, segment_ids,
           document_id, start_sort_order, end_sort_order,
           match_method, match_score, import_batch)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (passage_id, source) do update
          set translated_text = excluded.translated_text,
              source_ref = excluded.source_ref,
              document_id = excluded.document_id,
              start_sort_order = excluded.start_sort_order,
              end_sort_order = excluded.end_sort_order,
              match_method = excluded.match_method,
              match_score = excluded.match_score,
              import_batch = excluded.import_batch,
              updated_at = now()
          where human_translation_method_rank(excluded.match_method)
                > human_translation_method_rank(human_translations.match_method)
             or (
                  human_translation_method_rank(excluded.match_method)
                    = human_translation_method_rank(human_translations.match_method)
                  and human_translations.translated_text is distinct from excluded.translated_text
                )
        returning id
        """,
        [passage_id, source, LANGUAGE, text, ref, [], document_id, start, end,
         method, score, batch],
    ))


def stage_unresolved_pairs(
    scope: str,
    aligned: list[tuple[str, str, str | None]],
    resolved_indexes: set[int],
    batch: str,
) -> int:
    """Lưu các cặp PDF chưa ghép chắc chắn để đợt khác hoặc người duyệt xử lý."""
    rows = [
        {
            "source_ref": scope,
            "segment_id": f"pair-{index}",
            "raw_text": viet,
            "normalized_text": normalize_pali(pali),
            "reason": "ambiguous_or_unmatched_pdf_pair",
        }
        for index, (pali, viet, _section_id) in enumerate(aligned)
        if index not in resolved_indexes
    ]
    if not rows:
        return 0
    execute(
        """
        insert into human_translation_unresolved
          (source, language, scope, source_ref, segment_id, raw_text,
           normalized_text, reason, import_batch)
        select %s, %s, %s, item.source_ref, item.segment_id, item.raw_text,
               item.normalized_text, item.reason, %s
        from jsonb_to_recordset(%s::jsonb) as item(
          source_ref text, segment_id text, raw_text text, normalized_text text, reason text
        )
        on conflict do nothing
        """,
        [SOURCE_ID, LANGUAGE, scope, batch, json.dumps(rows, ensure_ascii=False)],
    )
    return len(rows)


def stage_unbalanced_indented_pages(
    scope: str,
    pages: list[dict],
    batch: str,
) -> int:
    """Giữ nguyên cặp trang không cân đoạn để lần sau xử lý, không làm rơi dữ liệu."""
    rows = [
        {
            "source_ref": scope,
            "segment_id": f"pdf-pages-{page['pali_page']}-{page['viet_page']}",
            "raw_text": page["viet_text"],
            "normalized_text": normalize_pali(page["pali_text"]),
            "reason": "unbalanced_indented_pdf_pages",
        }
        for page in pages
    ]
    if not rows:
        return 0
    execute(
        """
        insert into human_translation_unresolved
          (source, language, scope, source_ref, segment_id, raw_text,
           normalized_text, reason, import_batch)
        select %s, %s, %s, item.source_ref, item.segment_id, item.raw_text,
               item.normalized_text, item.reason, %s
        from jsonb_to_recordset(%s::jsonb) as item(
          source_ref text, segment_id text, raw_text text, normalized_text text, reason text
        )
        on conflict do nothing
        """,
        [SOURCE_ID, LANGUAGE, scope, batch, json.dumps(rows, ensure_ascii=False)],
    )
    return len(rows)


def remove_stale_whole_sutta_rows(
    passage_id: str,
    source: str,
    document_id: str,
    start: int,
    end: int,
    batch: str,
) -> int:
    """Một nguồn chỉ được có một bản cho cùng khoảng cả bài.

    Khi sửa được đoạn mở đầu, neo của Mahāpadāna đổi từ passage 5 về passage 4.
    Khóa `(passage_id, source)` không coi đó là xung đột, nên bản thiếu cũ còn nằm
    chồng lên bản đủ mới. Lưu bản cũ vào lịch sử rồi mới xóa để trang đọc không
    chọn ngẫu nhiên giữa hai phiên bản.
    """
    return len(fetch_all(
        """
        with stale as (
          select *
          from human_translations
          where source = %s
            and document_id = %s
            and start_sort_order = %s
            and end_sort_order = %s
            and passage_id <> %s
          for update
        ), archived as (
          insert into human_translation_history (
            human_translation_id, passage_id, source, language, translated_text,
            source_ref, segment_ids, document_id, start_sort_order, end_sort_order,
            match_method, match_score, import_batch, replaced_by_batch
          )
          select id, passage_id, source, language, translated_text, source_ref,
                 segment_ids, document_id, start_sort_order, end_sort_order,
                 match_method, match_score, import_batch, %s
          from stale
          returning human_translation_id
        )
        delete from human_translations current
        using archived
        where current.id = archived.human_translation_id
        returning current.id
        """,
        [source, document_id, start, end, passage_id, batch],
    ))


VIETNAMESE_CHARS = set("àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ")

# Tap nao ghep vao nhung tai lieu nao trong DB. Chi cau hinh nhung tap da doi chieu duoc.
VOLUMES: dict[str, dict] = {
    "sn": {"file": "29_Sn.pdf", "label": "Kinh Tập (Suttanipāta)", "docs": ["s0505m.mul.xml"]},
    "kn1": {"file": "28_Khp-Dh-Ud-It.pdf", "label": "Tiểu Tụng, Pháp Cú, Phật Tự Thuyết, Như Vậy",
            "docs": ["s0501m.mul.xml", "s0502m.mul.xml", "s0503m.mul.xml", "s0504m.mul.xml"]},
    "thag": {"file": "31_Thag_Thig.pdf", "label": "Trưởng Lão Kệ, Trưởng Lão Ni Kệ",
             "docs": ["s0508m.mul.xml", "s0509m.mul.xml"]},
    # TẠNG LUẬT - phần DB đang trống hoàn toàn: 12.957 đoạn, không có bản dịch tiếng Việt
    # nào (Sujato lẫn Minh Châu đều không dịch Luật). Đối chiếu tên mục trong DB để map:
    #   vin01m   Phân Tích Giới Bổn (Pārājika, Saṅghādisesa, Nissaggiya...)
    #   vin02m1  Pācittiya / Phân Tích Giới Tỳ Khưu Ni
    #   vin02m2  Đại Phẩm (Mahāvagga)
    #   vin02m3  Tiểu Phẩm (Cullavagga)
    #   vin02m4  Tập Yếu (Parivāra)
    # Chạy thử "pr" trước bằng --dry-run để biết tỉ lệ ghép thật của văn xuôi Luật, rồi
    # mới quyết có nạp 8 tập còn lại hay không.
    # Đã chạy thử "pr": 311/1461 = 21%, ngang mức Trường Bộ và vượt ngưỡng đáng nạp.
    "pr": {"file": "ttpv_01_Pr.pdf", "label": "Phân Tích Giới Bổn - Pārājika",
           "docs": ["vin01m.mul.xml"]},
    "pc1": {"file": "ttpv_02_Pc_I.pdf", "label": "Phân Tích Giới - Pācittiya I",
            "docs": ["vin02m1.mul.xml"]},
    "pc2": {"file": "ttpv_03_Pc_II.pdf", "label": "Phân Tích Giới - Pācittiya II",
            "docs": ["vin02m1.mul.xml"]},
    "mv1": {"file": "ttpv_04_Mv_I.pdf", "label": "Đại Phẩm - Mahāvagga I",
            "docs": ["vin02m2.mul.xml"]},
    "mv2": {"file": "ttpv_05_Mv_II.pdf", "label": "Đại Phẩm - Mahāvagga II",
            "docs": ["vin02m2.mul.xml"]},
    "cv1": {"file": "ttpv_06_Cv_I.pdf", "label": "Tiểu Phẩm - Cullavagga I",
            "docs": ["vin02m3.mul.xml"]},
    "cv2": {"file": "ttpv_07_Cv_II.pdf", "label": "Tiểu Phẩm - Cullavagga II",
            "docs": ["vin02m3.mul.xml"]},
    "par1": {"file": "ttpv_08_Par_I.pdf", "label": "Tập Yếu - Parivāra I",
             "docs": ["vin02m4.mul.xml"]},
    "par2": {"file": "ttpv_09_Par_II.pdf", "label": "Tập Yếu - Parivāra II",
             "docs": ["vin02m4.mul.xml"]},
    # TIỂU BỘ còn trống. Map lấy từ tiêu đề mục thật trong DB, không suy từ tên file:
    #   s0506m Thiên Cung Sự · s0507m Ngạ Quỷ Sự · s0510m1/m2 Thánh Nhân Ký Sự
    #   s0512m Hạnh Tạng · s0513m/s0514m Bổn Sanh · s0515m/s0516m Nghĩa Tích
    #   s0517m Phân Tích Đạo · s0518m Mi Tiên Vấn Đáp · s0519m Chỉ Đạo · s0520m Tạng Thích
    # Nhóm thơ kệ (Vv, Pv, Ap, Cp, Ja) đáng chạy TRƯỚC: đo trên Trưởng Lão Kệ và Kinh Tập
    # thì thơ kệ đạt 82-93%, còn văn xuôi chỉ 21%.
    "vvpv": {"file": "30_Vv_Pv.pdf", "label": "Thiên Cung Sự, Ngạ Quỷ Sự",
             "docs": ["s0506m.mul.xml", "s0507m.mul.xml"]},
    "ap1": {"file": "ttpv_39_Ap_I.pdf", "label": "Thánh Nhân Ký Sự I",
            "docs": ["s0510m1.mul.xml", "s0510m2.mul.xml"]},
    "ap2": {"file": "ttpv_40_Ap_II.pdf", "label": "Thánh Nhân Ký Sự II",
            "docs": ["s0510m1.mul.xml", "s0510m2.mul.xml"]},
    "ap3": {"file": "ttpv_41_Ap_III.pdf", "label": "Thánh Nhân Ký Sự III",
            "docs": ["s0510m1.mul.xml", "s0510m2.mul.xml"]},
    # Trang nguồn ghi nhãn là "Cp.pdf" nhưng href thật là "ttpv_42_Bv&Cp.pdf" - một tập
    # gộp Phật Sử và Hạnh Tạng, nên phủ được cả hai tài liệu.
    "bvcp": {"file": "ttpv_42_Bv&Cp.pdf", "label": "Phật Sử, Hạnh Tạng",
             "docs": ["s0511m.mul.xml", "s0512m.mul.xml"]},
    "ja1": {"file": "32_Ja_I.pdf", "label": "Bổn Sanh I",
            "docs": ["s0513m.mul.xml", "s0514m.mul.xml"]},
    "ja2": {"file": "33_Ja_II.pdf", "label": "Bổn Sanh II",
            "docs": ["s0513m.mul.xml", "s0514m.mul.xml"]},
    "ja3": {"file": "34_Ja_III.pdf", "label": "Bổn Sanh III",
            "docs": ["s0513m.mul.xml", "s0514m.mul.xml"]},
    # Văn xuôi Tiểu Bộ - tỉ lệ dự kiến thấp như Trường Bộ, chạy sau cùng.
    "nidd1": {"file": "35_Nidd_I.pdf", "label": "Đại Nghĩa Tích", "docs": ["s0515m.mul.xml"]},
    "nidd2": {"file": "36_Nidd_II.pdf", "label": "Tiểu Nghĩa Tích", "docs": ["s0516m.mul.xml"]},
    "pts1": {"file": "ttpv_37_Pts_I.pdf", "label": "Phân Tích Đạo I", "docs": ["s0517m.mul.xml"]},
    # Tập II không đánh số từng đoạn như 29 PDF còn lại. Thay vì nới `_VERSE` rồi
    # vô tình coi số TRANG là số đoạn, dùng bộ tách riêng dựa vào thụt đầu dòng của
    # sách song ngữ. Giới hạn trang loại hẳn lời nói đầu, phụ chú và thư mục cuối sách.
    "pts2": {
        "file": "ttpv_38_Pts_II.pdf",
        "label": "Phân Tích Đạo II",
        "docs": ["s0517m.mul.xml"],
        "parser": "paired_indented",
        "pdf_start_page": 36,
        "pdf_end_page": 303,
    },
    "net": {"file": "43_Net.pdf", "label": "Chỉ Đạo (Nettippakaraṇa)", "docs": ["s0519m.mul.xml"]},
    "pet": {"file": "ttpv_44_Pet.pdf", "label": "Tạng Thích (Peṭakopadesa)", "docs": ["s0520m.nrf.xml"]},
    "mil": {"file": "45_Mil.pdf", "label": "Mi Tiên Vấn Đáp", "docs": ["s0518m.nrf.xml"]},
    "dn1": {"file": "10_D_01.pdf", "label": "Trường Bộ tập 1", "docs": ["s0101m.mul.xml"]},
    "dn2": {"file": "11_D_02.pdf", "label": "Trường Bộ tập 2", "docs": ["s0102m.mul.xml"]},
    "dn3": {"file": "12_D_03.pdf", "label": "Trường Bộ tập 3", "docs": ["s0103m.mul.xml"]},
}

# So cau ke o dau dong. Sach dung ca "12." lan "12 ." tuy trang.
_VERSE = re.compile(r"^\s*(\d{1,4})\s*\.\s*", re.M)
# Do dai toi thieu de coi mot cau Pali la du dac trung ma di tim trong DB.
MIN_PALI_CHARS = 22
# Nguong giong nhau va khoang cach toi thieu voi ung vien thu hai.
SIMILARITY_MIN = 0.5
SIMILARITY_MARGIN = 0.1
# Chuoi do dai hon the nay thi trigram loang diem, cat bot truoc khi tra.
MAX_PROBE_CHARS = 200


def download(volume: dict) -> Path:
    CACHE.mkdir(exist_ok=True)
    target = CACHE / volume["file"]
    if target.exists() and target.stat().st_size > 10000:
        return target
    url = f"{BASE}/{urllib.parse.quote(volume['file'])}"
    print(f"  tải {volume['file']} ...")
    with urllib.request.urlopen(url, timeout=300) as response:
        target.write_bytes(response.read())
    return target


def is_vietnamese(text: str) -> bool:
    return sum(1 for ch in text.lower() if ch in VIETNAMESE_CHARS) > 30


def split_verses(text: str) -> dict[str, str]:
    """Tách các câu kệ đánh số trên một trang -> {số: nội dung}."""
    parts = _VERSE.split(text)
    verses: dict[str, str] = {}
    # parts = [phan dau, so, noi dung, so, noi dung, ...]
    for index in range(1, len(parts) - 1, 2):
        number = parts[index]
        body = re.sub(r"\s+", " ", parts[index + 1]).strip()
        if not body:
            continue
        if number not in verses:
            verses[number] = body
            continue

        # PDF có thể đánh cùng số cho tiêu đề và đoạn văn đầu tiên, ví dụ:
        #   1. MAHĀPADĀNASUTTAṂ
        #   1. Evaṃ me sutaṃ ...
        # Cách cũ giữ lần xuất hiện đầu nên làm mất hẳn đoạn 1. Chỉ thay một giá trị
        # đã có khi nó rõ ràng là tiêu đề ngắn, viết hoa; không chọn chuỗi dài nhất
        # vì chú thích cuối trang đôi lúc dài hơn nội dung chính.
        previous = verses[number]
        letters = [char for char in previous if char.isalpha()]
        uppercase_ratio = (
            sum(1 for char in letters if char.isupper()) / len(letters)
            if letters
            else 0.0
        )
        if len(previous) <= 100 and uppercase_ratio >= 0.8:
            verses[number] = body
    return verses


# Phân Tích Đạo II (pts2) là ngoại lệ có chủ ý: phần thân sách không đánh số từng
# đoạn, chỉ thụt dòng đầu đoạn khoảng 11-12 cột. Không được sửa `_VERSE` để nhận số
# trần vì số trần trong PDF này là SỐ TRANG; làm vậy sẽ tạo ra một cặp khổng lồ mỗi
# trang và gán sai dữ liệu.
INDENTED_PARAGRAPH_MIN = 8
INDENTED_PARAGRAPH_MAX = 16
INDENTED_MATCH_MIN = 0.65
INDENTED_QUERY_COVERAGE_MIN = 0.85
INDENTED_MATCH_MARGIN = 0.10


def _looks_like_centered_heading(text: str) -> bool:
    letters = [char for char in text if char.isalpha()]
    if not letters or len(text) >= 100:
        return False
    uppercase = sum(1 for char in letters if char.isupper()) / len(letters)
    return uppercase >= 0.72


def split_indented_paragraphs(layout_text: str) -> list[str]:
    """Tách đoạn văn theo thụt đầu dòng trong kết quả ``extraction_mode='layout'``.

    Pypdf giữ được cột bắt đầu của dòng dù đôi lúc chèn khoảng trắng rất dài giữa
    các glyph. Ta chỉ dùng khoảng trắng đầu dòng làm tín hiệu, còn khoảng trắng bên
    trong được thu gọn. Tiêu đề giữa trang, số trang và chú thích cuối trang bị loại.

    Hàm này cố tình nghiêm: trang nào cho số đoạn Pāli và Việt khác nhau sẽ bị tầng
    gọi bỏ cả cặp trang, chứ không ``zip`` rồi làm rơi âm thầm phần dư.
    """
    paragraphs: list[str] = []
    current: list[str] = []
    previous_was_blank = False

    def flush() -> None:
        nonlocal current
        value = re.sub(r"\s+", " ", " ".join(current)).strip()
        if len(value) >= MIN_PALI_CHARS and not _looks_like_centered_heading(value):
            paragraphs.append(value)
        current = []

    for raw_line in layout_text.splitlines():
        value = raw_line.strip()
        indent = len(raw_line) - len(raw_line.lstrip())
        if not value:
            previous_was_blank = True
            continue

        # Layout đặt số trang ở một cột rất xa (thường >700). Một dòng chỉ có số
        # cũng không bao giờ là nội dung ở parser này.
        if indent > 100 or re.fullmatch(r"\d{1,4}", value):
            previous_was_blank = False
            continue

        starts_paragraph = INDENTED_PARAGRAPH_MIN <= indent <= INDENTED_PARAGRAPH_MAX
        if starts_paragraph:
            flush()
            current = [value]
        elif indent == 0 and current:
            # Chú thích nằm sau một dòng trắng, bắt đầu bằng 1, 2... hoặc [a]. Dừng
            # ở đây để chú thích không bị nối vào đoạn văn cuối trang.
            if previous_was_blank and re.match(r"^(?:\d+|\[[a-z]\])\s+", value, re.I):
                flush()
                break
            current.append(value)
        elif current and previous_was_blank:
            # Tiêu đề căn giữa xuất hiện giữa hai phần: kết thúc đoạn cũ và bỏ tiêu đề.
            flush()
        previous_was_blank = False

    flush()
    return paragraphs


def _compact_pali_for_pdf_match(text: str) -> str:
    """Dạng dò khớp chịu được lỗi font PDF như ``suta ṃ`` và ``ā vuso``."""
    normalized = normalize_pali(clean_unicode_artifacts(text))
    return re.sub(r"[\W_\d]+", "", normalized, flags=re.UNICODE)


def _char_trigrams(text: str) -> set[str]:
    return {text[index : index + 3] for index in range(max(0, len(text) - 2))}


def match_indented_pairs(
    aligned: list[tuple[str, str, str | None]],
    passages: list[dict],
) -> tuple[list[dict], int]:
    """Khớp parser không số bằng bốn lớp bảo vệ.

    1. so trigram sau khi bỏ khoảng trắng lỗi font;
    2. đoạn PDF phải được ứng viên bao phủ ít nhất 85% (chặn một đoạn PDF trùm qua
       nhiều passage rồi bị gán hết vào passage cuối);
    3. ứng viên đầu phải bỏ xa ứng viên nhì;
    4. chỉ giữ chuỗi ``sort_order`` không giảm theo đúng thứ tự cuốn sách.

    Trả về chuỗi đã lọc và số ứng viên đã qua ba cổng đầu (trước cổng thứ tự).
    Nhiều đoạn PDF liên tiếp có thể trỏ cùng một passage; tầng ghi sẽ nối chúng lại,
    tránh lỗi cũ chỉ giữ đoạn đầu rồi làm mất phần còn lại.
    """
    passage_grams: list[set[str]] = []
    inverted: dict[str, list[int]] = defaultdict(list)
    for position, passage in enumerate(passages):
        grams = _char_trigrams(_compact_pali_for_pdf_match(passage["normalized_pali"] or ""))
        passage_grams.append(grams)
        for gram in grams:
            inverted[gram].append(position)

    candidates: list[dict] = []
    for pair_index, (pali, _viet, _section_id) in enumerate(aligned):
        query_grams = _char_trigrams(_compact_pali_for_pdf_match(pali))
        if not query_grams:
            continue
        overlaps: Counter[int] = Counter()
        for gram in query_grams:
            overlaps.update(inverted.get(gram, ()))
        scored = sorted(
            (
                2 * overlap / (len(query_grams) + len(passage_grams[position])),
                overlap / len(query_grams),
                position,
            )
            for position, overlap in overlaps.items()
            if passage_grams[position]
        )
        scored.reverse()
        if len(scored) < 2:
            continue
        score, query_coverage, position = scored[0]
        runner_up_score = scored[1][0]
        if score < INDENTED_MATCH_MIN:
            continue
        if query_coverage < INDENTED_QUERY_COVERAGE_MIN:
            continue
        if score - runner_up_score < INDENTED_MATCH_MARGIN:
            continue
        passage = passages[position]
        candidates.append(
            {
                "pair_index": pair_index,
                "passage_id": str(passage["id"]),
                "sort_order": int(passage["sort_order"]),
                "score": float(score),
                "query_coverage": float(query_coverage),
                "runner_up_score": float(runner_up_score),
            }
        )

    if not candidates:
        return [], 0

    # Longest non-decreasing subsequence: cùng sort_order được phép vì một passage
    # trong XML đôi khi chứa ba đoạn in liền nhau; ba bản Việt phải được nối lại.
    tails: list[int] = []
    tail_indexes: list[int] = []
    previous = [-1] * len(candidates)
    for index, candidate in enumerate(candidates):
        sort_order = candidate["sort_order"]
        length_index = bisect_right(tails, sort_order)
        if length_index == len(tails):
            tails.append(sort_order)
            tail_indexes.append(index)
        elif (
            sort_order < tails[length_index]
            or candidate["score"] > candidates[tail_indexes[length_index]]["score"]
        ):
            tails[length_index] = sort_order
            tail_indexes[length_index] = index
        if length_index:
            previous[index] = tail_indexes[length_index - 1]

    selected_indexes: list[int] = []
    index = tail_indexes[-1]
    while index >= 0:
        selected_indexes.append(index)
        index = previous[index]
    selected_indexes.reverse()
    return [candidates[index] for index in selected_indexes], len(candidates)


def group_indented_matches(
    matches: list[dict],
    aligned: list[tuple[str, str, str | None]],
) -> list[dict]:
    """Nối đủ các đoạn in thuộc cùng một passage trước khi ghi DB."""
    grouped: list[dict] = []
    for match in matches:
        pair_index = match["pair_index"]
        viet = mend_spacing(aligned[pair_index][1]).strip()
        if not viet:
            continue
        if grouped and grouped[-1]["passage_id"] == match["passage_id"]:
            grouped[-1]["texts"].append(viet)
            grouped[-1]["pair_indexes"].append(pair_index)
            grouped[-1]["score"] = min(grouped[-1]["score"], match["score"])
            continue
        grouped.append(
            {
                "passage_id": match["passage_id"],
                "sort_order": match["sort_order"],
                "score": match["score"],
                "texts": [viet],
                "pair_indexes": [pair_index],
            }
        )
    for group in grouped:
        group["text"] = "\n\n".join(group.pop("texts"))
    return grouped


# Bóc PDF hay chèn dấu cách ngay TRƯỚC nguyên âm mang dấu ("r ừng", "b ởi", "th ấy") -
# dính 71% số dòng, 15.128 chỗ. Chỉ nối lại khi mẩu đứng trước là phụ âm đầu THUẦN,
# tức không chứa nguyên âm nào. Nhờ ràng buộc đó mà "anh ấy" không bị nối thành "anhấy":
# mẩu "anh" có nguyên âm nên không khớp. Đổi lại, "quy ến" cũng không sửa được - chấp
# nhận bỏ sót còn hơn nối bừa làm hỏng chữ đúng.
# NỐI CHỮ BỊ TÁCH - quyết bằng TỪ VỰNG, không đoán hình dạng vết cắt
# --------------------------------------------------------------------
# `pypdf` đọc chữ Việt có dấu chồng thì hay chèn dấu cách vào giữa từ: "thu ộc", "lo ại",
# "xu ống". Bản trước bắt bằng hai regex mô tả hình dạng vết cắt (phụ âm đầu + cách +
# nguyên âm có dấu; rồi thêm một regex nữa cho ệ/ể/ề/ặ). Cách ấy hụt theo thiết kế: mỗi
# hình dạng chưa nghĩ tới lại phải thêm một luật. Đo trên dữ liệu đã nạp - 3.223/30.331
# dòng còn dính lỗi, vì vết cắt rơi sau NGUYÊN ÂM ĐỆM ("thu-ộc", "lo-ại") thì không luật
# nào với tới.
#
# Đổi hẳn cách hỏi. Không hỏi "vết cắt trông thế nào" mà hỏi "ghép lại có ra từ thật
# không". Mảnh do PDF cắt ra không bao giờ là từ ("ộc", "ại", "ất"), còn cụm hợp lệ ghép
# lại thì không thành từ ("do ý" -> "doý", "điều ấy" -> "điềuấy"), nên chính bộ từ vựng
# đã phân loại giúp - không cần liệt kê hình dạng nào cả.
#
# Đo trên toàn bộ văn bản Việt đã nạp (4,3 triệu lượt từ), ngưỡng tần suất >= 5:
#   10/10 ca phải nối  (thu+ộc, lo+ại, xu+ống, ngh+ĩa, th+ật, khu+ất...)
#   10/10 ca phải giữ  (do+ý, điều+ấy, nghĩa+ấy, nói+ác, một+ít...)
# Thêm điều kiện "phổ biến hơn mảnh sau" thì tụt còn 9/10 (mất "khuất"), nên không dùng.
# Vết cắt thực tế không chỉ nằm trước nguyên âm Việt có dấu. Kiểm tra production còn
# thấy `C hánh`, `vớ i`, `Sikh ī`, `U ttara`... nên phải xét mọi cặp mảnh chữ. Việc
# QUYẾT ĐỊNH ghép vẫn do từ vựng bên dưới, không do hình dạng regex; các cụm đúng như
# `điều ấy`, `do ý`, `vị Ānanda` không ghép vì chuỗi dính không phải một từ.
_LETTER_TOKEN = re.compile(r"[^\W\d_]+", re.UNICODE)
_WORDS_FILE = Path(__file__).resolve().parent / "app" / "data" / "vi_words.txt"
_VI_WORDS: set[str] | None = None

# Tên Pali này chỉ xuất hiện vài lần nên không đạt ngưỡng tần suất 5 của từ vựng, nhưng
# ảnh khách gửi và dữ liệu DB đều xác nhận đây là một từ bị PDF tách. Danh sách này chỉ
# nhận các trường hợp đã đối chiếu; không dùng quy tắc "một chữ hoa + từ" vì đại từ
# `Y` trong tiếng Việt (`Y đã`, `Y nói`) sẽ bị ghép sai hàng trăm lần.
_KNOWN_PDF_JOIN_WORDS = {
    # Đợt 1: ảnh khách gửi.
    "uttara",
    # Đợt 2: các từ/tên hiếm được vòng audit độc lập đối chiếu với dạng viết liền
    # đang có ở nơi khác trong cùng kho dữ liệu. Chỉ dùng cho importer Indacanda.
    "aṇīkadatta",
    "arindama",
    "bộp",
    "campeyyaka",
    "kakutthā",
    "kaṇṭaka",
    "kuṇāla",
    "luộc",
    "parivāsa",
    "poṭakila",
    "thuyết",
    # Đợt 3: phần đuôi còn lại sau khi mô phỏng đợt 2 trên toàn bộ hai nguồn Indacanda.
    # Nhiều mảnh ở đây tình cờ cũng là từ độc lập (`th` + `ai`, `tr` + `ai`) nên phải
    # xác nhận đích danh; nếu chỉ dựa vào bộ từ vựng, thuật toán sẽ chủ động giữ sai dấu cách.
    "bấp",
    "bặt",
    "bợm",
    "bủn",
    "bưởi",
    "bướm",
    "bươm",
    "bươi",
    "cằm",
    "chầm",
    "chần",
    "chĩa",
    "chợt",
    "chổng",
    "chững",
    "cườm",
    "dầm",
    "dậm",
    "dẽ",
    "gāthā",
    "gāthāpādo",
    "gian",
    "giấm",
    "gừ",
    "guốc",
    "hả",
    "hễ",
    "hừ",
    "hoan",
    "kathā",
    "kiềng",
    "lạn",
    "lấn",
    "lọn",
    "lốm",
    "lốt",
    "luẩn",
    "luộm",
    "lướt",
    "lành",
    "māluva",
    "màng",
    "miện",
    "miễn",
    "mọng",
    "mẩy",
    "nāva",
    "nāvā",
    "nandisena",
    "ngạch",
    "ngấn",
    "nghẽn",
    "nhặng",
    "nhuốm",
    "nỉ",
    "nỡ",
    "nới",
    "nức",
    "ñāṇadhara",
    "pārivāsa",
    "quan",
    "ramma",
    "rạc",
    "rọ",
    "rỏi",
    "rướn",
    "sāmāka",
    "saṅghādisesa",
    "sanh",
    "samudda",
    "sọc",
    "sủng",
    "than",
    "thai",
    "thūpoti",
    "tịt",
    "trai",
    "trằn",
    "trơ",
    "trướng",
    "tủa",
    "tuyến",
    "vạch",
    "vạm",
    "vặc",
    "vằng",
    "vẩy",
    "vểnh",
    "diệc",
    "diṭṭhi",
    "ghiếc",
    "khai",
    "khiễng",
    "kummāsa",
    "khulu",
    "luồn",
    "makuṭabandhana",
    "oằn",
    "quẩy",
    "romasa",
    "saṅghāṭi",
    "saṅkhepato",
    "sang",
    "somadeva",
    "tanh",
}
_PALI_LOWER_DIACRITICS = frozenset("āīūṅñṭḍṇḷṃṁĀĪŪṄÑṬḌṆḶṂṀ")
_PALI_FRAGMENT = re.compile(r"^[A-Za-zāīūṅñṭḍṇḷṃṁ]+$", re.IGNORECASE)
_PRESERVED_SPACED_PAIRS = {
    ("ba", "y"),
    ("này", "āvuso"),
    ("từ", "āvuso"),
}
_KNOWN_PDF_REPLACEMENTS = {
    # Đây không chỉ là dấu cách sai: PDF còn nhận nhầm dấu của chữ "á" thành "ả".
    "c ảc": "các",
    # Các lỗi dưới đây vừa tách chữ vừa làm sai/mất ký tự, nên nối chuỗi đơn thuần
    # sẽ cho ra từ sai. Chỉ thay đúng cụm đã thấy trong dữ liệu, không sửa gần đúng.
    "b ắng": "bằng",
    "b ậcTự": "bậc Tự",
    "Ch ủa": "Chúa",
    "d ến": "đến",
    "g ở": "gỡ",
    "ngi ệp": "nghiệp",
    "r ẵng": "rằng",
    "s ựPhân": "sự Phân",
    # Dòng này còn mất cả dấu cách trước tên riêng và tách tên thành nhiều mảnh.
    "làN an di sen a": "là Nandisena",
    # Pts II: glyph tiếng Việt bị đảo vị trí dấu và tách ngay trước ký tự cuối.
    # Đã đối chiếu trực quan trang in 189/191: bản in là "tưởng" và "cốt lõi".
    "tưỏ ng": "tưởng",
    "lỏ i": "lõi",
    # Trang in 207: các tên này nằm qua điểm ngắt glyph/dòng của PDF. Giữ dấu nối
    # thật sự trong "Sārī-putta", chỉ bỏ các khoảng trắng do bộ bóc PDF sinh ra.
    "Sārī - putta": "Sārī-putta",
    "Sañjī va": "Sañjīva",
    "Khāṇ ukoṇḍañña": "Khāṇukoṇḍañña",
}
# Các từ ngắn này đứng độc lập rất thường xuyên. Từ điển PDF bị nhiễu có thể khiến
# chuỗi dính cũng tình cờ là một từ (`mà ra` -> `màra`, `kosa là` -> `kosalà`). Nếu
# hai vế đều đã là từ và một vế thuộc nhóm này thì ưu tiên GIỮ dấu cách.
_COMMON_STANDALONE_WORDS = {
    "ai", "an", "ba", "bà", "bị", "bỏ", "bộ", "có", "do", "đã", "đi", "đó",
    "gì", "hai", "họ", "khi", "là", "mà", "nó", "ra", "sa", "ta", "từ", "và",
    "về", "vì", "vị", "ý", "ở", "y",
}


def vi_words() -> set[str]:
    """Từ vựng tiếng Việt dựng từ chính kho bản dịch (tần suất >= 5).

    Để thành FILE chứ không đếm lại từ DB mỗi lần chạy: nạp lại phải cho ra đúng kết quả
    cũ, mà đếm từ DB thì kết quả đổi theo việc lúc ấy đã nạp được bao nhiêu. Sinh lại bằng
    `dev_make_vi_words.py` khi thêm nguồn tiếng Việt mới.
    """
    global _VI_WORDS
    if _VI_WORDS is None:
        _VI_WORDS = {
            line.strip().lower()
            for line in _WORDS_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
    return _VI_WORDS

# "Bà -la-môn", "Sa- môn": dấu cách lọt vào cạnh gạch nối của từ ghép phiên âm. Hai vế
# đều phải dính liền chữ, nên dấu gạch ngang thật sự (" - " có cách hai bên, như
# "hữu biên - vô biên") không bị đụng tới.
_SPLIT_HYPHEN = re.compile(r"(\w) -(\w)|(\w)- (\w)")


# Thứ tự chạy cho `--all`, xếp theo GIÁ TRỊ GIẢM DẦN chứ không theo thứ tự trong tạng.
# Chạy cả bộ mất nhiều giờ; nếu phải dừng giữa chừng thì phần đã xong phải là phần đáng
# nhất. Đo được: thơ kệ đạt 82-93%, văn xuôi chỉ 21%.
#   1. thơ kệ Tiểu Bộ  - trống hoàn toàn, tỉ lệ cao nhất
#   2. Tạng Luật       - trống hoàn toàn, tỉ lệ ~21% nhưng không nguồn nào khác đụng tới
#   3. văn xuôi Tiểu Bộ- tỉ lệ thấp
#   4. các tập đã nạp  - để cuối, và mặc định bị bỏ qua
VOLUME_ORDER = (
    "vvpv", "ap1", "ap2", "ap3", "bvcp", "ja1", "ja2", "ja3",
    "pr", "pc1", "pc2", "mv1", "mv2", "cv1", "cv2", "par1", "par2",
    "nidd1", "nidd2", "pts1", "pts2", "net", "pet", "mil",
    "sn", "kn1", "thag", "dn1", "dn2", "dn3",
)


def already_imported() -> set[str]:
    """Các tập đã có bản ghi nạp thành công, để `--all` khỏi chạy lại.

    Chạy lại một tập là an toàn (`on conflict do update`) nhưng tốn hàng giờ mà không
    thêm được gì, nên mặc định bỏ qua. Dùng `--force` khi muốn nạp đè.
    """
    try:
        rows = fetch_all(
            "select distinct scope from human_translation_imports where source = %s", [SOURCE_ID]
        )
    except Exception:  # noqa: BLE001 - chưa có bảng thì coi như chưa nạp gì
        return set()
    return {str(r["scope"]) for r in rows}


def _known_word(value: str) -> bool:
    lowered = value.lower()
    return lowered in vi_words() or lowered in _KNOWN_PDF_JOIN_WORDS


def _forced_pali_join(parts: list[str]) -> bool:
    """Tên Pali bị cắt ngay trước một phụ âm/nguyên âm có dấu dài.

    `An āthapiṇḍika`, `Moggall āna`, `Gijjhak ūṭa` là nhóm lớn còn sót sau
    đợt đầu. Mảnh trái phải bắt đầu hoa và chỉ chứa bảng chữ Pali, vì thế cụm
    tiếng Việt đúng như `Này āvuso`, `Từ āvuso` không lọt vào quy tắc này.
    """
    if len(parts) != 2:
        return False
    left, right = parts
    lowered = (left.lower(), right.lower())
    if lowered in _PRESERVED_SPACED_PAIRS:
        return False
    return bool(
        right
        and right[0] in _PALI_LOWER_DIACRITICS
        and _PALI_FRAGMENT.fullmatch(left) is not None
        and left[:1].isupper()
    )


def _confirmed_join(parts: list[str]) -> bool:
    joined = "".join(parts)
    if len(parts) == 2 and tuple(part.lower() for part in parts) in _PRESERVED_SPACED_PAIRS:
        return False
    if _forced_pali_join(parts):
        return True
    # Danh sách này đã được audit theo ngữ cảnh, vì vậy phải thắng bộ chặn từ thông dụng.
    # Ví dụ `th` và `ai` đều vô tình có trong từ vựng, nhưng dữ liệu gốc `mang th ai`
    # chắc chắn là `mang thai`.
    if joined.lower() in _KNOWN_PDF_JOIN_WORDS:
        return True
    if _known_word(joined):
        lowered_parts = [part.lower() for part in parts]
        if (
            all(_known_word(part) for part in parts)
            and any(part in _COMMON_STANDALONE_WORDS for part in lowered_parts)
        ):
            return False
        return True
    if len(parts) == 2:
        left, right = parts
        # Tên Pali bị cắt ngay trước ký tự mang dấu: `Ph ārusaka`, `Bh āradvāja`,
        # `Sikh ī`. Chỉ nhận tên bắt đầu hoa hoặc mảnh phải rất ngắn, không đụng tới
        # cụm đúng như `vị ānanda`/`tên Ānanda`.
        return bool(
            right
            and right[0] in _PALI_LOWER_DIACRITICS
            and _PALI_FRAGMENT.fullmatch(left) is not None
            and (left[:1].isupper() or len(right) <= 2)
        )
    return False


def _join_word_spaces(text: str) -> str:
    """Chọn cách nối các mảnh sao cho tạo ra nhiều từ hợp lệ nhất.

    Không thể quyết từng cặp độc lập: `là N ārada` có cả `là`+`N` -> `làn` và
    `N`+`ārada` -> `Nārada` là từ hợp lệ, nhưng chỉ cách sau đúng trong toàn câu.
    Tương tự `sâ n hận` phải thành `sân hận`, không phải `sâ nhận` hay `sânhận`.

    Quy hoạch động chấm điểm toàn chuỗi token: giữ một token hợp lệ được điểm cao;
    mảnh vô nghĩa bị trừ điểm; ghép 2-5 mảnh chỉ được phép khi kết quả nằm trong từ
    vựng. Hai từ vốn đều hợp lệ luôn thắng phương án dính chúng thành một từ khác.
    """
    tokens = list(_LETTER_TOKEN.finditer(text))
    if len(tokens) < 2:
        return text

    count = len(tokens)
    best = [0.0] * (count + 1)
    choice = [1] * count
    for index in range(count - 1, -1, -1):
        token = tokens[index].group(0)
        # Giữ từ có thật tốt hơn; giữ một mảnh lạ vẫn được phép nhưng chịu phạt.
        best[index] = (2.0 if _known_word(token) else -3.0) + best[index + 1]
        parts = [token]
        for end in range(index + 1, min(count, index + 5)):
            if text[tokens[end - 1].end() : tokens[end].start()] != " ":
                break
            parts.append(tokens[end].group(0))
            if not _confirmed_join(parts):
                continue
            group_size = end - index + 1
            # Mảnh dài 1-2 ký tự là dấu hiệu PDF tách chữ rất mạnh (`khô ng`,
            # `th ang`, `Gi áo`). Nó phải thắng trường hợp từ điển nhiễu đã vô tình
            # coi chính mảnh đó là một "từ" do xuất hiện nhiều lần trong PDF lỗi.
            # Hai nhóm phải thắng cả khi từng mảnh tình cờ có trong từ điển:
            # 1) tên Pali cắt trước chữ có dấu (`Moggall` + `āna`),
            # 2) từ hiếm đã được audit và đưa vào danh sách xác nhận ở trên.
            forced = _forced_pali_join(parts) or "".join(parts).lower() in _KNOWN_PDF_JOIN_WORDS
            fragment_bonus = (
                6.0 if forced else 3.0 if any(len(part) <= 2 for part in parts) else 0.1
            )
            score = 2.0 + fragment_bonus + 0.1 * (group_size - 1) + best[end + 1]
            if score > best[index] + 1e-9:
                best[index] = score
                choice[index] = group_size

    remove_at: list[int] = []
    index = 0
    while index < count:
        group_size = choice[index]
        if group_size > 1:
            for offset in range(group_size - 1):
                remove_at.append(tokens[index + offset].end())
        index += group_size
    for index in reversed(remove_at):
        text = text[:index] + text[index + 1 :]
    return text


def mend_spacing(text: str) -> str:
    """Nối lại những chữ bị bóc PDF tách đôi. Chạy tới khi không còn đổi.

    Lặp vì một từ có thể bị tách hơn một lần ("th u ộc"), và vì nối xong mới lộ ra cặp
    tiếp theo. `_JOIN_CANDIDATE` không dùng lookbehind chặn ranh giới từ, nên mỗi vòng
    `re.sub` chỉ bắt các cặp không chồng nhau - vòng sau nhặt nốt phần còn lại.
    """
    text = clean_unicode_artifacts(text)
    for broken, replacement in _KNOWN_PDF_REPLACEMENTS.items():
        text = re.sub(
            rf"(?<!\w){re.escape(broken)}(?!\w)",
            replacement,
            text,
            flags=re.UNICODE,
        )

    # Một từ có thể bị tách thành hơn hai mảnh (`n g ười`), nên chạy tới ổn định với
    # trần an toàn 12 vòng.
    for _ in range(12):
        fixed = _join_word_spaces(text)
        fixed = _SPLIT_HYPHEN.sub(
            lambda m: (m.group(1) or m.group(3)) + "-" + (m.group(2) or m.group(4)), fixed
        )
        if fixed == text:
            break
        text = fixed
    return text


def build_probes(pali: str) -> list[str]:
    """Các chuỗi Pali đem đi dò DB, thử lần lượt cho tới khi ra kết quả.

    Trước đây chỉ dò bằng mệnh đề đầu (phần trước dấu phẩy). Với thơ kệ thì đúng - dòng
    sau thường là điệp khúc lặp ở mọi câu nên lấy cả câu thì điệp khúc lấn át. Nhưng
    Trường Bộ là văn xuôi: đo trên tập 1 thì 24% số cặp trượt vì điểm thấp và 13% không
    tìm thấy dòng nào, phần lớn do mệnh đề đầu quá chung. Nên thử thêm cả câu và mệnh đề
    dài nhất, chỉ dùng tới khi cách đầu không ra gì.
    """
    probes: list[str] = []
    clauses = [normalize_pali(c) for c in re.split(r"[,;]", pali)]
    for candidate in (clauses[0] if clauses else "", normalize_pali(pali),
                      max(clauses, key=len) if clauses else ""):
        candidate = candidate[:MAX_PROBE_CHARS].strip()
        if len(candidate) >= MIN_PALI_CHARS and candidate not in probes:
            probes.append(candidate)
    return probes


# Nguong nhan mot doan lam UNG VIEN o che do can chinh toan cuc. Thap hon nguong ghep
# truc tiep, vi o day khong quyet dinh gi ca - chi thu thap ung vien roi de buoc can
# chinh chon, va viec chon co ca day lam chung cu chu khong xet rieng tung cap.
GLOBAL_CANDIDATE_MIN = 0.35
GLOBAL_CANDIDATE_LIMIT = 10
# Diem tong toi thieu de giu mot cap sau khi can chinh.
GLOBAL_ACCEPT_MIN = 0.45


# ĐÃ THỬ VA ĐA BO (lan hai) - "lap giua hai moc neo": lay cac cap khop chac lam moc,
# roi voi cac cap lot giua hai moc thi chon doan GIONG NHAT trong dung khoang do, con
# tro tien mot chieu bi nhot trong khoang nen khong the vot ra ngoai. Do phu tang manh:
# Truong Bo 1 19% -> 59%, Parajika 2% -> 26%.
#
# NHUNG SAI. Nap that roi soi tay: mau 6 dong ngau nhien co 2 dong gan nham, va muc
# `Sassatavado` ma khach bao co 5/11 doan thi 1 doan bi thay bang ban dich cua doan khac
# - doan do TRUOC KHI SUA VON DUNG. Kieu sai co he thong: cac doan MO DAU ("Santi,
# bhikkhave, eke samanabrahmana...") bi gan ban dich cua doan nam sau vai nhip.
#
# Vi sao: khi cap dung nam NGOAI khoang (con tro da chay qua, hoac moc neo qua thua),
# thuat toan van buoc phai chon mot doan trong khoang. Nguong va bien do khong cuu duoc,
# vi cac doan lien nhau o Truong Bo dung chung toi 70-80% so tu.
#
# Bai hoc ve cach do: phep thu "giau bot moc" cho 100% dung nen tuong la an. Sai o cho
# moc bi giau deu la cap KHOP CHAC (chu dac trung, de tim), khong dai dien cho dam cap
# nhap nhang ma luot lap thuc su phai xu ly - chon mau thien lech.
#
# Muon lam lai thi phai neo theo TUNG BAI KINH: dung `build_targets` ben import_sujato.py
# lay khoang sort_order cua tung bai, do bien bai trong PDF theo tieu de Pali, roi reset
# con tro o moi bai. Khong co diem neo do thi dung dong vao huong nay nua.


# Do dai toi thieu de mot tieu de muc du dac trung ma dem di do trong PDF. Ngan hon thi
# de trung ("Uddeso", "Nidanam" xuat hien khap noi).
SECTION_STEM_MIN = 8


def section_stem(title: str) -> str:
    """Rut tieu de ve dang so sanh duoc: bo so thu tu, bo dau, bo khoang trang."""
    return normalize_pali(re.sub(r"^[\d\.\(\)\s]+", "", title or "")).replace(" ", "")


def load_sections(doc_ids: list[str]) -> list[dict]:
    rows = fetch_all(
        """
        select s.id, s.title, s.document_id, min(p.sort_order) as first_sort
        from sections s join passages p on p.section_id = s.id
        where s.document_id = any(%s::uuid[])
        group by s.id, s.title, s.document_id
        order by s.document_id, min(p.sort_order)
        """,
        [doc_ids],
    )
    return [dict(row) for row in rows]


def tag_pages_with_sections(pages: list[str], sections: list[dict]) -> list[str | None]:
    """Moi trang Pali thuoc muc nao, doc theo tieu de Pali in tren trang.

    Ban song ngu in tieu de muc bang Pali o trang Pali ("Vinitavatthu", "2. Dutiyaparajikam").
    Do duoc chung thi moi cap cau ke biet minh thuoc muc nao, va viec tim doan thu tu "ca
    tap" xuong "may doan trong muc" - du hep de luat nghiem cu khong con bi hai ung vien
    giong het nhau o hai dau tap lam cho be tac.
    KHONG dung duoc cho Truong Bo: PDF bo do khong in tieu de muc (do duoc 1%), nen cac
    tap do van chay nhu cu.

    Chi cho tien: tieu de nao tro nguoc ve muc da di qua thi bo, vi do gan nhu chac chan
    la trung ten chu khong phai sach quay lai.
    """
    by_stem: dict[str, dict] = {}
    duplicated: set[str] = set()
    for section in sections:
        stem = section_stem(section["title"])
        if len(stem) < SECTION_STEM_MIN:
            continue
        if stem in by_stem:
            duplicated.add(stem)
            continue
        by_stem[stem] = section
    for stem in duplicated:
        by_stem.pop(stem, None)

    order = {str(section["id"]): index for index, section in enumerate(sections)}
    tags: list[str | None] = []
    current: dict | None = None
    for page in pages:
        if not is_vietnamese(page):
            for line in page.split("\n"):
                line = line.strip()
                if not line or len(line) > 70:
                    continue
                found = by_stem.get(section_stem(line))
                if found and (current is None or order[str(found["id"])] > order[str(current["id"])]):
                    current = found
        tags.append(str(current["id"]) if current else None)
    return tags


# ── Bản CẢ BÀI KINH ──────────────────────────────────────────────────────────
# Ghép cấp đoạn chỉ phủ 1-58% tuỳ tập (trung bình ~30%), nên bấm "xem toàn bộ bài kinh"
# ra một bản lỗ chỗ - đúng chỗ khách phàn nàn. Nhưng bản in thì KHÔNG thiếu: chỗ trống
# là do câu Pali bên cạnh không dò được về đúng đoạn trong DB, chứ không phải sách không
# có chữ. Nên dựng thêm bản cả bài, lấy trọn phần tiếng Việt chứ không chỉ phần ghép được.
#
# NEO BẰNG CHÍNH CÁC CẶP ĐÃ KHỚP. Các cặp câu kệ nằm theo đúng thứ tự đọc của sách, nên
# những cặp đã dò được về mục X khoanh ra một KHOẢNG trang; mọi cặp nằm giữa hai đầu
# khoảng ấy cũng thuộc mục X, kể cả cặp không tự dò được. Đó là chỗ lấy lại phần thiếu.
#
# Không dùng `tag_pages_with_sections` được: Trường Bộ - đúng bộ khách nêu ví dụ
# (Mahāpadānasutta) - PDF không in tiêu đề mục, dò được 1%.
WHOLE_SOURCE_ID = "indacanda_full"

# Hậu tố tiêu đề của một ĐƠN VỊ ĐỌC TRỌN VẸN, đếm từ tiêu đề THẬT trong DB chứ không
# suy đoán. Lần đầu dùng `import_sujato._is_sutta_title` (đòi kết thúc bằng "sutta") thì
# 24/30 tập ghi 0 mục - đúng những bộ ghép cấp đoạn TỐT NHẤT (82-93%) lại bị loại sạch,
# vì Tiểu Bộ thơ kệ và Tạng Luật đặt tên hoàn toàn khác. Cấp mục cũng không đồng nhất
# (Bổn Sanh ở L6, Ký Sự L5, Luật L4) nên phải nhận theo hậu tố, không theo cấp.
WHOLE_UNIT_SUFFIXES = (
    "sutta", "suttam",   # kinh
    "jatakam",           # Bổn Sanh          · 418 mục
    "apadanam",          # Thánh Nhân Ký Sự  · 423 + 181 mục
    "gatha",             # Trưởng Lão (Ni) Kệ · 170 + 73 mục
    "vatthu",            # Thiên Cung Sự, Ngạ Quỷ Sự · 86 + 51 mục
    "cariya",            # Hạnh Tạng
    "vamso",             # Phật Sử
    "sikkhapadam",       # Tạng Luật, điều học · 45 + 213 mục
    "parajikam",
)
# KHÔNG nhận `vaggo`, `nipato`, `kandam`, `khandhakam`, `bhanavaro`, `katha`, `vibhango`:
# đều là CHƯƠNG chứa nhiều đơn vị. Nhận nhầm thì gộp cả chục bài thành một khối và bản
# "cả bài" lại thành bản "cả chương" - sai còn nặng hơn thiếu.


def _is_whole_unit_title(title: str) -> bool:
    """Tiêu đề này có phải một đơn vị đọc trọn vẹn không (kinh, bổn sanh, kệ, điều học)."""
    raw = str(title or "").strip()
    if not re.match(r"^\(?\d", raw):
        # Không đánh số thì gần như chắc là tiêu đề chương hoặc tên bộ.
        return False
    return normalize_pali(raw).strip().endswith(WHOLE_UNIT_SUFFIXES)
# Dưới ngần này mốc neo thì khoảng trang đoán ra không đáng tin.
WHOLE_MIN_ANCHORS = 3
# Mốc neo đầu/cuối được phép cách mép bài bao nhiêu, tính theo tỉ lệ độ dài bài.
WHOLE_EDGE_SLACK = 0.15


def write_whole_suttas(placed: list[tuple[int, str]], aligned: list[tuple[str, str, str | None]],
                       doc_ids: list[str], key: str, batch: str, dry_run: bool) -> tuple[int, int]:
    """Ghi bản CẢ BÀI KINH, suy khoảng trang của mỗi mục từ các cặp đã khớp.

    `placed` là (thứ tự cặp trong sách, passage_id) của những cặp đã ghi được ở cấp đoạn.
    Trả về (số mục ghi, số mục bỏ vì khoảng trang chồng lấn).
    """
    if not placed:
        return 0, 0

    # Phải neo ở CẤP BÀI KINH, không phải `passages.section_id`. Cột ấy trỏ tới mục sâu
    # nhất, nên lần đầu bản "cả bài" rơi vào các mục con BÊN TRONG Mahāpadānasutta
    # (Pubbenivāsapaṭisaṃyuttakathā, Bodhisattadhammatā…) - vẫn là trích đoạn, đúng thứ
    # khách muốn bỏ. Bản Minh Châu neo ở `1. Mahāpadānasuttaṃ` phủ 4-207; theo đúng vậy.
    suttas = [
        row
        for row in fetch_all(
            """
            select s.id, s.document_id, s.title,
                   s.start_sort_order as lo, s.end_sort_order as hi
            from sections s
            where s.document_id = any(%s::uuid[])
            order by s.document_id, s.start_sort_order, s.level
            """,
            [doc_ids],
        )
        if _is_whole_unit_title(str(row["title"] or ""))
    ]
    if not suttas:
        # Tập nào không có mục nào là đơn vị đọc trọn vẹn thì không dựng bản cả bài;
        # cấp đoạn vẫn giữ nguyên.
        return 0, 0

    located = {
        str(row["id"]): (str(row["document_id"]), row["sort_order"])
        for row in fetch_all(
            "select id, document_id, sort_order from passages where id = any(%s::uuid[])",
            [[pid for _index, pid in placed]],
        )
    }

    def sutta_of(passage_id: str) -> dict | None:
        where = located.get(passage_id)
        if not where:
            return None
        document_id, sort_order = where
        for row in suttas:
            if str(row["document_id"]) == document_id and row["lo"] <= sort_order <= row["hi"]:
                return row
        return None

    ranges: dict[str, dict] = {}
    # Mỗi bài kinh: khoảng thứ tự cặp trong sách, và đoạn neo đầu tiên.
    spans: dict[str, dict] = {}
    for pair_index, passage_id in placed:
        sutta = sutta_of(passage_id)
        if not sutta:
            continue
        section_id = str(sutta["id"])
        ranges[section_id] = sutta
        sort_order = located[passage_id][1]
        span = spans.setdefault(section_id, {"lo": pair_index, "hi": pair_index,
                                             "anchor": passage_id, "count": 0,
                                             "first_sort": sort_order, "last_sort": sort_order})
        span["lo"] = min(span["lo"], pair_index)
        span["hi"] = max(span["hi"], pair_index)
        span["first_sort"] = min(span["first_sort"], sort_order)
        span["last_sort"] = max(span["last_sort"], sort_order)
        span["count"] += 1

    # Mốc neo phải ÔM TRỌN hai đầu bài kinh thì khoảng trang suy ra mới đáng tin. Không
    # siết chỗ này thì ranh giới trôi và bài sau nuốt văn bài trước - đo trên Trường Bộ
    # tập 2: `7. Mahāsamayasuttaṃ` mở đầu bằng văn của Mahāgovindasutta, tức nói với người
    # đọc "đây là trọn bài" rồi đưa bài khác. Thà ghi ít bài mà đúng.
    usable = []
    for section_id, span in spans.items():
        if span["count"] < WHOLE_MIN_ANCHORS:
            continue
        sutta = ranges[section_id]
        length = max(1, sutta["hi"] - sutta["lo"])
        if (span["first_sort"] - sutta["lo"]) / length > WHOLE_EDGE_SLACK:
            continue
        if (sutta["hi"] - span["last_sort"]) / length > WHOLE_EDGE_SLACK:
            continue
        usable.append((section_id, span))
    # Xếp theo vị trí trong sách rồi loại mục nào có khoảng ĐÈ LÊN mục trước: chồng lấn
    # nghĩa là mốc neo nhiễu, mà lấy nhầm khoảng thì bê nguyên văn bài khác sang.
    usable.sort(key=lambda pair: (pair[1]["lo"], pair[1]["hi"]))
    # Loại các bài có khoảng ĐÈ LÊN nhau trước, rồi mới chia ranh giới trên phần còn lại.
    ordered: list[tuple[str, dict]] = []
    skipped = 0
    last_hi = -1
    for section_id, span in usable:
        if span["lo"] <= last_hi or section_id not in ranges:
            skipped += 1
            continue
        ordered.append((section_id, span))
        last_hi = span["hi"]

    # CẮT ĐÚNG TRONG MỐC NEO, không nới mép. Đã thử hai cách nới và cả hai đều đưa văn
    # bài khác vào: đẩy khoảng trống về bài sau thì Mahānidāna mở bằng đuôi Mahāpadāna;
    # chia đôi khoảng trống thì Mahāsamaya mở bằng văn Mahāgovinda - vì Mahāgovinda bị
    # loại ở guard trên, phần trang của nó thành vô chủ và rơi sang hàng xóm. Mọi cách
    # nới đều hỏng khi hàng xóm có thể vắng mặt.
    #
    # Nên chấp nhận mất vài dòng đầu/cuối mỗi bài, đổi lấy bảo đảm: chữ trong bài nào
    # đúng là của bài đó. Guard mép ở trên đã chặn phần mất không quá `WHOLE_EDGE_SLACK`.
    written = 0
    for section_id, span in ordered:
        start, end = span["lo"], span["hi"]
        section_range = ranges[section_id]
        text = "\n\n".join(
            aligned[i][1] for i in range(start, end + 1) if aligned[i][1].strip()
        ).strip()
        if not text:
            continue
        if dry_run:
            written += 1
            continue
        changed = write_translation(
            span["anchor"], WHOLE_SOURCE_ID, text, key,
            method="whole_unit", batch=batch,
            document_id=str(section_range["document_id"]),
            start=section_range["lo"], end=section_range["hi"],
        )
        remove_stale_whole_sutta_rows(
            span["anchor"],
            WHOLE_SOURCE_ID,
            str(section_range["document_id"]),
            section_range["lo"],
            section_range["hi"],
            batch,
        )
        if changed:
            written += 1
    return written, skipped


def strip_running_head(text: str) -> str:
    """Bỏ dòng tiêu đề chạy và số trang ở đầu mỗi trang."""
    lines = text.split("\n")
    while lines and (not lines[0].strip() or re.fullmatch(r"\s*\d{1,4}\s*", lines[0])):
        lines.pop(0)
    if lines and not re.match(r"^\s*\d{1,4}\s*\.", lines[0]):
        lines.pop(0)
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Nạp bản dịch tiếng Việt của TK. Indacanda.")
    parser.add_argument("volumes", nargs="*", help=f"Tập: {', '.join(VOLUMES)}")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--all", action="store_true",
                        help="chạy mọi tập chưa nạp, xếp theo giá trị giảm dần")
    parser.add_argument("--force", action="store_true",
                        help="với --all: nạp đè cả những tập đã nạp rồi")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--global-align", action="store_true",
                        help="căn chỉnh toàn cục: xét cả tập cùng lúc thay vì ghép từng cặp"
                             " (xem align_globally)")
    args = parser.parse_args()

    if args.list:
        for key, volume in VOLUMES.items():
            print(f"  {key:<6} {volume['label']:<46} {volume['file']}")
        return

    if args.all:
        done = set() if args.force else already_imported()
        args.volumes = [key for key in VOLUME_ORDER if key not in done]
        skipped = [key for key in VOLUME_ORDER if key in done]
        print(f"Sẽ chạy {len(args.volumes)} tập: {' '.join(args.volumes)}")
        if skipped:
            print(f"Bỏ qua {len(skipped)} tập đã nạp: {' '.join(skipped)}  (dùng --force để nạp đè)")
        print()
        if not args.volumes:
            print("Không còn tập nào chưa nạp.")
            return
    if not args.volumes:
        parser.error("cần chỉ định tập, hoặc dùng --all / --list")
    if args.global_align and any(
        VOLUMES.get(key, {}).get("parser") == "paired_indented" for key in args.volumes
    ):
        parser.error(
            "pts2 có bộ ghép riêng đã khóa theo thứ tự trang; không dùng --global-align với pts2"
        )

    if not args.dry_run:
        migrations = Path(__file__).resolve().parents[1] / "db" / "migrations"
        for name in ("002_human_translations.sql", "004_match_provenance.sql", "005_import_batches.sql"):
            execute((migrations / name).read_text(encoding="utf-8"))
        print("đã áp dụng 002 + 004 + 005 (xếp hạng, lịch sử và đợt nạp)")

    # Nhãn của đợt nạp này, để về sau truy được dòng nào do đợt nào ghi.
    method = "global_align" if args.global_align else "strict_unique"
    batch = f"{method}-{datetime.now():%Y%m%d-%H%M%S}"
    print(f"đợt nạp: {batch}\n")
    if not args.dry_run:
        execute(
            """
            insert into human_translation_batches (import_batch, source, language, scope, notes)
            values (%s, %s, %s, %s, %s)
            on conflict (import_batch) do nothing
            """,
            [batch, SOURCE_ID, LANGUAGE, ",".join(args.volumes), method],
        )

    grand = {"pairs": 0, "matched": 0, "written": 0, "whole": 0}

    for key in args.volumes:
        volume = VOLUMES.get(key)
        if not volume:
            print(f"!! không biết tập {key}\n")
            continue

        print(f"=== {volume['label']} ({volume['file']}) ===")
        path = download(volume)
        reader = PdfReader(str(path))
        parser_kind = volume.get("parser", "numbered")
        pages = (
            []
            if parser_kind == "paired_indented"
            else [strip_running_head(page.extract_text() or "") for page in reader.pages]
        )

        # Giu dung thu tu nhu cau hinh: con tro tien-mot-chieu chay theo thu tu nay.
        by_name = {
            row["file_name"]: str(row["id"])
            for row in fetch_all(
                "select id, file_name from documents where file_name = any(%s)", [volume["docs"]]
            )
        }
        doc_ids = [by_name[name] for name in volume["docs"] if name in by_name]
        if not doc_ids:
            print("  !! không tìm thấy tài liệu tương ứng trong DB\n")
            continue

        if parser_kind == "paired_indented":
            start_page = int(volume["pdf_start_page"])
            end_page = int(volume["pdf_end_page"])
            if start_page < 1 or end_page > len(reader.pages) or (end_page - start_page + 1) % 2:
                raise ValueError(
                    f"khoảng trang pts2 không hợp lệ: {start_page}-{end_page}/"
                    f"{len(reader.pages)}"
                )

            aligned: list[tuple[str, str, str | None]] = []
            unbalanced_pages: list[dict] = []
            balanced_page_pairs = 0
            unbalanced_block_count = 0
            for pali_page in range(start_page, end_page + 1, 2):
                viet_page = pali_page + 1
                pali_pdf_page = reader.pages[pali_page - 1]
                viet_pdf_page = reader.pages[viet_page - 1]
                pali_layout = pali_pdf_page.extract_text(extraction_mode="layout") or ""
                viet_layout = viet_pdf_page.extract_text(extraction_mode="layout") or ""
                pali_blocks = split_indented_paragraphs(pali_layout)
                viet_blocks = split_indented_paragraphs(viet_layout)
                if not pali_blocks or len(pali_blocks) != len(viet_blocks):
                    unbalanced_block_count += max(len(pali_blocks), len(viet_blocks))
                    unbalanced_pages.append(
                        {
                            "pali_page": pali_page,
                            "viet_page": viet_page,
                            "pali_text": re.sub(
                                r"\s+", " ", pali_pdf_page.extract_text() or ""
                            ).strip(),
                            "viet_text": mend_spacing(
                                re.sub(r"\s+", " ", viet_pdf_page.extract_text() or "").strip()
                            ),
                        }
                    )
                    continue
                balanced_page_pairs += 1
                aligned.extend(
                    (pali, mend_spacing(viet), None)
                    for pali, viet in zip(pali_blocks, viet_blocks)
                )

            passages = fetch_all(
                """
                select id, document_id, sort_order, normalized_pali
                from passages
                where document_id = any(%s::uuid[])
                order by document_id, sort_order
                """,
                [doc_ids],
            )
            matches, quality_candidates = match_indented_pairs(aligned, passages)
            groups = group_indented_matches(matches, aligned)
            matched = len(groups)
            written = 0
            if args.verbose:
                for group in groups[:4]:
                    pair_index = group["pair_indexes"][0]
                    print(
                        f"     sort={group['sort_order']} · điểm={group['score']:.3f}"
                        f" · ghép {len(group['pair_indexes'])} đoạn in"
                    )
                    print(f"     PALI: {aligned[pair_index][0][:74]}")
                    print(f"     VIỆT: {group['text'][:74]}")
            if not args.dry_run:
                for group in groups:
                    if write_translation(
                        group["passage_id"], SOURCE_ID, group["text"], key,
                        method="strict_unique", batch=batch, score=group["score"],
                    ):
                        written += 1

            resolved_indexes = {match["pair_index"] for match in matches}
            unresolved_count = len(aligned) - len(resolved_indexes)
            unbalanced_count = len(unbalanced_pages)
            if not args.dry_run:
                unresolved_count = stage_unresolved_pairs(
                    key, aligned, resolved_indexes, batch
                )
                unbalanced_count = stage_unbalanced_indented_pages(
                    key, unbalanced_pages, batch
                )

            total_page_pairs = (end_page - start_page + 1) // 2
            print(
                f"  {len(reader.pages)} trang · xét thân sách {start_page}-{end_page}: "
                f"{balanced_page_pairs}/{total_page_pairs} cặp trang cân đoạn"
            )
            print(
                f"  {len(aligned)} cặp đoạn Pali-Việt · qua cổng chất lượng "
                f"{quality_candidates} · đúng thứ tự {len(matches)} đoạn in "
                f"→ {matched} passage DB · ghi {written}"
            )
            print(
                f"  giữ lại {unresolved_count} cặp chưa đủ chắc và "
                f"{unbalanced_count} cặp trang lệch ({unbalanced_block_count} đoạn), không đoán"
            )
            print(
                "  bản trọn bài: không dựng từ pts2 ở đợt này vì còn trang bị giữ lại; "
                "tránh gắn nhãn 'trọn bài' cho dữ liệu chưa phủ đủ"
            )

            grand["pairs"] += len(aligned)
            grand["matched"] += matched
            grand["written"] += written
            if not args.dry_run:
                execute(
                    """
                    insert into human_translation_imports
                      (source, language, scope, segments_total, segments_matched,
                       passages_written, notes, import_batch)
                    values (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    [
                        SOURCE_ID, LANGUAGE, key, len(aligned), matched, written,
                        (
                            f"{volume['label']}; paired_indented; "
                            f"balanced_pages={balanced_page_pairs}/{total_page_pairs}; "
                            f"unbalanced_blocks={unbalanced_block_count}"
                        ),
                        batch,
                    ],
                )
            print()
            continue

        sections = load_sections(doc_ids)
        page_section = tag_pages_with_sections(pages, sections)
        tagged_pages = sum(1 for tag in page_section if tag)

        # Ghep cap trang Pali <-> trang Viet ke nhau, roi ghep cau ke theo SO.
        aligned: list[tuple[str, str, str | None]] = []
        for index in range(len(pages) - 1):
            left, right = pages[index], pages[index + 1]
            if is_vietnamese(left) or not is_vietnamese(right):
                continue
            pali_verses = split_verses(left)
            viet_verses = split_verses(right)
            for number, pali in pali_verses.items():
                viet = viet_verses.get(number)
                if viet and len(pali) >= MIN_PALI_CHARS:
                    # Chi va ban Viet; ban Pali giu nguyen vi con dung de do chuoi trong DB
                    # va `_VN_VOWEL` khong dinh toi chu Pali.
                    aligned.append((pali, mend_spacing(viet), page_section[index]))

        print(f"  {len(pages)} trang · ghép được {len(aligned)} cặp câu kệ Pali-Việt")
        grand["pairs"] += len(aligned)

        if args.global_align:
            passages = []
            for document_id in doc_ids:
                passages.extend(
                    fetch_all(
                        "select id from passages where document_id = %s order by sort_order",
                        [document_id],
                    )
                )
            position_of = {str(row["id"]): index for index, row in enumerate(passages)}

            # Thu thap ung vien: nguong thap, KHONG doi bo xa ung vien nhi. O day chua
            # quyet dinh gi - viec chon de buoc can chinh lam, va no chon dua vao ca day.
            candidates: list[list[tuple[int, float]]] = []
            for pali, _viet, _section in aligned:
                best_by_position: dict[int, float] = {}
                for probe in build_probes(pali):
                    for row in fetch_all(
                        """
                        select id, similarity(normalized_pali, %s) as sim
                        from passages
                        where document_id = any(%s::uuid[]) and normalized_pali %% %s
                        order by sim desc limit %s
                        """,
                        [probe, doc_ids, probe, GLOBAL_CANDIDATE_LIMIT],
                    ):
                        if row["sim"] < GLOBAL_CANDIDATE_MIN:
                            continue
                        position = position_of.get(str(row["id"]))
                        if position is not None and row["sim"] > best_by_position.get(position, 0.0):
                            best_by_position[position] = float(row["sim"])
                candidates.append(sorted(best_by_position.items()))

            chosen = align_globally(candidates)
            accepted = written = 0
            placed: list[tuple[int, str]] = []
            for pair_index in sorted(chosen):
                pali, viet, _section = aligned[pair_index]
                position = chosen[pair_index]
                if dict(candidates[pair_index]).get(position, 0.0) < GLOBAL_ACCEPT_MIN:
                    continue
                accepted += 1
                if args.verbose and written < 4:
                    print(f"     PALI: {pali[:74]}")
                    print(f"     VIỆT: {viet[:74]}")
                if not args.dry_run:
                    if write_translation(
                        str(passages[position]["id"]), SOURCE_ID, viet, key,
                        method="global_align", batch=batch,
                        score=dict(candidates[pair_index]).get(position),
                    ):
                        written += 1
                placed.append((pair_index, str(passages[position]["id"])))

            unresolved_count = 0
            if not args.dry_run:
                unresolved_count = stage_unresolved_pairs(
                    key, aligned, {pair_index for pair_index, _passage_id in placed}, batch
                )
            rate = 100 * accepted // max(1, len(aligned))
            print(f"  căn chỉnh toàn cục: {accepted}/{len(aligned)} cặp ({rate}%)"
                  f" · phủ {100 * accepted // max(1, len(passages))}% số đoạn · ghi {written}"
                  f" · giữ {len(aligned) - accepted if args.dry_run else unresolved_count} ca chưa khớp")
            whole, overlap = write_whole_suttas(placed, aligned, doc_ids, key, batch, args.dry_run)
            print(f"  bản cả bài kinh: ghi {whole} mục"
                  f"{f' · bỏ {overlap} mục vì khoảng trang chồng lấn' if overlap else ''}")
            grand["matched"] += accepted
            grand["written"] += written
            grand["whole"] += whole
            print()
            continue

        matched = written = 0
        by_section = 0
        seen: set[str] = set()
        placed = []
        for pair_index, (pali, viet, section_id) in enumerate(aligned):
            hit = None
            # ĐÃ THỬ VA ĐA BO (lan ba) - thu hep pham vi tim vao dung MUC ma trang PDF do
            # thuoc ve, giu nguyen luat chon. Do duoc muc cho 640/735 trang Parajika va
            # them 35 dong (311 -> 346).
            #
            # NHUNG THU HEP PHAM VI TAO RA TU TIN GIA. Luat "phai bo xa ung vien nhi" chi
            # co nghia khi ung vien that nam trong tam nhin. Loai doi thu ra khoi tam nhin
            # thi mot doan khong lien quan trong muc bong thanh "ung vien duy nhat" va
            # vuot qua luat de dang. Soi tay 8 dong moi: 6 dung, 1 lech dau doan, 1 SAI
            # han (sort=1790 Puranacivara: Pali noi "bao giat thi pham dukkata", ban Viet
            # gan vao lai la nghi thuc xa bo y - doan khac trong cung muc).
            #
            # Do them: 35/38 ca nhap nhang co CA HAI ung vien trong cung mot muc (Tang Luat
            # liet ke hang loat vu viec chi khac vai chu ngay trong mot muc), nen thu hep
            # den cap muc vua khong go duoc phan lon, vua sinh loi moi. Muon di tiep thi
            # phai co diem neo den TUNG DOAN, khong phai tung muc.
            # Giu nguyen `section_id` o `aligned` va `tag_pages_with_sections` vi phan do
            # do dung (640/735 trang) va con dung duoc neu sau nay tim ra neo min hon.
            for scope in ("volume",):
                if scope == "section" and not section_id:
                    continue
                for probe in build_probes(pali):
                    # Khop mo bang index trigram: ban in noi tu khac ban goc
                    # ("jinnamiva tacam" vs "jinnamivattacam") nen khop chuoi chinh xac se truot.
                    if scope == "section":
                        rows = fetch_all(
                            """
                            select id, similarity(normalized_pali, %s) as sim
                            from passages
                            where section_id = %s and normalized_pali %% %s
                            order by sim desc limit 2
                            """,
                            [probe, section_id, probe],
                        )
                    else:
                        rows = fetch_all(
                            """
                            select id, similarity(normalized_pali, %s) as sim
                            from passages
                            where document_id = any(%s::uuid[]) and normalized_pali %% %s
                            order by sim desc limit 2
                            """,
                            [probe, doc_ids, probe],
                        )
                    # Phai vua giong du cao, vua bo xa ung vien thu hai. Sat nhau thi bo qua,
                    # tha thieu con hon gan nham cau ke ben canh.
                    if not rows or rows[0]["sim"] < SIMILARITY_MIN:
                        continue
                    if len(rows) > 1 and rows[0]["sim"] - rows[1]["sim"] < SIMILARITY_MARGIN:
                        continue
                    hit = rows[0]
                    break
                if hit:
                    if scope == "section":
                        by_section += 1
                    break
            if not hit:
                continue
            passage_id = str(hit["id"])
            # Cap DAU TIEN doi duoc mot doan thi giu doan do. Da thu doi sang cach chon
            # khac (giu day moc tang dan dai nhat): so dong tut 252 xuong 196 VA doan mo
            # dau cua Sassatavado bi thay bang ban dich cua doan khac - tuc chon lai la
            # hong ca cai dang dung. Xem ghi chu that bai o dau file.
            if passage_id in seen:
                continue
            seen.add(passage_id)
            matched += 1
            placed.append((pair_index, passage_id))
            if args.verbose and matched <= 4:
                print(f"     PALI: {pali[:74]}")
                print(f"     VIỆT: {viet[:74]}")
            if args.dry_run:
                continue
            if write_translation(passage_id, SOURCE_ID, viet, key,
                                 method="strict_unique", batch=batch,
                                 score=float(hit["sim"])):
                written += 1

        rate = 100 * matched // max(1, len(aligned))
        unresolved_count = 0
        if not args.dry_run:
            unresolved_count = stage_unresolved_pairs(
                key, aligned, {pair_index for pair_index, _passage_id in placed}, batch
            )
        print(f"  tìm được đúng một chỗ trong DB: {matched}/{len(aligned)} ({rate}%) · ghi {written}"
              f" · giữ {len(aligned) - matched if args.dry_run else unresolved_count} ca chưa khớp"
              f"  [dò được mục cho {tagged_pages}/{len(pages)} trang · {by_section} cặp khớp nhờ giới hạn trong mục]")
        whole, overlap = write_whole_suttas(placed, aligned, doc_ids, key, batch, args.dry_run)
        print(f"  bản cả bài kinh: ghi {whole} mục"
              f"{f' · bỏ {overlap} mục vì khoảng trang chồng lấn' if overlap else ''}")
        grand["matched"] += matched
        grand["written"] += written
        grand["whole"] += whole

        if not args.dry_run and matched:
            execute(
                """
                insert into human_translation_imports
                  (source, language, scope, segments_total, segments_matched, passages_written, notes,
                   import_batch)
                values (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [SOURCE_ID, LANGUAGE, key, len(aligned), matched, written, volume["label"], batch],
            )
        print()

    print(f"TỔNG: {grand['pairs']} cặp Pali-Việt · khớp DB {grand['matched']} · ghi {grand['written']}")
    if not args.dry_run:
        execute(
            """
            update human_translation_batches
            set status = 'completed', finished_at = now()
            where import_batch = %s
            """,
            [batch],
        )


if __name__ == "__main__":

    main()
