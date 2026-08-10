"""Kiểm tra qua HTTP thật, đúng đường đi của trình duyệt.

Chạy server trước: .venv\\Scripts\\python.exe -m uvicorn app.main:app --port 8010
Rồi:               .venv\\Scripts\\python.exe dev_http_check.py [base_url]
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8010"

CASES = [
    ("1 dòng Pali", "Sabbe saṅkhārā aniccāti, yadā paññāya passati", "mul", "sutta"),
    (
        "2 dòng Pali dán liền (Vấn đề 1)",
        "Sabbe saṅkhārā aniccāti, yadā paññāya passati; Atha nibbindati dukkhe, esa maggo visuddhiyā.",
        "mul",
        "sutta",
    ),
    ("Từ Pali đơn (Vấn đề 2)", "Sona", "mul", "sutta"),
    ("Tiếng Việt ngắn (Vấn đề 2)", "Thịt chó", "mul", "sutta"),
    (
        "Câu Việt dài (Vấn đề 2)",
        "có người đi tìm rắn, chuyên tâm tìm rắn, khi đang tìm kiếm rắn, người ấy thấy một con rắn lớn",
        "mul",
        "sutta",
    ),
    ("Tìm kiếm tất cả", "metta", "all", "all"),
    ("Tìm tất cả Tạng, chỉ Tam Tạng", "adinnadana veramani", "mul", "all"),
]


def post_form(path: str, fields: dict) -> str:
    data = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(f"{BASE}{path}", data=data)
    with urllib.request.urlopen(request, timeout=180) as response:
        return response.read().decode("utf-8")


def post_json(path: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(f"{BASE}{path}", data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.loads(response.read().decode("utf-8"))


def get(path: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=180) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def check_search() -> None:
    print("== /search (JSON API) ==")
    for label, query, corpus, pitaka in CASES:
        started = time.time()
        payload = post_json(
            "/search",
            {
                "query": query,
                "filters": {"corpusType": [corpus], "pitakaType": pitaka},
                "pageSize": 5,
                "includeTranslations": False,
            },
        )
        results = payload.get("results") or []
        fallback = payload.get("fallback") or {}
        status = "OK  " if results else "FAIL"
        note = f"  [fallback -> {fallback.get('usedQuery')!r}]" if fallback.get("used") else ""
        print(f"{status} {len(results)} kq · {time.time() - started:.1f}s · {label}{note}")
        for item in results[:1]:
            print(f"       #{item['rank']} score {item['score']} · {item['sourcePath'][:96]}")
            print(f"       {item['paliText'][:96].replace(chr(10), ' ')}")
    print()


def check_pages() -> None:
    print("== Trang & fragment ==")
    for path in ["/", "/?lang=en", "/?lang=my", "/admin/login"]:
        status, body = get(path)
        print(f"{'OK  ' if status == 200 else 'FAIL'} {status} {path} ({len(body)} bytes)")

    html = post_form(
        "/search-page",
        {"query": "metta", "corpus_type": "all", "pitaka_type": "all", "page": "1", "lang": "en", "translation_source": "ai"},
    )
    ok = "Citation" in html and "Original excerpt" in html
    print(f"{'OK  ' if ok else 'FAIL'} /search-page trả fragment tiếng Anh ({len(html)} bytes)")

    html_vi = post_form(
        "/search-page",
        {"query": "Thịt chó", "corpus_type": "mul", "pitaka_type": "sutta", "page": "1", "lang": "vi", "translation_source": "ai"},
    )
    print(f"{'OK  ' if 'Trích nguồn' in html_vi else 'FAIL'} /search-page trả fragment tiếng Việt ({len(html_vi)} bytes)")
    print()


def fetch_json_first_sujato_passage() -> str | None:
    from app.db import fetch_one

    try:
        row = fetch_one("select passage_id from human_translations where source = 'sujato' limit 1")
    except Exception:  # noqa: BLE001 - chưa chạy migration
        return None
    return str(row["passage_id"]) if row else None


def check_translation_sources() -> None:
    print("== Nguồn bản dịch ==")
    passage = post_json(
        "/search",
        {"query": "metta", "filters": {"corpusType": ["mul"], "pitakaType": "sutta"}, "pageSize": 1, "includeTranslations": False},
    )
    results = passage.get("results") or []
    if not results:
        print("FAIL không lấy được passage để test dịch")
        return
    passage_id = results[0]["id"]

    # Chưa nạp dữ liệu thì phải báo rõ, không được lặng lẽ rơi về bản dịch AI.
    for source in ["minh_chau", "indacanda"]:
        payload = post_json("/api/translate-result", {"passageId": passage_id, "usePassageCache": True, "source": source, "language": "vi"})
        message = (payload.get("translation") or {}).get("error")
        ok = payload.get("ok") is False and "không có bản dịch chính thức" in str(message).lower()
        print(f"{'OK  ' if ok else 'FAIL'} nguồn {source} -> {message}")

    # Sujato đã nạp cho Trường Bộ + Trung Bộ: đoạn thuộc hai bộ đó phải trả bản dịch thật.
    row = fetch_json_first_sujato_passage()
    if row:
        payload = post_json("/api/translate-result", {"passageId": row, "usePassageCache": True, "source": "sujato", "language": "vi"})
        translation = payload.get("translation") or {}
        text = translation.get("text")
        ok = payload.get("ok") is True and bool(text) and translation.get("language") == "en"
        print(f"{'OK  ' if ok else 'FAIL'} nguồn sujato -> {str(text)[:70]!r}")
    else:
        print("SKIP nguồn sujato (chưa chạy import_sujato.py)")

    for language, marker in [("vi", "vi"), ("en", "en")]:
        payload = post_json("/api/translate-result", {"passageId": passage_id, "usePassageCache": True, "source": "ai", "language": language})
        translation = payload.get("translation") or {}
        text = translation.get("text") or translation.get("vi")
        got = translation.get("language")
        ok = bool(text) and got == marker
        print(f"{'OK  ' if ok else 'FAIL'} AI dịch language={language} (trả về {got!r}): {str(text)[:80]!r}")
    print()


def check_notice() -> None:
    print("== Bảng thông báo ==")
    for lang, marker in [("vi", "Lưu ý"), ("en", "Please note"), ("my", "notice.body")]:
        _, body = get(f"/?lang={lang}")
        has_modal = 'id="noticeModal"' in body
        print(f"{'OK  ' if has_modal else 'FAIL'} lang={lang} có modal thông báo")
    print()


if __name__ == "__main__":
    check_pages()
    check_search()
    check_translation_sources()
    check_notice()
