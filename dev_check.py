"""Smoke check cho pipeline tìm kiếm.

Chạy: .venv\\Scripts\\python.exe dev_check.py
Đặt PY_SEARCH_AI_MODE=off trước khi chạy nếu muốn đo riêng nhánh lexical (không gọi Gemini).
"""

import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

from app.config import settings
from app.search_engine import search_passages


CASES = [
    # (nhãn, query, corpus_types, pitaka_type)
    ("1 dòng Pali (vốn đã chạy được)", "Sabbe saṅkhārā aniccāti, yadā paññāya passati", ["mul"], "sutta"),
    (
        "2 dòng Pali dán liền (Vấn đề 1)",
        "Sabbe saṅkhārā aniccāti, yadā paññāya passati; Atha nibbindati dukkhe, esa maggo visuddhiyā.",
        ["mul"],
        "sutta",
    ),
    ("Từ Pali đơn (Vấn đề 2)", "Sona", ["mul"], "sutta"),
    ("Tiếng Việt ngắn (Vấn đề 2)", "Thịt chó", ["mul"], "sutta"),
    (
        "Câu Việt dài (Vấn đề 2)",
        "có người đi tìm rắn, chuyên tâm tìm rắn, khi đang tìm kiếm rắn, người ấy thấy một con rắn lớn",
        ["mul"],
        "sutta",
    ),
    ("Tìm tất cả (không lọc Tạng)", "metta sutta", ["mul", "att", "tik", "nrf"], None),
]


def main() -> None:
    print(f"AI mode: {settings()['search_ai_mode']} | vector: {settings()['search_enable_vector']}")
    print("=" * 100)
    for label, query, corpus_types, pitaka_type in CASES:
        started = time.time()
        try:
            result = search_passages(query, corpus_types, pitaka_type, 1, 5, include_translations=False)
            results = result["results"]
            elapsed = time.time() - started
            status = "OK " if results else "FAIL"
            fallback = result.get("fallback") or {}
            note = f" [fallback: {fallback.get('usedQuery')!r}]" if fallback.get("used") else ""
            print(f"{status} {len(results)} kq · {elapsed:.1f}s · {label}{note}")
            print(f"     query: {query[:90]}")
            for item in results[:2]:
                print(f"     - score {item['score']} · {item['sourcePath'][:110]}")
                print(f"       {item['paliText'][:110].replace(chr(10), ' ')}")
        except Exception as exc:  # noqa: BLE001
            print(f"ERR  {label}: {type(exc).__name__}: {exc}")
        print("-" * 100)


if __name__ == "__main__":
    main()
