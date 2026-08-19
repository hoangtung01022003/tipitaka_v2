"""Áp dụng migration `007_help_feedback.sql` - tạo bảng hướng dẫn và góp ý.

Dự án này là Python-only cho phần backend; việc "tạo file sql rồi chạy lệnh nạp db"
được làm ngay ở đây, đọc file migration rồi `execute` giống hệt cách `import_indacanda.py`
áp dụng các migration dịch thuật. File migration nằm ở sibling `../db/migrations` thuộc
root git (xem CLAUDE.md) - nếu chỉ đẩy `python_app` mà không mang sibling sang máy chạy
thì script sẽ thiếu file và báo lỗi rõ ràng.

Chạy:
    .venv\\Scripts\\python.exe apply_help_feedback_schema.py
Thử trước mà không ghi gì:
    .venv\\Scripts\\python.exe apply_help_feedback_schema.py --dry-run
"""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from app.db import execute

MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations"
MIGRATION = "007_help_feedback.sql"


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    path = MIGRATIONS / MIGRATION
    if not path.exists():
        print(f"THIẾU FILE MIGRATION: {path}")
        print("Chạy từ thư mục python_app, và phải mang sibling ../db sang máy chạy.")
        sys.exit(1)

    sql = path.read_text(encoding="utf-8")
    print(f"Áp dụng {MIGRATION} ({path}) {'- DRY RUN, không ghi' if dry_run else ''}")
    if dry_run:
        print("Kiểm tra đọc file OK. Không thực thi vì --dry-run.")
        return
    execute(sql)
    print("Đã áp dụng xong. Bảng help_guide và user_feedback sẵn sàng.")


if __name__ == "__main__":
    main()
