"""Kiểm toán độc lập các vết tách chữ còn sót trong dữ liệu Indacanda.

Script này không sửa dữ liệu. Khác với ``repair_indacanda_spacing.py``, nó không dùng
danh sách từ cần nối để kết luận đạt/chưa đạt mà dò lại theo hình dạng vết cắt và theo
dạng viết liền thực sự tồn tại trong kho. Dùng ``--preview-repair`` để kiểm toán kết quả
mô phỏng trước khi chạy ``--apply``.
"""

from __future__ import annotations

import argparse
from collections import Counter
import re
import sys
import unicodedata

from app.db import fetch_all
from app.text_artifacts import clean_unicode_artifacts, unicode_artifacts
from import_indacanda import mend_spacing


SOURCES = ("indacanda", "indacanda_full")
LETTER_TOKEN = re.compile(r"[^\W\d_]+", re.UNICODE)
PALI_MARKED = frozenset("āīūṅñṭḍṇḷṃṁĀĪŪṄÑṬḌṆḶṂṀ")
PALI_FRAGMENT = re.compile(r"^[A-Za-zāīūṅñṭḍṇḷṃṁ]+$", re.IGNORECASE)
VOWELS = frozenset("aeiouy")

# Các cặp hợp lệ nhưng dạng viết dính tình cờ cũng xuất hiện ở một dòng lỗi khác.
# So sánh không phân biệt hoa/thường.
LEGITIMATE_PAIRS = {
    ("cây", "pāṭali"),
    ("dhammenā", "ti"),
    ("do", "anh"),
    ("điều", "học"),
    ("được", "đạt"),
    ("đức", "thế"),
    ("hoảng", "hốt"),
    ("gaṅgā", "có"),
    ("idam", "eva"),
    ("kết", "với"),
    ("khi", "ra"),
    ("kosa", "là"),
    ("sự", "hãi"),
    ("sự", "hiện"),
    ("trong", "khi"),
    ("và", "y"),
}

def _base_letters(value: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFD", value)
        if unicodedata.category(char) != "Mn"
    ).lower()


def _starts_with_marked_letter(value: str) -> bool:
    if not value:
        return False
    first = value[0]
    return first in PALI_MARKED or len(unicodedata.normalize("NFD", first)) > 1


def _excerpt(text: str, start: int, end: int, radius: int = 70) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    return ("…" if left else "") + text[left:right].replace("\n", " ")


def audit(preview_repair: bool = False) -> tuple[list[tuple], Counter[str]]:
    rows = fetch_all(
        """
        select passage_id, source, translated_text
        from human_translations
        order by source, passage_id
        """,
    )
    prepared: list[tuple[str, str, str]] = []
    token_frequency: Counter[str] = Counter()
    hazards: Counter[str] = Counter()
    for row in rows:
        text = str(row["translated_text"] or "")
        if preview_repair:
            text = clean_unicode_artifacts(text)
        hazards.update(unicode_artifacts(text))
        if str(row["source"]) not in SOURCES:
            continue
        if preview_repair:
            text = mend_spacing(text)
        prepared.append((str(row["passage_id"]), str(row["source"]), text))
        token_frequency.update(token.lower() for token in LETTER_TOKEN.findall(text))

    suspects: list[tuple] = []
    for passage_id, source, text in prepared:
        separated_marks = len(re.findall(r"\w\s+[\u0300-\u036f]", text))
        if separated_marks:
            hazards["dấu Unicode bị tách khỏi chữ"] += separated_marks

        tokens = list(LETTER_TOKEN.finditer(text))
        for left_match, right_match in zip(tokens, tokens[1:]):
            if text[left_match.end() : right_match.start()] != " ":
                continue
            left = left_match.group(0)
            right = right_match.group(0)
            pair = (left.lower(), right.lower())
            if pair in LEGITIMATE_PAIRS:
                continue

            left_base = _base_letters(left)
            joined = (left + right).lower()
            reasons: list[str] = []
            if not any(char in VOWELS for char in left_base) and _starts_with_marked_letter(right):
                reasons.append("mảnh phụ âm đứng trước chữ có dấu")
            if (len(left) <= 3 or len(right) <= 3) and token_frequency[joined] > 0:
                reasons.append(f"dạng viết liền xuất hiện {token_frequency[joined]} lần")
            if (
                left[:1].isupper()
                and right[:1] in PALI_MARKED
                and PALI_FRAGMENT.fullmatch(left) is not None
            ):
                reasons.append("tên Pāli viết hoa bị cắt")
            if reasons:
                suspects.append(
                    (
                        passage_id,
                        source,
                        left,
                        right,
                        "; ".join(reasons),
                        _excerpt(text, left_match.start(), right_match.end()),
                    )
                )
    return suspects, hazards


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Dò độc lập vết tách chữ còn sót của Indacanda.")
    parser.add_argument(
        "--preview-repair",
        action="store_true",
        help="Kiểm toán dữ liệu sau khi mô phỏng mend_spacing, không ghi DB.",
    )
    parser.add_argument("--sample-size", type=int, default=30)
    args = parser.parse_args()

    suspects, hazards = audit(args.preview_repair)
    by_source = Counter(item[1] for item in suspects)
    print("=== kiểm toán độc lập lỗi tách chữ Indacanda ===")
    print("chế độ:", "mô phỏng sau sửa" if args.preview_repair else "dữ liệu hiện có trong DB")
    for source in SOURCES:
        print(f"{source:16} {by_source[source]:6,} vị trí nghi vấn")
    print(f"tổng: {len(suspects):,} vị trí nghi vấn")
    print("ký tự Unicode lỗi (tất cả nguồn):", sum(hazards.values()))
    for label, count in hazards.items():
        print(f"  {label}: {count:,}")

    for index, item in enumerate(suspects[: max(0, args.sample_size)], 1):
        passage_id, source, left, right, reason, excerpt = item
        print(f"\n[{index}] {source} · {passage_id}\n{left} {right} -> {left + right}\n{reason}\n{excerpt}")

    if suspects or hazards:
        raise SystemExit("Còn điểm nghi vấn; chưa nên export dữ liệu.")
    print("\nĐẠT: không còn dấu hiệu tách chữ theo bộ kiểm toán độc lập.")


if __name__ == "__main__":
    main()
