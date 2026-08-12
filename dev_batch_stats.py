"""Xem bản dịch trong DB đến từ đợt nạp nào, ghép bằng phương pháp nào.

Dùng để kiểm sau mỗi đợt nạp: đợt sau chỉ được LẤP CHỖ TRỐNG, không được làm tụt số
dòng của phương pháp chắc hơn. Nếu `strict_unique` giảm sau một đợt `global_align`
thì luật thứ hạng ở `human_translation_method_rank` đã hỏng - dừng lại, đừng nạp tiếp.

Chạy:
    .venv\\Scripts\\python.exe dev_batch_stats.py
    .venv\\Scripts\\python.exe dev_batch_stats.py --source indacanda
"""

import argparse
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from app.db import fetch_all


def main() -> None:
    parser = argparse.ArgumentParser(description="Thống kê bản dịch theo đợt nạp và phương pháp.")
    parser.add_argument("--source", help="chỉ xem một nguồn")
    args = parser.parse_args()

    where, params = "", []
    if args.source:
        where, params = "where source = %s", [args.source]

    print("=== theo nguồn và phương pháp ===")
    for row in fetch_all(
        f"""
        select source,
               coalesce(match_method, '(chưa gắn nhãn)') as method,
               count(*) as n
        from human_translations {where}
        group by 1, 2 order by 1, 3 desc
        """,
        params,
    ):
        print(f"  {row['source']:<16} {row['method']:<20} {row['n']:>7}")

    print("\n=== theo đợt nạp ===")
    for row in fetch_all(
        f"""
        select coalesce(import_batch, '(trước khi có cột)') as batch,
               coalesce(match_method, '-') as method,
               count(*) as n,
               max(updated_at) as last
        from human_translations {where}
        group by 1, 2 order by 3 desc limit 20
        """,
        params,
    ):
        print(f"  {row['batch']:<30} {row['method']:<16} {row['n']:>7}  {row['last']:%d/%m %H:%M}")

    print("\n=== tổng ===")
    for row in fetch_all(
        f"select count(*) as n, count(distinct source) as s from human_translations {where}",
        params,
    ):
        print(f"  {row['n']:,} dòng · {row['s']} nguồn")


if __name__ == "__main__":
    main()
