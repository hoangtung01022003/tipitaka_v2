"""Sửa các dòng `indacanda` có nhãn phẩm nhúng KHÔNG khớp đoạn đang gắn.

Bối cảnh
--------
`dev_indacanda_label_audit.py` tìm được 3 dòng ở Pháp Cú (`s0502m.mul.xml`) mà nội dung
là câu MỞ ĐẦU của một phẩm, nhưng lại đang gắn vào một `passage_id` ở gần CUỐI phẩm
TRƯỚC đó - lỗi khớp ranh giới phẩm lúc `import_indacanda.py` nhập liệu ban đầu:

    câu 97  (7. Arahantavaggo)  mang nhãn "PHẨM MỘT NGÀN [100]"  (8. Sahassavaggo)
    câu 193 (14. Buddhavaggo)   mang nhãn "PHẨM AN LẠC [197]"    (15. Sukhavaggo)
    câu 358 (24. Taṇhāvaggo)    mang nhãn "PHẨM TỲ KHƯU [360]"   (25. Bhikkhuvaggo)

Cả ba câu ĐÍCH (100, 197, 360) đều CHƯA có bản dịch nào (đã tra tay xác nhận) - nên đây
không phải ghi đè, mà là dọn đúng chỗ cho dữ liệu đã có sẵn nhưng lạc chỗ.

Sửa bằng cách nào
------------------
Không XOÁ rồi CHÈN LẠI - chỉ UPDATE `passage_id` của đúng 3 dòng này sang `passage_id`
neo (đoạn đầu tiên, tức dòng số thứ tự) của câu đích. Bảng `human_translations` đã có
trigger `human_translations_archive_update` (migration 005) tự lưu bản CŨ vào
`human_translation_history` mỗi khi có cột theo dõi thay đổi - nhưng trigger đó KHÔNG
theo dõi `passage_id` (xem migration 005). Nên ở đây cũng đổi `import_batch` trong cùng
câu UPDATE để chắc chắn kích hoạt lưu lịch sử, không chỉ đổi mỗi `passage_id` rồi im lặng
mất dấu vết `passage_id` cũ.

Mặc định chỉ dò lại bằng chính `dev_indacanda_label_audit.py` và IN kế hoạch - không
INSERT/UPDATE/DELETE. Cần `--apply` mới thực sự ghi, trong một transaction.

Chạy:
    .venv\\Scripts\\python.exe fix_indacanda_label_swap.py
    .venv\\Scripts\\python.exe fix_indacanda_label_swap.py --apply
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from app.db import fetch_all, get_conn
from dev_indacanda_label_audit import audit_document

SOURCE = "indacanda"
METHOD = "label_boundary_fix"


def _anchor_passage(document_id: str, label_pno: int) -> dict | None:
    rows = fetch_all(
        """
        select id, sort_order from passages
        where document_id = %s and display_paragraph_no = %s
        order by sort_order limit 1
        """,
        [document_id, str(label_pno)],
    )
    return rows[0] if rows else None


def build_plan() -> list[dict]:
    docs = fetch_all(
        """
        select distinct d.id, d.file_name
        from human_translations h
        join passages p on p.id = h.passage_id
        join documents d on d.id = p.document_id
        where h.source = %s
        """,
        [SOURCE],
    )

    plan = []
    for doc in docs:
        document_id = str(doc["id"])
        for finding in audit_document(document_id, doc["file_name"], SOURCE):
            target = _anchor_passage(document_id, finding["label_pno"])
            reason = ""
            if target is None:
                reason = "không tìm thấy đoạn neo cho số câu trong nhãn"
            else:
                existing = fetch_all(
                    "select id from human_translations where passage_id = %s and source = %s",
                    [str(target["id"]), SOURCE],
                )
                if existing:
                    reason = "đoạn đích đã có bản dịch khác - không tự ý ghi đè"
            plan.append({**finding, "target_passage_id": str(target["id"]) if target else None,
                         "blocked_reason": reason})
    return plan


def print_plan(plan: list[dict]) -> None:
    ok = [p for p in plan if not p["blocked_reason"]]
    blocked = [p for p in plan if p["blocked_reason"]]
    print(f"Kế hoạch: {len(ok)} dòng sẽ chuyển passage_id | {len(blocked)} bị chặn\n")
    for p in ok:
        print(f"  [{p['file_name']}] câu {p['display_paragraph_no']} ({p['actual_vagga']})"
              f" -> câu {p['label_pno']} ({p['label_vagga']})")
        print(f"      human_translation_id={p['human_translation_id']}"
              f"  passage_id_moi={p['target_passage_id']}")
    for p in blocked:
        print(f"  [BỊ CHẶN] {p['file_name']} câu {p['display_paragraph_no']}: {p['blocked_reason']}")


def apply_plan(cursor, plan: list[dict]) -> str:
    if any(p["blocked_reason"] for p in plan):
        raise RuntimeError("Có mục bị chặn; không ghi một phần. Hãy xử lý xong rồi chạy lại.")
    batch = f"{METHOD}-{datetime.now():%Y%m%d-%H%M%S-%f}"
    for p in plan:
        cursor.execute(
            """
            update human_translations
            set passage_id = %s, match_method = %s, import_batch = %s, updated_at = now()
            where id = %s
            returning id
            """,
            [p["target_passage_id"], METHOD, batch, p["human_translation_id"]],
        )
        if cursor.fetchone() is None:
            raise RuntimeError(f"Không tìm thấy dòng {p['human_translation_id']} để cập nhật")
    return batch


def main() -> None:
    parser = argparse.ArgumentParser(description="Dò lại rồi sửa các dòng bị lạc chỗ do lệch ranh giới phẩm.")
    parser.add_argument("--apply", action="store_true", help="thực sự ghi DB trong một transaction")
    args = parser.parse_args()

    plan = build_plan()
    if not plan:
        print("Không còn dòng nào cần sửa.")
        return
    print_plan(plan)

    if not args.apply:
        print("\nDRY-RUN - chưa ghi gì. Khi đồng ý: "
              ".venv\\Scripts\\python.exe fix_indacanda_label_swap.py --apply")
        return

    with get_conn() as conn:
        with conn.transaction():
            with conn.cursor() as cursor:
                batch = apply_plan(cursor, plan)
    print(f"\nĐÃ GHI DB: {len(plan)} dòng đã chuyển passage_id | đợt: {batch}")


if __name__ == "__main__":
    main()
