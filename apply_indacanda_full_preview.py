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
from indacanda_full_extract import OUTPUT_ROOT, SUPPORTED_VOLUMES


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


def build_plan(cursor, items: list[VerifiedItem]) -> list[PlannedItem]:
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
        if (
            str(section["document_id"]) != item.document_id
            or int(section["start_sort_order"]) != item.start
            or int(section["end_sort_order"]) != item.end
            or str(section["title"]) != item.title
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
        exact_rows = cursor.fetchall()
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
        overlapping_rows = cursor.fetchall()

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


def print_plan(plan: list[PlannedItem], review_count: int) -> None:
    counts = {name: sum(entry.action == name for entry in plan) for name in (
        "insert", "update", "unchanged", "blocked"
    )}
    stale = sum(len(entry.stale_ids) for entry in plan if entry.action != "blocked")
    print(
        f"Kế hoạch DB: thêm {counts['insert']} | cập nhật {counts['update']} | "
        f"không đổi {counts['unchanged']} | bị chặn {counts['blocked']}"
    )
    print(f"Bản neo cũ cùng range sẽ lưu lịch sử rồi dọn: {stale}")
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
) -> tuple[str, int, int]:
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
    stale_removed = 0
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
                    plan = build_plan(cursor, all_items)
                    print_plan(plan, review_count)
                    batch, changed, stale_removed = apply_plan(
                        cursor, plan, volumes, review_count, manifest_hashes
                    )
            print(f"\nĐÃ GHI DB: {changed} dòng thay đổi | dọn {stale_removed} neo cũ")
            print(f"Đợt nạp: {batch}")
        else:
            with conn.cursor() as cursor:
                plan = build_plan(cursor, all_items)
            print_plan(plan, review_count)
            if any(entry.action == "blocked" for entry in plan):
                raise SystemExit("\nDRY-RUN CHƯA ĐẠT: không dùng --apply.")
            print("\nDRY-RUN ĐẠT: chưa có INSERT/UPDATE/DELETE DB.")
            print("Khi đồng ý ghi: .venv\\Scripts\\python.exe apply_indacanda_full_preview.py --apply")


if __name__ == "__main__":
    main()
