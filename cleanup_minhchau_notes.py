"""Dọn dẹp bản dịch `minh_chau`: cắt footer SuttaCentral + ghi chú chéo, GIỮ dòng tiêu đề.

CHẠY AN TOÀN (mặc định, KHÔNG đổi dữ liệu):
    python cleanup_minhchau_notes.py            # dry-run: in số lượng + ví dụ
    python cleanup_minhchau_notes.py --verbose  # dry-run: in toàn bộ dòng thay đổi

ÁP DỤNG THẬT (bắt buộc cờ --apply, tự tạo bảng backup trước):
    python cleanup_minhchau_notes.py --apply

QUY TẮC XỬ LÝ (chỉ nguồn `minh_chau`, cột translated_text):
1. Cắt footer SuttaCentral: bỏ mọi dòng từ dòng footer đầu tiên đến hết
   (Bộ kinh đã/nầy được Hòa thượng..., Hòa thượng Thích Minh Châu dịch Việt,
   [Bản dịch Anh ngữ], Chân thành cám ơn anh HDC, Bình Anson hiệu đính,
   Hiệu đính:, Prepared for SuttaCentral, Người dịch:, dòng ngày).
2. Cắt dòng ghi chú chéo: dòng NẰM TRỌN trong ngoặc đơn (hoặc giữa …) chứa từ
   khóa biên tập (kinh trên/kinh trước/như trên/giống như/chỉ khác/chỉ thế/xem
   kinh...) — ví dụ "(Như kinh trước, chỉ thế vào: Loại Nāga hóa sanh)."
3. Bỏ dòng tham chiếu ấn bản thuần túy (S.iv,234 / Vi-n 1-2 / I-VI.).
4. GIỮ NGUYÊN mọi dòng tiêu đề (Chương…, Phẩm…, số.tên bài, Kinh …) và nội dung thật.
5. Rows có ngoặc đơn là NỘI DUNG THẬT (vd "(Thế Tôn):", "(Metta Sutta)") không
   khớp điều kiện 2 nên không bị chạm — cơ chế tự bảo vệ hai chiều.

BACKUP: trước khi áp dụng, tạo bảng `human_translations_backup_<ngay>` chứa toàn
bộ dòng `minh_chau`; nếu bảng đã tồn tại thì TỪ CHỐI chạy (tránh ghi đè backup cũ).
Khôi phục nếu cần:
    insert into human_translations (…các cột…)
    select … from human_translations_backup_<ngay>;
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date

sys.path.insert(0, ".")
from app.db import execute, fetch_all, get_conn

SOURCE = "minh_chau"

# ── 1. Dòng footer SuttaCentral ────────────────────────────────────────────
FOOTER_START = re.compile(
    r"^(?:Bộ kinh (?:đã|nầy) được Hòa thượng|"
    r"Hòa thượng Thích Minh Châu dịch Việt|"
    r"Chân thành cám ơn anh HDC|"
    r"Prepared for SuttaCentral|"
    r"Bình Anson hiệu đính|"
    r"Hiệu đính:|"
    r"\[Bản dịch Anh ngữ\]|"
    r"Người dịch:|"
    r"These texts have been used|"
    r"\d{1,2}–\d{1,2}–\d{4})",
    re.IGNORECASE,
)

# ── 2. Dòng ghi chú chéo: cả dòng nằm trong ngoặc đơn + từ khóa biên tập ────
NOTE_RE = re.compile(
    r"^\(.*(?:kinh trên|kinh trước|như trên|giống như|chỉ khác|chỉ thế|"
    r"thay thế|thay cho|chỉ đổi|chỉ thêm|chỉ có|chỉ bắt đầu|Tới đây|"
    r"xem kinh|Xem kinh|giống với kinh|đưa đến).*\)\.?\s*$",
    re.IGNORECASE,
)
ELLIPSIS_NOTE_RE = re.compile(r"^….*(?:kinh|giống như|như trên|trên).*…\s*$")

# ── 3. Dòng tham chiếu ấn bản thuần túy ────────────────────────────────────
PAGE_REF = re.compile(
    r"^(?:[A-Za-z]{1,3}\.\s*[ivxlcdm\d,–-]+\s*\.?|Vi-n\s*[\d–-]+\.?\s*\.?)$",
    re.IGNORECASE,
)


def clean(text: str) -> str:
    """Trả về text đã cắt footer + ghi chú chéo + page-ref; giữ tiêu đề và nội dung."""
    lines = [ln.strip() for ln in (text or "").split("\n") if ln.strip()]
    kept: list[str] = []
    for ln in lines:
        if FOOTER_START.match(ln):
            # Footer nằm cuối; bỏ nó và mọi dòng sau.
            break
        if NOTE_RE.match(ln):
            continue
        if ELLIPSIS_NOTE_RE.match(ln):
            continue
        if PAGE_REF.match(ln):
            continue
        kept.append(ln)
    return "\n".join(kept)


def main() -> None:
    parser = argparse.ArgumentParser(description="Dọn dẹp bản dịch minh_chau (dry-run mặc định).")
    parser.add_argument("--apply", action="store_true", help="Áp dụng thật (tự tạo bảng backup trước)")
    parser.add_argument("--verbose", action="store_true", help="In toàn bộ dòng thay đổi")
    args = parser.parse_args()

    rows = fetch_all(
        "select id, source_ref, translated_text from human_translations where source = %s",
        [SOURCE],
    )
    print(f"minh_chau: {len(rows)} dòng")

    changed = []
    for r in rows:
        cleaned = clean(r["translated_text"])
        if cleaned != r["translated_text"]:
            changed.append((r, cleaned))

    print(f"\nSẽ thay đổi: {len(changed)} dòng")
    removed_chars = sum(len(r["translated_text"]) - len(c) for r, c in changed)
    print(f"Tổng ký tự cắt: {removed_chars:,}")

    # Phân loại: còn nội dung thật vs chỉ còn tiêu đề
    TITLE_LINE = re.compile(
        r"^(?:Aṅguttara Nikāya|"
        r"Chương \d+:.*|"
        r"(?:[IVXLCDM]+[.:]\s*)?Phẩm .*|"
        r"Phần .*|"
        r"Tương Ưng .*|"
        r"Kinh .*|"
        r"\d+\.\d+\.\s.*|\d+–\d+\.\s.*|\d+\.\s.*)"
    )
    title_only = 0
    body_kept = 0
    for r, c in changed:
        lines = [ln for ln in c.split("\n") if ln.strip()]
        if lines and all(TITLE_LINE.match(ln) for ln in lines):
            title_only += 1
        else:
            body_kept += 1
    print(f"  - dòng còn lại CHỈ tiêu đề: {title_only}")
    print(f"  - dòng còn giữ nội dung thật: {body_kept}")

    if args.verbose:
        print("\n=== CHI TIẾT THAY ĐỔI ===")
        for r, c in changed[:80]:
            print(f"\n--- {r['source_ref']}")
            print(f"  TRƯỚC: {r['translated_text'][:150]!r}")
            print(f"  SAU  : {c[:150]!r}")
        if len(changed) > 80:
            print(f"\n... còn {len(changed) - 80} dòng nữa")

    if not args.apply:
        print("\nDRY-RUN: chưa thay đổi gì. Chạy lại với --apply để áp dụng.")
        return

    # ── Áp dụng thật ────────────────────────────────────────────────────────
    today = date.today().isoformat().replace("-", "")
    backup = f"human_translations_backup_{today}"
    existing = fetch_all(
        "select to_regclass(%s) as t", [backup]
    )[0]["t"]
    if existing:
        print(f"\n!! Bảng backup {backup} đã tồn tại — dừng để không ghi đè backup cũ.")
        print("   Muốn chạy tiếp: đổi tên/backup bảng cũ trước, hoặc chờ ngày khác.")
        sys.exit(1)

    print(f"\nTạo bảng backup: {backup}")
    execute(f"create table {backup} as select * from human_translations where source = %s", [SOURCE])

    print(f"Cập nhật {len(changed)} dòng...")
    updated = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            for r, c in changed:
                cur.execute(
                    "update human_translations set translated_text = %s, updated_at = now() where id = %s",
                    [c, r["id"]],
                )
                updated += 1
    print(f"Đã cập nhật: {updated} dòng")
    print(f"Backup tại: {backup}  —  khôi phục: insert into human_translations select * from {backup};")


if __name__ == "__main__":
    main()
