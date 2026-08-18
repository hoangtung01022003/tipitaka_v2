"""Đo xem nút "Xem toàn bộ bài kinh" có thật sự mở ra TRỌN bài kinh hay không.

Vì sao cần đo
-------------
`main._canonical_reader_section` nâng mục con mà đoạn tìm kiếm đang nằm trong đó lên
đơn vị đọc hoàn chỉnh gần nhất. Nhưng nó chỉ nhận một mục làm "đơn vị đọc" khi tiêu đề
qua được `_is_reader_unit_title`, và khi KHÔNG mục nào qua được thì hàm **âm thầm trả
lại chính mục con** - người đọc bấm "toàn bộ bài kinh" nhưng nhận về một trích đoạn,
kèm luôn phần Pali cũng chỉ là trích đoạn.

Đó là điều khách báo ở ý (2). Sửa xong phần nhãn/gộp nguồn vẫn chưa trả lời được câu
"có bao nhiêu bài bị như vậy", nên script này đếm ra con số trước khi tuyên bố đã xong.

Rủi ro tập trung ở Tạng Luật, Vi Diệu Pháp và các sách kệ, nơi tiêu đề không kết thúc
bằng `sutta(ṃ)`.

Script CHỈ ĐỌC, không có INSERT/UPDATE/DELETE. Kết quả ghi ra JSON để một turn bị ngắt
giữa chừng không làm mất số đo.

Chạy:
    .venv\\Scripts\\python.exe dev_reader_unit_check.py
    .venv\\Scripts\\python.exe dev_reader_unit_check.py --samples 30 --out reader_units.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from app.db import fetch_all
from app.main import (
    READER_FALLBACK_MAX_PASSAGES,
    _READER_DEEPEST_CORPUS,
    READER_FALLBACK_MIN_PASSAGES,
    _is_context_dependent_reader_title,
    _is_enumerated_title,
    _is_reader_unit_row,
    _source_path_is_prefix,
)


def _canonical_in_memory(section: dict, siblings: list[dict]) -> dict | None:
    """Bản sao logic của `_canonical_reader_section`, chạy trên dữ liệu đã nạp sẵn.

    Gọi thẳng hàm thật thì tốn một truy vấn cho mỗi mục (hàng chục nghìn lượt). Ở đây
    nạp toàn bộ `sections` một lần rồi lọc trong bộ nhớ, nhưng phải giữ ĐÚNG thứ tự sắp
    xếp và ĐÚNG hai bậc của bản gốc - nếu bản sao lệch thì con số đo được không nói gì
    về hành vi thật.

    Trả về `None` khi cả hai bậc đều trượt, tức là bản gốc sẽ giữ nguyên mục con.
    """
    # Chú giải/phụ chú giải/sách phụ: lấy thẳng mục sâu nhất, xem `_READER_DEEPEST_CORPUS`.
    if str(section.get("corpus_type") or "") in _READER_DEEPEST_CORPUS:
        return section

    start = section["start_sort_order"]
    end = section["end_sort_order"]
    candidates = [
        row
        for row in siblings
        if row["start_sort_order"] <= start and row["end_sort_order"] >= end
    ]
    candidates.sort(
        key=lambda row: (
            row["end_sort_order"] - row["start_sort_order"],
            -len(row["source_path"] or []),
        )
    )
    within_tree = [
        row
        for row in candidates
        if _source_path_is_prefix(row.get("source_path"), section.get("source_path"))
    ]

    # Bậc 1: tổ tiên có tiêu đề là đơn vị đọc thật, xét theo HAI LỚP hậu tố - lớp dứt
    # khoát trước, lớp phụ thuộc ngữ cảnh (`kathā`/`khandhaka`) sau. Xem
    # `_READER_CONTEXT_SUFFIXES` trong `app/main.py`; lệch phần này thì con số đo ra không
    # nói gì về hành vi thật của `_canonical_reader_section`.
    unit_rows = [row for row in within_tree if _is_reader_unit_row(row)]
    for row in unit_rows:
        if _is_context_dependent_reader_title(str(row.get("title") or "")):
            continue
        return row
    if unit_rows:
        return unit_rows[0]

    # Bậc 2: chỉ khi mục con là mẩu vụn thì mới leo lên tổ tiên gần nhất trong trần - và
    # tiêu đề CÓ SỐ THỨ TỰ thì không phải mẩu vụn, xem `_is_enumerated_title`.
    own = section["end_sort_order"] - section["start_sort_order"] + 1
    if own < READER_FALLBACK_MIN_PASSAGES and not _is_enumerated_title(section.get("title")):
        for row in within_tree:
            if str(row["id"]) == str(section["id"]):
                continue
            if row["end_sort_order"] - row["start_sort_order"] + 1 <= READER_FALLBACK_MAX_PASSAGES:
                return row
            break
    return None


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Đo độ phủ của đơn vị đọc trọn bài.")
    parser.add_argument("--samples", type=int, default=15, help="Số mục hỏng cần in ra.")
    parser.add_argument("--out", default="reader_unit_check.json", help="File JSON kết quả.")
    args = parser.parse_args()

    print("đang nạp sections…", flush=True)
    sections = fetch_all(
        """
        select s.id, s.document_id, s.title, s.source_path, s.start_sort_order,
               coalesce(s.end_sort_order, s.start_sort_order) as end_sort_order,
               d.file_name, d.corpus_type
        from sections s
        join documents d on d.id = s.document_id
        """
    )
    by_document: dict[str, list[dict]] = defaultdict(list)
    for row in sections:
        by_document[str(row["document_id"])].append(row)
    print(f"  {len(sections):,} sections trong {len(by_document):,} tài liệu")

    # Chỉ những mục mà `passages.section_id` thật sự trỏ tới mới là chỗ người đọc có thể
    # rơi vào; đếm cả những mục không đoạn nào trỏ tới sẽ thổi phồng con số.
    print("đang nạp số đoạn theo mục…", flush=True)
    landing = fetch_all(
        """
        select section_id, count(*) as passage_count
        from passages
        where section_id is not null
        group by section_id
        """
    )
    by_id = {str(row["id"]): row for row in sections}
    print(f"  {len(landing):,} mục có đoạn trỏ tới")

    ok_sections = ok_passages = 0
    bad_sections = bad_passages = 0
    self_unit = promoted = 0
    bad_by_corpus: Counter[str] = Counter()
    all_by_corpus: Counter[str] = Counter()
    bad_by_file: Counter[str] = Counter()
    samples: list[dict] = []

    for row in landing:
        section = by_id.get(str(row["section_id"]))
        if section is None:
            continue
        count = int(row["passage_count"])
        corpus = str(section["corpus_type"])
        all_by_corpus[corpus] += count
        resolved = _canonical_in_memory(section, by_document[str(section["document_id"])])
        if resolved is None:
            bad_sections += 1
            bad_passages += count
            bad_by_corpus[corpus] += count
            bad_by_file[str(section["file_name"])] += count
            if len(samples) < args.samples:
                samples.append(
                    {
                        "file_name": str(section["file_name"]),
                        "corpus_type": corpus,
                        "title": str(section["title"] or ""),
                        "source_path": [str(x) for x in (section["source_path"] or [])],
                        "range": [section["start_sort_order"], section["end_sort_order"]],
                        "passages": count,
                    }
                )
        else:
            ok_sections += 1
            ok_passages += count
            if str(resolved["id"]) == str(section["id"]):
                self_unit += 1
            else:
                promoted += 1

    total_sections = ok_sections + bad_sections
    total_passages = ok_passages + bad_passages
    print("\n=== nút 'Xem toàn bộ bài kinh' mở ra đúng trọn bài? ===")
    print(f"  mục đọc được trọn đơn vị : {ok_sections:>7,}/{total_sections:,}")
    print(f"     - bản thân đã là đơn vị: {self_unit:>7,}")
    print(f"     - được nâng lên bài cha: {promoted:>7,}")
    print(f"  mục RƠI VỀ mục con        : {bad_sections:>7,}/{total_sections:,}")
    if total_passages:
        print(
            f"  tính theo số đoạn người đọc thật sự chạm tới: "
            f"{bad_passages:,}/{total_passages:,} "
            f"({bad_passages * 100 / total_passages:.1f}%) mở ra trích đoạn"
        )

    print("\n--- tỉ lệ hỏng theo corpus (theo số đoạn) ---")
    for corpus, total in all_by_corpus.most_common():
        bad = bad_by_corpus.get(corpus, 0)
        print(f"  {corpus:6} {bad:>7,}/{total:>7,}  ({bad * 100 / total:5.1f}%)")

    print("\n--- 15 tài liệu hỏng nhiều nhất (theo số đoạn) ---")
    for file_name, bad in bad_by_file.most_common(15):
        print(f"  {file_name:28} {bad:>7,}")

    print(f"\n--- {len(samples)} mẫu mục rơi về mục con ---")
    for item in samples:
        print(f"  [{item['corpus_type']}] {item['file_name']} · {item['passages']} đoạn")
        print(f"      title      : {item['title']!r}")
        print(f"      source_path: {' -> '.join(item['source_path'])}")

    report = {
        "sections_total": total_sections,
        "sections_ok": ok_sections,
        "sections_fallback": bad_sections,
        "sections_self_unit": self_unit,
        "sections_promoted": promoted,
        "passages_total": total_passages,
        "passages_fallback": bad_passages,
        "by_corpus": {
            corpus: {"passages": total, "fallback": bad_by_corpus.get(corpus, 0)}
            for corpus, total in all_by_corpus.items()
        },
        "worst_files": bad_by_file.most_common(50),
        "samples": samples,
    }
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nđã ghi {args.out}")


if __name__ == "__main__":
    main()
