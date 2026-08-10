"""Chẻ bản dịch Minh Châu xuống CẤP ĐOẠN, ghép vào từng `passages`.

VÌ SAO CẦN
----------
`import_minhchau.py` chỉ ghép được tới cấp bài kinh: một khối văn bản cho cả bài, kèm
khoảng `sort_order`. Thẻ kết quả vì thế không biết khúc nào ứng với đoạn Pali đang hiện,
đành in đoạn mở đầu bài - đo trên SN 55.7 thì đoạn Pali nằm ở 27% chiều dài bài mà phần
in ra chỉ phủ 0-5%, tức là chú thích "bản dịch chính thức" đặt cạnh một khúc văn không
liên quan tới đoạn phía trên.

Ba cách ghép rẻ đều đã thử và đều hỏng, đừng làm lại:
- `passages.paragraph_no` / `display_paragraph_no` / `xml_paragraph_no` so với số đoạn
  của bilara: trùng 0% trên 2.963 đoạn đo được.
- Mốc `Vi-n N` in trong bản dịch: số có tồn tại nhưng KHÔNG trỏ đúng chỗ - trên SN 55.7,
  đoạn bilara `5.1` (nói về trộm cắp) rơi vào `Vi-n 5` là đoạn nói về "pháp môn đưa đến
  lợi ích", lệch 2 đoạn. Độ lệch cũng không cố định trong một bài (độ thuần nhất 8%).
- Cắt theo tỉ lệ vị trí: dựa trên giả định bản dịch chạy song song đều với bản gốc, mà
  hai phép đo trên vừa bác bỏ.

TRẠNG THÁI: CHƯA DÙNG ĐƯỢC - đã đo, cách ghép bằng embedding KHÔNG đạt
------------------------------------------------------------------------
Giữ lại file này làm bộ đo và làm biên bản, đừng viết lại từ đầu cùng một hướng.

Đo trên SN 10.1 / 10.10 / 10.11 (embedding `gemini-embedding-2`, 768 chiều):

    đoạn văn Việt được gán cho đoạn Pali nào (argmax), mong đợi phải TĂNG DẦN:
        sn10.1   (5 đoạn Việt / 8 đoạn Pali)  ->  [0, 0, 0, 0, 0]
        sn10.10  (6 / 3)                      ->  [2, 0, 2, 0, 0, 0]
        sn10.11  (6 / 3)                      ->  [0, 0, 2, 2, 0, 0]

Biên độ tương đồng trong mỗi hàng có thật (0,08-0,15) nên tín hiệu không phẳng, nhưng
argmax là nhiễu chứ không đơn điệu. Trừ trung bình theo hàng và phạt nhảy cóc đều không
cứu được: sau khi thêm cả hai, kết quả vẫn dồn hết đoạn văn vào một đoạn Pali.

Lý do có vẻ là độ mịn hai bên vênh nhau và vênh không theo quy luật - SN 10.1 có 5 đoạn
Việt cho 8 đoạn Pali (thô hơn), SN 10.10 có 6 đoạn Việt cho 3 đoạn Pali (mịn hơn) - cộng
với việc văn kinh lặp công thức nên các đoạn trong cùng một bài rất giống nhau.

Hướng còn lại chưa thử: nhờ LLM chẻ trực tiếp (đưa cả bản Việt + danh sách đoạn Pali,
bảo nó cắt), đắt hơn embedding nhiều nhưng hiểu được cấu trúc thay vì chỉ đo khoảng cách.
Làm hướng đó thì phải có bộ kiểm định riêng như `dev_verify.py` phần A.

CÁCH GHÉP - mượn vị trí của bản Sujato
---------------------------------------
Ghép thẳng Pali <-> Việt bằng embedding rất yếu vì mô hình đa ngữ xử lý Pali kém. Nhưng
86% số đoạn trong phạm vi Minh Châu đã có bản Sujato ghép sẵn TỚI TỪNG CÂU. Nên đi vòng:

    đoạn Pali --(Sujato, đã ghép sẵn)--> tiếng Anh --(embedding vi<->en)--> đoạn văn Việt

Hai đầu cuối đều là ngôn ngữ hiện đại nên embedding đa ngữ làm tốt.

Ghép bằng quy hoạch động ĐƠN ĐIỆU: đoạn văn Việt thứ j chỉ được gán cho đoạn Pali có
thứ tự không nhỏ hơn đoạn mà j-1 đã gán. Kinh Pali lặp công thức nguyên văn (MN 10 và
DN 22 gần như trùng nhau), nên nếu để ghép tự do thì một công thức lặp sẽ kéo khúc dịch
giật về đầu bài - đúng cái bẫy mà `import_sujato.py` chặn bằng con trỏ tiến-một-chiều.

LƯỚI AN TOÀN
------------
Bài nào ghép ra độ tương đồng trung bình dưới `SUTTA_MIN_SIM` thì BỎ QUA CẢ BÀI, không
ghi gì - thà thiếu còn hơn gán nhầm khúc dịch cho đoạn khác. Cùng tinh thần với
`titles_agree` bên `import_sujato.py`: nơi đó một bài lệch tên là bị loại, không ghi.

Chạy:
    python align_minhchau.py sn --limit 20 --dry-run    # đo chất lượng, không tiêu tiền ghi
    python align_minhchau.py dn mn sn an                # ghép thật
    python align_minhchau.py --list
"""

import argparse
import hashlib
import json
import math
import re
import sys
import time
from pathlib import Path

# line_buffering: khong co no thi Python dem stdout khi chay nen/ghi ra file,
# nhin vao thay rong va tuong la treo.
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from google import genai

from app.config import settings
from app.db import execute, fetch_all

SOURCE_ID = "minh_chau"
ANCHOR_SOURCE = "sujato"
LANGUAGE = "vi"

EMBED_MODEL = "gemini-embedding-2"
EMBED_DIMS = 768
EMBED_BATCH = 64
# Doan qua ngan (tieu de chuong, "—Thua vang, bach The Ton.") khong du dac trung de ghep;
# van giu lai trong ket qua nhung gan theo doan lien truoc.
MIN_PARA_CHARS = 40
# Duoi nguong nay coi nhu ca bai ghep hong -> bo qua, khong ghi.
SUTTA_MIN_SIM = 0.55
# Doan Pali khong co ban Sujato thi khong co neo; bai nao thieu qua nhieu thi bo.
MIN_ANCHOR_RATIO = 0.6

CACHE = Path(__file__).resolve().parent / ".embed_cache"


# --------------------------------------------------------------------------- embedding


def _cache_path(text: str) -> Path:
    digest = hashlib.sha256(f"{EMBED_MODEL}:{EMBED_DIMS}:{text}".encode("utf-8")).hexdigest()
    return CACHE / digest[:2] / f"{digest}.json"


def embed_batch(texts: list[str], client: genai.Client) -> list[list[float] | None]:
    """Nhúng cả loạt, có cache trên đĩa.

    Chạy lại sau khi sửa ngưỡng là chuyện thường, mà mỗi lần chạy lại tốn ~81.000 lượt gọi
    nếu không nhớ - nên cache theo nội dung đoạn văn, không theo vị trí.
    """
    CACHE.mkdir(exist_ok=True)
    out: list[list[float] | None] = [None] * len(texts)
    todo: list[int] = []
    for index, text in enumerate(texts):
        path = _cache_path(text)
        if path.exists():
            try:
                out[index] = json.loads(path.read_text(encoding="utf-8"))
                continue
            except Exception:  # noqa: BLE001 - cache hong thi nhung lai
                pass
        todo.append(index)

    for start in range(0, len(todo), EMBED_BATCH):
        chunk = todo[start : start + EMBED_BATCH]
        payload = [texts[i] for i in chunk]
        vectors = None
        for attempt in range(3):
            try:
                response = client.models.embed_content(
                    model=EMBED_MODEL,
                    contents=payload,
                    config={"output_dimensionality": EMBED_DIMS},
                )
                vectors = [list(e.values) for e in (response.embeddings or [])]
                break
            except Exception as exc:  # noqa: BLE001 - API chap chon, thu lai roi moi bo
                if attempt == 2:
                    print(f"  !! nhúng lỗi: {type(exc).__name__}: {str(exc)[:120]}")
                    vectors = None
                else:
                    time.sleep(2 * (attempt + 1))
        if not vectors or len(vectors) != len(chunk):
            continue
        for index, vector in zip(chunk, vectors):
            out[index] = vector
            path = _cache_path(texts[index])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(vector), encoding="utf-8")
    return out


def normalize(vector: list[float] | None) -> list[float] | None:
    if not vector:
        return None
    norm = math.sqrt(sum(v * v for v in vector))
    return [v / norm for v in vector] if norm else None


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


# --------------------------------------------------------------------------- ghép


def split_paragraphs(text: str) -> list[str]:
    """Tách bản dịch thành đoạn văn, gộp mẩu quá ngắn vào đoạn liền trước.

    Tiêu đề chương và câu đáp một dòng ("—Thưa vâng, bạch Thế Tôn.") không đủ đặc trưng
    để ghép riêng; để đứng một mình thì chúng nhận điểm tương đồng ngẫu nhiên và kéo lệch
    cả dãy.
    """
    paragraphs: list[str] = []
    for line in (l.strip() for l in text.split("\n")):
        if not line:
            continue
        if paragraphs and len(line) < MIN_PARA_CHARS:
            paragraphs[-1] += "\n" + line
        elif paragraphs and len(paragraphs[-1]) < MIN_PARA_CHARS:
            paragraphs[-1] += "\n" + line
        else:
            paragraphs.append(line)
    return paragraphs


def similarity_matrix(
    para_vectors: list[list[float] | None], anchor_vectors: list[list[float] | None]
) -> list[list[float]]:
    """Ma trận tương đồng đã TRỪ TRUNG BÌNH THEO HÀNG.

    Mọi đoạn trong cùng một bài kinh đều nói cùng một chuyện nên điểm tuyệt đối bám sát
    nhau (đo được: quanh 0,73 cho mọi cặp). Để nguyên thì bảng quy hoạch động không phân
    biệt nổi đoạn nào ứng với đoạn nào, và lời giải tối ưu là dồn hết đoạn văn vào một
    đoạn Pali - đúng hiện tượng "ghép 1/8 đoạn" ở lần chạy đầu.

    Trừ đi trung bình của chính hàng đó thì cái còn lại là câu hỏi đúng: trong các đoạn
    Pali, đoạn nào HỢP VỚI đoạn văn này hơn mức thường? Đậu lại một chỗ khi đó phải trả
    giá bằng những điểm âm liên tiếp.
    """
    matrix: list[list[float]] = []
    for pv in para_vectors:
        row = [cosine(pv, av) if (pv and av) else 0.0 for av in anchor_vectors]
        mean = sum(row) / len(row) if row else 0.0
        matrix.append([value - mean for value in row])
    return matrix


def align_monotonic(
    para_vectors: list[list[float] | None],
    anchor_vectors: list[list[float] | None],
    gap_penalty: float = 0.01,
) -> tuple[list[int], float]:
    """Gán mỗi đoạn văn Việt cho một đoạn Pali, thứ tự không được lùi.

    `best[i]` = tổng điểm tốt nhất khi đoạn văn đang xét gán vào đoạn Pali thứ i. Vì chỉ
    được tiến, giá trị kế thừa là max của mọi `best[k]` với k <= i - tính bằng prefix-max
    nên cả bảng chạy O(số đoạn văn × số đoạn Pali).

    `gap_penalty` trừ điểm cho mỗi đoạn Pali bị nhảy qua. Không có nó thì lời giải hay
    nhảy cóc tới cuối bài rồi bỏ trống cả khúc giữa.

    Trả về (gán cho từng đoạn văn, điểm tương đồng THẬT trung bình - không phải điểm đã
    trừ trung bình, vì ngưỡng an toàn cần đọc trên thang gốc).
    """
    n_para, n_anchor = len(para_vectors), len(anchor_vectors)
    if not n_para or not n_anchor:
        return [], 0.0

    centered = similarity_matrix(para_vectors, anchor_vectors)
    NEG = float("-inf")
    previous = [0.0] * n_anchor
    choice: list[list[int]] = []

    for j in range(n_para):
        row_choice = [0] * n_anchor
        running_best, running_arg = NEG, 0
        current = [NEG] * n_anchor
        for i in range(n_anchor):
            # prefix-max co tru phi nhay coc: gia tri ke thua giam dan khi bo xa lai phia sau
            if previous[i] > running_best:
                running_best, running_arg = previous[i], i
            running_best -= gap_penalty
            base = running_best if j else 0.0
            row_choice[i] = running_arg if j else i
            current[i] = base + centered[j][i]
        choice.append(row_choice)
        previous = current

    end = max(range(n_anchor), key=lambda i: previous[i])
    assignment = [0] * n_para
    cursor = end
    for j in range(n_para - 1, -1, -1):
        assignment[j] = cursor
        cursor = choice[j][cursor]

    total = 0.0
    for j, i in enumerate(assignment):
        pv, av = para_vectors[j], anchor_vectors[i]
        total += cosine(pv, av) if (pv and av) else 0.0
    return assignment, total / n_para


# --------------------------------------------------------------------------- chạy


def suttas_in_scope(nikaya_filter: list[str]) -> list[dict]:
    rows = fetch_all(
        """
        select h.passage_id, h.translated_text, h.source_ref,
               h.document_id, h.start_sort_order, h.end_sort_order
        from human_translations h
        where h.source = %s and h.start_sort_order is not null
        order by h.source_ref
        """,
        [SOURCE_ID],
    )
    if not nikaya_filter:
        return rows
    prefixes = tuple(nikaya_filter)
    return [r for r in rows if re.match(r"^[a-z]+", str(r["source_ref"]) or "")
            and re.match(r"^[a-z]+", str(r["source_ref"])).group(0) in prefixes]


def process(sutta: dict, client: genai.Client, args) -> dict:
    result = {"uid": sutta["source_ref"], "status": "", "sim": 0.0, "written": 0}

    anchors = fetch_all(
        """
        select p.id, p.sort_order, hs.translated_text as english
        from passages p
        left join human_translations hs on hs.passage_id = p.id and hs.source = %s
        where p.document_id = %s and p.sort_order between %s and %s
        order by p.sort_order
        """,
        [ANCHOR_SOURCE, sutta["document_id"], sutta["start_sort_order"], sutta["end_sort_order"]],
    )
    if not anchors:
        result["status"] = "không có đoạn Pali"
        return result

    have = [a for a in anchors if (a["english"] or "").strip()]
    if len(have) / len(anchors) < MIN_ANCHOR_RATIO:
        result["status"] = f"thiếu neo Sujato ({len(have)}/{len(anchors)})"
        return result

    paragraphs = split_paragraphs(sutta["translated_text"])
    if len(paragraphs) < 2:
        result["status"] = "bản dịch không tách được đoạn"
        return result

    english = [(a["english"] or "").strip() for a in anchors]
    vectors = embed_batch(paragraphs + english, client)
    para_vectors = [normalize(v) for v in vectors[: len(paragraphs)]]
    anchor_vectors = [normalize(v) for v in vectors[len(paragraphs) :]]

    assignment, mean_sim = align_monotonic(para_vectors, anchor_vectors)
    result["sim"] = mean_sim
    if not assignment:
        result["status"] = "không nhúng được"
        return result
    if mean_sim < SUTTA_MIN_SIM:
        result["status"] = f"BỎ QUA - tương đồng {mean_sim:.2f} < {SUTTA_MIN_SIM}"
        return result

    buckets: dict[int, list[str]] = {}
    for j, i in enumerate(assignment):
        buckets.setdefault(i, []).append(paragraphs[j])

    result["status"] = f"ghép {len(buckets)}/{len(anchors)} đoạn"
    if args.verbose:
        for i in sorted(buckets)[:3]:
            print(f"       [{anchors[i]['sort_order']}] {' '.join(buckets[i])[:96]}")
    if args.dry_run:
        return result

    # Doi tu CAP BAI KINH sang CAP DOAN: xoa ban ghi cu cua bai roi ghi tung doan.
    # Xoa sau khi da ghep xong, de bai ghep hong van giu nguyen du lieu cu.
    execute(
        "delete from human_translations where source = %s and passage_id = %s",
        [SOURCE_ID, sutta["passage_id"]],
    )
    for i, parts in buckets.items():
        execute(
            """
            insert into human_translations
              (passage_id, source, language, translated_text, source_ref, segment_ids)
            values (%s, %s, %s, %s, %s, %s)
            on conflict (passage_id, source) do update
              set translated_text = excluded.translated_text,
                  source_ref = excluded.source_ref,
                  document_id = null, start_sort_order = null, end_sort_order = null,
                  updated_at = now()
            """,
            [anchors[i]["id"], SOURCE_ID, LANGUAGE, "\n\n".join(parts), sutta["source_ref"], []],
        )
        result["written"] += 1
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Ghép bản dịch Minh Châu xuống cấp đoạn.")
    parser.add_argument("nikayas", nargs="*", help="dn mn sn an ... (bỏ trống = tất cả)")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true", help="chỉ đo, không ghi vào DB")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    suttas = suttas_in_scope([n.lower() for n in args.nikayas])
    if args.list:
        groups: dict[str, int] = {}
        for s in suttas:
            key = re.match(r"^[a-z]+", str(s["source_ref"]) or "?")
            groups[key.group(0) if key else "?"] = groups.get(key.group(0) if key else "?", 0) + 1
        for key, count in sorted(groups.items()):
            print(f"  {key:<6} {count} bài còn ở cấp bài kinh")
        return
    if not suttas:
        print("Không còn bài nào ở cấp bài kinh cho phạm vi này.")
        return
    if args.limit:
        suttas = suttas[: args.limit]

    if not str(settings()["gemini_api_key"]):
        print("Thiếu GEMINI_API_KEY - không nhúng được.")
        raise SystemExit(1)
    client = genai.Client(
        api_key=str(settings()["gemini_api_key"]),
        http_options={"timeout": int(settings()["gemini_request_timeout_ms"])},
    )

    print(f"{'CHẠY THỬ - không ghi' if args.dry_run else 'GHÉP THẬT'} · {len(suttas)} bài\n")
    stats = {"ok": 0, "skip": 0, "written": 0}
    sims: list[float] = []

    for sutta in suttas:
        outcome = process(sutta, client, args)
        flag = "BỎ" if outcome["status"].startswith(("BỎ", "thiếu", "không")) else "ok"
        if flag == "ok":
            stats["ok"] += 1
            sims.append(outcome["sim"])
        else:
            stats["skip"] += 1
        stats["written"] += outcome["written"]
        print(f"  {outcome['uid']:<12} sim={outcome['sim']:.2f}  {outcome['status']}")

    print()
    print(f"TỔNG: ghép được {stats['ok']} · bỏ qua {stats['skip']} · ghi {stats['written']} đoạn")
    if sims:
        sims.sort()
        print(f"  tương đồng trung bình {sum(sims)/len(sims):.3f} · "
              f"thấp nhất {sims[0]:.3f} · trung vị {sims[len(sims)//2]:.3f}")


if __name__ == "__main__":
    main()
