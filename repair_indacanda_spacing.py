"""Kiểm tra và sửa tại chỗ lỗi khoảng trắng trong dữ liệu bản dịch đã nạp.

Mặc định chỉ thống kê và in mẫu (dry-run). Chỉ ghi DB khi truyền ``--apply``.

MỖI NGUỒN MỘT LUẬT SỬA - không dùng chung một hàm cho tất cả
------------------------------------------------------------
``mend_spacing`` được dựng cho text layer PDF của Indacanda: nó nối lại các mảnh chữ
bằng từ điển ``app/data/vi_words.txt``. Áp nguyên hàm đó sang nguồn khác thì phá dữ liệu,
đã đo trên chính kho hiện có:

- ``minh_chau`` đến từ HTML (SuttaCentral/budsas) chứ không phải PDF, nên KHÔNG có lỗi
  tách chữ để mà sửa; ngược lại nó viết tên Pali tách rời theo lối phiên âm cũ
  (``A tu la``, ``Sà la``, ``Ma hà``). Cho ``_join_word_spaces`` chạy vào thì 18/21 ca
  là sai, trong đó có ca đổi hẳn nghĩa: ``Nếu Ta y trước`` -> ``Nếu Tay trước``,
  ``Kalandaka Nivapa`` -> ``KalandakaNivapa``, ``Bhikkhu Ṭhānissaro`` -> ``BhikkhuṬhānissaro``.
  ``_SPLIT_HYPHEN`` cũng nuốt gạch đầu dòng thoại: ``Vi-n 24 -Này Hiền giả`` -> ``24-Này``.
  Nên nguồn này chỉ được áp hai luật dấu câu, đã soát đủ 563/563 ca và không có ca sai.
- ``sujato``/``brahmali`` là tiếng Anh, tuyệt đối không đưa vào đây: ``So I have heard``
  -> ``SoI have heard``, ``the suffix -cakka`` -> ``suffix-cakka``, và luật dấu câu phá
  ký hiệu elision Pali ``hīne ’dhimuttaṁ`` -> ``hīne’dhimuttaṁ``.

Mọi luật đều bất biến khi chạy lại, nên chạy nhiều lần không sinh thêm thay đổi.
"""

from __future__ import annotations

import argparse
import sys
from typing import Callable

from app.db import fetch_all, get_conn
from app.text_artifacts import clean_unicode_artifacts
from import_indacanda import (
    _SPACE_AFTER_OPENING_PUNCTUATION,
    _SPACE_BEFORE_CLOSING_PUNCTUATION,
    mend_spacing,
)


def punctuation_spacing_only(text: str) -> str:
    """Chỉ gỡ khoảng trắng thừa sát dấu câu, không đụng tới ranh giới giữa hai chữ.

    Dùng cho nguồn không phải PDF: sửa được ``đoạt .`` -> ``đoạt.`` và ``( … )`` ->
    ``(…)`` mà không có cơ hội nối nhầm hai từ vốn đã đúng.
    """
    text = clean_unicode_artifacts(text)
    text = _SPACE_AFTER_OPENING_PUNCTUATION.sub("", text)
    return _SPACE_BEFORE_CLOSING_PUNCTUATION.sub("", text)


# Nguồn nào chưa có tên ở đây thì không sửa được - thêm nguồn phải kèm việc soát mẫu
# trước, đừng mặc định gán `mend_spacing`.
REPAIRS: dict[str, Callable[[str], str]] = {
    "indacanda": mend_spacing,
    "indacanda_full": mend_spacing,
    "minh_chau": punctuation_spacing_only,
}

DEFAULT_SOURCES = tuple(REPAIRS)


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
        repair = REPAIRS.get(source)
        if repair is None:
            continue
        old = str(row["translated_text"] or "")
        new = repair(old)
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
        description="Kiểm tra/sửa lỗi khoảng trắng của bản dịch đã nạp; mặc định chỉ dry-run."
    )
    parser.add_argument(
        "--source",
        action="append",
        choices=DEFAULT_SOURCES,
        dest="sources",
        help="Nguồn cần xử lý; có thể truyền nhiều lần. Mặc định xử lý mọi nguồn có luật sửa.",
    )
    parser.add_argument("--apply", action="store_true", help="Ghi các thay đổi đã kiểm tra vào DB.")
    parser.add_argument("--sample-size", type=int, default=20, help="Số mẫu trước/sau cần in.")
    parser.add_argument("--limit", type=int, help="Chỉ quét N dòng đầu để thử script.")
    args = parser.parse_args()

    sources = args.sources or list(DEFAULT_SOURCES)
    changes, totals = scan(sources, args.limit)
    print("=== kiểm tra lỗi khoảng trắng trong bản dịch đã nạp ===")
    for source in sources:
        changed = sum(1 for item in changes if item[1] == source)
        rule = "nối chữ + dấu câu" if REPAIRS[source] is mend_spacing else "chỉ dấu câu"
        print(f"{source:16} {changed:6,}/{totals.get(source, 0):6,} dòng cần sửa  [{rule}]")
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
