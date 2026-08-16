"""Dò xem 27 tập còn lại có tồn tại "đơn vị đọc" để cắt trọn bài hay không.

Vì sao cần bước này trước
-------------------------
`indacanda_full_extract._is_unit()` có nhánh cứng cho `dn2`/`sn`/`pts2` và trả False cho
mọi tập khác, nên không thể "chạy thử cả 27 tập rồi xem". Mỗi tập phải có luật riêng:
lấy section ở CẤP nào, nhận những hậu tố tiêu đề nào.

Script này trả lời đúng một câu, cho từng tập: **trong DB có tồn tại một cấp nào mà các
section ở đó trông giống đơn vị người đọc không?** Tiêu chí:

- các khoảng anh em KHÔNG chồng lấn (chồng lấn nghĩa là chọn nhầm cấp);
- số lượng nằm trong khoảng hợp lý (quá ít là chương, quá nhiều là đoạn con);
- độ dài trung vị đủ để là một bài chứ không phải một câu.

Tập nào không cấp nào đạt thì kết luận được ngay là khó, khỏi tốn công dựng cấu hình.

CHỈ ĐỌC DB, không đụng PDF, không ghi gì. Kết quả ra JSON để turn bị ngắt không mất số đo.

Chạy:
    .venv\\Scripts\\python.exe profile_indacanda_full.py
    .venv\\Scripts\\python.exe profile_indacanda_full.py kn1 thag vvpv
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

from app.db import fetch_all
from app.normalize import normalize_pali
from import_indacanda import VOLUMES
from indacanda_full_extract import SUPPORTED_VOLUMES

# Ngưỡng chỉ để XẾP LOẠI trong báo cáo, không phải luật nạp dữ liệu. Cố ý rộng: mục đích
# là khoanh vùng tập nào đáng làm tiếp, không phải chốt cấu hình cuối.
MIN_UNITS = 3
MAX_UNITS = 900
MIN_MEDIAN_SPAN = 3

# Hậu tố CHỈ ĐIỂM một đơn vị người đọc. Bản đầu của script này không xét hậu tố mà chỉ
# lấy cấp nông nhất không chồng lấn - và ra kết quả sai ở gần hết các tập, vì cấp đó là
# CHƯƠNG: `-vaggo` (phẩm), `-nipato`, `-kandam`, `-vibhango`, `-varo`, `-bhumi`. Nhận
# nhầm chương thành "toàn bộ bài kinh" đúng là cái bẫy CLAUDE.md đã cảnh báo.
#
# `katha` nằm đây vì `pts2` cố ý dùng nó làm đơn vị, dù luật đọc toàn cục từ chối - đây
# là script dò, không phải luật nạp, nên để rộng rồi người đọc tự chốt theo từng tập.
READER_SUFFIXES = (
    "sutta", "suttam", "suttanta", "suttantam",
    "jatakam", "apadanam", "gatha", "vatthu", "cariya", "vamso",
    "sikkhapadam", "parajikam", "khandhako", "khandhakam", "puccha",
    "panho", "niddeso", "vimanam", "theragatha", "therigatha", "katha",
)
# Cấp nào có ít nhất ngần này tiêu đề mang hậu tố đơn vị đọc thì coi là ứng viên thật.
READER_SUFFIX_RATIO = 0.6
# Vài chỗ chồng lấn thường là section gốc tài liệu bao trùm tất cả, không phải chọn nhầm
# cấp. Cho phép một ít thay vì loại thẳng - loại thẳng chính là thứ đẩy bản đầu xuống
# cấp chương.
MAX_OVERLAP_RATIO = 0.05


def _suffix(title: str) -> str:
    stem = normalize_pali(str(title or "")).replace(" ", "")
    for size in (12, 10, 8, 6):
        if len(stem) > size:
            return stem[-size:]
    return stem


def profile(volume: str) -> dict:
    config = VOLUMES[volume]
    docs = fetch_all(
        "select id, file_name from documents where file_name = any(%s)", [config["docs"]]
    )
    if not docs:
        return {"volume": volume, "verdict": "KHÔNG CÓ TÀI LIỆU", "docs": config["docs"]}

    doc_ids = [str(row["id"]) for row in docs]
    rows = fetch_all(
        """
        select s.document_id, s.title, s.level, s.start_sort_order,
               coalesce(s.end_sort_order, s.start_sort_order) as end_sort_order
        from sections s where s.document_id = any(%s::uuid[])
        order by s.document_id, s.start_sort_order
        """,
        [doc_ids],
    )
    by_level: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        by_level[int(row["level"])].append(row)

    levels = []
    for level, items in sorted(by_level.items()):
        spans = [r["end_sort_order"] - r["start_sort_order"] + 1 for r in items]
        # Chồng lấn xét TRONG TỪNG tài liệu; nhiều tập dùng chung vài document.
        overlap = 0
        per_doc: dict[str, list[dict]] = defaultdict(list)
        for row in items:
            per_doc[str(row["document_id"])].append(row)
        for group in per_doc.values():
            group.sort(key=lambda r: r["start_sort_order"])
            for a, b in zip(group, group[1:]):
                if b["start_sort_order"] <= a["end_sort_order"]:
                    overlap += 1
        suffixes = Counter(_suffix(r["title"]) for r in items)
        reader_like = sum(
            1
            for r in items
            if normalize_pali(str(r["title"] or "")).replace(" ", "").rstrip("0123456789()")
            .endswith(READER_SUFFIXES)
        )
        levels.append(
            {
                "level": level,
                "count": len(items),
                "median_span": statistics.median(spans),
                "max_span": max(spans),
                "overlaps": overlap,
                "reader_suffix_ratio": round(reader_like / len(items), 2),
                "top_suffixes": suffixes.most_common(4),
                "sample_titles": [str(r["title"]) for r in items[:3]],
            }
        )

    usable = [
        lv
        for lv in levels
        if lv["reader_suffix_ratio"] >= READER_SUFFIX_RATIO
        and lv["overlaps"] <= max(1, lv["count"] * MAX_OVERLAP_RATIO)
        and MIN_UNITS <= lv["count"] <= MAX_UNITS
        and lv["median_span"] >= MIN_MEDIAN_SPAN
    ]
    # Cấp SÂU NHẤT đạt tiêu chí: cấp nông hơn gần như luôn là chương chứa nó.
    best = max(usable, key=lambda lv: lv["level"]) if usable else None
    return {
        "volume": volume,
        "docs": config["docs"],
        "verdict": "CÓ ĐƠN VỊ ĐỌC" if best else "CHỈ THẤY CẤP CHƯƠNG",
        "suggested_level": best["level"] if best else None,
        "suggested_units": best["count"] if best else None,
        "suggested_suffixes": best["top_suffixes"] if best else None,
        "suggested_overlaps": best["overlaps"] if best else None,
        "levels": levels,
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    wanted = [v for v in sys.argv[1:] if not v.startswith("-")]
    if not wanted:
        wanted = [v for v in VOLUMES if v not in SUPPORTED_VOLUMES]

    out: list[dict] = []
    for volume in wanted:
        if volume not in VOLUMES:
            print(f"  bỏ qua {volume}: không có trong VOLUMES")
            continue
        try:
            report = profile(volume)
        except Exception as error:  # noqa: BLE001 - một tập hỏng không được chặn cả loạt
            report = {"volume": volume, "verdict": f"LỖI: {type(error).__name__}: {error}"}
        out.append(report)
        Path("indacanda_full_profile.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        head = f"{volume:6} {report['verdict']}"
        if report.get("suggested_level") is not None:
            head += (
                f"  → cấp {report['suggested_level']}"
                f" · {report['suggested_units']:,} đơn vị"
            )
        print(head, flush=True)
        if report.get("suggested_suffixes"):
            tail = ", ".join(f"{s}({n})" for s, n in report["suggested_suffixes"])
            print(f"        hậu tố hay gặp: {tail}", flush=True)

    ok = [r for r in out if r["verdict"] == "CÓ ĐƠN VỊ ĐỌC"]
    print(f"\n=== {len(ok)}/{len(out)} tập có đơn vị đọc thật ===")
    for report in out:
        if report["verdict"] != "CÓ ĐƠN VỊ ĐỌC":
            print(f"  {report['volume']:6} {report['verdict']}")
    print("\nđã ghi indacanda_full_profile.json")
    print("Đây MỚI là bước khoanh vùng. Có ứng viên chưa có nghĩa là cắt được:")
    print("còn phải dò tiêu đề in trong PDF và kiểm mắt từng bố cục mới biết.")


if __name__ == "__main__":
    main()
