"""Xuất mọi thứ TRỪ văn bản Pali và lịch sử tìm kiếm, thành một file .sql chạy được trên VPS.

Vì sao không dùng thẳng `pg_dump`
---------------------------------
`human_translations.passage_id` và `translations.passage_id` là UUID trỏ tới `passages`.
UUID sinh ra lúc nạp XML, nên hai máy cùng nạp một bộ văn bản y hệt vẫn ra UUID khác nhau.
Dump theo UUID đổ sang máy khác là lỗi khoá ngoại, hoặc tệ hơn là gắn nhầm đoạn.

Nên ở đây mỗi dòng được neo lại theo (tên file tài liệu, sort_order) - hai thứ ổn định
giữa mọi lần dựng CSDL - và câu lệnh tự tra ngược ra UUID trên máy đích.

File xuất ra gồm ba phần, chạy được nhiều lần mà không hỏng gì:
  1. Câu lệnh tạo bảng / thêm cột (lấy nguyên hai file migration, vốn đã idempotent)
  2. Dữ liệu bản dịch, đệm dịch AI, nhật ký nạp
  3. Câu lệnh đối chiếu số dòng để kiểm ngay sau khi nạp

KHÔNG xuất: passages, sections, documents (văn bản Pali - đã có sẵn trên VPS) và
search_logs (lịch sử tìm kiếm).

Chạy:
    .venv\\Scripts\\python.exe export_data.py
"""

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from app.db import fetch_all
from app.text_artifacts import unicode_artifacts

OUT = Path(__file__).with_name("export_data.sql")
MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations"


def lit(value) -> str:
    """Hằng chuỗi an toàn cho Postgres. Dùng escape kiểu E'' để không vỡ vì xuống dòng."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        inner = ",".join(str(x).replace("\\", "\\\\").replace('"', '\\"') for x in value)
        return "'{" + inner + "}'"
    raw_text = str(value)
    artifacts = unicode_artifacts(raw_text)
    if artifacts:
        details = ", ".join(f"{label}: {count}" for label, count in artifacts.items())
        raise ValueError(f"Không xuất chuỗi còn ký tự Unicode lỗi ({details}).")
    text = raw_text.replace("\\", "\\\\").replace("'", "''")
    return "E'" + text.replace("\r", "\\r").replace("\n", "\\n") + "'"


def passage_lookup(file_name: str, sort_order: int) -> str:
    return (
        "(select p.id from passages p join documents d on d.id = p.document_id "
        f"where d.file_name = {lit(file_name)} and p.sort_order = {sort_order})"
    )


lines: list[str] = [
    "-- Xuất từ máy phát triển. Nạp trên VPS bằng:",
    "--   docker exec -i <container> psql -U postgres -d tipitaka_ai < export_data.sql",
    "-- Chạy lại nhiều lần vẫn an toàn (mọi câu đều có 'if not exists' hoặc 'on conflict').",
    "begin;",
    "",
    "-- ── PHẦN 1: tạo bảng và thêm cột nếu còn thiếu ─────────────────────────",
]
for name in (
    "002_human_translations.sql",
    "003_query_cache.sql",
    "004_match_provenance.sql",
    "005_import_batches.sql",
):
    path = MIGRATIONS / name
    lines += [f"-- nguồn: db/migrations/{name}", path.read_text(encoding="utf-8"), ""]

lines += ["-- ── PHẦN 2: dữ liệu ───────────────────────────────────────────────────"]

# human_translations: neo theo (file, sort_order); cột document_id tra theo tên file.
rows = fetch_all(
    """
    select d.file_name, p.sort_order, h.source, h.language, h.translated_text,
           h.source_ref, h.segment_ids, h.start_sort_order, h.end_sort_order,
           h.match_method, h.match_score, h.import_batch,
           hd.file_name as range_file
    from human_translations h
    join passages p on p.id = h.passage_id
    join documents d on d.id = p.document_id
    left join documents hd on hd.id = h.document_id
    order by d.file_name, p.sort_order, h.source
    """
)
print(f"human_translations: {len(rows)} dòng")
lines.append(f"-- human_translations: {len(rows)} dòng")
for row in rows:
    document = (
        f"(select id from documents where file_name = {lit(row['range_file'])})"
        if row["range_file"]
        else "null"
    )
    lines.append(
        "insert into human_translations (passage_id, source, language, translated_text,"
        " source_ref, segment_ids, document_id, start_sort_order, end_sort_order,"
        " match_method, match_score, import_batch) select "
        f"{passage_lookup(row['file_name'], row['sort_order'])}, {lit(row['source'])},"
        f" {lit(row['language'])}, {lit(row['translated_text'])}, {lit(row['source_ref'])},"
        f" {lit(row['segment_ids'])}, {document}, {lit(row['start_sort_order'])},"
        f" {lit(row['end_sort_order'])}, {lit(row['match_method'])},"
        f" {lit(row['match_score'])}, {lit(row['import_batch'])}"
        f" where {passage_lookup(row['file_name'], row['sort_order'])} is not null"
        " on conflict (passage_id, source) do update set"
        " language = excluded.language, translated_text = excluded.translated_text,"
        " source_ref = excluded.source_ref, segment_ids = excluded.segment_ids,"
        " document_id = excluded.document_id, start_sort_order = excluded.start_sort_order,"
        " end_sort_order = excluded.end_sort_order, match_method = excluded.match_method,"
        " match_score = excluded.match_score, import_batch = excluded.import_batch,"
        " updated_at = now();"
    )
    if row["range_file"]:
        # Khóa DB là (passage_id, source). Khi importer sửa được đoạn đầu của một bài,
        # passage neo có thể đổi và bản cũ cùng khoảng sẽ không tự xung đột. Dọn nó cả
        # trên VPS để export mới không để lại hai bản mà giao diện có thể chọn ngẫu nhiên.
        lines.append(
            "delete from human_translations where"
            f" source = {lit(row['source'])} and document_id = {document}"
            f" and start_sort_order = {lit(row['start_sort_order'])}"
            f" and end_sort_order = {lit(row['end_sort_order'])}"
            f" and passage_id <> {passage_lookup(row['file_name'], row['sort_order'])};"
        )

# translations: đệm bản dịch AI theo từng đoạn, cũng phải neo lại.
rows = fetch_all(
    """
    select d.file_name, p.sort_order, t.language, t.model, t.prompt_version,
           t.translated_text, t.notes
    from translations t
    join passages p on p.id = t.passage_id
    join documents d on d.id = p.document_id
    order by d.file_name, p.sort_order
    """
)
print(f"translations (đệm AI theo đoạn): {len(rows)} dòng")
lines.append(f"-- translations: {len(rows)} dòng")
for row in rows:
    lines.append(
        "insert into translations (passage_id, language, model, prompt_version,"
        " translated_text, notes) select "
        f"{passage_lookup(row['file_name'], row['sort_order'])}, {lit(row['language'])},"
        f" {lit(row['model'])}, {lit(row['prompt_version'])}, {lit(row['translated_text'])},"
        f" {lit(row['notes'])}"
        f" where {passage_lookup(row['file_name'], row['sort_order'])} is not null"
        " on conflict do nothing;"
    )

# Các bảng dưới không trỏ tới passages nên chép thẳng.
plain = {
    "text_translations": ["text_hash", "language", "model", "prompt_version",
                          "source_text", "translated_text", "notes"],
    "query_ai_cache": ["cache_key", "kind", "pipeline_version", "payload"],
    "human_translation_imports": ["source", "language", "scope", "segments_total",
                                  "segments_matched", "passages_written", "notes", "import_batch"],
    "human_translation_batches": ["import_batch", "source", "language", "scope", "status",
                                  "notes", "started_at", "finished_at"],
    "human_translation_unresolved": ["source", "language", "scope", "source_ref", "segment_id",
                                     "raw_text", "normalized_text", "candidate_score", "reason",
                                     "import_batch", "resolved_at", "resolution"],
}
for table, columns in plain.items():
    rows = fetch_all(f"select {', '.join(columns)} from {table}")
    print(f"{table}: {len(rows)} dòng")
    lines.append(f"-- {table}: {len(rows)} dòng")
    for row in rows:
        # `payload` là jsonb: phải qua json.dumps, không được dùng repr của Python
        # (repr sinh dấu nháy đơn, Postgres từ chối vì không phải JSON hợp lệ).
        values = ", ".join(
            lit(json.dumps(row[column], ensure_ascii=False)) + "::jsonb"
            if column == "payload"
            else lit(row[column])
            for column in columns
        )
        lines.append(
            f"insert into {table} ({', '.join(columns)}) values ({values}) on conflict do nothing;"
        )

lines += [
    "",
    "-- ── PHẦN 3: kiểm lại ngay sau khi nạp ─────────────────────────────────",
    "commit;",
    "select source, language, count(*) as so_dong from human_translations group by 1, 2 order by 3 desc;",
    "select count(*) as tong_ban_dich_nguoi from human_translations;",
]

OUT.write_text("\n".join(lines), encoding="utf-8")
size = OUT.stat().st_size / 1024 / 1024
print(f"\nđã ghi {OUT.name} · {size:.1f} MB")
