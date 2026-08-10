"""Kiem dinh kha thi cua tung file PDF trong feat_new/ truoc khi nap.

Cham diem bang so, KHONG doan:
  1. Boc duoc text khong, chat luong the nao (ty le chu bi vo)
  2. Trong PDF co ten Pali de neo vao kinh goc khong
  3. Bao nhieu % tieu de chuong muc cua tipitaka.org tim thay duoc trong PDF
  4. Neo duoc toi cap nao: bai kinh / chuong muc / tung doan

Chay: .venv\\Scripts\\python.exe dev_pdf_audit.py
"""

import re
import sys
import warnings
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
warnings.filterwarnings("ignore")

import logging

logging.getLogger("pypdf").setLevel(logging.CRITICAL)

from pypdf import PdfReader

from app.db import fetch_all
from app.normalize import normalize_pali, strip_vietnamese

FEAT = Path(__file__).resolve().parents[1] / "feat_new"

# (nhan, duong dan PDF, tai lieu DB tuong ung)
SOURCES: list[tuple[str, str, str, str]] = [
    # --- Chanh tang Vi Dieu Phap, Tinh Su dich ---
    ("Tịnh Sự", "Bộ Pháp Tụ", "vidieuphap_bandich_NgaiTinhSu/vidieuphap_bandich_NgaiTinhSu/bophaptu.pdf", "abh01m.mul.xml"),
    ("Tịnh Sự", "Bộ Phân Tích", "vidieuphap_bandich_NgaiTinhSu/vidieuphap_bandich_NgaiTinhSu/bophantich.pdf", "abh02m.mul.xml"),
    ("Tịnh Sự", "Bộ Nguyên Chất Ngữ", "vidieuphap_bandich_NgaiTinhSu/vidieuphap_bandich_NgaiTinhSu/bonguyenchatngu.pdf", "abh03m1.mul.xml"),
    ("Tịnh Sự", "Bộ Nhân Chế Định", "vidieuphap_bandich_NgaiTinhSu/vidieuphap_bandich_NgaiTinhSu/bonhanchedinh.pdf", "abh03m2.mul.xml"),
    ("Tịnh Sự", "Bộ Ngữ Tông", "vidieuphap_bandich_NgaiTinhSu/vidieuphap_bandich_NgaiTinhSu/bongutong.pdf", "abh03m3.mul.xml"),
    ("Tịnh Sự", "Bộ Song Đối", "vidieuphap_bandich_NgaiTinhSu/vidieuphap_bandich_NgaiTinhSu/bosongdoi.pdf", "abh03m4.mul.xml"),
    ("Tịnh Sự", "Bộ Vị Trí 1", "vidieuphap_bandich_NgaiTinhSu/vidieuphap_bandich_NgaiTinhSu/bovitri1.pdf", "abh03m5.mul.xml"),
    ("Tịnh Sự", "Bộ Vị Trí 2", "vidieuphap_bandich_NgaiTinhSu/vidieuphap_bandich_NgaiTinhSu/bovitri2.pdf", "abh03m6.mul.xml"),
    ("Tịnh Sự", "Bộ Vị Trí 3", "vidieuphap_bandich_NgaiTinhSu/vidieuphap_bandich_NgaiTinhSu/bovitri3.pdf", "abh03m7.mul.xml"),
    # --- Chu giai Vi Dieu Phap ---
    ("Chú giải", "CG Bộ Pháp Tụ - Siêu Thành", "3. CHÚ GIẢI TẠNG VI DIỆU PHÁP-20260804T020851Z-1-001/3. CHÚ GIẢI TẠNG VI DIỆU PHÁP/3.1. CHÚ GIẢI BỘ PHÁP TỤ/1. Chú Giải Bộ Pháp Tụ - TK. Siêu Thành.pdf", "abh01a.att.xml"),
    ("Chú giải", "CG Bộ Pháp Tụ - Thiện Minh", "3. CHÚ GIẢI TẠNG VI DIỆU PHÁP-20260804T020851Z-1-001/3. CHÚ GIẢI TẠNG VI DIỆU PHÁP/3.1. CHÚ GIẢI BỘ PHÁP TỤ/1. Chú Giải Bộ Pháp Tụ - TK. Thiện Minh.pdf", "abh01a.att.xml"),
    ("Chú giải", "CG Pháp Tụ+Phân Tích - Sán Nhiên", "3. CHÚ GIẢI TẠNG VI DIỆU PHÁP-20260804T020851Z-1-001/3. CHÚ GIẢI TẠNG VI DIỆU PHÁP/3.1. CHÚ GIẢI BỘ PHÁP TỤ/1. Chú giải Bộ Pháp Tụ & Bộ Phân Tích - TK. Sán Nhiên.pdf", "abh01a.att.xml"),
    ("Chú giải", "CG Bộ Phân Tích - Thiện Minh", "3. CHÚ GIẢI TẠNG VI DIỆU PHÁP-20260804T020851Z-1-001/3. CHÚ GIẢI TẠNG VI DIỆU PHÁP/3.2. CHÚ GIẢI BỘ PHÂN TÍCH/2. Chú giải Bộ Phân Tích - TK. Thiện Minh.pdf", "abh02a.att.xml"),
    ("Chú giải", "CG Bộ Nguyên Chất Ngữ - Tâm An", "3. CHÚ GIẢI TẠNG VI DIỆU PHÁP-20260804T020851Z-1-001/3. CHÚ GIẢI TẠNG VI DIỆU PHÁP/3.3. CHÚ GIẢI BỘ NGUYÊN CHẤT NGỮ/3. Chú giải Bộ Nguyên Chất Ngữ - Tâm An dịch.pdf", "abh03a.att.xml"),
    ("Chú giải", "CG Bộ Ngữ Tông - Minh Huệ", "3. CHÚ GIẢI TẠNG VI DIỆU PHÁP-20260804T020851Z-1-001/3. CHÚ GIẢI TẠNG VI DIỆU PHÁP/3.5. CHÚ GIẢI BỘ NGỮ TÔNG/5. Chú Giải Bộ Ngữ Tông - TK. Minh Huệ, Tâm An.pdf", "abh03a.att.xml"),
    ("Chú giải", "CG Bộ Ngữ Tông - Thiện Minh", "3. CHÚ GIẢI TẠNG VI DIỆU PHÁP-20260804T020851Z-1-001/3. CHÚ GIẢI TẠNG VI DIỆU PHÁP/3.5. CHÚ GIẢI BỘ NGỮ TÔNG/5. Chú Giải Thuyết Luận sự (Bộ Ngữ Tông) - TK. Thiện Minh.pdf", "abh03a.att.xml"),
    ("Chú giải", "CG Bộ Vị Trí Q1 - Tịnh Sự", "3. CHÚ GIẢI TẠNG VI DIỆU PHÁP-20260804T020851Z-1-001/3. CHÚ GIẢI TẠNG VI DIỆU PHÁP/3.7. CHÚ GIẢI BỘ VỊ TRÍ/7. Chú Giải Bộ Vị trí, Quyển 1 - HT. Tịnh Sự, TK. Sán Nhiên biên tập.pdf", "abh03a.att.xml"),
    ("Chú giải", "CG Đại Phát Thú t1 - Sán Nhiên", "3. CHÚ GIẢI TẠNG VI DIỆU PHÁP-20260804T020851Z-1-001/3. CHÚ GIẢI TẠNG VI DIỆU PHÁP/3.7. CHÚ GIẢI BỘ VỊ TRÍ/7. Chú Giải Bộ Vị trí, Đại Phát Thú, tập 1 - TK. Sán Nhiên.pdf", "abh03a.att.xml"),
    # --- Kinh tang, Minh Chau dich ---
    ("Minh Châu", "Trung Bộ Kinh", "Bandich_ngaiMinhChau/Bandich_ngaiMinhChau/2.-Trung-Bo-Kinh.pdf", "s0201m.mul.xml"),
    ("Minh Châu", "Tăng Chi Bộ Kinh", "Bandich_ngaiMinhChau/Bandich_ngaiMinhChau/3.-Tang-Chi-Bo-Kinh.pdf", "s0402m2.mul.xml"),
    ("Minh Châu", "Tương Ưng Bộ Kinh", "Bandich_ngaiMinhChau/Bandich_ngaiMinhChau/4.-Tuong-Ung-Bo-Kinh.pdf", "s0301m.mul.xml"),
]

BROKEN_WORD = re.compile(r"(?<=[a-zàáảãạăâđêôơưêô])\s(?=[ựảầốếệịộớứửữấắặẻẽỉĩỏõủũỳỷỹ])", re.I)


def extract(path: Path) -> str:
    reader = PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def fold(text: str) -> str:
    """Bo moi dau phu, ca kieu Pali chuan (a) lan kieu Viet cu (a) trong ban dich xua."""
    return re.sub(r"[^a-z0-9]+", "", strip_vietnamese(text))


def db_section_stems(file_name: str) -> list[str]:
    rows = fetch_all(
        """
        select s.title from sections s join documents d on d.id = s.document_id
        where d.file_name = %s order by s.start_sort_order, s.level
        """,
        [file_name],
    )
    stems: list[str] = []
    for row in rows:
        title = re.sub(r"^[\d\.\s\(\)\-]+", "", str(row["title"] or "").strip())
        stem = fold(title)
        if len(stem) >= 7 and stem not in stems:
            stems.append(stem)
    return stems


def main() -> None:
    print(f"{'Dịch giả':<11} {'Tập':<32} {'trang':>6} {'chữ vỡ':>7} {'mục DB':>7} {'khớp':>6} {'%':>5}")
    print("-" * 84)
    summary: dict[str, list[float]] = {}

    for group, label, rel, doc in SOURCES:
        path = FEAT / rel
        if not path.exists():
            print(f"{group:<11} {label:<32} {'KHÔNG THẤY FILE':>28}")
            continue
        try:
            reader = PdfReader(str(path))
            pages = len(reader.pages)
            text = "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception as exc:  # noqa: BLE001
            print(f"{group:<11} {label:<32} LỖI ĐỌC: {type(exc).__name__}")
            continue

        words = len(re.findall(r"\S+", text))
        broken = len(BROKEN_WORD.findall(text))
        broken_pct = 100 * broken / max(1, words)

        # Tap token Pali xuat hien trong PDF (da chuan hoa, bo dau cach)
        pdf_blob = fold(text)

        stems = db_section_stems(doc)
        # Cat duoi "suttam/vaggo/..." va lay goc >=7 ky tu roi do trong PDF.
        hits = sum(1 for stem in stems if len(stem) >= 7 and stem[:11] in pdf_blob)
        pct = 100 * hits / max(1, len(stems))

        summary.setdefault(group, []).append(pct)
        flag = "" if pct >= 40 else "  <-- thấp"
        print(f"{group:<11} {label:<32} {pages:>6} {broken_pct:>6.1f}% {len(stems):>7} {hits:>6} {pct:>4.0f}%{flag}")

    print()
    print("Trung bình theo nhóm dịch giả:")
    for group, values in summary.items():
        print(f"  {group:<11} {sum(values)/len(values):>5.0f}%  ({len(values)} tập)")


if __name__ == "__main__":
    main()
