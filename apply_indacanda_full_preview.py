"""Kiểm tra và nạp các bản Indacanda trọn đơn vị đã PASS từ preview.

Mặc định chỉ đọc manifest/TXT/DB và in kế hoạch. Chỉ khi có ``--apply`` chương
trình mới mở một transaction và ghi nguồn ``indacanda_full``. Mục REVIEW luôn
bị bỏ qua; dữ liệu sửa tay (match_method=manual) không bao giờ bị ghi đè.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys

from app.db import get_conn
from app.text_artifacts import unicode_artifacts
from import_indacanda import VOLUMES, mend_spacing
from indacanda_full_extract import (
    DEEPEST_ALIGNED_SPECS,
    OUTPUT_ROOT,
    SUPPORTED_VOLUMES,
)


sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

SOURCE = "indacanda_full"
LANGUAGE = "vi"
METHOD = "pdf_heading_boundary"
MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations"
METHOD_RANK = {
    None: 0,
    "heuristic": 10,
    "whole_unit": 20,
    "global_align": 30,
    "strict_unique": 40,
    METHOD: 45,
    "manual": 50,
}
CV1_EXPECTED_SEGMENTS = 119
CV1_LAST_SORT_ORDER = 973
CV1_LEGACY_RANGES = {(0, 352), (353, 478), (479, 754)}
CV1_SOURCE_REF_PREFIX = "cv1:ttpv_06_Cv_I.pdf:"
CV2_EXPECTED_SEGMENTS = 73
CV2_FIRST_SORT_ORDER = 974
CV2_LAST_SORT_ORDER = 2462
CV2_LEGACY_RANGES = {
    (974, 1274),
    (1275, 1556),
    (1557, 1695),
    (2094, 2335),
    (2336, 2385),
    (2386, 2462),
}
CV2_SOURCE_REF_PREFIX = "cv2:ttpv_07_Cv_II.pdf:"


@dataclass(frozen=True)
class VerifiedItem:
    volume: str
    title: str
    section_id: str
    document_id: str
    start: int
    end: int
    text: str
    text_sha256: str
    source_ref: str
    match_score: float


@dataclass
class PlannedItem:
    item: VerifiedItem
    passage_id: str
    anchor_sort_order: int
    action: str
    stale_ids: list[str]
    reason: str = ""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clean_preview_text(raw: str) -> str:
    # Trình xuất luôn thêm đúng một newline cuối tệp; hash/độ dài trong manifest
    # được tính trên nội dung trước newline này.
    return raw[:-1] if raw.endswith("\n") else raw


def load_verified_volume(volume: str, preview_root: Path) -> tuple[list[VerifiedItem], int, str]:
    manifest_path = preview_root / volume / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError(f"Thiếu manifest: {manifest_path}")
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    if manifest.get("schema_version") != 1:
        raise RuntimeError(f"{volume}: manifest cũ/chưa có SHA-256; hãy chạy lại extractor")
    if manifest.get("mode") != "preview_only_no_database_writes":
        raise RuntimeError(f"{volume}: sai chế độ manifest")
    if manifest.get("volume") != volume:
        raise RuntimeError(f"{volume}: tên volume trong manifest không khớp")
    if int(manifest.get("review_text_pages", -1)) != 0:
        raise RuntimeError(f"{volume}: còn trang REVIEW trong kiểm toán chữ")

    source_pdf = Path(str(manifest.get("source_pdf") or ""))
    expected_pdf_name = str(VOLUMES[volume]["file"])
    if source_pdf.name != expected_pdf_name or not source_pdf.is_file():
        raise RuntimeError(f"{volume}: PDF nguồn không tồn tại hoặc sai tên {expected_pdf_name}")
    actual_pdf_hash = sha256_file(source_pdf)
    if actual_pdf_hash != manifest.get("source_pdf_sha256"):
        raise RuntimeError(f"{volume}: PDF đã thay đổi sau khi kiểm thử")

    raw_items = list(manifest.get("items") or [])
    pass_items = [item for item in raw_items if item.get("status") == "PASS"]
    review_items = [item for item in raw_items if item.get("status") != "PASS"]
    if len(raw_items) != int(manifest.get("total_units", -1)):
        raise RuntimeError(f"{volume}: total_units không khớp danh sách")
    if len(pass_items) != int(manifest.get("pass_units", -1)):
        raise RuntimeError(f"{volume}: pass_units không khớp danh sách")
    if len(review_items) != int(manifest.get("review_units", -1)):
        raise RuntimeError(f"{volume}: review_units không khớp danh sách")

    verified: list[VerifiedItem] = []
    volume_dir = manifest_path.parent.resolve()
    for raw_item in pass_items:
        output_name = str(raw_item.get("output_file") or "")
        if not output_name or Path(output_name).name != output_name:
            raise RuntimeError(f"{volume}/{raw_item.get('title')}: đường dẫn TXT không an toàn")
        output_path = (volume_dir / output_name).resolve()
        if output_path.parent != volume_dir or not output_path.is_file():
            raise RuntimeError(f"{volume}/{raw_item.get('title')}: thiếu TXT {output_name}")
        text = _clean_preview_text(output_path.read_text(encoding="utf-8"))
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if text_hash != raw_item.get("text_sha256"):
            raise RuntimeError(f"{volume}/{raw_item.get('title')}: TXT đã đổi sau khi kiểm thử")
        if len(text) != int(raw_item.get("text_characters", -1)):
            raise RuntimeError(f"{volume}/{raw_item.get('title')}: độ dài TXT không khớp")
        hazards = unicode_artifacts(text)
        if hazards:
            raise RuntimeError(f"{volume}/{raw_item.get('title')}: còn ký tự Unicode lỗi {dict(hazards)}")
        if mend_spacing(text) != text or re.search(r"\( | \)", text):
            raise RuntimeError(f"{volume}/{raw_item.get('title')}: còn khoảng trắng PDF chưa làm sạch")
        viet_pages = list(raw_item.get("vietnamese_pages") or [])
        if not viet_pages:
            raise RuntimeError(f"{volume}/{raw_item.get('title')}: không có trang Việt")
        verified.append(
            VerifiedItem(
                volume=volume,
                title=str(raw_item["title"]),
                section_id=str(raw_item["section_id"]),
                document_id=str(raw_item["document_id"]),
                start=int(raw_item["start_sort_order"]),
                end=int(raw_item["end_sort_order"]),
                text=text,
                text_sha256=text_hash,
                source_ref=(
                    f"{volume}:{source_pdf.name}:vi-pages-{min(viet_pages)}-{max(viet_pages)}:"
                    f"sha256-{text_hash[:16]}"
                ),
                match_score=float(raw_item.get("title_match_score") or 1.0),
            )
        )

    # Hai bản PASS trong cùng document không được đè khoảng sort_order lên nhau.
    by_document: dict[str, list[VerifiedItem]] = {}
    for item in verified:
        by_document.setdefault(item.document_id, []).append(item)
    for document_items in by_document.values():
        previous: VerifiedItem | None = None
        for item in sorted(document_items, key=lambda entry: (entry.start, entry.end)):
            if previous and item.start <= previous.end:
                raise RuntimeError(
                    f"{volume}: khoảng DB chồng lấn giữa {previous.title} và {item.title}"
                )
            previous = item

    return verified, len(review_items), hashlib.sha256(manifest_bytes).hexdigest()


def _cv1_direct_run_matches(cursor, item: VerifiedItem, section: dict) -> bool:
    """Cho phép một run trực tiếp sâu nhất, nhưng không cho range tùy ý.

    Cullavagga I và năm tập nạp lại đều có passage thuộc trực tiếp section cha,
    đôi khi thành nhiều khoảng rời xen giữa section lá. Mỗi item phải là một run
    liên tục tối đa do chính section đó sở hữu; không thể nạp lại range cha rộng.
    """
    if item.volume != "cv1" and item.volume not in DEEPEST_ALIGNED_SPECS:
        return False
    if (
        str(section["document_id"]) != item.document_id
        or str(section["title"]) != item.title
        or item.start < int(section["start_sort_order"])
        or item.end > int(section["end_sort_order"])
    ):
        return False

    expected = item.end - item.start + 1
    cursor.execute(
        """
        select count(*) as total,
               count(*) filter (where section_id = %s) as directly_owned
        from passages
        where document_id = %s and sort_order between %s and %s
        """,
        [item.section_id, item.document_id, item.start, item.end],
    )
    counts = cursor.fetchone()
    if (
        not counts
        or int(counts["total"]) != expected
        or int(counts["directly_owned"]) != expected
    ):
        return False

    cursor.execute(
        """
        select count(*) as same_section_neighbors
        from passages
        where document_id = %s and section_id = %s
          and sort_order in (%s, %s)
        """,
        [item.document_id, item.section_id, item.start - 1, item.end + 1],
    )
    neighbors = cursor.fetchone()
    return bool(neighbors) and int(neighbors["same_section_neighbors"]) == 0


def cv1_legacy_replacement_ids(
    cursor, volumes: list[str], items: list[VerifiedItem]
) -> list[str]:
    """Xác định đúng ba row chương cv1 cũ chỉ khi bản thay thế đã hoàn chỉnh."""
    if volumes != ["cv1"]:
        return []

    ordered = sorted(items, key=lambda item: (item.start, item.end))
    expected_start = 0
    coverage_ok = len(ordered) == CV1_EXPECTED_SEGMENTS
    document_ids = {item.document_id for item in ordered}
    for item in ordered:
        if item.start != expected_start or item.end < item.start:
            coverage_ok = False
            break
        expected_start = item.end + 1
    coverage_ok = (
        coverage_ok
        and len(document_ids) == 1
        and expected_start == CV1_LAST_SORT_ORDER + 1
    )
    if not coverage_ok:
        raise RuntimeError(
            "cv1: chỉ được thay dữ liệu cũ khi đủ 119 PASS phủ liên tục sort_order 0-973"
        )

    document_id = next(iter(document_ids))
    cursor.execute(
        """
        select id, start_sort_order, end_sort_order, source_ref, match_method
        from human_translations
        where source = %s and document_id = %s
          and (
            (start_sort_order = 0 and end_sort_order = 352)
            or (start_sort_order = 353 and end_sort_order = 478)
            or (start_sort_order = 479 and end_sort_order = 754)
          )
        order by start_sort_order, id
        """,
        [SOURCE, document_id],
    )
    rows = cursor.fetchall()
    if not rows:
        return []
    ranges = {
        (int(row["start_sort_order"]), int(row["end_sort_order"])) for row in rows
    }
    if len(rows) != 3 or ranges != CV1_LEGACY_RANGES:
        raise RuntimeError("cv1: tập row chương cũ không còn đúng ba range đã kiểm chứng")
    if any(
        row.get("match_method") == "manual"
        or not str(row.get("source_ref") or "").startswith(CV1_SOURCE_REF_PREFIX)
        for row in rows
    ):
        raise RuntimeError("cv1: row chương cũ có nguồn/method được bảo vệ; không tự động thay")
    return [str(row["id"]) for row in rows]


def cv2_legacy_replacement_ids(
    cursor, volumes: list[str], items: list[VerifiedItem]
) -> list[str]:
    """Xác định sáu row chương cv2 cũ chỉ khi đủ 73 section thay thế."""
    if volumes != ["cv2"]:
        return []

    ordered = sorted(items, key=lambda item: (item.start, item.end))
    expected_start = CV2_FIRST_SORT_ORDER
    coverage_ok = len(ordered) == CV2_EXPECTED_SEGMENTS
    document_ids = {item.document_id for item in ordered}
    for item in ordered:
        if item.start != expected_start or item.end < item.start:
            coverage_ok = False
            break
        expected_start = item.end + 1
    coverage_ok = (
        coverage_ok
        and len(document_ids) == 1
        and expected_start == CV2_LAST_SORT_ORDER + 1
    )
    if not coverage_ok:
        raise RuntimeError(
            "cv2: chỉ được thay dữ liệu cũ khi đủ 73 PASS phủ liên tục sort_order 974-2462"
        )

    document_id = next(iter(document_ids))
    cursor.execute(
        """
        select id, start_sort_order, end_sort_order, translated_text,
               source_ref, match_method
        from human_translations
        where source = %s and document_id = %s
          and (
            (start_sort_order = 974 and end_sort_order = 1274)
            or (start_sort_order = 1275 and end_sort_order = 1556)
            or (start_sort_order = 1557 and end_sort_order = 1695)
            or (start_sort_order = 2094 and end_sort_order = 2335)
            or (start_sort_order = 2336 and end_sort_order = 2385)
            or (start_sort_order = 2386 and end_sort_order = 2462)
          )
        order by start_sort_order, id
        """,
        [SOURCE, document_id],
    )
    rows = cursor.fetchall()
    intended_by_range = {(item.start, item.end): item for item in ordered}
    # Sau lần áp dụng đầu tiên, section sâu nhất đầu tiên (974-1274) tình cờ có
    # cùng range với row chương cũ. Không được nhận nhầm chính row mới này là
    # dữ liệu legacy ở lần dry-run kế tiếp.
    rows = [
        row
        for row in rows
        if not (
            (intended := intended_by_range.get(
                (int(row["start_sort_order"]), int(row["end_sort_order"]))
            ))
            and row.get("translated_text") == intended.text
            and row.get("source_ref") == intended.source_ref
            and row.get("match_method") == METHOD
        )
    ]
    if not rows:
        return []
    ranges = {
        (int(row["start_sort_order"]), int(row["end_sort_order"])) for row in rows
    }
    if len(rows) != 6 or ranges != CV2_LEGACY_RANGES:
        raise RuntimeError("cv2: tập row chương cũ không còn đúng sáu range đã kiểm chứng")
    if any(
        row.get("match_method") == "manual"
        or not str(row.get("source_ref") or "").startswith(CV2_SOURCE_REF_PREFIX)
        for row in rows
    ):
        raise RuntimeError("cv2: row chương cũ có nguồn/method được bảo vệ; không tự động thay")
    return [str(row["id"]) for row in rows]


def deepest_legacy_replacement_ids(
    cursor, volumes: list[str], items: list[VerifiedItem]
) -> list[str]:
    """Thay preview cũ của đúng một PDF theo transaction.

    Chỉ kích hoạt khi manifest đủ 100% segment PASS và phủ liên tục toàn phạm
    vi đã khóa. Nhờ prefix gồm cả volume+tên PDF, mv1/mv2 dùng chung document
    nhưng không bao giờ xóa dữ liệu của nhau. Một spec có `replace_range_only`
    chỉ thay các row chồng đúng phạm vi con đã khóa; các chương khác được giữ lại.
    """
    if len(volumes) != 1 or volumes[0] not in DEEPEST_ALIGNED_SPECS:
        return []
    volume = volumes[0]
    spec = DEEPEST_ALIGNED_SPECS[volume]
    ordered = sorted(items, key=lambda item: (item.start, item.end))
    expected_start = int(spec["first_sort_order"])
    coverage_ok = len(ordered) == int(spec["expected_count"])
    document_ids = {item.document_id for item in ordered}
    for item in ordered:
        if item.volume != volume or item.start != expected_start or item.end < item.start:
            coverage_ok = False
            break
        expected_start = item.end + 1
    coverage_ok = (
        coverage_ok
        and len(document_ids) == 1
        and expected_start == int(spec["last_sort_order"]) + 1
    )
    if not coverage_ok:
        raise RuntimeError(
            f"{volume}: chỉ thay dữ liệu cũ khi đủ {spec['expected_count']} PASS "
            f"phủ liên tục sort_order {spec['first_sort_order']}-"
            f"{spec['last_sort_order']}"
        )

    document_id = next(iter(document_ids))
    prefix = f"{volume}:{VOLUMES[volume]['file']}:"
    range_sql = ""
    range_params: list[int] = []
    if spec.get("replace_range_only"):
        range_sql = "and start_sort_order <= %s and end_sort_order >= %s"
        range_params = [
            int(spec["last_sort_order"]),
            int(spec["first_sort_order"]),
        ]
    cursor.execute(
        f"""
        select id, start_sort_order, end_sort_order, translated_text,
               source_ref, match_method
        from human_translations
        where source = %s and document_id = %s and source_ref like %s
          {range_sql}
        order by start_sort_order, end_sort_order, id
        """,
        [SOURCE, document_id, prefix + "%", *range_params],
    )
    rows = cursor.fetchall()
    intended_by_range = {(item.start, item.end): item for item in ordered}
    stale: list[dict] = []
    for row in rows:
        item = intended_by_range.get(
            (int(row["start_sort_order"]), int(row["end_sort_order"]))
        )
        if (
            item
            and row.get("translated_text") == item.text
            and row.get("source_ref") == item.source_ref
            and row.get("match_method") == METHOD
        ):
            continue
        if row.get("match_method") == "manual":
            raise RuntimeError(
                f"{volume}: có row manual trong dữ liệu PDF cũ; không tự động thay"
            )
        if not str(row.get("source_ref") or "").startswith(prefix):
            raise RuntimeError(
                f"{volume}: gặp row không đúng nguồn PDF; không tự động thay"
            )
        stale.append(row)
    return [str(row["id"]) for row in stale]


def build_plan(
    cursor,
    items: list[VerifiedItem],
    ignored_overlap_ids: set[str] | None = None,
) -> list[PlannedItem]:
    ignored_overlap_ids = ignored_overlap_ids or set()
    plan: list[PlannedItem] = []
    for item in items:
        cursor.execute(
            """
            select id, document_id, title, start_sort_order,
                   coalesce(end_sort_order, start_sort_order) as end_sort_order
            from sections where id = %s
            """,
            [item.section_id],
        )
        section = cursor.fetchone()
        if not section:
            raise RuntimeError(f"{item.volume}/{item.title}: section không còn trong DB")
        exact_section_range = (
            str(section["document_id"]) == item.document_id
            and int(section["start_sort_order"]) == item.start
            and int(section["end_sort_order"]) == item.end
            and str(section["title"]) == item.title
        )
        if not exact_section_range and not _cv1_direct_run_matches(
            cursor, item, section
        ):
            raise RuntimeError(f"{item.volume}/{item.title}: section/range trong DB đã thay đổi")

        cursor.execute(
            """
            select id, sort_order from passages
            where document_id = %s and sort_order between %s and %s
            order by sort_order, id limit 1
            """,
            [item.document_id, item.start, item.end],
        )
        anchor = cursor.fetchone()
        if not anchor:
            raise RuntimeError(f"{item.volume}/{item.title}: range không có passage neo")
        passage_id = str(anchor["id"])

        cursor.execute(
            """
            select id, passage_id, translated_text, source_ref, document_id,
                   start_sort_order, end_sort_order, match_method
            from human_translations
            where passage_id = %s and source = %s
            """,
            [passage_id, SOURCE],
        )
        current = cursor.fetchone()
        if current and str(current["id"]) in ignored_overlap_ids:
            current = None

        cursor.execute(
            """
            select id, passage_id, match_method
            from human_translations
            where source = %s and document_id = %s
              and start_sort_order = %s and end_sort_order = %s
            order by id
            """,
            [SOURCE, item.document_id, item.start, item.end],
        )
        exact_rows = [
            row for row in cursor.fetchall() if str(row["id"]) not in ignored_overlap_ids
        ]
        stale_rows = [row for row in exact_rows if str(row["passage_id"]) != passage_id]

        cursor.execute(
            """
            select id, passage_id, start_sort_order, end_sort_order, match_method
            from human_translations
            where source = %s and document_id = %s
              and start_sort_order is not null and end_sort_order is not null
              and start_sort_order <= %s and end_sort_order >= %s
              and not (start_sort_order = %s and end_sort_order = %s)
            order by start_sort_order, end_sort_order
            """,
            [SOURCE, item.document_id, item.end, item.start, item.start, item.end],
        )
        overlapping_rows = [
            row for row in cursor.fetchall() if str(row["id"]) not in ignored_overlap_ids
        ]

        reason = ""
        action = "insert" if current is None else "update"
        if overlapping_rows:
            action = "blocked"
            reason = f"có {len(overlapping_rows)} bản full cũ chồng khoảng nhưng khác range"
        elif any(row.get("match_method") == "manual" for row in stale_rows):
            action = "blocked"
            reason = "có bản manual ở passage neo cũ"
        elif current and METHOD_RANK.get(current.get("match_method"), 0) > METHOD_RANK[METHOD]:
            if (
                current.get("translated_text") == item.text
                and str(current.get("document_id")) == item.document_id
                and current.get("start_sort_order") == item.start
                and current.get("end_sort_order") == item.end
            ):
                action = "unchanged"
            else:
                action = "blocked"
                reason = f"được bảo vệ bởi method={current.get('match_method')}"
        elif current and (
            current.get("translated_text") == item.text
            and current.get("source_ref") == item.source_ref
            and str(current.get("document_id")) == item.document_id
            and current.get("start_sort_order") == item.start
            and current.get("end_sort_order") == item.end
            and current.get("match_method") == METHOD
        ):
            action = "unchanged"

        plan.append(
            PlannedItem(
                item=item,
                passage_id=passage_id,
                anchor_sort_order=int(anchor["sort_order"]),
                action=action,
                stale_ids=[str(row["id"]) for row in stale_rows],
                reason=reason,
            )
        )
    return plan


def print_plan(
    plan: list[PlannedItem], review_count: int, legacy_replacement_count: int = 0
) -> None:
    counts = {name: sum(entry.action == name for entry in plan) for name in (
        "insert", "update", "unchanged", "blocked"
    )}
    stale = sum(len(entry.stale_ids) for entry in plan if entry.action != "blocked")
    print(
        f"Kế hoạch DB: thêm {counts['insert']} | cập nhật {counts['update']} | "
        f"không đổi {counts['unchanged']} | bị chặn {counts['blocked']}"
    )
    print(f"Bản neo cũ cùng range sẽ lưu lịch sử rồi dọn: {stale}")
    print(
        "Bản chương PDF quá rộng sẽ lưu lịch sử rồi thay nguyên tử: "
        f"{legacy_replacement_count}"
    )
    print(f"Mục REVIEW luôn bỏ qua: {review_count}")
    for entry in plan:
        if entry.action == "blocked":
            print(f"  [BLOCKED] {entry.item.volume}/{entry.item.title}: {entry.reason}")


def archive_and_delete_stale(cursor, stale_ids: list[str], batch: str) -> int:
    if not stale_ids:
        return 0
    cursor.execute(
        """
        insert into human_translation_history (
          human_translation_id, passage_id, source, language, translated_text,
          source_ref, segment_ids, document_id, start_sort_order, end_sort_order,
          match_method, match_score, import_batch, replaced_by_batch
        )
        select id, passage_id, source, language, translated_text, source_ref,
               segment_ids, document_id, start_sort_order, end_sort_order,
               match_method, match_score, import_batch, %s
        from human_translations where id = any(%s::uuid[])
        """,
        [batch, stale_ids],
    )
    cursor.execute(
        "delete from human_translations where id = any(%s::uuid[]) returning id",
        [stale_ids],
    )
    return len(cursor.fetchall())


def apply_plan(
    cursor,
    plan: list[PlannedItem],
    volumes: list[str],
    review_count: int,
    manifest_hashes: dict[str, str],
    legacy_replacement_ids: list[str] | None = None,
) -> tuple[str, int, int]:
    legacy_replacement_ids = legacy_replacement_ids or []
    if any(entry.action == "blocked" for entry in plan):
        raise RuntimeError("Có mục BLOCKED; không ghi một phần. Hãy xử lý xung đột trước.")
    for name in (
        "002_human_translations.sql",
        "004_match_provenance.sql",
        "005_import_batches.sql",
        "006_pdf_heading_boundary.sql",
    ):
        cursor.execute((MIGRATIONS / name).read_text(encoding="utf-8"))

    batch = f"{METHOD}-{datetime.now():%Y%m%d-%H%M%S-%f}"
    notes = json.dumps(
        {
            "method": METHOD,
            "manifest_sha256": manifest_hashes,
            "pass_units": len(plan),
            "review_skipped": review_count,
            "legacy_pdf_rows_replaced": len(legacy_replacement_ids),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    cursor.execute(
        """
        insert into human_translation_batches
          (import_batch, source, language, scope, status, notes)
        values (%s, %s, %s, %s, 'running', %s)
        """,
        [batch, SOURCE, LANGUAGE, ",".join(volumes), notes],
    )

    changed = 0
    # Xóa trong cùng transaction, sau khi đã ghi batch và trước INSERT đầu tiên.
    # Row chương đầu cũ dùng cùng passage neo với segment đầu mới nên không thể trì
    # hoãn đến cuối vòng lặp; nếu INSERT nào lỗi thì transaction phục hồi toàn bộ.
    stale_removed = archive_and_delete_stale(
        cursor, legacy_replacement_ids, batch
    )
    for entry in plan:
        if entry.action in {"insert", "update"}:
            item = entry.item
            cursor.execute(
                """
                insert into human_translations
                  (passage_id, source, language, translated_text, source_ref, segment_ids,
                   document_id, start_sort_order, end_sort_order,
                   match_method, match_score, import_batch)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (passage_id, source) do update
                  set language = excluded.language,
                      translated_text = excluded.translated_text,
                      source_ref = excluded.source_ref,
                      segment_ids = excluded.segment_ids,
                      document_id = excluded.document_id,
                      start_sort_order = excluded.start_sort_order,
                      end_sort_order = excluded.end_sort_order,
                      match_method = excluded.match_method,
                      match_score = excluded.match_score,
                      import_batch = excluded.import_batch,
                      updated_at = now()
                  where human_translation_method_rank(excluded.match_method)
                        > human_translation_method_rank(human_translations.match_method)
                     or (
                          human_translation_method_rank(excluded.match_method)
                            = human_translation_method_rank(human_translations.match_method)
                          and row(
                            human_translations.translated_text,
                            human_translations.source_ref,
                            human_translations.document_id,
                            human_translations.start_sort_order,
                            human_translations.end_sort_order
                          ) is distinct from row(
                            excluded.translated_text,
                            excluded.source_ref,
                            excluded.document_id,
                            excluded.start_sort_order,
                            excluded.end_sort_order
                          )
                        )
                returning id
                """,
                [
                    entry.passage_id,
                    SOURCE,
                    LANGUAGE,
                    item.text,
                    item.source_ref,
                    [],
                    item.document_id,
                    item.start,
                    item.end,
                    METHOD,
                    item.match_score,
                    batch,
                ],
            )
            if cursor.fetchone() is None:
                raise RuntimeError(f"DB từ chối ghi {item.volume}/{item.title}")
            changed += 1
        stale_removed += archive_and_delete_stale(cursor, entry.stale_ids, batch)

    cursor.execute(
        """
        insert into human_translation_imports
          (source, language, scope, segments_total, segments_matched,
           passages_written, notes, import_batch)
        values (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        [
            SOURCE,
            LANGUAGE,
            ",".join(volumes),
            len(plan) + review_count,
            len(plan),
            changed,
            notes,
            batch,
        ],
    )
    cursor.execute(
        """
        update human_translation_batches
        set status = 'completed', finished_at = now()
        where import_batch = %s
        """,
        [batch],
    )
    return batch, changed, stale_removed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dry-run hoặc nạp các preview Indacanda full đã PASS."
    )
    parser.add_argument(
        "volumes",
        nargs="*",
        help="mặc định: dn2 pts2 sn",
    )
    parser.add_argument("--apply", action="store_true", help="thực sự ghi DB trong một transaction")
    parser.add_argument("--preview-root", type=Path, default=OUTPUT_ROOT)
    args = parser.parse_args()
    volumes = list(args.volumes or ("dn2", "pts2", "sn"))
    unknown = [volume for volume in volumes if volume not in SUPPORTED_VOLUMES]
    if unknown:
        parser.error(
            f"volume không hợp lệ: {', '.join(unknown)}; chỉ nhận {', '.join(SUPPORTED_VOLUMES)}"
        )

    all_items: list[VerifiedItem] = []
    review_count = 0
    manifest_hashes: dict[str, str] = {}
    for volume in volumes:
        items, reviews, manifest_hash = load_verified_volume(volume, args.preview_root)
        all_items.extend(items)
        review_count += reviews
        manifest_hashes[volume] = manifest_hash
        print(f"{volume}: xác minh {len(items)} PASS | bỏ qua {reviews} REVIEW")

    with get_conn() as conn:
        if args.apply:
            with conn.transaction():
                with conn.cursor() as cursor:
                    legacy_ids = cv1_legacy_replacement_ids(cursor, volumes, all_items)
                    legacy_ids += cv2_legacy_replacement_ids(cursor, volumes, all_items)
                    legacy_ids += deepest_legacy_replacement_ids(
                        cursor, volumes, all_items
                    )
                    plan = build_plan(cursor, all_items, set(legacy_ids))
                    print_plan(plan, review_count, len(legacy_ids))
                    batch, changed, stale_removed = apply_plan(
                        cursor,
                        plan,
                        volumes,
                        review_count,
                        manifest_hashes,
                        legacy_ids,
                    )
            print(f"\nĐÃ GHI DB: {changed} dòng thay đổi | dọn {stale_removed} neo cũ")
            print(f"Đợt nạp: {batch}")
        else:
            with conn.cursor() as cursor:
                legacy_ids = cv1_legacy_replacement_ids(cursor, volumes, all_items)
                legacy_ids += cv2_legacy_replacement_ids(cursor, volumes, all_items)
                legacy_ids += deepest_legacy_replacement_ids(
                    cursor, volumes, all_items
                )
                plan = build_plan(cursor, all_items, set(legacy_ids))
            print_plan(plan, review_count, len(legacy_ids))
            if any(entry.action == "blocked" for entry in plan):
                raise SystemExit("\nDRY-RUN CHƯA ĐẠT: không dùng --apply.")
            print("\nDRY-RUN ĐẠT: chưa có INSERT/UPDATE/DELETE DB.")
            print("Khi đồng ý ghi: .venv\\Scripts\\python.exe apply_indacanda_full_preview.py --apply")


if __name__ == "__main__":
    main()
