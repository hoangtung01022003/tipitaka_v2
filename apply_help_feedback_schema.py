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
    print("Đã áp dụng xong. Bảng help_guide và user_feedback sẵn sàng.")


if __name__ == "__main__":
    main()
