"""Cổng kiểm cuối cùng cho `export_data.sql` trước khi nạp VPS.

Vì sao cần
----------
Bản `export_data.sql` ngày 2026-08-14 nhìn từ ngoài không khác gì bản đúng, nhưng nó
được sinh TRƯỚC khi có migration 006 nên thiếu hẳn phần đó, thiếu luôn 101 dòng
`pdf_heading_boundary`, và không có `set client_encoding`. Nạp lên VPS thì
`human_translation_method_rank('pdf_heading_boundary')` trả 0 và đợt import kế tiếp đè
phẳng dữ liệu tốt nhất - hỏng âm thầm, không câu lệnh nào báo lỗi.

Script chỉ ĐỌC file, không chạm DB. Thoát khác 0 khi có mục KHÔNG ĐẠT.

Chạy:
    .venv\\Scripts\\python.exe dev_check_export_sql.py
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

MIGRATIONS = ("002_human_translations", "003_query_cache", "004_match_provenance",
              "005_import_batches", "006_pdf_heading_boundary")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Kiểm export_data.sql trước khi nạp VPS.")
    parser.add_argument("--file", default="export_data.sql")
    parser.add_argument(
        "--against-db",
        action="store_true",
        help="So số dòng trong file với DB đang chạy - bắt được bản xuất CŨ. Cần cho deploy.",
    )
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"KHÔNG ĐẠT: không thấy {path}")

    raw = path.read_bytes()
    print(f"file  : {path}  ({len(raw) / 1024 / 1024:.1f} MB)")

    checks: list[tuple[bool, str, str]] = []

    # 1. Mã hoá -----------------------------------------------------------------
    checks.append((raw[:3] != b"\xef\xbb\xbf", "không có BOM đầu file", "còn BOM"))
    try:
        text = raw.decode("utf-8")
        checks.append((True, "UTF-8 hợp lệ toàn file", ""))
    except UnicodeDecodeError as error:
        print(f"KHÔNG ĐẠT: file không phải UTF-8 hợp lệ ({error})")
        raise SystemExit(1)

    head = text[:4000].lower()
    checks.append((
        "set client_encoding = 'utf8'" in head,
        "có `set client_encoding = 'UTF8'` ở đầu",
        "THIẾU - psql trên Windows sẽ diễn giải sai toàn bộ tiếng Việt/Pali",
    ))

    # 2. Cấu trúc giao dịch -------------------------------------------------------
    lines = text.split("\n")
    stripped = [line.rstrip("\r") for line in lines]
    checks.append((stripped.count("begin;") == 1, "đúng một `begin;`", "sai số lượng `begin;`"))
    checks.append((stripped.count("commit;") == 1, "đúng một `commit;`", "sai số lượng `commit;`"))

    # 3. Migration nhúng ----------------------------------------------------------
    for name in MIGRATIONS:
        found = f"-- nguồn: db/migrations/{name}.sql" in text
        checks.append((found, f"có migration {name}", f"THIẾU migration {name}"))
    checks.append((
        "when 'pdf_heading_boundary' then 45" in text,
        "hàm xếp hạng biết `pdf_heading_boundary` = 45",
        "hàm xếp hạng KHÔNG biết `pdf_heading_boundary` - đợt import sau trên VPS sẽ đè phẳng",
    ))

    # 4. Dữ liệu ------------------------------------------------------------------
    sources = Counter(
        re.findall(r"insert into human_translations \(.*?\) select \(select p\.id.*?\), E'([a-z_]+)',", text)
    )
    methods = Counter(re.findall(r"E'(manual|pdf_heading_boundary|strict_unique|global_align|whole_unit|heuristic)', ", text))
    print("\nsố dòng human_translations theo nguồn:")
    for source, count in sorted(sources.items()):
        print(f"  {source:16} {count:>7,}")
    print(f"  {'TỔNG':16} {sum(sources.values()):>7,}")
    print("\nmatch_method:")
    for method, count in sorted(methods.items()):
        print(f"  {method:22} {count:>7,}")

    checks.append((
        methods.get("pdf_heading_boundary", 0) > 0,
        f"có {methods.get('pdf_heading_boundary', 0):,} dòng `pdf_heading_boundary`",
        "KHÔNG có dòng `pdf_heading_boundary` nào - bản xuất cũ hơn đợt nạp PDF",
    ))

    # 4b. So với DB ------------------------------------------------------------------
    # `> 0` một mình KHÔNG đủ, và chuyện đó đã xảy ra thật: bản xuất 2026-08-16 mang 125
    # dòng `pdf_heading_boundary` trong khi DB đã có 174 (thiếu trọn 49 dòng của `pr` sau
    # đợt sửa ranh giới), mà mọi mục vẫn ĐẠT và script in "sẵn sàng nạp VPS". Ca năm 2026-08-14
    # mà script này được viết ra để chặn là ca thiếu HẲN (0 dòng), nên `> 0` bắt được; thiếu
    # MỘT PHẦN thì nó không thấy gì.
    #
    # Chỉ so được với DB, vì một file cũ tự nó vẫn nhất quán - không có dấu hiệu nội tại nào
    # để phát hiện. Vì thế `--against-db` là cờ RIÊNG: mặc định vẫn giữ đúng cam kết "chỉ đọc
    # file, không chạm DB", nhưng trước khi nạp VPS thì phải chạy kèm cờ này.
    if args.against_db:
        from app.db import fetch_all

        db_methods = {
            str(row["match_method"]): row["n"]
            for row in fetch_all(
                "select match_method, count(*) as n from human_translations"
                " where match_method is not null group by match_method"
            )
        }
        db_sources = {
            str(row["source"]): row["n"]
            for row in fetch_all("select source, count(*) as n from human_translations group by source")
        }
        print("\nso với DB đang chạy:")
        for label, in_file, in_db in (
            *[(f"method {m}", methods.get(m, 0), db_methods.get(m, 0)) for m in sorted(db_methods)],
            *[(f"source {s}", sources.get(s, 0), db_sources.get(s, 0)) for s in sorted(db_sources)],
        ):
            mark = "khớp" if in_file == in_db else "LỆCH"
            print(f"  {label:34} file {in_file:>7,}  DB {in_db:>7,}  {mark}")
            checks.append((
                in_file == in_db,
                f"{label}: file khớp DB ({in_db:,})",
                f"{label}: file có {in_file:,} nhưng DB có {in_db:,} - BẢN XUẤT CŨ, phải chạy lại export_data.py",
            ))

    # 5. Chuỗi có hợp lệ không ------------------------------------------------------
    odd = sum(1 for line in stripped if line.startswith(("insert", "delete")) and line.count("'") % 2)
    checks.append((odd == 0, "mọi hằng chuỗi đều đóng đúng", f"{odd} dòng lẻ dấu nháy"))

    escapes = set(re.findall(r"\\(.)", text))
    allowed = escapes <= {"n", "r", "\\"}
    checks.append((allowed, f"chỉ dùng escape hợp lệ {sorted(escapes)}", f"có escape lạ {sorted(escapes - {'n', 'r', chr(92)})}"))

    # 6. Ký tự hỏng ------------------------------------------------------------------
    bad = Counter()
    for char in text:
        if char in ("�", "﻿", "​"):
            bad[f"U+{ord(char):04X}"] += 1
        elif unicodedata.category(char) in ("Co",) or (
            unicodedata.category(char) == "Cc" and char not in "\r\n\t"
        ):
            bad[f"U+{ord(char):04X}"] += 1
    checks.append((not bad, "không còn ký tự Unicode hỏng", f"còn {dict(bad)}"))

    for pattern, label in ((r"Ã[\x80-\xbf¡-ÿ]", "Ã…"), ("â€", "â€"), ("Ä‘", "Ä‘")):
        hits = len(re.findall(pattern, text))
        checks.append((hits == 0, f"không có dấu hiệu mojibake {label}", f"{hits} chỗ mojibake {label}"))

    # Kết luận -----------------------------------------------------------------------
    print("\n=== KẾT QUẢ ===")
    failed = 0
    for ok, good, bad_text in checks:
        print(f"  {'ĐẠT     ' if ok else 'KHÔNG ĐẠT'} {good if ok else bad_text}")
        failed += 0 if ok else 1

    if failed:
        print(f"\n{failed} mục KHÔNG ĐẠT - chưa được nạp lên VPS.")
        raise SystemExit(1)
    print("\nTẤT CẢ ĐẠT - file sẵn sàng nạp VPS.")


if __name__ == "__main__":
    main()
