"""Tra cứu từ điển Pāḷi qua Digital Pāḷi Dictionary (dpdict.net).

Hai nguồn dữ liệu, và chúng KHÁC nhau về bản chất - đừng gộp:

* **Gợi ý khi gõ** chạy tại chỗ, từ `app/data/dpd_headwords.txt` (76.060 mục, nạp một lần
  lúc khởi động). Bắt buộc phải tại chỗ vì dpdict.net không có endpoint gợi ý - ô tìm của
  họ để `autocomplete="off"`. Gọi sang máy họ mỗi lần gõ một chữ thì vừa chậm (~1-2
  giây/lượt) vừa là lạm dụng dịch vụ miễn phí.
* **Nội dung mục từ** lấy trực tiếp từ `https://www.dpdict.net/search_json?q=`, trả về
  `{summary_html, dpd_html}`. Đây là API chính thức của họ (xem /docs), sạch hơn hẳn việc
  bóc HTML trang `/gd` vì không dính giao diện và CSS của trang.

Bản dịch tiếng Việt sinh từ phần nghĩa tiếng Anh bằng `translate_dictionary_glosses`, gộp
một lượt gọi cho cả trang và cache theo hash - từ nào đã tra rồi thì lần sau ra ngay.

Giấy phép: DPD phát hành theo CC BY-NC-SA. Trang tra cứu PHẢI ghi nguồn và dẫn link về
dpdict.net; `templates/dictionary.html` giữ phần đó ở chân trang.
"""

from __future__ import annotations

import re
import time
from bisect import bisect_left
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from heapq import nsmallest
from pathlib import Path
from threading import Lock

import httpx

from .db import execute, fetch_all, fetch_one
from .i18n import DEFAULT_LANGUAGE, normalize_language
from .normalize import normalize_pali
from .translator import translate_dictionary_glosses


DPD_BASE_URL = "https://www.dpdict.net"
DPD_SEARCH_URL = f"{DPD_BASE_URL}/search_json"
DPD_AUDIO_URL = f"{DPD_BASE_URL}/audio"
HEADWORDS_PATH = Path(__file__).parent / "data" / "dpd_headwords.txt"

# Nghĩa trong DPD vốn đã là tiếng Anh. Người đọc chọn giao diện tiếng Anh thì không có gì
# để dịch: gọi AI ở đây chỉ tốn một lượt để nhận lại đúng câu vừa gửi đi, rồi in ra hai ô
# giống hệt nhau.
SOURCE_LANGUAGE = "en"
REQUEST_TIMEOUT_SECONDS = 25.0
SUGGEST_LIMIT = 20
MAX_QUERY_CHARS = 60
# Đệm trong tiến trình: một mục từ nặng ~120 KB và mất ~2 giây để lấy về, trong khi nội
# dung từ điển gần như không đổi. Giữ 15 phút là đủ cho một phiên tra cứu mà vẫn nhận được
# bản cập nhật của DPD trong ngày.
CACHE_TTL_SECONDS = 900
CACHE_MAX_ENTRIES = 200

_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_LOCK = Lock()


class DictionaryError(RuntimeError):
    """dpdict.net không trả lời được - phân biệt với lỗi lập trình để giao diện báo đúng."""


# --------------------------------------------------------------------------------------
# Gợi ý từ (chạy tại chỗ)
# --------------------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _headword_index() -> tuple[list[str], list[str]]:
    """(khoá đã chuẩn hoá, từ hiển thị) - hai danh sách song song, sắp theo khoá.

    Sắp sẵn để dò tiền tố bằng `bisect` thay vì quét 76.060 dòng mỗi lần gõ một chữ.
    """
    if not HEADWORDS_PATH.exists():
        return ([], [])
    words = [line.strip() for line in HEADWORDS_PATH.read_text(encoding="utf-8").splitlines()]
    pairs = sorted(
        ((normalize_pali(word), word) for word in words if word),
        key=lambda pair: (pair[0], pair[1]),
    )
    return ([pair[0] for pair in pairs], [pair[1] for pair in pairs])


def suggest(prefix: str, limit: int = SUGGEST_LIMIT) -> list[str]:
    """Các từ bắt đầu bằng `prefix`, bỏ qua dấu Pāḷi.

    Bỏ dấu có chủ đích: gõ `bhagavata` trên bàn phím thường vẫn phải ra `bhagavatā`. Đây
    cũng là lý do không gọi thẳng dpdict.net để gợi ý - tra `bhagavata` không dấu ở đó chỉ
    ra phần tách từ, không ra mục từ nào.
    """
    key = normalize_pali(prefix or "")
    if not key:
        return []
    keys, words = _headword_index()
    start = bisect_left(keys, key)

    def matches():
        for index in range(start, len(keys)):
            if not keys[index].startswith(key):
                return
            yield index

    # Từ khớp đúng nguyên văn lên đầu, rồi tới từ ngắn nhất: người gõ `miga` gần như luôn
    # muốn `miga` chứ không phải `migabandhaka`.
    #
    # `nsmallest` chứ không phải cắt bớt rồi sắp: dải khớp đang theo thứ tự CHỮ CÁI, nên
    # cắt sớm là cắt theo chữ cái rồi mới xếp theo độ dài - gõ `mig` mà giới hạn 6 thì mất
    # cả `migī` lẫn `√migh`, vì chúng đứng sau `migacīra`, `migadāya`... trong bảng chữ cái
    # dù ngắn hơn hẳn. Cách này duyệt hết dải khớp nhưng chỉ giữ `limit` phần tử.
    best = nsmallest(limit, matches(), key=lambda index: (keys[index] != key, len(words[index]), words[index]))
    return [words[index] for index in best]


# --------------------------------------------------------------------------------------
# Lấy nội dung mục từ
# --------------------------------------------------------------------------------------


def _cached_fetch(query: str) -> dict:
    now = time.time()
    with _CACHE_LOCK:
        hit = _CACHE.get(query)
        if hit and now - hit[0] < CACHE_TTL_SECONDS:
            return hit[1]

    payload = _fetch(query)

    with _CACHE_LOCK:
        if len(_CACHE) >= CACHE_MAX_ENTRIES:
            oldest = min(_CACHE, key=lambda item: _CACHE[item][0])
            _CACHE.pop(oldest, None)
        _CACHE[query] = (now, payload)
    return payload


def _fetch(query: str) -> dict:
    try:
        response = httpx.get(
            DPD_SEARCH_URL,
            params={"q": query},
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": "suttasearch.net dictionary lookup"},
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # httpx.HTTPError, JSONDecodeError, ...
        raise DictionaryError(f"{type(exc).__name__}: {str(exc)[:200]}") from exc

    if not isinstance(payload, dict):
        raise DictionaryError("dpdict.net trả về dữ liệu không đúng định dạng.")
    return {
        "summaryHtml": _sanitize(str(payload.get("summary_html") or "")),
        "dpdHtml": _sanitize(str(payload.get("dpd_html") or "")),
    }


# HTML của DPD do máy chủ của họ sinh ra và ta chèn thẳng vào trang mình, nên phải cắt
# những thứ có thể chạy được. Đây là rào chắn tối thiểu chứ không phải bộ lọc HTML đầy đủ:
# nếu sau này nhận HTML từ nguồn không tin cậy thì phải dùng thư viện chuyên dụng.
_SCRIPTISH = re.compile(
    r"<\s*(script|iframe|object|embed|form)\b.*?<\s*/\s*\1\s*>|<\s*(script|iframe|object|embed|form)\b[^>]*>",
    re.IGNORECASE | re.DOTALL,
)
_EVENT_ATTR = re.compile(r"""\s+on[a-z]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""", re.IGNORECASE)
_JS_HREF = re.compile(r"""\s+(href|src)\s*=\s*(?:"\s*javascript:[^"]*"|'\s*javascript:[^']*')""", re.IGNORECASE)


def _sanitize(html: str) -> str:
    html = _SCRIPTISH.sub("", html)
    html = _EVENT_ATTR.sub("", html)
    return _JS_HREF.sub("", html)


# --------------------------------------------------------------------------------------
# Tách mục từ để chèn bản dịch tiếng Việt
# --------------------------------------------------------------------------------------

_ENTRY_HEADING = re.compile(r'<h3 class="dpd"[^>]*id="([^"]*)"[^>]*>(.*?)</h3>', re.DOTALL)
# Ô nghĩa của một mục từ. Chỉ mục TỪ mới có; các mục `grammar:`, `variants:`, `root:` là
# bảng nên không khớp - và đúng như vậy, chúng không có gì để dịch.
_MEANING_BOX = re.compile(r"<div class=[\"']dpd summary[\"']>(.*?)</div>", re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
# Dấu ✔/✘ của DPD cho biết mục từ đã được biên tập kỹ hay chưa. Giữ trong phần hiển thị,
# nhưng cắt khỏi chuỗi đưa cho AI - nó không phải chữ nghĩa gì để dịch.
_QUALITY_MARK = re.compile(r"<span class=[\"']gray[\"']>[✔✘]</span>")


def _plain_text(html: str) -> str:
    text = _QUALITY_MARK.sub("", html)
    text = _TAG.sub(" ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&#39;", "'")
        .replace("&quot;", '"')
    )
    text = re.sub(r"\s+", " ", text)
    # Thẻ `<b>` bọc nghĩa chính nên khi bỏ thẻ sẽ dôi ra khoảng trắng trước dấu câu:
    # "epithet of the Buddha ; lit. ...". Dọn cho câu đưa vào AI đọc như câu bình thường.
    return re.sub(r"\s+([;,.:])", r"\1", text).strip()


def _split_entries(dpd_html: str) -> list[dict]:
    """Cắt `dpd_html` thành từng mục từ, mỗi mục giữ nguyên phần thân của DPD.

    Giữ nguyên thân HTML là có chủ đích: các nút grammar/examples/declension/root family...
    và bảng biến cách đều nằm sẵn trong đó. Dựng lại chúng bằng tay vừa thừa vừa chắc chắn
    sẽ lệch khi DPD thêm mục mới.
    """
    headings = list(_ENTRY_HEADING.finditer(dpd_html))
    entries: list[dict] = []
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(dpd_html)
        body = dpd_html[heading.end() : end]
        meaning = _MEANING_BOX.search(body)
        entries.append(
            {
                "anchor": heading.group(1),
                "title": _plain_text(heading.group(2)),
                "meaningHtml": meaning.group(0) if meaning else "",
                "meaningText": _plain_text(meaning.group(1)) if meaning else "",
                # Phần còn lại sau ô nghĩa: nút bấm + các khối ẩn.
                "bodyHtml": body[meaning.end() :] if meaning else body,
                "translation": None,
            }
        )
    return entries


def lookup(query: str, language: str = DEFAULT_LANGUAGE, translate: bool = True) -> dict:
    """Tra một từ và trả về dữ liệu đã sẵn sàng để dựng trang.

    `translate=False` để xem nguyên bản tiếng Anh mà không tốn lượt gọi AI.
    """
    language = normalize_language(language)
    query = (query or "").strip()[:MAX_QUERY_CHARS]
    if not query:
        return {"query": "", "entries": [], "summaryHtml": "", "found": False}

    raw = _cached_fetch(query)
    entries = _split_entries(raw["dpdHtml"])

    bilingual = language != SOURCE_LANGUAGE
    if translate and bilingual:
        _attach_translations(entries, language)

    return {
        "query": query,
        "summaryHtml": raw["summaryHtml"],
        "entries": entries,
        "found": bool(entries),
        # Có tách làm hai khối "bản dịch" / "nguyên bản" hay không. Giao diện tiếng Anh chỉ
        # hiện một khối, vì hai khối sẽ giống hệt nhau.
        "bilingual": bilingual,
        "sourceUrl": f"{DPD_BASE_URL}/?q={query}",
    }


# --------------------------------------------------------------------------------------
# Tra nguyên câu/dòng kệ - tách từng từ, tra song song, chỉ lấy nghĩa ngắn gọn
# --------------------------------------------------------------------------------------
#
# dpdict.net (và API `search_json` của họ) chỉ hiểu MỘT từ khoá mỗi lượt - gõ nguyên câu
# "Sabbe sankhara anicca" vào thẳng ô tìm của chính họ cũng ra rỗng, đã kiểm tra trực tiếp.
# Nên câu/dòng kệ phải tự tách ra rồi tra từng từ, không có cách gọi gộp nào từ phía họ.

PHRASE_MAX_WORDS = 40
PHRASE_MAX_CHARS = 800
# Tối đa lượt gọi mạng cùng lúc sang dpdict.net cho MỘT câu. Không phải giới hạn tốc độ hệ
# thống, chỉ là không dội hàng chục kết nối cùng lúc vào dịch vụ miễn phí của người khác.
PHRASE_FETCH_WORKERS = 8

# Ký tự coi là ranh giới từ khi tách câu - không dùng regex \W vì Pāḷi la-tinh hoá vẫn nằm
# trong dải chữ cái Unicode bình thường (ā, ī, ū, ṃ, ṅ...), \W sẽ không đụng tới chúng.
_PHRASE_STRIP = " \t\r\n,.;:!?\"'“”‘’()[]{}–—-"


def _split_words(text: str) -> list[str]:
    words = [word.strip(_PHRASE_STRIP) for word in (text or "").split()]
    return [word for word in words if word]


def _safe_fetch(query: str) -> dict | None:
    """Như `_cached_fetch`, nhưng một từ lỗi không được kéo sập cả câu.

    Khác với tra một từ (nơi lỗi mạng nghĩa là phải báo cho người dùng ngay), ở đây một
    câu có thể có 10-15 từ - một lượt trong số đó timeout không nên xoá mất kết quả của
    chín từ còn lại.
    """
    try:
        return _cached_fetch(query)
    except DictionaryError:
        return None


def lookup_phrase(text: str, language: str = DEFAULT_LANGUAGE, translate: bool = True) -> dict:
    """Tra từng từ của một câu/dòng kệ, trả về nghĩa NGẮN GỌN cho mỗi từ.

    Chủ ý chỉ lấy nghĩa của mục đầu tiên (không kèm bảng ngữ pháp/biến cách) - đây là bản
    xem nhanh cho cả câu; xem đầy đủ một từ thì bấm vào nó, giao diện gọi lại `lookup()`
    bình thường cho đúng từ đó.
    """
    language = normalize_language(language)
    words = _split_words((text or "")[:PHRASE_MAX_CHARS])[:PHRASE_MAX_WORDS]
    bilingual = translate and language != SOURCE_LANGUAGE

    if not words:
        return {"query": text or "", "words": [], "bilingual": bilingual}

    # Mỗi từ là một lượt gọi mạng ~1-2 giây; chạy song song để một dòng kệ 8-10 từ không
    # mất cả chục giây đợi tuần tự.
    with ThreadPoolExecutor(max_workers=PHRASE_FETCH_WORKERS) as pool:
        raws = list(pool.map(_safe_fetch, words))

    results: list[dict] = []
    for surface, raw in zip(words, raws):
        if raw is None:
            results.append(
                {
                    "surface": surface,
                    "found": False,
                    "hasEntries": False,
                    "error": True,
                    "senseCount": 0,
                    "meaningText": "",
                }
            )
            continue
        entries = _split_entries(raw["dpdHtml"])
        senses = [entry for entry in entries if entry["meaningText"]]
        first = senses[0] if senses else None
        results.append(
            {
                "surface": surface,
                # `bool(senses)`, không phải `bool(entries)`: một từ không dấu thường chỉ
                # trả về mục "deconstructor" (tách từ), có tiêu đề nhưng không có nghĩa -
                # `entries` khác rỗng trong khi không có gì để hiện làm nghĩa ngắn gọn.
                "found": bool(senses),
                # Vẫn có gì đó để bấm xem (ít nhất là phần tách từ), dù không có nghĩa
                # ngắn gọn để hiện ngay. Giao diện dùng cờ này để không khoá nút bấm.
                "hasEntries": bool(entries),
                "error": False,
                "senseCount": len(senses),
                "meaningText": first["meaningText"] if first else "",
                "translation": None,
            }
        )

    if bilingual:
        _attach_phrase_translations(results, language)

    return {"query": text, "words": results, "bilingual": bilingual}


def _attach_phrase_translations(words: list[dict], language: str) -> None:
    """Dịch nghĩa ngắn của cả câu trong MỘT lượt gọi AI, không phải một lượt mỗi từ.

    Một dòng kệ 10 từ mà dịch riêng từng từ là 10 lượt gọi Gemini cho một lần bấm; gộp lại
    còn một lượt, đúng nguyên tắc đã áp dụng cho `_attach_translations`.
    """
    targets = [word for word in words if word["meaningText"]]
    if not targets:
        return
    try:
        translated = translate_dictionary_glosses([word["meaningText"] for word in targets], language)
    except Exception:
        return
    if not translated:
        return
    for word, text in zip(targets, translated):
        word["translation"] = text


def _attach_translations(entries: list[dict], language: str) -> None:
    """Dịch phần nghĩa, GỘP một lượt gọi cho cả trang.

    Gọi riêng từng mục thì tra `bhagavatā` là 3 lượt, mà mô hình lại mất ngữ cảnh rằng
    chúng là các nghĩa của cùng một từ.

    Dịch hỏng thì bỏ qua, giữ nguyên tiếng Anh: mất phần tiếng Việt vẫn còn dùng được,
    còn để trang báo lỗi thì hỏng cả tra cứu chỉ vì AI trục trặc.
    """
    targets = [entry for entry in entries if entry["meaningText"]]
    if not targets:
        return
    try:
        translated = translate_dictionary_glosses([entry["meaningText"] for entry in targets], language)
    except Exception:
        return
    if not translated:
        return
    for entry, text in zip(targets, translated):
        entry["translation"] = text


# --------------------------------------------------------------------------------------
# Lịch sử tra cứu (admin xem ở /admin/dictionary-history)
# --------------------------------------------------------------------------------------
#
# Ghi MỌI lượt tra, không đợi người dùng bấm lưu gì - cùng triết lý với `search_logs` của
# tìm kiếm kinh điển ("There is no way to search without logging", xem CLAUDE.md). Khác
# hẳn "Đã tra gần đây" trong `localStorage` phía trình duyệt (riêng của từng máy, admin
# không xem được) - đây là nhật ký chung, dùng để admin biết khách tra gì, từ nào chưa ra
# nghĩa, và mở lại đúng kết quả khi cần.

HISTORY_MAX_QUERY_CHARS = 500


def log_lookup(query: str, language: str, user_id: str | None, found: bool) -> None:
    query = (query or "").strip()[:HISTORY_MAX_QUERY_CHARS]
    if not query:
        return
    execute(
        "insert into dictionary_search_logs (user_id, query, language, found) values (%s, %s, %s, %s)",
        [user_id, query, normalize_language(language), bool(found)],
    )


def history_rows(
    keyword: str,
    limit: int,
    before_time: str | None = None,
    before_id: str | None = None,
    only_empty: bool = False,
) -> list[dict]:
    """Một mẻ lịch sử, cũ dần kể từ mốc `before`.

    Phân trang theo CON TRỎ `(created_at, id)`, không theo `offset` - bảng được ghi thêm
    liên tục trong lúc admin đang cuộn nên offset sẽ lệch, y hệt lý do `_admin_history_rows`
    (search_logs) đã áp dụng cách này.
    """
    conditions: list[str] = []
    params: list[object] = []
    if keyword:
        conditions.append("d.query ilike %s")
        params.append(f"%{keyword}%")
    if only_empty:
        conditions.append("d.found = false")
    if before_time and before_id:
        conditions.append("(d.created_at, d.id) < (%s::timestamptz, %s::uuid)")
        params.extend([before_time, before_id])
    where_sql = ("where " + " and ".join(conditions)) if conditions else ""
    return fetch_all(
        f"""
        select d.id, d.query, d.language, d.found, d.created_at, u.username
        from dictionary_search_logs d
        left join users u on d.user_id = u.id
        {where_sql}
        order by d.created_at desc, d.id desc
        limit %s
        """,
        [*params, limit],
    )


def history_counts(keyword: str, only_empty: bool) -> dict:
    """Số dòng khớp bộ lọc hiện tại, cộng vài số liệu tổng quan cho đầu trang admin."""
    conditions: list[str] = []
    params: list[object] = []
    if keyword:
        conditions.append("query ilike %s")
        params.append(f"%{keyword}%")
    if only_empty:
        conditions.append("found = false")
    where_sql = ("where " + " and ".join(conditions)) if conditions else ""
    filtered = fetch_one(f"select count(*) as cnt from dictionary_search_logs {where_sql}", params)
    total = fetch_one("select count(*) as cnt from dictionary_search_logs")
    empty = fetch_one("select count(*) as cnt from dictionary_search_logs where found = false")
    return {
        "filtered": int(filtered["cnt"]) if filtered else 0,
        "total": int(total["cnt"]) if total else 0,
        "empty": int(empty["cnt"]) if empty else 0,
    }


def history_top_queries(limit: int = 10) -> list[dict]:
    return fetch_all(
        """
        select query, count(*) as count
        from dictionary_search_logs
        group by query
        order by count desc
        limit %s
        """,
        [limit],
    )


def clear_history() -> None:
    execute("delete from dictionary_search_logs")
