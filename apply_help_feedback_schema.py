"""Áp dụng migration `007_help_feedback.sql` - tạo bảng hướng dẫn và góp ý.

Dự án này là Python-only cho phần backend; việc "tạo file sql rồi chạy lệnh nạp db"
được làm ngay ở đây. File sql gốc nằm ở `../db/migrations/007_help_feedback.sql`,
nhưng để tiện lợi cho việc triển khai (chỉ cần kéo source `python_app` về VPS là chạy được),
nội dung SQL đã được nhúng trực tiếp vào script này.

Chạy:
    .venv\Scripts\python.exe apply_help_feedback_schema.py
Thử trước mà không ghi gì:
    .venv\Scripts\python.exe apply_help_feedback_schema.py --dry-run
"""

import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from app.db import execute

SCHEMA_SQL = """
create table if not exists help_guide (
    id         serial primary key,
    language   text not null,
    heading    text not null default '',
    body       text not null default '',
    font_size  integer not null default 16,
    font_color text not null default '#333333',
    updated_at timestamptz not null default now(),
    unique (language)
);

create index if not exists help_guide_language_idx on help_guide (language);

create table if not exists help_guide_items (
    id                uuid primary key default gen_random_uuid(),
    language          text not null,
    position          integer not null default 0,
    body              text not null default '',
    sutta_title       text not null default '',
    sutta_pali_text   text not null default '',
    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now()
);

create index if not exists help_guide_items_language_position_idx
    on help_guide_items (language, position);

-- Chuyển nội dung dạng chuỗi cũ thành từng mục đúng một lần.
insert into help_guide_items (language, position, body)
select guide.language, blocks.ordinality - 1, btrim(blocks.body)
from help_guide guide
cross join lateral regexp_split_to_table(guide.body, E'\\r?\\n[[:space:]]*\\r?\\n')
    with ordinality as blocks(body, ordinality)
where btrim(blocks.body) <> ''
  and not exists (
      select 1 from help_guide_items item where item.language = guide.language
  );

create table if not exists text_summaries (
    text_hash       text not null,
    language        text not null,
    prompt_version  text not null,
    model           text,
    source_text     text not null,
    summary         jsonb not null,
    created_at      timestamptz not null default now(),
    primary key (text_hash, language, prompt_version)
);

create table if not exists user_feedback (
    id         uuid primary key default gen_random_uuid(),
    language   text not null default 'vi',
    message    text not null,
    created_at timestamptz not null default now()
);

create index if not exists user_feedback_created_idx on user_feedback (created_at desc);
"""

def main() -> None:
    dry_run = "--dry-run" in sys.argv
    
    print(f"Áp dụng schema help_guide và user_feedback {'- DRY RUN, không ghi' if dry_run else ''}")
    if dry_run:
        print("Kiểm tra OK. Không thực thi vì --dry-run.")
        return
        
    execute(SCHEMA_SQL)
    print("Đã áp dụng xong. Bảng hướng dẫn, bài kinh thủ công, cache tóm tắt và góp ý đã sẵn sàng.")


if __name__ == "__main__":
    main()
