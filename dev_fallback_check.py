"""Kiểm tra cơ chế rút gọn từ khóa khi không tìm thấy kết quả nào.

Chạy: .venv\\Scripts\\python.exe dev_fallback_check.py

Nhánh này chỉ chạy khi pipeline chính trả về 0 kết quả, mà sau các bản vá thì
tình huống đó đã hiếm đi nhiều. Vì vậy ở đây ta dựng thẳng tình huống 0 kết quả
để kiểm tra logic, thay vì chờ nó tình cờ xảy ra.
"""

import sys

sys.stdout.reconfigure(encoding="utf-8")

from app import search_engine
from app.fallback_search import build_query_ladder, run_fallback

CLIENT_EXAMPLE = "có người đi tìm rắn, chuyên tâm tìm rắn, khi đang tìm kiếm rắn, người ấy thấy một con rắn lớn"

passed = 0
failed = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
    print(f"{'OK  ' if ok else 'FAIL'} {label}{(' · ' + extra) if extra else ''}")


print("== Bậc rút gọn từ khóa ==")
ladder = build_query_ladder(CLIENT_EXAMPLE)
print("   ", ladder)
check("rút gọn dần tới một từ khóa duy nhất", ladder[-1].lower() == "rắn", f"bậc cuối = {ladder[-1]!r}")
check("có bậc trung gian 'rắn tìm'", any(step.lower() == "rắn tìm" for step in ladder))
check("bậc dài hơn đứng trước bậc ngắn hơn", all(len(ladder[i]) >= len(ladder[i + 1]) for i in range(len(ladder) - 1)))
check('"chó" không bị nhận nhầm là từ đệm "cho"', build_query_ladder("Thịt chó thơm ngon") and "chó" in " ".join(build_query_ladder("Thịt chó thơm ngon")))
check("truy vấn một từ thì không có bậc nào", build_query_ladder("Sona") == [], f"{build_query_ladder('Sona')}")

print()
print("== run_fallback dừng ở bậc đầu tiên có kết quả ==")
calls: list[str] = []


def stub_search(query, corpus_types, pitaka_type, page, page_size):
    calls.append(query)
    # Giả lập: chỉ bậc rút gọn còn đúng một từ mới ra kết quả.
    return {"results": [{"id": "x"}] if len(query.split()) == 1 else []}


outcome = run_fallback(CLIENT_EXAMPLE, ["mul"], "sutta", 5, stub_search)
check("tìm được ở bậc rút gọn", bool(outcome))
if outcome:
    check("dùng đúng từ khóa một từ", outcome["usedQuery"].lower() == "rắn", f"usedQuery={outcome['usedQuery']!r}")
    check("ghi lại các bậc đã thử", len(outcome["triedQueries"]) == len(calls), f"tried={outcome['triedQueries']}")
    check("dừng ngay khi có kết quả, không chạy thừa", calls[-1] == outcome["usedQuery"])

calls.clear()
outcome_none = run_fallback(CLIENT_EXAMPLE, ["mul"], "sutta", 5, lambda *_: {"results": []})
check("mọi bậc đều rỗng thì trả None", outcome_none is None)

print()
print("== Ghép vào search_passages ==")
original_retrieve = search_engine._retrieve_candidates
seen_queries: list[str] = []


def fake_retrieve(query, corpus_types, pitaka_type, analysis, limit):
    # Câu dài trả rỗng; chỉ khi rút còn <= 2 từ mới trả về ứng viên thật.
    seen_queries.append(analysis.get("rawQuery") or query)
    raw = analysis.get("rawQuery") or query
    if len(raw.split()) > 2:
        return []
    return original_retrieve("rắn", corpus_types, pitaka_type, analysis, limit)


search_engine._retrieve_candidates = fake_retrieve
try:
    result = search_engine.search_passages(
        CLIENT_EXAMPLE, ["mul"], "sutta", 1, 3, include_translations=False, log_search=False
    )
finally:
    search_engine._retrieve_candidates = original_retrieve

fallback = result.get("fallback") or {}
check("search_passages tự chuyển sang fallback", fallback.get("used") is True)
check("giữ nguyên câu gốc trong kết quả trả về", result.get("query") == CLIENT_EXAMPLE)
check("báo rõ từ khóa đã dùng thay thế", bool(fallback.get("usedQuery")), f"usedQuery={fallback.get('usedQuery')!r}")
check("có kết quả sau khi rút gọn", bool(result.get("results")), f"{len(result.get('results') or [])} kết quả")

print()
print(f"Tổng: {passed} OK, {failed} FAIL")
sys.exit(1 if failed else 0)
