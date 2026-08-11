"""Kiểm định chất lượng: kết quả tìm kiếm có đúng không, bản dịch có chuẩn không.

Chạy: .venv\\Scripts\\python.exe dev_verify.py [--sample 40] [--skip-judge]

Gồm 4 phần:
  A. Bản dịch của dịch giả có ghép đúng đoạn Pali không  (tải lại nguồn gốc để đối chiếu)
  B. Bản dịch AI có đúng ngôn ngữ và đủ nội dung không
  C. Bản dịch AI có cùng nghĩa với bản dịch của Ngài Sujato không  (dùng Gemini chấm)
  D. Tìm kiếm có trả về đúng bài kinh không  (bài kiểm tra có đáp án biết trước)
"""

import argparse
import json
import random
import re
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

from app.db import fetch_all, fetch_one
from app.normalize import normalize_pali
from app.search_engine import search_passages
from app.translator import translate_passage

RAW_BASE = "https://raw.githubusercontent.com/suttacentral/bilara-data/published"

passed = 0
failed = 0
warned = 0


def check(label: str, ok: bool, detail: str = "", warn_only: bool = False) -> bool:
    global passed, failed, warned
    if ok:
        passed += 1
        tag = "OK  "
    elif warn_only:
        warned += 1
        tag = "WARN"
    else:
        failed += 1
        tag = "SAI "
    print(f"{tag} {label}{(' · ' + detail) if detail else ''}")
    return ok


def section(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


# ---------------------------------------------------------------- A
def verify_human_alignment(sample_size: int) -> None:
    section("A. Bản dịch của dịch giả có ghép đúng đoạn Pali không")
    print("Tải lại file gốc trên SuttaCentral, kiểm tra từng segment đã ghi có thật sự\n"
          "nằm trong đoạn Pali của dòng đó không. Đây là kiểm tra độc lập với importer.\n")

    # Mọi nguồn ghi kèm `segment_ids` đều kiểm được bằng cách này. Trước đây chỉ chạy
    # cho `sujato`, nên bản Brahmali - nguồn duy nhất của Tạng Luật, và là nguồn phải
    # tự dò tập chứ không neo theo bài kinh - hoàn toàn không có cổng kiểm định nào.
    for source in ("sujato", "brahmali"):
        _verify_alignment_of(source, sample_size)


def _verify_alignment_of(source: str, sample_size: int) -> None:
    rows = fetch_all(
        """
        select h.source_ref, h.segment_ids, p.normalized_pali, p.pali_text
        from human_translations h
        join passages p on p.id = h.passage_id
        where h.source = %s and array_length(h.segment_ids, 1) > 0
        order by random()
        limit %s
        """,
        [source, sample_size],
    )
    if not rows:
        check(f"[{source}] có dữ liệu để kiểm tra", False, "chưa nạp nguồn này")
        return

    # Gom theo bài kinh để mỗi bài chỉ tải một lần.
    by_ref: dict[str, list[dict]] = {}
    for row in rows:
        by_ref.setdefault(row["source_ref"], []).append(row)

    tree = _bilara_tree()
    total_segments = 0
    bad_segments = 0
    checked_refs = 0

    for ref, group in by_ref.items():
        stem = tree.get(ref)
        if not stem:
            continue
        root = _fetch(f"{RAW_BASE}/root/pli/ms/{stem}_root-pli-ms.json")
        if not root:
            continue
        checked_refs += 1
        for row in group:
            for key in row["segment_ids"]:
                pali = normalize_pali(str(root.get(key) or ""))
                if not pali:
                    continue
                total_segments += 1
                if pali not in row["normalized_pali"]:
                    bad_segments += 1
                    if bad_segments <= 3:
                        print(f"     lệch ở {key}: {pali[:60]!r}")
                        print(f"       đoạn DB: {row['normalized_pali'][:80]!r}")

    rate = 100 * (total_segments - bad_segments) // max(1, total_segments)
    check(
        f"[{source}] {checked_refs} file · {total_segments} segment kiểm tra",
        bad_segments == 0,
        f"{bad_segments} segment ghép sai ({rate}% đúng)",
    )


def _fetch(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


_TREE: dict[str, str] = {}


def _bilara_tree() -> dict[str, str]:
    if _TREE:
        return _TREE
    data = _fetch("https://api.github.com/repos/suttacentral/bilara-data/git/trees/published?recursive=1")
    suffix = "_root-pli-ms.json"
    prefix = "root/pli/ms/"
    for item in (data or {}).get("tree", []):
        path = str(item.get("path") or "")
        # Cả `sutta/` lẫn `vinaya/`: bản Brahmali là Tạng Luật, nếu chỉ lấy `sutta/`
        # thì mọi `source_ref` của nguồn ấy tra không ra và phần kiểm tra lặng lẽ
        # bỏ trắng thay vì báo sai.
        if path.startswith((prefix + "sutta/", prefix + "vinaya/")) and path.endswith(suffix):
            _TREE[path.rsplit("/", 1)[-1][: -len(suffix)]] = path[len(prefix) : -len(suffix)]
    return _TREE


# ---------------------------------------------------------------- B
VIETNAMESE_MARKERS = "àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ"


def looks_vietnamese(text: str) -> bool:
    return any(ch in VIETNAMESE_MARKERS for ch in text.lower())


def verify_ai_translation(sample_size: int) -> None:
    section("B. Bản dịch AI có đúng ngôn ngữ và đủ nội dung không")

    rows = fetch_all(
        """
        select p.id, p.pali_text
        from passages p
        join documents d on d.id = p.document_id
        where d.corpus_type = 'mul' and length(p.pali_text) between 200 and 1200
        order by random() limit %s
        """,
        [sample_size],
    )

    for row in rows:
        pali = row["pali_text"]
        try:
            vi = translate_passage(str(row["id"]), "vi")
            en = translate_passage(str(row["id"]), "en")
        except Exception as exc:  # noqa: BLE001
            check(f"dịch đoạn {str(row['id'])[:8]}", False, f"{type(exc).__name__}: {exc}")
            continue

        vi_text = str(vi.get("text") or "")
        en_text = str(en.get("text") or "")
        label = f"đoạn {pali[:34]!r}"

        check(f"{label} · có bản tiếng Việt", bool(vi_text.strip()))
        check(f"{label} · đúng là tiếng Việt", looks_vietnamese(vi_text), vi_text[:50])
        check(f"{label} · có bản tiếng Anh", bool(en_text.strip()))
        check(f"{label} · tiếng Anh không lẫn tiếng Việt", not looks_vietnamese(en_text), en_text[:50])
        # Bản dịch quá ngắn so với bản gốc là dấu hiệu bị cắt cụt giữa chừng.
        ratio = len(vi_text) / max(1, len(pali))
        check(f"{label} · không bị cắt cụt", ratio >= 0.35, f"tỷ lệ độ dài {ratio:.2f}")
        # Lộ khung JSON hoặc markdown nghĩa là parser chưa bóc sạch.
        dirty = any(marker in vi_text for marker in ['{"translatedText"', "```", '"notes":'])
        check(f"{label} · không lẫn JSON/markdown", not dirty)


# ---------------------------------------------------------------- C
def verify_ai_vs_human(sample_size: int) -> None:
    section("C. Bản dịch AI có cùng nghĩa với bản dịch của Ngài Sujato không")
    print("Lấy các đoạn có CẢ hai bản, nhờ Gemini chấm xem hai bản có cùng nội dung không.\n"
          "Bản của Ngài Sujato coi như chuẩn đối chiếu.\n")

    rows = fetch_all(
        """
        select p.id, p.pali_text, h.translated_text as en, h.source_ref
        from human_translations h
        join passages p on p.id = h.passage_id
        where h.source = 'sujato' and length(p.pali_text) between 200 and 1000
        order by random() limit %s
        """,
        [sample_size],
    )

    from google import genai
    from app.config import settings

    client = genai.Client(api_key=str(settings()["gemini_api_key"]))
    models = list(settings()["gemini_text_models"])

    scores: list[int] = []
    for row in rows:
        try:
            vi = translate_passage(str(row["id"]), "vi").get("text") or ""
        except Exception:  # noqa: BLE001
            continue
        if not vi:
            continue

        prompt = "\n".join([
            "Bạn là giám khảo đối chiếu bản dịch kinh điển Pali.",
            "Cho một đoạn Pali, một bản dịch tiếng Anh đã được công nhận, và một bản dịch tiếng Việt do AI tạo ra.",
            "Chấm xem bản tiếng Việt có truyền đạt ĐÚNG nội dung của đoạn Pali hay không, lấy bản tiếng Anh làm đối chiếu.",
            "Bỏ qua khác biệt về văn phong, chỉ xét nội dung và nghĩa.",
            'Trả JSON thuần: {"score": 0-5, "reason": "..."}',
            "score 5 = trùng khớp nội dung; 3 = đúng ý chính nhưng thiếu chi tiết; 0 = sai nghĩa hoặc lạc đề.",
            "", f"PALI: {row['pali_text'][:1200]}",
            "", f"ANH (Sujato): {row['en'][:1200]}",
            "", f"VIỆT (AI): {vi[:1200]}",
        ])

        verdict = None
        for model in models:
            try:
                response = client.models.generate_content(
                    model=model, contents=prompt, config={"response_mime_type": "application/json"}
                )
                verdict = json.loads(re.sub(r"^```(?:json)?|```$", "", (response.text or "").strip()))
                break
            except Exception:  # noqa: BLE001
                continue
        if not verdict:
            continue

        score = int(verdict.get("score") or 0)
        scores.append(score)
        mark = "OK  " if score >= 4 else ("WARN" if score == 3 else "SAI ")
        print(f"{mark} {row['source_ref']:<8} điểm {score}/5 · {str(verdict.get('reason'))[:78]}")

    if scores:
        average = sum(scores) / len(scores)
        good = sum(1 for s in scores if s >= 4)
        check(
            f"trung bình {average:.1f}/5 trên {len(scores)} đoạn",
            average >= 4.0,
            f"{good}/{len(scores)} đoạn đạt 4 điểm trở lên",
        )


# ---------------------------------------------------------------- D
# Câu hỏi kèm bài kinh phải xuất hiện trong kết quả (khớp theo đường dẫn nguồn).
SEARCH_CASES = [
    ("trích nguyên văn 1 dòng Pali", "Sabbe saṅkhārā aniccāti, yadā paññāya passati", "theragatha", 1),
    # Câu kệ ba từ: nằm nguyên văn trong 140 đoạn nên mọi tín hiệu "có chứa" đều cộng đều
    # nhau, chỉ mật độ (câu trích chiếm bao nhiêu phần của đoạn) mới tách được đoạn LÀ câu
    # kệ khỏi đoạn dài TRÍCH LẠI nó. Khớp theo tên bộ chứ không theo chữ trong đoạn: đoạn
    # nào cũng chứa câu đó, nên chỉ tên bộ mới phân biệt được đúng/sai. Trước khi có
    # `EXACT_QUOTE_DENSITY_BONUS`, ba hạng đầu đều là Nghĩa Tích - tức là trượt.
    ("trích câu kệ ngắn 3 từ", "Sabbe saṅkhārā aniccā",
     "nettippakarana|kathavatthu|patisambhida|dhammapada|theragatha|dhatukatha", 3),
    ("trích nguyên văn 2 dòng Pali", "Sabbe saṅkhārā aniccāti, yadā paññāya passati; Atha nibbindati dukkhe, esa maggo visuddhiyā.", "theragatha", 1),
    ("tên riêng Pali", "Sona", "sona", 3),
    ("tiếng Việt: rắn", "có người đi tìm rắn, chuyên tâm tìm rắn, khi đang tìm kiếm rắn, người ấy thấy một con rắn lớn", "alagadd", 3),
    ("tiếng Việt: trộm cắp", "Tìm cho tôi bài kinh về sự trộm cắp", "adinnadan|theyya|veludvareyya", 5),
    ("tiếng Việt: niệm hơi thở", "kinh về niệm hơi thở vào ra", "anapana", 5),
    # Mẫu cũ là "satipatthana" - trúng cả những đoạn CHỈ NHẮC cụm từ (Vibhaṅga,
    # Peṭakopadesa, Niddesa), nên ca này báo ĐẠT trong khi chính bài kinh Đại Niệm Xứ
    # không có trong 15 hạng đầu. Người kiểm thử ngoài bắt được, bộ kiểm của tôi thì không.
    # Đòi đúng tên BÀI KINH mới là đo được thứ mình định đo.
    ("tiếng Việt: bốn niệm xứ", "bốn niệm xứ là gì", "satipatthanasutt|mahasatipatthana", 5),
    ("tiếng Việt: lòng từ", "kinh dạy về lòng từ bi", "metta|karuna", 5),
    ("tiếng Anh", "mindfulness of breathing", "anapana", 5),
    ("Pali: tứ diệu đế", "cattari ariyasaccani", "sacca", 5),
]


# Hỏi "bài kinh" thì kết quả đầu phải là Chánh tạng, không phải Chú giải.
ROOT_FIRST_CASES = [
    "Tìm cho tôi bài kinh về sự trộm cắp",
    "bài kinh nói về sự bố thí",
    "bài kinh về giữ giới không nói dối",
]


def verify_root_canon_first(runs: int = 2) -> None:
    section("E. Hỏi 'bài kinh' thì Chánh tạng có đứng trước Chú giải không")
    print("Chạy lặp lại vì AI rerank không tất định; kiểm tra kết quả ổn định.\n")

    for query in ROOT_FIRST_CASES:
        tops: list[str] = []
        for _ in range(runs):
            try:
                result = search_passages(query, ["all"], None, 1, 5, include_translations=False, log_search=False)
            except Exception as exc:  # noqa: BLE001
                check(query[:44], False, f"{type(exc).__name__}: {exc}")
                break
            results = result.get("results") or []
            tops.append(results[0]["sourcePath"].split(" -> ")[0] if results else "(rỗng)")
        if not tops:
            continue
        root_runs = sum(1 for top in tops if top.startswith("Tipiṭaka"))
        check(query[:44], root_runs == len(tops), f"{root_runs}/{len(tops)} lượt đứng đầu là Chánh tạng · {tops}")


def verify_search() -> None:
    section("D. Tìm kiếm có trả về đúng bài kinh không")
    print("Mỗi câu hỏi có đáp án biết trước; kiểm tra bài đúng có nằm trong top-N không.\n")

    for label, query, expect_pattern, top_n in SEARCH_CASES:
        try:
            result = search_passages(query, ["all"], None, 1, max(top_n, 5), include_translations=False, log_search=False)
        except Exception as exc:  # noqa: BLE001
            check(label, False, f"{type(exc).__name__}: {exc}")
            continue

        results = result.get("results") or []
        if not results:
            check(label, False, "0 kết quả")
            continue

        pattern = re.compile(expect_pattern, re.I)
        hit_rank = None
        for item in results[:top_n]:
            haystack = normalize_pali(item.get("sourcePath", "") + " " + item.get("paliText", ""))
            if pattern.search(haystack):
                hit_rank = item["rank"]
                break

        check(label, hit_rank is not None, f"hạng {hit_rank}" if hit_rank else f"không thấy trong top {top_n}")
        if hit_rank is None:
            print(f"       top1: {results[0]['sourcePath'][:96]}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=30)
    parser.add_argument("--skip-judge", action="store_true", help="Bỏ phần C (tốn nhiều lượt gọi Gemini)")
    args = parser.parse_args()

    random.seed(0)
    verify_human_alignment(args.sample)
    verify_search()
    verify_ai_translation(max(3, args.sample // 6))
    if not args.skip_judge:
        verify_ai_vs_human(max(5, args.sample // 3))

    print(f"\n{'=' * 78}")
    print(f"KẾT QUẢ: {passed} đạt · {warned} cảnh báo · {failed} sai")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
