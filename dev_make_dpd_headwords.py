"""Sinh `app/data/dpd_headwords.txt` - danh sách từ gốc dùng cho ô gợi ý ở trang tra cứu.

Vì sao phải có file này: dpdict.net KHÔNG có endpoint gợi ý (ô tìm của họ để
`autocomplete="off"`), nên gợi ý phải chạy tại chỗ. Gọi sang máy chủ của họ mỗi lần gõ một
chữ vừa chậm (~1-2 giây/lần) vừa là lạm dụng dịch vụ miễn phí của người ta.

Nguồn: bản xuất `dpd-txt.zip` của Digital Pāḷi Dictionary (CC BY-NC-SA), lấy ở release mới
nhất trên GitHub. File tải về ~4 MB, giải nén ra `dpd.txt` ~21 MB; ta chỉ giữ lại dòng tiêu
đề mục từ nên kết quả còn khoảng 1 MB.

Chạy lại khi DPD ra bản mới:

    .venv\\Scripts\\python.exe dev_make_dpd_headwords.py

Đây là DỮ LIỆU CỦA IMPORTER, giống `dev_make_vi_words.py` - xem `git diff` trước khi commit,
vì thêm/bớt từ ở đây là đổi hành vi của ô gợi ý.
"""

from __future__ import annotations

import io
import json
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

# Console Windows mặc định cp1252, in tiếng Việt hoặc chữ Pāḷi là vỡ ngay. Cùng lý do như
# `dev_make_vi_words.py`.
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

RELEASE_API = "https://api.github.com/repos/digitalpalidictionary/dpd-db/releases/latest"
ASSET_NAME = "dpd-txt.zip"
OUT_PATH = Path(__file__).parent / "app" / "data" / "dpd_headwords.txt"

# Dòng tiêu đề mục từ có dạng `akakkasa, adj. smooth; tender...` hoặc `a 1.1, letter. ...`.
# Phần sau dấu phẩy đầu tiên là từ loại, ta không cần - chỉ lấy chính từ.
ENTRY_LINE = re.compile(r"(?m)^(\S[^,\n]*), \S+\.[ \n]")
# `miga 1`, `akaṅkha 2.1` -> `miga`, `akaṅkha`. DPD đánh số để phân biệt các nghĩa, nhưng
# tra `miga` vẫn ra đủ `miga 1` và `miga 2`, nên gợi ý dạng không số là đủ và gọn hơn.
ENTRY_NUMBER = re.compile(r"\s+\d+(\.\d+)?$")
# Ngữ căn không có mục từ riêng trong dpd.txt, chỉ xuất hiện ở trường `Root:` của các mục
# dẫn xuất. Ví dụ trong yêu cầu của khách (`√mid`) là một truy vấn ngữ căn nên phải gom.
ROOT_FIELD = re.compile(r"(?m)^\s+Root: (√\S+)")


def download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "tipitaka-dpd-headwords"})
    with urllib.request.urlopen(request, timeout=300) as response:
        return response.read()


def asset_url() -> tuple[str, str]:
    release = json.loads(download(RELEASE_API))
    for asset in release["assets"]:
        if asset["name"] == ASSET_NAME:
            return asset["browser_download_url"], release["tag_name"]
    raise SystemExit(f"Không thấy {ASSET_NAME} trong release {release.get('tag_name')}")


def main() -> int:
    url, tag = asset_url()
    print(f"Bản DPD: {tag}")
    print(f"Đang tải {url} ...")
    archive = zipfile.ZipFile(io.BytesIO(download(url)))
    text = archive.read("dpd.txt").decode("utf-8")

    words: set[str] = set()
    for match in ENTRY_LINE.finditer(text):
        word = ENTRY_NUMBER.sub("", match.group(1)).strip()
        if word:
            words.add(word)
    entries = len(words)

    roots = {root.strip() for root in ROOT_FIELD.findall(text)}
    words |= roots

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text("\n".join(sorted(words)) + "\n", encoding="utf-8")

    size_mb = OUT_PATH.stat().st_size / 1048576
    print(f"Đã ghi {OUT_PATH.relative_to(Path(__file__).parent)}")
    print(f"  {entries} từ gốc + {len(roots)} ngữ căn = {len(words)} mục, {size_mb:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
