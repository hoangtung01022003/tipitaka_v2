"""Báo cáo độ phủ của các bản dịch có tên dịch giả.

Chạy: .venv\\Scripts\\python.exe dev_coverage.py
"""

import sys

sys.stdout.reconfigure(encoding="utf-8")

from app.db import fetch_all, fetch_one

# Tên tiếng Việt của từng bộ, khớp theo tiền tố mã bài kinh (source_ref).
NIKAYA_LABELS = {
    "dn": "Trường Bộ",
    "mn": "Trung Bộ",
    "sn": "Tương Ưng Bộ",
    "an": "Tăng Chi Bộ",
    "ud": "Tiểu Bộ - Phật Tự Thuyết",
    "iti": "Tiểu Bộ - Phật Thuyết Như Vậy",
    "snp": "Tiểu Bộ - Kinh Tập",
}


def main() -> None:
    total = fetch_one("select count(*) c from human_translations")
    print(f"Tổng số đoạn có bản dịch của dịch giả: {total['c']}\n")

    rows = fetch_all(
        """
        select source,
               regexp_replace(source_ref, '[0-9].*$', '') as nikaya,
               count(distinct source_ref) as suttas,
               count(*) as passages
        from human_translations
        group by 1, 2
        order by 1, 4 desc
        """
    )
    print(f"{'nguồn':<10} {'bộ kinh':<32} {'bài':>6} {'đoạn':>8}")
    print("-" * 60)
    for row in rows:
        label = NIKAYA_LABELS.get(row["nikaya"], row["nikaya"])
        print(f"{row['source']:<10} {label:<32} {row['suttas']:>6} {row['passages']:>8}")

    print("\nCác lần nạp đã chạy:")
    for row in fetch_all(
        """
        select scope, segments_total, segments_matched, passages_written, created_at
        from human_translation_imports order by created_at
        """
    ):
        rate = 100 * row["segments_matched"] // max(1, row["segments_total"])
        stamp = row["created_at"].strftime("%d/%m %H:%M")
        print(f"  {stamp}  {row['scope']:<6} {row['segments_matched']:>6}/{row['segments_total']:<6} ({rate:>3}%) -> {row['passages_written']} đoạn")

    print("\nĐộ phủ trên các tài liệu đã nạp:")
    for row in fetch_all(
        """
        select d.file_name, d.title,
               count(p.id) as passages,
               count(h.id) as translated
        from documents d
        join passages p on p.document_id = d.id
        left join human_translations h on h.passage_id = p.id
        where d.corpus_type = 'mul'
        group by d.file_name, d.title
        having count(h.id) > 0
        order by d.file_name
        """
    ):
        pct = 100 * row["translated"] // max(1, row["passages"])
        print(f"  {row['file_name']:<18} {str(row['title'])[:26]:<28} {row['translated']:>6}/{row['passages']:<6} ({pct:>3}%)")


if __name__ == "__main__":
    main()
