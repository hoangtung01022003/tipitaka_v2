"""Kiểm tra và sửa tại chỗ lỗi tách chữ trong dữ liệu Indacanda đã nạp.

Mặc định chỉ thống kê và in mẫu (dry-run). Chỉ ghi DB khi truyền ``--apply``.
Hàm sửa dùng chung ``import_indacanda.mend_spacing`` nên dữ liệu cũ và các lần import
mới tuân theo đúng một quy tắc, chạy lặp lại không làm thay đổi thêm dữ liệu.
"""

from __future__ import annotations

import argparse
import sys

from app.db import fetch_all, get_conn
from import_indacanda import mend_spacing


DEFAULT_SOURCES = ("indacanda", "indacanda_full")


def _excerpt(old: str, new: str, radius: int = 90) -> tuple[str, str]:
    index = 0
    limit = min(len(old), len(new))
    while index < limit and old[index] == new[index]:
        index += 1
    start = max(0, index - radius)
    end_old = min(len(old), index + radius)
    end_new = min(len(new), index + radius)
    prefix = "…" if start else ""
    return prefix + old[start:end_old], prefix + new[start:end_new]


def scan(sources: list[str], limit: int | None = None) -> tuple[list[tuple], dict[str, int]]:
    sql = """
        select passage_id, source, translated_text
        from human_translations
        where source = any(%s)
        order by source, passage_id
    """
    params: list[object] = [sources]
    if limit:
        sql += " limit %s"
        params.append(limit)

    changes: list[tuple] = []
    totals = {source: 0 for source in sources}
    for row in fetch_all(sql, params):
        source = str(row["source"])
        totals[source] = totals.get(source, 0) + 1
        old = str(row["translated_text"] or "")
        new = mend_spacing(old)
        if new != old:
            changes.append((row["passage_id"], source, old, new))
    return changes, totals


def apply_changes(changes: list[tuple]) -> int:
    if not changes:
        return 0
    params = [(new, passage_id, source, old) for passage_id, source, old, new in changes]
    with get_conn() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    update human_translations
                    set translated_text = %s
                    where passage_id = %s and source = %s and translated_text = %s
                    """,
                    params,
                )
                return cur.rowcount


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Kiểm tra/sửa lỗi tách chữ của Indacanda; mặc định chỉ dry-run."
    )
    parser.add_argument(
        "--source",
        action="append",
        choices=DEFAULT_SOURCES,
        dest="sources",
        help="Nguồn cần xử lý; có thể truyền nhiều lần. Mặc định xử lý cả hai nguồn.",
    )
    parser.add_argument("--apply", action="store_true", help="Ghi các thay đổi đã kiểm tra vào DB.")
    parser.add_argument("--sample-size", type=int, default=20, help="Số mẫu trước/sau cần in.")
    parser.add_argument("--limit", type=int, help="Chỉ quét N dòng đầu để thử script.")
    args = parser.parse_args()

    sources = args.sources or list(DEFAULT_SOURCES)
    changes, totals = scan(sources, args.limit)
    print("=== kiểm tra lỗi tách chữ Indacanda ===")
    for source in sources:
        changed = sum(1 for item in changes if item[1] == source)
        print(f"{source:16} {changed:6,}/{totals.get(source, 0):6,} dòng cần sửa")
    print(f"tổng: {len(changes):,} dòng cần sửa")

    for index, (_passage_id, source, old, new) in enumerate(changes[: max(0, args.sample_size)], 1):
        old_excerpt, new_excerpt = _excerpt(old, new)
        print(f"\n[{index}] {source}\n- {old_excerpt}\n+ {new_excerpt}")

    if not args.apply:
        print("\nDRY-RUN: chưa ghi dữ liệu. Chạy lại với --apply sau khi xem các mẫu trên.")
        return

    updated = apply_changes(changes)
    remaining, _ = scan(sources, args.limit)
    print(f"\nđã cập nhật: {updated:,} dòng")
    print(f"còn có thể sửa theo cùng quy tắc: {len(remaining):,} dòng")
    if remaining:
        raise SystemExit("Còn dòng chưa ổn định; không nên export dữ liệu lúc này.")


if __name__ == "__main__":
    main()
