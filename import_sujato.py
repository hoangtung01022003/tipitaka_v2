"""Nạp bản dịch tiếng Anh của Bhikkhu Sujato vào DB, ghép theo từng đoạn Pali.

Nguồn: kho bilara-data của SuttaCentral (giấy phép CC0). Mỗi bài kinh có hai file
song song, chung khoá segment, nên Pali và tiếng Anh vốn đã khớp từng câu:

    root/pli/ms/sutta/mn/mn10_root-pli-ms.json        {"mn10:1.1": "Evaṁ me sutaṁ—", ...}
    translation/en/sujato/sutta/mn/mn10_translation-en-sujato.json

Việc cần làm là ghép các segment đó vào đúng dòng `passages` của DB này.

CÁCH GHÉP
---------
KHÔNG dò văn bản trên toàn kho. Kinh Pali lặp nguyên khối công thức - Kinh Niệm Xứ
của Trung Bộ và Trường Bộ gần như trùng chữ - nên dò kiểu đó gán nhầm bài mà vẫn cho
tỷ lệ khớp trông rất thuyết phục.

Thay vào đó:

1. Neo theo cấu trúc: đánh số bài kinh trong DB theo đúng thứ tự kinh điển để suy ra
   mã của SuttaCentral. Số bài kinh trong CST đếm lại từ đầu ở mỗi phẩm, nên phải dựa
   vào THỨ TỰ trong tài liệu chứ không dựa vào số in trong tiêu đề.
2. Kiểm chứng bằng tên bài kinh: bilara có tên Pali của bài ở segment "<uid>:0.2".
   Đem so với tiêu đề section trong DB. Lệch tên thì BỎ QUA bài đó, không ghi.
   Đây là lưới an toàn chống lệch số thứ tự - lỗi này nếu lọt sẽ âm thầm gán sai
   bản dịch cho hàng loạt bài.
3. Chỉ sau khi đã neo đúng bài mới quét segment trong phạm vi sort_order của bài đó,
   con trỏ chỉ tiến không lùi để công thức lặp không kéo ngược về đầu bài.

Chạy:
    python import_sujato.py mn dn sn an          # các bộ chính
    python import_sujato.py --all                # tất cả những bộ đã hỗ trợ
    python import_sujato.py sn --limit 20 --dry-run
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

# line_buffering: khong co no thi Python dem stdout khi chay nen/ghi ra file,
# nhin vao thay rong va tuong la treo.
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from app.db import execute, fetch_all, fetch_one
from app.normalize import normalize_pali

RAW_BASE = "https://raw.githubusercontent.com/suttacentral/bilara-data/published"
SOURCE_ID = "sujato"
LANGUAGE = "en"

# "flat"      -> uid dạng dn1, mn10, iti5      (đánh số liên tục trong cả bộ)
# "grouped"   -> uid dạng sn47.1, ud1.1        (nhóm.bài, nhóm nhận từ tiêu đề nhóm)
# "per_file"  -> uid dạng an3.65               (mỗi file là một nhóm, số nhóm ghi sẵn)
NIKAYA_CONFIG: dict[str, dict] = {
    "dn": {"mode": "flat", "files": ["s0101m.mul.xml", "s0102m.mul.xml", "s0103m.mul.xml"], "expect": 34},
    "mn": {"mode": "flat", "files": ["s0201m.mul.xml", "s0202m.mul.xml", "s0203m.mul.xml"], "expect": 152},
    "sn": {
        "mode": "grouped",
        "files": ["s0301m.mul.xml", "s0302m.mul.xml", "s0303m.mul.xml", "s0304m.mul.xml", "s0305m.mul.xml"],
        "group_suffix": "samyuttam",
        "expect_groups": 56,
    },
    "an": {
        "mode": "per_file",
        "file_groups": [
            ("s0401m.mul.xml", 1), ("s0402m1.mul.xml", 2), ("s0402m2.mul.xml", 3),
            ("s0402m3.mul.xml", 4), ("s0403m1.mul.xml", 5), ("s0403m2.mul.xml", 6),
            ("s0403m3.mul.xml", 7), ("s0404m1.mul.xml", 8), ("s0404m2.mul.xml", 9),
            ("s0404m3.mul.xml", 10), ("s0404m4.mul.xml", 11),
        ],
    },
    # Thơ kệ: bilara không đánh số theo bài kinh mà theo DẢI CÂU KỆ ("dhp1-20") hoặc
    # dải bài ("an1.1-10"), nên không tra được theo mã bài như các bộ trên. Ở đây mỗi
    # MỤC trong DB ứng với đúng một file bilara, ghép theo thứ tự - xem `_targets_by_section`.
    "dhp": {"mode": "by_section", "files": ["s0502m.mul.xml"], "uid_pattern": r"^dhp\d",
            "section_suffixes": ("vaggo",), "expect": 26},
    # Đuôi 'gatha' chứ không phải 'theragatha': một mục là `4. Sivakasāmaṇeragāthā`,
    # kệ của vị sa-di chứ không phải trưởng lão. Bỏ sót nó là lệch cả bộ 264 bài.
    "thag": {"mode": "by_section", "files": ["s0508m.mul.xml"], "uid_pattern": r"^thag\d",
             "section_suffixes": ("gatha",), "expect": 264},
    "thig": {"mode": "by_section", "files": ["s0509m.mul.xml"], "uid_pattern": r"^thig\d",
             "section_suffixes": ("therigatha",), "expect": 73},
    "cp": {"mode": "by_section", "files": ["s0512m.mul.xml"], "uid_pattern": r"^cp\d",
           "section_suffixes": ("cariya",), "expect": 35},
    "an1": {"mode": "by_section", "files": ["s0401m.mul.xml"], "uid_pattern": r"^an1\.",
            "section_suffixes": ("vaggo",), "expect": 31},
    # CHƯA nạp được, không phải quên:
    # - an2: Tăng Chi chương Hai trong CST trộn ba kiểu mục (`1. Vajjasuttaṃ`,
    #   `(6) 1. Puggalavaggo`, `1. Kodhapeyyālaṃ`), đếm kiểu nào cũng không ra 19 file
    #   của bilara. Ghép theo thứ tự khi số mục không khớp là ghép sai cả bộ.
    # - ja: Sujato mới dịch 82 trong 547 chuyện Bổn Sanh, nên không thể ghép theo thứ
    #   tự; phải dò theo tên chuyện, là việc riêng.
    # Hai bộ dưới đây không theo đúng khuôn "số thứ tự + đuôi sutta", xem
    # `_is_sutta_title`. Thiếu hai ngoại lệ này thì iti mất 16 bài và snp mất 19.
    "iti": {"mode": "flat", "files": ["s0504m.mul.xml"], "allow_unnumbered": True, "expect": 112},
    "ud": {"mode": "grouped", "files": ["s0503m.mul.xml"], "group_suffix": "vaggo",
           "expect_groups": 8, "expect": 80},
    "snp": {"mode": "grouped", "files": ["s0505m.mul.xml"], "group_suffix": "vaggo",
            "expect_groups": 5, "allow_unnumbered": True, "extra_suffixes": ("puccha", "gatha"),
            "expect": 73},
}

_SUTTA_SUFFIXES = ("suttam", "sutta", "suttanta", "suttantam")


def _is_sutta_title(title: str, *, allow_unnumbered: bool = False,
                    extra_suffixes: tuple[str, ...] = ()) -> bool:
    """Tiêu đề bài kinh: có số ở đầu và kết thúc bằng 'sutta(ṃ)'.

    Mục con bên trong một bài (Uddeso, Kāyānupassanā ...) không có số ở đầu,
    nên điều kiện này tách được hai loại.

    Tiểu Bộ phá cả hai điều kiện đó, nên `NIKAYA_CONFIG` nới riêng cho hai bộ:
    - `allow_unnumbered` (iti): CST bỏ quên số thứ tự của `Kalyāṇasīlasuttaṃ`.
      Vì uid của iti đánh liền một mạch, bỏ sót một bài làm 15 bài đứng sau bị
      đánh lùi một nấc (DB iti97 thành ra là iti98 của SuttaCentral) - lưới so
      tên bài chặn lại nên chúng không bị ghép sai, nhưng cũng không nạp được.
    - `extra_suffixes` (snp): trọn phẩm Pārāyana đặt tên `...pucchā` /
      `...gāthā` chứ không phải `...sutta`, nên cả 19 bài không hiện ra.

    Chỉ nới cho bộ nào đã đối chiếu hết danh sách mục của bộ đó: với iti và snp,
    điều kiện mới nhận đúng những mục còn thiếu, không nhận thêm mục nào khác.
    """
    if not allow_unnumbered and not re.match(r"^\(?\d", title.strip()):
        return False
    return normalize_pali(title).endswith(_SUTTA_SUFFIXES + extra_suffixes)


def _strip_leading_number(title: str) -> str:
    return normalize_pali(re.sub(r"^[\(\)\d\.\-\s]+", "", title.strip()))


_TITLE_TAIL = re.compile(r"(suttantam|suttanta|suttam|sutta)$")


def _title_stems(title: str) -> list[str]:
    """Các dạng tên có thể có của một bài kinh, đã bỏ số thứ tự và đuôi 'sutta'.

    Có tiêu đề mang tên thay thế trong ngoặc ("Dhamma (nāvā) sutta"), nên tách
    riêng phần trong ngoặc thành một ứng viên độc lập.
    """
    normalized = _strip_leading_number(title)
    inner = re.findall(r"\(([^)]*)\)", normalized)
    outer = re.sub(r"\([^)]*\)", " ", normalized)
    stems = []
    for candidate in [outer, *inner]:
        stem = _TITLE_TAIL.sub("", re.sub(r"\s+", "", candidate)).strip()
        if len(stem) >= 4 and stem not in stems:
            stems.append(stem)
    return stems


def _stems_agree(left: str, right: str) -> bool:
    if left in right or right in left:
        return True
    # CST và SuttaCentral hay đặt tên dài ngắn khác nhau quanh cùng một gốc
    # (puttamaṁsa / puttamaṁsūpama), nên chấp nhận khi phần đầu trùng đủ nhiều.
    shorter = min(len(left), len(right))
    common = 0
    for a, b in zip(left, right):
        if a != b:
            break
        common += 1
    return common >= max(5, int(shorter * 0.7))


def _one_title_agrees(bilara_title: str, db_title: str) -> bool:
    return any(
        _stems_agree(left, right)
        for left in _title_stems(bilara_title)
        for right in _title_stems(db_title)
    )


def bilara_header_titles(root: dict, uid: str) -> list[str]:
    """Các dòng tiêu đề bilara đặt trước thân bài (khoá "<uid>:0.x").

    Vị trí của tên bài kinh khác nhau tuỳ bộ: Trung Bộ để ở :0.2, còn Tương Ưng
    để tên phẩm ở :0.2 và tên bài ở :0.3. Nên lấy hết rồi so lần lượt.
    """
    return [str(value).strip() for key, value in root.items() if key.startswith(f"{uid}:0.") and str(value).strip()]


def titles_agree(bilara_titles: list[str], db_title: str) -> bool:
    """Khớp nếu BẤT KỲ dòng tiêu đề nào của bilara trùng tiêu đề trong DB.

    Hai bên đặt tên lệch nhau chút ít (bilara 'Satipaṭṭhānasutta' vs CST
    '10. Mahāsatipaṭṭhānasuttaṃ'), nên chỉ cần một bên chứa bên kia là đủ.
    """
    return any(_one_title_agrees(title, db_title) for title in bilara_titles)


def _document(file_name: str) -> dict:
    document = fetch_one("select id from documents where file_name = %s", [file_name])
    if not document:
        raise RuntimeError(f"Không có tài liệu {file_name} trong DB.")
    return document


def _sutta_starts(document_id: str) -> list[tuple[int, str, int]]:
    """(sort_order, title, level) của mọi section, theo thứ tự tài liệu."""
    rows = fetch_all(
        "select title, start_sort_order, level from sections where document_id = %s order by start_sort_order, level",
        [document_id],
    )
    return [(row["start_sort_order"], str(row["title"] or "").strip(), row["level"]) for row in rows]


def _last_sort(document_id: str) -> int:
    row = fetch_one("select max(sort_order) as m from passages where document_id = %s", [document_id])
    return int(row["m"] or 0)


def _close_ranges(entries: list[dict], last_sort: int) -> None:
    """Bài kinh chạy tới ngay trước bài kế tiếp; bài cuối chạy hết tài liệu."""
    for position, entry in enumerate(entries):
        if position + 1 < len(entries) and entries[position + 1]["document_id"] == entry["document_id"]:
            entry["end"] = entries[position + 1]["start"] - 1
        else:
            entry["end"] = last_sort if entry["document_id"] == entries[-1]["document_id"] else entry["start"]


def _uid_order(uid: str) -> list[int]:
    """Thứ tự kinh điển của uid dạng dải: dhp1-20 < dhp21-32 < dhp33-43.

    Sắp theo chữ cái thì "dhp21-32" đứng trước "dhp3-43", nên phải so theo số.
    """
    return [int(number) for number in re.findall(r"\d+", uid)]


def _targets_by_section(nikaya: str, config: dict) -> list[dict]:
    """Mỗi mục trong DB ứng với một file bilara, ghép theo THỨ TỰ ĐỌC.

    Thơ kệ không có mã bài kinh để tra: bilara gom theo dải câu kệ (`dhp1-20`) hay dải
    bài (`an1.1-10`), còn CST chia theo phẩm hoặc theo bài kệ của từng vị trưởng lão.
    Chỗ neo duy nhất còn lại là thứ tự, mà thứ tự chỉ đúng khi hai bên có ĐÚNG cùng số
    mục - lệch một mục là mọi mục sau nó ghép sai mà không có triệu chứng gì. Nên lệch
    thì bỏ cả bộ, không ghép phần khớp được.

    Đây mới là lưới thứ nhất. Lưới thứ hai là `titles_agree` trong `import_nikaya`, so
    tên từng mục giữa bilara và CST; một cú trượt thứ tự vẫn bị chặn lần nữa ở đó.
    """
    suffixes = tuple(config["section_suffixes"])
    uids = sorted((uid for uid in bilara_tree() if re.match(config["uid_pattern"], uid)), key=_uid_order)

    entries: list[dict] = []
    for file_name in config["files"]:
        document = _document(file_name)
        rows: list[dict] = []
        for start, title, _level in _sutta_starts(document["id"]):
            if not re.match(r"^\(?\d", title.strip()) or not normalize_pali(title).endswith(suffixes):
                continue
            if any(row["start"] == start for row in rows):
                continue
            rows.append({"document_id": document["id"], "file_name": file_name,
                         "db_title": title, "start": start})
        _close_ranges(rows, _last_sort(document["id"]))
        entries.extend(rows)

    if len(entries) != len(uids):
        print(f"  !! bilara có {len(uids)} file, DB có {len(entries)} mục - lệch nhau,"
              " bỏ qua bộ này để tránh ghép sai")
        return []

    for uid, entry in zip(uids, entries):
        entry["uid"] = uid
    return entries


def build_targets(nikaya: str) -> list[dict]:
    config = NIKAYA_CONFIG[nikaya]
    mode = config["mode"]

    if mode == "by_section":
        return _targets_by_section(nikaya, config)

    title_rules = {
        "allow_unnumbered": bool(config.get("allow_unnumbered")),
        "extra_suffixes": tuple(config.get("extra_suffixes", ())),
    }
    targets: list[dict] = []

    if mode == "per_file":
        for file_name, group in config["file_groups"]:
            document = _document(file_name)
            entries: list[dict] = []
            for start, title, _level in _sutta_starts(document["id"]):
                if not _is_sutta_title(title, **title_rules) or any(e["start"] == start for e in entries):
                    continue
                entries.append(
                    {"uid": f"{nikaya}{group}.{len(entries) + 1}", "document_id": document["id"],
                     "file_name": file_name, "db_title": title, "start": start}
                )
            _close_ranges(entries, _last_sort(document["id"]))
            targets.extend(entries)
        return targets

    files = config["files"]
    group_suffix = config.get("group_suffix")
    group_number = 0
    index_in_group = 0
    flat_index = 0

    for file_name in files:
        document = _document(file_name)
        entries: list[dict] = []
        seen: set[int] = set()
        for start, title, _level in _sutta_starts(document["id"]):
            normalized = normalize_pali(title)
            if group_suffix and normalized.endswith(group_suffix) and re.match(r"^\(?\d", title):
                group_number += 1
                index_in_group = 0
                continue
            if not _is_sutta_title(title, **title_rules) or start in seen:
                continue
            seen.add(start)
            if group_suffix:
                index_in_group += 1
                uid = f"{nikaya}{group_number}.{index_in_group}"
            else:
                flat_index += 1
                uid = f"{nikaya}{flat_index}"
            entries.append(
                {"uid": uid, "document_id": document["id"], "file_name": file_name,
                 "db_title": title, "start": start}
            )
        _close_ranges(entries, _last_sort(document["id"]))
        targets.extend(entries)

    if group_suffix:
        expected = config.get("expect_groups")
        if expected and group_number != expected:
            print(f"  !! đếm được {group_number} nhóm, kinh điển có {expected}")
    return targets


def fetch_json(url: str, retries: int = 2) -> dict | None:
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
        except Exception:  # noqa: BLE001 - mạng chập chờn thì thử lại
            pass
    return None


# bilara xếp thư mục mỗi bộ một kiểu (sutta/mn/, sutta/sn/sn47/, sutta/kn/ud/vagga1/)
# và có bộ còn gộp nhiều bài vào một file (an1.1-10). Đoán đường dẫn theo mẫu thì trượt,
# nên lấy thẳng cây thư mục của repo về rồi tra chính xác.
TREE_API = "https://api.github.com/repos/suttacentral/bilara-data/git/trees/published?recursive=1"
_ROOT_SUFFIX = "_root-pli-ms.json"
_TREE_CACHE: dict[str, str] = {}


def bilara_tree() -> dict[str, str]:
    """uid -> phần đường dẫn dùng chung cho cả file Pali lẫn file bản dịch."""
    if _TREE_CACHE:
        return _TREE_CACHE
    data = fetch_json(TREE_API)
    if not data:
        raise RuntimeError("Không tải được cây thư mục bilara-data.")
    if data.get("truncated"):
        print("  !! cây thư mục bị cắt bớt, có thể thiếu bài")
    prefix = "root/pli/ms/"
    for item in data.get("tree", []):
        path = str(item.get("path") or "")
        if not path.startswith(prefix + "sutta/") or not path.endswith(_ROOT_SUFFIX):
            continue
        uid = path.rsplit("/", 1)[-1][: -len(_ROOT_SUFFIX)]
        _TREE_CACHE[uid] = path[len(prefix) : -len(_ROOT_SUFFIX)]
    return _TREE_CACHE


def sutta_files(nikaya: str, uid: str) -> tuple[dict | None, dict | None]:
    del nikaya
    stem = bilara_tree().get(uid)
    if not stem:
        return None, None
    root = fetch_json(f"{RAW_BASE}/root/pli/ms/{stem}{_ROOT_SUFFIX}")
    if root is None:
        return None, None
    translation = fetch_json(f"{RAW_BASE}/translation/en/sujato/{stem}_translation-en-sujato.json")
    return root, translation


# bilara nhúng thẻ định dạng (<em>, <j>, <i>) trong văn bản dịch. Giao diện hiển thị
# bản dịch dưới dạng chữ thuần nên phải bóc thẻ ra, không thì người đọc thấy cả "<em>".
_HTML_TAG = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    return re.sub(r"[ \t]+", " ", _HTML_TAG.sub("", str(text or ""))).strip()


# Đoạn CST bị rút gọn: chỉ ghi mấy chữ đầu rồi bỏ lửng bằng "…pe…" hoặc "…".
# Ví dụ trong Mahāpadānasutta, mỗi tướng của bậc Đại Nhân chỉ còn một mẩu 32-40 ký tự
# ("‘Ayañhi, deva, kumāro dīghaṅgulī…"), trong khi bản bilara chép đủ cả câu.
_ELIDED = re.compile(r"(?:…\s*pe\s*…|…|\.\.\.)\s*[’'‘\"]*\s*$")
# Số từ đầu tối thiểu phải trùng thì mới coi là cùng một câu. Ít hơn thì "Ayañhi deva
# kumāro" - mở đầu chung của cả 32 tướng - sẽ khớp bừa vào tướng bất kỳ.
ELIDED_MIN_WORDS = 4


def _elided_prefix(raw_text: str) -> str:
    """Phần chữ thật của một đoạn bị rút gọn, đã chuẩn hoá. Rỗng nếu đoạn không bị rút gọn.

    Phải dò trên văn bản GỐC: `normalize_pali` xoá sạch dấu "…" nên nhìn vào bản chuẩn hoá
    thì không còn phân biệt được đoạn rút gọn với đoạn viết đủ.
    """
    if not _ELIDED.search(str(raw_text or "").strip()):
        return ""
    body = normalize_pali(_ELIDED.sub("", str(raw_text)).strip())
    # Bỏ đuôi "pe" mà normalize_pali để lại từ "…pe…".
    body = re.sub(r"\bpe$", "", body).strip()
    return body if len(body.split()) >= ELIDED_MIN_WORDS else ""


class _MaxTree:
    """Cây Fenwick giữ giá trị lớn nhất trên tiền tố, kèm vị trí đạt giá trị đó."""

    def __init__(self, size: int) -> None:
        self.size = size
        self.best = [(0.0, -1)] * (size + 1)

    def update(self, index: int, value: float, tag: int) -> None:
        index += 1
        while index <= self.size:
            if value > self.best[index][0]:
                self.best[index] = (value, tag)
            index += index & -index

    def query(self, index: int) -> tuple[float, int]:
        """Giá trị lớn nhất trong khoảng [0, index]."""
        index += 1
        found = (0.0, -1)
        while index > 0:
            if self.best[index][0] > found[0]:
                found = self.best[index]
            index -= index & -index
        return found


def align_globally(
    candidates: list[list[tuple[int, float]]], allow_same_position: bool = False
) -> dict[int, int]:
    """Chọn cách ghép cho TỔNG độ khớp cao nhất, với ràng buộc không đảo thứ tự.

    Đây là chỗ khác hẳn ba cách đã thất bại trước đó. Cả ba đều quyết định từng cặp một
    rồi đi tiếp, nên một lần chọn sai kéo hỏng cả dây phía sau. Ở đây cả tập được xét
    cùng lúc: một công thức lặp ba lần trong tập, xét riêng thì không phân biệt nổi,
    nhưng xét cả dây thì HÀNG XÓM GHIM NÓ LẠI - cặp trước đã ở đoạn 200, cặp sau ở đoạn
    205, vậy cặp giữa chỉ có thể nằm trong 201-204.

    `candidates[i]` là danh sách (vị trí đoạn, độ giống) của cặp thứ i, cặp xếp theo thứ
    tự đọc. Trả về {chỉ số cặp: vị trí đoạn}.

    Cài bằng quy hoạch động kiểu dãy con tăng có trọng số, dùng cây Fenwick nên chạy
    O(số ứng viên × log số đoạn) thay vì bảng vuông - Bổn Sanh có 20.000 đoạn, bảng vuông
    thì không kham nổi.
    """
    total_passages = max((position for row in candidates for position, _ in row), default=-1) + 1
    if total_passages <= 0:
        return {}

    tree = _MaxTree(total_passages)
    # parent[(i, j)] = ô đứng trước trong chuỗi tối ưu, để lần ngược lại lúc cuối.
    parent: dict[tuple[int, int], tuple[int, int] | None] = {}
    score: dict[tuple[int, int], float] = {}
    best_end: tuple[float, tuple[int, int] | None] = (0.0, None)

    for pair_index, row in enumerate(candidates):
        # Tính hết cho cặp này TRƯỚC khi nạp vào cây, để một cặp không tự nối vào chính nó.
        pending = []
        for position, weight in row:
            # `allow_same_position`: cho phép nhiều mục cùng gắn vào MỘT đoạn. Bản Sujato
            # cần điều này - một đoạn CST thường chứa vài ba segment của bilara; bắt mỗi
            # đoạn chỉ nhận một segment thì mất 60% nội dung. Bản Indacanda thì không, một
            # mục trong sách đã trùm sẵn cả đoạn.
            limit = position if allow_same_position else position - 1
            previous_value, previous_tag = tree.query(limit) if limit >= 0 else (0.0, -1)
            value = previous_value + weight
            pending.append((position, value, previous_tag))
            score[(pair_index, position)] = value
            parent[(pair_index, position)] = previous_tag if previous_tag != -1 else None
            if value > best_end[0]:
                best_end = (value, (pair_index, position))
        for position, value, _ in pending:
            # tag gói (cặp, đoạn) vào một số nguyên để cây chỉ phải giữ một con số.
            tree.update(position, value, pair_index * total_passages + position)

    # Lần ngược chuỗi tối ưu.
    chosen: dict[int, int] = {}
    node = best_end[1]
    while node is not None:
        pair_index, position = node
        chosen[pair_index] = position
        tag = parent.get(node)
        node = (tag // total_passages, tag % total_passages) if isinstance(tag, int) else None
    return chosen


def align(root: dict, translation: dict, passages: list[dict]) -> tuple[dict[int, list[str]], int, int]:
    """Gán từng segment vào dòng passage chứa nó, quét một chiều từ đầu bài.

    Có HAI cách khớp, vì hai bên rút gọn khác nhau:

    1. Segment nằm trọn trong đoạn - trường hợp thường gặp.
    2. Đoạn CST bị rút gọn bằng "…pe…" còn bilara chép đủ: khi ấy cách 1 luôn trượt vì
       đoạn CST quá ngắn để chứa cả câu. Đảo lại, lấy phần chữ thật của đoạn CST xem có
       phải là phần MỞ ĐẦU của segment không. Đo trên mục Dvattiṃsamahāpurisalakkhaṇā
       (32 tướng của bậc Đại Nhân, Mahāpadānasutta): chỉ 3/35 đoạn khớp được bằng cách 1,
       vì 32 đoạn còn lại chỉ dài 32-90 ký tự.
    """
    segments = [
        (key, normalize_pali(value))
        for key, value in root.items()
        if normalize_pali(value) and not re.match(r"^[a-z]+[\d.]*:0\.", key)
    ]
    prefixes = [_elided_prefix(row.get("pali_text", "")) for row in passages]

    # Ứng viên của mỗi segment, rồi để `align_globally` chọn bộ ăn khớp nhất.
    # ĐÃ THỬ con trỏ tiến-một-chiều (chỉ lấy đoạn đầu tiên chứa segment): một segment
    # khớp nhầm vào công thức lặp nằm xa phía sau là con trỏ vọt lên và chặn sạch phần
    # còn lại. Đo trên Mahāpadānasutta: tới lượt segment `dn14:1.32.10` thì con trỏ đã ở
    # sort_order 86, trong khi đoạn đúng của nó nằm ở 57 - cả mục "32 tướng của bậc Đại
    # Nhân" mất trắng, đúng chỗ khách báo thiếu.
    candidates: list[list[tuple[int, float]]] = []
    for _key, normalized in segments:
        row = []
        for offset, passage in enumerate(passages):
            if normalized in passage["normalized_pali"] or (
                prefixes[offset] and normalized.startswith(prefixes[offset])
            ):
                row.append((offset, min(3.0, len(normalized) / 60)))
        candidates.append(row)

    chosen = align_globally(candidates, allow_same_position=True)
    assignment: dict[int, list[str]] = {}
    for index in sorted(chosen):
        key = segments[index][0]
        if translation.get(key, "").strip():
            assignment.setdefault(chosen[index], []).append(key)

    return assignment, len(chosen), len(segments)


def import_nikaya(nikaya: str, limit: int, dry_run: bool, verbose: bool) -> dict:
    targets = build_targets(nikaya)
    config = NIKAYA_CONFIG[nikaya]
    expect = config.get("expect")
    print(f"=== {nikaya.upper()}: DB có {len(targets)} bài kinh" + (f" (kinh điển: {expect})" if expect else "") + " ===")
    if expect and len(targets) != expect:
        print("  !! lệch số bài kinh, bỏ qua bộ này để tránh ghép sai\n")
        return {"skipped": True}

    print(f"  bilara có {len(bilara_tree())} bài kinh")
    if limit:
        targets = targets[:limit]

    stats = {"segments": 0, "matched": 0, "written": 0, "suttas": 0,
             "missing": [], "title_mismatch": [], "skipped": False}

    for target in targets:
        root, translation = sutta_files(nikaya, target["uid"])
        if not root or not translation:
            stats["missing"].append(target["uid"])
            continue

        headers = bilara_header_titles(root, target["uid"])
        if headers and not titles_agree(headers, target["db_title"]):
            # Lệch tên nghĩa là số thứ tự đã trượt -> ghi vào sẽ sai hàng loạt.
            stats["title_mismatch"].append(f"{target['uid']} ({' / '.join(headers[1:3])} != {target['db_title']})")
            continue

        passages = fetch_all(
            """
            select id, sort_order, normalized_pali, pali_text
            from passages
            where document_id = %s and sort_order between %s and %s
            order by sort_order
            """,
            [target["document_id"], target["start"], target["end"]],
        )
        assignment, matched, segment_count = align(root, translation, passages)
        stats["segments"] += segment_count
        stats["matched"] += matched
        stats["suttas"] += 1

        written = 0
        for offset, keys in assignment.items():
            text = "\n".join(_clean(translation[key]) for key in keys if _clean(translation.get(key, "")))
            if not text:
                continue
            written += 1
            if dry_run:
                continue
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
                [passages[offset]["id"], SOURCE_ID, LANGUAGE, text, target["uid"], keys],
            )
        stats["written"] += written

        if verbose:
            rate = 100 * matched // max(1, segment_count)
            print(f"     {target['uid']:<10} {target['db_title'][:34]:<36} {matched:>4}/{segment_count:<4} ({rate:>3}%) -> {written}")

    rate = 100 * stats["matched"] // max(1, stats["segments"])
    print(f"  {stats['suttas']} bài · {stats['matched']}/{stats['segments']} segment khớp ({rate}%) · ghi {stats['written']} đoạn")
    if stats["missing"]:
        print(f"  không có trên bilara: {len(stats['missing'])} bài ({', '.join(stats['missing'][:6])}{' ...' if len(stats['missing']) > 6 else ''})")
    if stats["title_mismatch"]:
        print(f"  BỎ QUA vì lệch tên: {len(stats['title_mismatch'])} bài")
        for item in stats["title_mismatch"][:6]:
            print(f"      {item[:110]}")

    if not dry_run and stats["suttas"]:
        execute(
            """
            insert into human_translation_imports
              (source, language, scope, segments_total, segments_matched, passages_written, notes)
            values (%s, %s, %s, %s, %s, %s, %s)
            """,
            [SOURCE_ID, LANGUAGE, nikaya, stats["segments"], stats["matched"], stats["written"],
             f"thiếu {len(stats['missing'])}, lệch tên {len(stats['title_mismatch'])}"],
        )
    print()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Nạp bản dịch tiếng Anh của Bhikkhu Sujato.")
    # Không dùng choices= vì argparse sẽ bắt lỗi cả danh sách rỗng khi chạy với --all.
    parser.add_argument("nikayas", nargs="*", help=f"Bộ kinh cần nạp: {', '.join(sorted(NIKAYA_CONFIG))}")
    parser.add_argument("--all", action="store_true", help="Nạp mọi bộ đã hỗ trợ")
    parser.add_argument("--limit", type=int, default=0, help="Chỉ nạp N bài đầu (để thử)")
    parser.add_argument("--dry-run", action="store_true", help="Chỉ đo độ khớp, không ghi DB")
    parser.add_argument("--verbose", action="store_true", help="In từng bài kinh")
    args = parser.parse_args()

    nikayas = sorted(NIKAYA_CONFIG) if args.all else args.nikayas
    if not nikayas:
        parser.error("cần chỉ định bộ kinh, hoặc dùng --all")
    unknown = [item for item in nikayas if item not in NIKAYA_CONFIG]
    if unknown:
        parser.error(f"không biết bộ kinh: {', '.join(unknown)}")

    if not args.dry_run:
        migration = Path(__file__).resolve().parents[1] / "db" / "migrations" / "002_human_translations.sql"
        execute(migration.read_text(encoding="utf-8"))
        print(f"đã áp dụng {migration.name}\n")

    grand = {"segments": 0, "matched": 0, "written": 0, "suttas": 0}
    for nikaya in nikayas:
        stats = import_nikaya(nikaya, args.limit, args.dry_run, args.verbose)
        if stats.get("skipped"):
            continue
        for key in grand:
            grand[key] += stats[key]

    rate = 100 * grand["matched"] // max(1, grand["segments"])
    print(f"TỔNG: {grand['suttas']} bài kinh · {grand['matched']}/{grand['segments']} segment ({rate}%) · {grand['written']} đoạn được ghi")


if __name__ == "__main__":
    main()
