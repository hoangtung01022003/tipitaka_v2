"""Sửa có kiểm soát các ký tự Unicode lỗi đã đối chiếu được trong bản dịch.

Mặc định chỉ dry-run. Truyền ``--apply`` để ghi DB; câu UPDATE kèm nội dung cũ nên
không thể ghi đè nếu một tiến trình khác vừa sửa cùng bản ghi.
"""

from __future__ import annotations

import argparse
import sys

from app.db import fetch_all, get_conn
from app.text_artifacts import clean_unicode_artifacts, unicode_artifacts
from import_indacanda import mend_spacing


INDACANDA_SOURCES = {"indacanda", "indacanda_full"}


def repaired_text(source: str, text: str) -> str:
    cleaned = clean_unicode_artifacts(text)
    if source in INDACANDA_SOURCES:
        cleaned = mend_spacing(cleaned)
    return cleaned


def scan() -> tuple[list[tuple], int]:
    rows = fetch_all(
        """
        select passage_id, source, translated_text
        from human_translations
        order by source, passage_id
        """
    )
    changes: list[tuple] = []
    for row in rows:
        source = str(row["source"])
        old = str(row["translated_text"] or "")
        new = repaired_text(source, old)
        if new != old:
            changes.append((row["passage_id"], source, old, new))
    return changes, len(rows)


def apply_changes(changes: list[tuple]) -> int:
    if not changes:
        return 0
    params = [(new, passage_id, source, old) for passage_id, source, old, new in changes]
    with get_conn() as conn:
        with conn.transaction():
            with conn.cursor() as cursor:
                cursor.executemany(
                    """
                    update human_translations
                    set translated_text = %s, updated_at = now()
                    where passage_id = %s and source = %s and translated_text = %s
                    """,
                    params,
                )
                return cursor.rowcount


def excerpt(old: str, new: str, radius: int = 100) -> tuple[str, str]:
    index = 0
    while index < min(len(old), len(new)) and old[index] == new[index]:
        index += 1
    start = max(0, index - radius)
    return old[start : index + radius], new[start : index + radius]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Dò/sửa Unicode lỗi; mặc định chỉ dry-run.")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    changes, total = scan()
    print(f"đã quét: {total:,} dòng")
    print(f"cần sửa: {len(changes):,} dòng")
    for index, (_passage_id, source, old, new) in enumerate(changes, 1):
        before, after = excerpt(old, new)
        print(f"\n[{index}] {source}\n- {before}\n+ {after}")
    if not args.apply:
        print("\nDRY-RUN: chưa ghi database.")
        return

    updated = apply_changes(changes)
    remaining, _ = scan()
    hazards = sum(
        sum(unicode_artifacts(str(row["translated_text"] or "")).values())
        for row in fetch_all("select translated_text from human_translations")
    )
    print(f"\nđã cập nhật: {updated:,} dòng")
    print(f"còn thay đổi theo bộ sửa: {len(remaining):,} dòng")
    print(f"còn ký tự Unicode lỗi: {hazards:,}")
    if remaining or hazards:
        raise SystemExit("Chưa sạch; không nên export dữ liệu.")


if __name__ == "__main__":
    main()
