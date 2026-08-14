"""Kiem tra nhanh pham vi 'toan bo bai kinh' va do phu cac ban dich.

Mac dinh dung Mahapadanasutta vi day la truong hop khach hang da phan anh:
mot muc con phai duoc nang len dung bai kinh cha khi mo cua so doi chieu.
Script chi doc du lieu, khong thay doi database.
"""

from __future__ import annotations

import sys

from fastapi.testclient import TestClient

from app.db import fetch_one
from app.main import _section_payload, app
from app.normalize import normalize_pali


EXPECTED_READER = "mahapadanasuttam"
CHILD_SECTION_TITLE = "Pubbenivāsapaṭisaṃyuttakathā"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    child = fetch_one(
        """
        select s.id, s.title
        from sections s
        join documents d on d.id = s.document_id
        where s.title = %s
          and d.corpus_type = 'mul'
          and '1. Mahāpadānasuttaṃ' = any(s.source_path)
        order by s.start_sort_order
        limit 1
        """,
        [CHILD_SECTION_TITLE],
    )
    if not child:
        raise SystemExit(
            "KHONG DAT: khong tim thay muc con Mahapadanasutta trong database hien tai."
        )

    base = _section_payload(
        str(child["id"]),
        include_translation=False,
        language="vi",
        source="indacanda",
    )
    normalized_title = normalize_pali(str(base["title"])).replace(" ", "")
    if EXPECTED_READER not in normalized_title:
        raise SystemExit(
            f"KHONG DAT: muc con dang mo '{base['title']}', chua phai Mahapadanasutta."
        )
    if int(base["passageCount"]) < 100:
        raise SystemExit(
            f"KHONG DAT: chi co {base['passageCount']} doan, co the van dang mo muc con."
        )

    print("=== PHAM VI TRON BAI ===")
    print(f"Muc duoc bam : {child['title']}")
    print(f"Bai duoc mo   : {base['title']}")
    print(f"So doan Pali  : {base['passageCount']}")
    print(f"So ky tu Pali : {len(base['paliText']):,}")

    print("\n=== DO PHU BAN DICH ===")
    for item in base["officialTranslations"]:
        print(
            f"{item['source']:<16} "
            f"{item['coverageCount']:>3}/{item['coverageTotal']:<3} "
            f"({item['coveragePercent']:>3}%)  "
            f"{len(item['text']):>7,} ky tu"
        )

    whole = next(
        (item for item in base["officialTranslations"] if item["source"] == "indacanda_full"),
        None,
    )
    if whole and not whole["complete"]:
        raise SystemExit(
            "KHONG DAT: Indacanda toan bo bai kinh khong bao phu het pham vi Pali."
        )

    response = TestClient(app).get(
        f"/section-page/{child['id']}?lang=vi&source=indacanda"
    )
    response.raise_for_status()
    html = response.text
    passage_source = next(
        (item for item in base["officialTranslations"] if item["source"] == "indacanda"),
        None,
    )
    expected_coverage = (
        f"{passage_source['coverageCount']}/{passage_source['coverageTotal']} đoạn "
        f"({passage_source['coveragePercent']}%)"
        if passage_source
        else ""
    )
    if (
        "Bản gốc Pali trọn bài kinh" not in html
        or str(base["title"]) not in html
        or (expected_coverage and expected_coverage not in html)
    ):
        raise SystemExit("KHONG DAT: HTML cua so doc chua hien dung pham vi/ti le bao phu.")

    print("\nDAT: logic va HTML deu mo dung Pali tron bai; do phu tung nguon da duoc thong ke.")


if __name__ == "__main__":
    main()
