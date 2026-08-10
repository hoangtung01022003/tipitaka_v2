"""Bản dịch Tiểu Bộ của HT. Thích Minh Châu, lấy từ budsas.org.

VÌ SAO PHẢI CÓ NGUỒN NÀY
------------------------
`import_minhchau.py` lấy bản Minh Châu từ SuttaCentral (`html_text/vi/pli/sutta/`),
nhưng thư mục đó CHỈ có tiếng Việt cho Trường / Trung / Tương Ưng / Tăng Chi, cộng
đúng 3 bài Kinh Tập trong `kn/snp/chau`. Phật Thuyết Như Vậy và Phật Tự Thuyết
không có một dòng nào - kiểm bằng cả cây thư mục sc-data lẫn API suttaplex, cả hai
đều trả về rỗng. Nên `import_minhchau.py iti snp ud` ra 3 bài là ĐÚNG với nguồn cũ,
không phải lỗi sinh mã bài kinh.

budsas.org (Bình Anson) đăng trọn bộ Tiểu Bộ bản Minh Châu dưới dạng HTML thuần.

CÁCH CẮT BÀI
------------
Mỗi bài kinh mở đầu bằng một mốc số La Mã ở đầu dòng:

    (I) (Ud 1)                      - Phật Tự Thuyết
    (XLIX) (Duk. II, 12) (It. 43)   - Phật Thuyết Như Vậy
    (I) Kinh Rắn (Sn 1)             - Kinh Tập

Số La Mã ĐẶT LẠI TỪ ĐẦU ở mỗi phẩm với Phật Tự Thuyết và Kinh Tập, còn Phật Thuyết
Như Vậy đánh liền một mạch I..CXII. Nên phẩm được cắt theo mốc `(I)`, không suy từ
tên trang - trang `tb13-ptt1.htm` chứa 3 phẩm, `tb13-ptt2.htm` chứa 2.

BẢN THÂN SỐ LA MÃ IN SAI Ở VÀI CHỖ nên chỉ dùng để nhận đầu phẩm, không dùng làm
thứ tự bài: Phật Tự Thuyết phẩm 2 in `(VIII)` hai lần (mất `(VII)`), Kinh Tập phẩm
4 in `(V)` hai lần (mất `(IV)`). Thứ tự bài lấy theo thứ tự xuất hiện, và số in
được giữ lại ở khoá `printed` để đối chiếu khi cần.

`fetch_texts` chỉ trả về vị trí (phẩm thứ mấy, bài thứ mấy); việc gán thành uid và
đối chiếu với DB là của `import_minhchau.py`.
"""

from __future__ import annotations

import html as html_lib
import re
import time
import urllib.request

BASE = "https://www.budsas.org/uni/u-kinh-tieubo1/"

# Thứ tự trang là thứ tự đọc, phẩm nối tiếp qua trang nên không được xáo.
PAGES: dict[str, list[str]] = {
    "ud": ["tb13-ptt1.htm", "tb13-ptt2.htm", "tb13-ptt3.htm"],
    "iti": ["tb14-ptnv1.htm", "tb14-ptnv2.htm", "tb14-ptnv3.htm"],
    "snp": ["tb15-kt1.htm", "tb15-kt2.htm", "tb15-kt3.htm", "tb15-kt4.htm", "tb15-kt5.htm"],
}

# Máy chủ trả 429 khi gọi dồn, nên giữ khoảng cách giữa hai lần tải.
_MIN_INTERVAL = 3.0
_last_fetch = 0.0
_CACHE: dict[str, str] = {}

_MARKER = re.compile(r"^\(\s*([IVXLCDM]+)\s*\)\s*(.*)$")
# Mốc ấn bản trong tiêu đề: "(Sn 190)", "(It. 43)", "(Ek I, 1)", "(Duk. II, 12)".
# Bỏ đi, nhưng giữ phần chú tên Pali như "Kinh Từ Bi (Metta Sutta)".
_EDITION_REF = re.compile(r"\(\s*(?:Sn|It|Ud|Ek|Duk|Tik|Cat)[^)]*\)", re.IGNORECASE)
_NOISE = re.compile(r"^(?:\[|-ooOoo-|Revised:|BuddhaSasana|This document|Xem thêm)")
_SUB_HEADING = re.compile(r"^(?:Chương|Phẩm)\b")
_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def _roman(text: str) -> int:
    total = 0
    highest = 0
    for char in reversed(text.upper()):
        value = _ROMAN_VALUES.get(char)
        if value is None:
            return 0
        total = total - value if value < highest else total + value
        highest = max(highest, value)
    return total


def _fetch(name: str) -> str:
    global _last_fetch
    if name in _CACHE:
        return _CACHE[name]
    last_error: Exception | None = None
    for attempt in range(5):
        wait = _MIN_INTERVAL - (time.monotonic() - _last_fetch)
        if wait > 0:
            time.sleep(wait)
        try:
            request = urllib.request.Request(BASE + name, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read().decode("utf-8", "replace")
            _last_fetch = time.monotonic()
            _CACHE[name] = raw
            return raw
        except Exception as error:  # noqa: BLE001 - 429 và lỗi mạng đều chỉ cần chờ rồi thử lại
            last_error = error
            _last_fetch = time.monotonic()
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Không tải được {BASE}{name}: {last_error}")


def _lines(raw: str) -> list[str]:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    text = re.sub(r"(?i)</p\s*>|<br\s*/?>|</h[1-6]>|</div>|</tr>|</li>", "\n", text)
    text = html_lib.unescape(re.sub(r"<[^>]+>", "", text))
    out = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t\xa0]+", " ", line).strip()
        if not line or _NOISE.match(line):
            continue
        # Tiêu đề phẩm nằm xen giữa các bài; bỏ để nó không dính vào cuối bài trước.
        if _SUB_HEADING.match(line) and len(line) <= 90:
            continue
        out.append(line)
    return out


def _clean_heading(rest: str) -> str:
    return re.sub(r"\s+", " ", _EDITION_REF.sub(" ", rest)).strip(" .-")


def fetch_texts(collection: str, *, grouped: bool) -> list[dict]:
    """Các bài kinh của một bộ, theo đúng thứ tự đọc.

    Trả về [{"group": int, "index": int, "printed": int, "heading": str, "text": str}].
    `index` là thứ tự xuất hiện trong phẩm, `printed` là số La Mã in trên trang (có
    thể sai). Với bộ đánh số liền mạch (`grouped=False`) thì `group` luôn là 1.
    """
    if collection not in PAGES:
        raise KeyError(f"budsas.org: chưa cấu hình bộ {collection}")

    suttas: list[dict] = []
    current: dict | None = None
    group = 0
    index = 0

    for name in PAGES[collection]:
        for line in _lines(_fetch(name)):
            match = _MARKER.match(line)
            number = _roman(match.group(1)) if match else 0
            if number:
                if group == 0 or (grouped and number == 1):
                    group += 1
                    index = 0
                index += 1
                current = {"group": group, "index": index, "printed": number,
                           "heading": _clean_heading(match.group(2)), "body": []}
                suttas.append(current)
                continue
            if current is not None:
                current["body"].append(line)

    for sutta in suttas:
        body = sutta.pop("body")
        sutta["text"] = "\n".join(([sutta["heading"]] if sutta["heading"] else []) + body)
    return suttas
