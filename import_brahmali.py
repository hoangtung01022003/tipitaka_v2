"""Nạp bản dịch tiếng Anh Tạng Luật của Bhikkhu Brahmali.

VÌ SAO CẦN: Tạng Luật đang KHÔNG có bản tiếng Anh nào. HT. Minh Châu và Bhikkhu Sujato
đều không dịch Luật, chỉ mình Tỳ Khưu Indacanda dịch (tiếng Việt, phủ 42%).

NGUỒN: bilara-data, cùng kho với bản Sujato, nên có sẵn mã segment cho từng câu. Đây là
khác biệt quyết định so với bản PDF của Ngài Indacanda: ở đây không phải đoán chỗ nào cả,
câu Pali gốc đi kèm ngay bên cạnh câu tiếng Anh.

CÁCH GHÉP - neo theo TÀI LIỆU rồi căn chỉnh toàn cục
-----------------------------------------------------
Bốn bộ Nikāya neo theo BÀI KINH (`build_targets` + so tên bài). Tạng Luật không có "bài
kinh" để neo: bilara chia theo điều học (`pli-tv-bu-vb-pj1`), còn CST chia theo tập
(`vin01m` ... `vin02m4`) rồi mới tới chương mục. Hai cách chia không ánh xạ 1-1.

ĐÃ THỬ con trỏ tiến-một-chiều chạy suốt tài liệu (như `import_sujato.align`, nhưng phạm
vi là cả tập thay vì một bài kinh) - HỎNG: `pj1` được 87% rồi `pj2` tụt còn 12%, `ss1`
xuống 0%. Một segment khớp nhầm vào công thức giới luật nằm xa phía sau là con trỏ vọt
lên và chặn sạch phần còn lại. Ở Nikāya lỗi này bị chặn bởi phạm vi một bài kinh; ở Tạng
Luật không có phạm vi nào để chặn.

Nên dùng `align_globally` (quy hoạch động, viết cho bản Indacanda): xét cả tập cùng lúc,
tìm cách ghép cho tổng độ khớp cao nhất với ràng buộc không đảo thứ tự. Một segment khớp
nhầm chỗ xa sẽ bị chính các segment hàng xóm bác bỏ.

Ứng viên được lọc bằng chỉ mục từ hiếm chứ không dò tuần tự: 20.000 segment nhân 2.200
đoạn là 44 triệu phép so chuỗi mỗi tập, không kham nổi.

Tài liệu đích của mỗi file được DÒ TỰ ĐỘNG chứ không khai báo tay: đem segment của file
đi tìm trong từng tập, tập nào chứa nhiều nhất thì đó là tập của nó. Cách này tự đúng kể
cả khi CST xếp chương khác với bilara.

Phải xét TOÀN BỘ segment, không được lấy vài chục segment ĐẦU cho nhanh. Bản đầu chỉ dò
12 segment đầu và sai 5 file - trong đó hai file sai mà KHÔNG hề báo lỗi:

  * `pli-tv-bu-vb-pc8` bị xếp vào `vin01m` với 12/12 segment đầu khớp, tưởng như chắc
    chắn. Nhưng điều pācittiya 8 (khoe pháp thượng nhân với người chưa tu) dùng chung
    câu chuyện duyên khởi với pārājika 4, mà pārājika thì nằm ở `vin01m`. Xét cả file
    thì `vin02m1` 114/160 còn `vin01m` chỉ 64/160: 160 segment đã bị nạp nhầm tập.
  * `pli-tv-bu-pm` bị xếp vào `vin02m2` vì Giới bổn mở đầu bằng công thức tụng Uposatha,
    mà công thức ấy nằm trong Mahāvagga. Cả file: `vin02m1` 180, `vin01m` 162, `vin02m2`
    vỏn vẹn 9.

Đầu file là chỗ dễ đánh lừa nhất: duyên khởi và công thức nghi thức hay được dùng lại
nguyên văn ở tập khác, còn phần thân mới là phần riêng của điều học. Độ chắc chắn ở đầu
file không nói lên điều gì - `pc8` khớp 12/12 mà vẫn sai tập.

Ngưỡng chấp nhận cũng không được đặt cứng: `pli-tv-bi-vb-as1-7` chỉ có đúng 2 segment
(phần còn lại là dòng tiêu đề), nên sàn "ít nhất 2 hit" thì nó không bao giờ với tới.
Một hit duy nhất vẫn tin được nếu không tập nào khác chứa segment ấy.

Một file bilara chỉ được gắn vào MỘT tập, dù `pli-tv-bu-pm` thật sự trải trên hai tập
(180 ở `vin02m1`, 162 ở `vin01m` - Giới bổn liệt kê điều học của cả hai). Đã đo tỉ lệ
tập-nhì/tập-nhất trên cả 421 file: phân bố trải đều 0.1-0.5 rồi thưa dần, không có khe
nào tách được "trải thật" khỏi "trùng công thức", nên mọi ngưỡng đều là số bịa. Mà nạp
thêm cũng không được gì: những đoạn `vin01m` mà Giới bổn khớp thì file Phân tích của
chính điều học ấy đã phủ rồi, với `source_ref` đúng chỗ hơn.

Đo trước khi viết: 108/120 segment đầu của `pli-tv-bu-vb-pj1` có mặt nguyên văn trong
`vin01m` (90%) - ngang mức bản Sujato ở Nikāya.

Chạy:
    python import_brahmali.py --dry-run
    python import_brahmali.py
"""

import argparse
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from app.db import execute, fetch_all
from app.normalize import normalize_pali
from import_sujato import RAW_BASE, TREE_API, _clean, align_globally, fetch_json

SOURCE_ID = "brahmali"
LANGUAGE = "en"
_ROOT_SUFFIX = "_root-pli-ms.json"

# Các tập Tạng Luật trong CSDL, theo thứ tự kinh điển.
VINAYA_FILES = [
    "vin01m.mul.xml",   # Pārājikapāḷi
    "vin02m1.mul.xml",  # Pācittiyapāḷi
    "vin02m2.mul.xml",  # Mahāvaggapāḷi
    "vin02m3.mul.xml",  # Cullavaggapāḷi
    "vin02m4.mul.xml",  # Parivārapāḷi
]

# Thứ tự kinh điển của các nhóm và các loại điều học. Sắp theo tên file thì "pc10" đứng
# trước "pc2", và "sk" đứng trước "ss" - đều sai, nên phải khai báo.
GROUP_ORDER = ["pli-tv-bu-pm", "pli-tv-bu-vb", "pli-tv-bi-pm", "pli-tv-bi-vb",
               "pli-tv-kd", "pli-tv-pvr"]
CLASS_ORDER = ["pj", "ss", "ay", "np", "pc", "pd", "sk", "as"]

# Segment ngắn hơn thế này thì quá chung, dễ khớp nhầm chỗ.
MIN_SEGMENT_CHARS = 25


def sort_key(uid: str) -> tuple:
    """Thứ tự kinh điển của một uid Tạng Luật."""
    group = next((index for index, name in enumerate(GROUP_ORDER) if uid.startswith(name)), 99)
    rest = uid
    for name in GROUP_ORDER:
        if uid.startswith(name):
            rest = uid[len(name):].lstrip("-")
            break
    match = re.match(r"([a-z]+)", rest)
    klass = CLASS_ORDER.index(match.group(1)) if match and match.group(1) in CLASS_ORDER else -1
    numbers = [int(n) for n in re.findall(r"\d+", rest)] or [0]
    return (group, klass, *numbers)


def vinaya_files() -> list[tuple[str, str]]:
    """(uid, đường dẫn dùng chung) của mọi file Tạng Luật, theo thứ tự kinh điển."""
    data = fetch_json(TREE_API)
    if not data:
        raise RuntimeError("Không tải được cây thư mục bilara-data.")
    prefix = "root/pli/ms/vinaya/"
    found = []
    for item in data.get("tree", []):
        path = str(item.get("path") or "")
        if path.startswith(prefix) and path.endswith(_ROOT_SUFFIX):
            uid = path.rsplit("/", 1)[-1][: -len(_ROOT_SUFFIX)]
            found.append((uid, path[len("root/pli/ms/") : -len(_ROOT_SUFFIX)]))
    return sorted(found, key=lambda pair: sort_key(pair[0]))


def load_pair(stem: str) -> tuple[dict, dict]:
    root = fetch_json(f"{RAW_BASE}/root/pli/ms/{stem}{_ROOT_SUFFIX}") or {}
    english = fetch_json(f"{RAW_BASE}/translation/en/brahmali/{stem}_translation-en-brahmali.json") or {}
    return root, english


def segments_of(root: dict) -> list[tuple[str, str]]:
    """(mã segment, chữ Pali đã chuẩn hoá), bỏ các dòng tiêu đề `<uid>:0.x`."""
    out = []
    for key, value in root.items():
        if re.search(r":0\.", key):
            continue
        text = normalize_pali(_clean(value))
        if len(text) >= MIN_SEGMENT_CHARS:
            out.append((key, text))
    return out


def build_token_index(rows: list[dict]) -> dict[str, list[int]]:
    """từ -> các vị trí đoạn có chứa từ đó. Dùng để thu hẹp ứng viên trước khi so chuỗi."""
    index: dict[str, list[int]] = {}
    for position, row in enumerate(rows):
        for token in set(row["normalized_pali"].split()):
            if len(token) >= 5:
                index.setdefault(token, []).append(position)
    return index


def candidate_positions(text: str, rows: list[dict], index: dict[str, list[int]]) -> list[int]:
    """Các đoạn có thể chứa nguyên văn `text`, lọc qua từ hiếm nhất rồi mới so chuỗi."""
    tokens = [t for t in set(text.split()) if len(t) >= 5 and t in index]
    if not tokens:
        return []
    tokens.sort(key=lambda t: len(index[t]))
    narrowed = set(index[tokens[0]])
    for token in tokens[1:3]:
        narrowed &= set(index[token])
        if len(narrowed) <= 4:
            break
    return [p for p in sorted(narrowed) if text in rows[p]["normalized_pali"]]


def guess_document(
    segments: list[tuple[str, str]], blobs: dict[str, str]
) -> tuple[str | None, dict[str, int]]:
    """Tập nào chứa nhiều segment của file này nhất, kèm bảng điểm từng tập.

    Xét cả file chứ không lấy mẫu ở đầu, và không đặt sàn hit cứng - lý do cả hai nằm
    ở đầu file.
    """
    hits = {name: sum(1 for _key, text in segments if text in blob) for name, blob in blobs.items()}
    ranked = sorted(hits.items(), key=lambda pair: -pair[1])
    if not ranked or ranked[0][1] == 0:
        return None, hits
    best_name, best = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0
    # Hai hit trở lên thì đủ chắc. Một hit duy nhất chỉ tin khi không tập nào khác chứa
    # segment ấy - nhờ vậy file chỉ có vài segment vẫn gắn được đúng chỗ.
    if best >= 2 or second == 0:
        return best_name, hits
    return None, hits


def main() -> None:
    parser = argparse.ArgumentParser(description="Nạp bản dịch Tạng Luật tiếng Anh của Bhikkhu Brahmali.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="chỉ chạy N file đầu, để thử")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if not args.dry_run:
        migration = Path(__file__).resolve().parents[1] / "db" / "migrations" / "002_human_translations.sql"
        execute(migration.read_text(encoding="utf-8"))
        print(f"đã áp dụng {migration.name}\n")

    documents = {}
    passages = {}
    blobs = {}
    for name in VINAYA_FILES:
        row = fetch_all("select id from documents where file_name = %s", [name])
        if not row:
            print(f"  !! không có {name} trong CSDL")
            continue
        documents[name] = str(row[0]["id"])
        passages[name] = fetch_all(
            "select id, normalized_pali from passages where document_id = %s order by sort_order",
            [documents[name]],
        )
        blobs[name] = "\n".join(r["normalized_pali"] for r in passages[name])
    print(f"Tạng Luật trong CSDL: {sum(len(v) for v in passages.values())} đoạn / {len(passages)} tập")

    files = vinaya_files()
    if args.limit:
        files = files[: args.limit]
    print(f"bilara có {len(files)} file Tạng Luật")
    english_cache: dict[str, dict] = {}

    # Gom segment của mọi file về đúng tập của nó, giữ thứ tự kinh điển.
    gom: dict[str, list[tuple[str, str, str]]] = {name: [] for name in passages}
    grand = {"segments": 0, "matched": 0, "written": 0, "files": 0, "skipped": []}
    for uid, stem in files:
        root, english = load_pair(stem)
        if not root or not english:
            grand["skipped"].append(f"{uid} (thiếu file)")
            continue
        segments = segments_of(root)
        if not segments:
            grand["skipped"].append(f"{uid} (bilara không có văn bản Pali)")
            continue
        name, hits = guess_document(segments, blobs)
        if not name:
            grand["skipped"].append(f"{uid} (không đoán được tập)")
            continue
        if args.verbose:
            table = " ".join(f"{n.split('.')[0]}={h}" for n, h in hits.items() if h)
            print(f"  {uid:<24} -> {name} ({hits[name]}/{len(segments)}) · {table}")
        for key, text in segments:
            gom[name].append((uid, key, text))
        grand["files"] += 1
        grand["segments"] += len(segments)
        english_cache[uid] = english
    print(f"đã tải {grand['files']} file\n")

    for name, items in gom.items():
        if not items:
            continue
        rows = passages[name]
        index = build_token_index(rows)
        # Trọng số theo độ dài: segment dài khớp được thì đáng tin hơn segment ngắn.
        candidates = [
            [(position, min(3.0, len(text) / 60)) for position in candidate_positions(text, rows, index)]
            for _uid, _key, text in items
        ]
        # Một đoạn CST chứa nhiều segment bilara, xem chú thích trong align_globally.
        chosen = align_globally(candidates, allow_same_position=True)

        assignment: dict[int, list[tuple[str, str]]] = {}
        for item_index in sorted(chosen):
            uid, key, _text = items[item_index]
            if str(english_cache[uid].get(key, "")).strip():
                assignment.setdefault(chosen[item_index], []).append((uid, key))

        written = 0
        for position, keys in assignment.items():
            text = " ".join(_clean(english_cache[uid][key]) for uid, key in keys).strip()
            if not text:
                continue
            if not args.dry_run:
                execute(
                    """
                    insert into human_translations
                      (passage_id, source, language, translated_text, source_ref, segment_ids)
                    values (%s, %s, %s, %s, %s, %s)
                    on conflict (passage_id, source) do update
                      set translated_text = excluded.translated_text,
                          source_ref = excluded.source_ref,
                          segment_ids = excluded.segment_ids,
                          updated_at = now()
                    """,
                    [str(rows[position]["id"]), SOURCE_ID, LANGUAGE, text, keys[0][0],
                     [key for _uid, key in keys]],
                )
            written += 1

        grand["matched"] += len(chosen)
        grand["written"] += written
        rate = 100 * len(chosen) // max(1, len(items))
        print(f"  {name:<16} {len(chosen)}/{len(items)} segment ({rate}%)"
              f" · phủ {100 * written // max(1, len(rows))}% số đoạn · ghi {written}")

    rate = 100 * grand["matched"] // max(1, grand["segments"])
    print(f"\nTỔNG: {grand['files']} file · {grand['matched']}/{grand['segments']} segment"
          f" ({rate}%) · ghi {grand['written']} đoạn")
    if grand["skipped"]:
        # In hết, không cắt bớt: danh sách bị cắt là chỗ lỗi nấp được lâu nhất.
        print(f"bỏ qua {len(grand['skipped'])} file:")
        for note in grand["skipped"]:
            print(f"  - {note}")

    if not args.dry_run and grand["written"]:
        execute(
            """
            insert into human_translation_imports
              (source, language, scope, segments_total, segments_matched, passages_written, notes)
            values (%s, %s, %s, %s, %s, %s, %s)
            """,
            [SOURCE_ID, LANGUAGE, "vinaya", grand["segments"], grand["matched"],
             grand["written"], "Tạng Luật, bilara-data"],
        )


if __name__ == "__main__":
    main()
