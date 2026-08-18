"""Dò các dòng `indacanda` tự nhúng nhãn phẩm/chương sai chỗ.

Vì sao cần script này
----------------------
Khách báo trích dẫn sai: bấm vào câu 179 (Phẩm Đức Phật) nhưng bản dịch Indacanda hiện
ra lại là "PHẨM AN LẠC [197]" - nội dung của phẩm SAU. Tra tay phát hiện: dòng này gắn
đúng vào đoạn câu 193 (vẫn thuộc Phẩm Đức Phật, đúng theo `passage_id`), nhưng CHỮ bên
trong lại là câu mở đầu Phẩm An Lạc (197) - văn bản bị gán nhầm đoạn lúc nhập liệu.

Nhiều dòng dịch Việt Kinh Pháp Cú tự in nhãn phẩm ngay trong câu đầu tiên của phẩm mới,
kiểu "PHẨM AN LẠC [197] 1. Thật vậy...". Đây là tín hiệu RẺ và KHÔNG PHỤ THUỘC NGÔN NGỮ
để dò lỗi: chỉ cần so số trong ngoặc `[N]` với việc đoạn đang gắn (`passage_id`) có thật
sự thuộc phẩm mang số N đó hay không - không cần so chữ Việt với chữ Pali.

Dò tay 3/3 ca tìm được trong Pháp Cú thì cả 3 đều lệch đúng MỘT kiểu: nhãn phẩm SAU bị
gắn vào một đoạn gần CUỐI phẩm TRƯỚC - gợi ý lỗi hệ thống lúc khớp ranh giới phẩm của
`import_indacanda.py`, không phải lỗi rời rạc từng dòng.

Script CHỈ ĐỌC, không có INSERT/UPDATE/DELETE.

Chạy:
    .venv\\Scripts\\python.exe dev_indacanda_label_audit.py
    .venv\\Scripts\\python.exe dev_indacanda_label_audit.py --source indacanda_full
"""

from __future__ import annotations

import argparse
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from app.db import fetch_all

# "PHẨM AN LẠC [197]" - nhãn chữ hoa tiếng Việt rồi tới số câu trong ngoặc. Không khoá
# cứng từ "PHẨM": một số nguồn/tập khác có thể dùng "CHƯƠNG"/"TỤNG PHẨM"/... nên chỉ đòi
# một chuỗi CHỮ HOA (kèm dấu) rồi ngoặc số - bắt được cả những dạng chưa gặp.
_LABEL_PATTERN = re.compile(
    r"^([A-ZĐÀÁẢÃẠÂẦẤẨẪẬĂẰẮẲẴẶÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴ ]{4,40})"
    r"\s*\[(\d+)\]"
)

# Hậu tố cấp phẩm/chương của các sách kệ có đánh số câu liên tục xuyên suốt cả sách -
# đúng kiểu văn bản mà nhãn nhúng kiểu "[N]" mới có nghĩa để so (Pháp Cú, Trưởng/Trưởng
# Lão Ni Kệ, Kinh Tập...). Không đụng gì tới Kinh/Luật vì các sách đó không đánh số kiểu
# này.
_VAGGA_SUFFIXES = ("vaggo", "vaggam", "nipato", "nipatam")


def _vagga_sections(document_id: str) -> list[dict]:
    rows = fetch_all(
        """
        select s.title, s.level, s.start_sort_order,
               coalesce(s.end_sort_order, s.start_sort_order) as end_sort_order
        from sections s
        where s.document_id = %s
        order by s.start_sort_order
        """,
        [document_id],
    )
    from app.normalize import normalize_pali

    out = []
    for row in rows:
        stem = normalize_pali(str(row["title"])).replace(" ", "")
        if stem.endswith(_VAGGA_SUFFIXES):
            out.append(row)
    return out


def _vagga_containing(vaggas: list[dict], first_pno: int, last_pno: int) -> dict | None:
    """Phẩm mà khoảng số câu [first_pno, last_pno] của MỘT đoạn Pali nằm trong đó."""
    for v in vaggas:
        if v["first_pno"] is None or v["last_pno"] is None:
            continue
        if int(v["first_pno"]) <= first_pno and last_pno <= int(v["last_pno"]):
            return v
    return None


def audit_document(document_id: str, file_name: str, source: str) -> list[dict]:
    vagga_secs = _vagga_sections(document_id)
    if not vagga_secs:
        return []

    # Số câu in (display_paragraph_no) đầu/cuối của từng phẩm - để biết phẩm nào chứa số
    # N ghi trong ngoặc. `display_paragraph_no` là cột TEXT, nên MIN/MAX mặc định so
    # theo từ điển ("9" > "20") chứ không theo số - phải ép kiểu int TRƯỚC khi so, và
    # chỉ lấy những dòng thật sự là số (bỏ nhãn kiểu "uddāna" không phải số câu).
    vaggas = []
    for v in vagga_secs:
        edge = fetch_all(
            """
            select min((display_paragraph_no)::int) as first_pno,
                   max((display_paragraph_no)::int) as last_pno
            from passages
            where document_id = %s and sort_order between %s and %s
              and display_paragraph_no ~ '^[0-9]+$'
            """,
            [document_id, v["start_sort_order"], v["end_sort_order"]],
        )[0]
        vaggas.append({**v, **edge})

    rows = fetch_all(
        """
        select h.id, h.translated_text, p.sort_order, p.display_paragraph_no
        from human_translations h
        join passages p on p.id = h.passage_id
        where h.source = %s and p.document_id = %s
        order by p.sort_order
        """,
        [source, document_id],
    )

    findings = []
    for row in rows:
        match = _LABEL_PATTERN.match(str(row["translated_text"] or ""))
        if not match:
            continue
        label_text, label_pno = match.group(1).strip(), int(match.group(2))
        # `display_paragraph_no` là cột TEXT (xem chú thích ở phần dựng `vaggas` phía
        # trên) - ép kiểu int TẠI ĐÂY nữa, không chỉ lúc tính biên phẩm, kẻo lại so
        # "193" (str) với 179/196 (int) và luôn ra None do lệch kiểu.
        raw_pno = row["display_paragraph_no"]
        if raw_pno is None or not re.fullmatch(r"[0-9]+", str(raw_pno)):
            continue
        pno = int(raw_pno)

        actual_vagga = _vagga_containing(vaggas, pno, pno)
        label_vagga = _vagga_containing(vaggas, label_pno, label_pno)
        if actual_vagga is None or label_vagga is None:
            continue
        if str(actual_vagga["title"]) == str(label_vagga["title"]):
            continue  # nhãn và đoạn cùng một phẩm - không có gì bất thường

        findings.append(
            {
                "file_name": file_name,
                "human_translation_id": str(row["id"]),
                "sort_order": row["sort_order"],
                "display_paragraph_no": pno,
                "actual_vagga": str(actual_vagga["title"]),
                "label_text": label_text,
                "label_pno": label_pno,
                "label_vagga": str(label_vagga["title"]),
                "snippet": str(row["translated_text"])[:90],
            }
        )
    return findings


def main() -> None:
    parser = argparse.ArgumentParser(description="Dò nhãn phẩm/chương nhúng sai chỗ trong bản dịch.")
    parser.add_argument("--source", default="indacanda", help="nguồn cần dò (mặc định: indacanda)")
    args = parser.parse_args()

    # KHÔNG join qua `h.document_id`: cột đó chỉ có ở nguồn CẤP BÀI (`indacanda_full`,
    # `minh_chau`) - nguồn CẤP ĐOẠN như `indacanda` luôn để NULL (xem docstring đầu
    # `translation_sources.py`). Join qua `passages` mới đúng với mọi kiểu gắn.
    docs = fetch_all(
        """
        select distinct d.id, d.file_name
        from human_translations h
        join passages p on p.id = h.passage_id
        join documents d on d.id = p.document_id
        where h.source = %s
        """,
        [args.source],
    )

    all_findings = []
    for doc in docs:
        all_findings.extend(audit_document(str(doc["id"]), doc["file_name"], args.source))

    if not all_findings:
        print(f"Không tìm thấy nhãn phẩm/chương nào lệch chỗ trong nguồn '{args.source}'.")
        return

    print(f"Tìm thấy {len(all_findings)} dòng có nhãn nhúng KHÔNG khớp phẩm chứa nó:\n")
    for f in all_findings:
        print(f"[{f['file_name']}] câu {f['display_paragraph_no']} ({f['actual_vagga']})"
              f" mang nhãn \"{f['label_text']} [{f['label_pno']}]\" ({f['label_vagga']})")
        print(f"   human_translation_id: {f['human_translation_id']}")
        print(f"   trích: {f['snippet']}")
        print()


if __name__ == "__main__":
    main()
