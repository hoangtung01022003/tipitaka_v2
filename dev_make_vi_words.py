"""Sinh lại `app/data/vi_words.txt` - bộ từ vựng dùng để nối chữ bị PDF tách đôi.

`import_indacanda.mend_spacing` nối hai mảnh khi và chỉ khi ghép lại ra một từ có trong
file này, nên file là DỮ LIỆU CỦA IMPORTER chứ không phải kết quả phụ. Để thành file thay
vì đếm lại từ DB mỗi lần chạy: nạp lại phải cho ra đúng kết quả cũ, mà đếm từ DB thì kết
quả đổi theo việc lúc ấy đã nạp được bao nhiêu nguồn.

Chỉ chạy lại khi thêm nguồn tiếng Việt mới, và phải xem `git diff` trước khi commit -
từ mới lọt vào đây là đổi hành vi của importer.

Chạy:
    .venv\\Scripts\\python.exe dev_make_vi_words.py
"""

import collections
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from app.db import fetch_all

WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
# Dưới ngưỡng này thì rất có thể chính nó là mảnh vỡ hoặc lỗi bóc chữ, đưa vào từ vựng
# là dạy importer nối bậy. Đo được: ngưỡng 5 đạt 10/10 cả hai chiều.
MIN_FREQ = 5
OUT = Path(__file__).resolve().parent / "app" / "data" / "vi_words.txt"


def main() -> None:
    rows = fetch_all("select translated_text from human_translations where language = 'vi'")
    freq: collections.Counter = collections.Counter()
    for row in rows:
        freq.update(word.lower() for word in WORD.findall(str(row["translated_text"] or "")))

    keep = sorted(word for word, count in freq.items() if count >= MIN_FREQ and len(word) >= 2)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(keep), encoding="utf-8")

    print(f"{len(rows):,} dòng · {sum(freq.values()):,} lượt từ · {len(freq):,} dạng")
    print(f"giữ {len(keep):,} dạng (tần suất >= {MIN_FREQ}) -> {OUT.name}")


if __name__ == "__main__":
    main()
