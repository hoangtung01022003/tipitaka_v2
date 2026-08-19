""""Góp ý và hỗ trợ tìm kiếm" - tin ẩn danh người dùng gửi, admin đọc.

Lưu trong bảng `user_feedback` (xem `db/migrations/007_help_feedback.sql`). Người dùng
KHÔNG nhập tên (ẩn danh), một người chỉ cần gõ nội dung rồi gửi. Tin mới nhất hiện
trên đầu danh sách admin; admin đọc theo lô, cuộn tới đáy thì nạp tiếp (giống cách
`_admin_history_rows` của lịch sử tìm kiếm).

Giới hạn độ dài tin được áp ở tầng route: nguồn gốc dữ liệu là người dùng bất kỳ nên
không tin tưởng client, nhưng cũng chặn cả ở đây để bảng khỏi phình nếu lỡ gọi thẳng
hàm này.
"""

from app.db import execute, fetch_all
from app.i18n import normalize_language

FEEDBACK_MAX_CHARS = 5000
FEEDBACK_BATCH = 20


def add_feedback(message: str, language: str = "vi") -> dict:
    """Lưu một tin góp ý ẩn danh. Trả toàn bộ dòng vừa thêm."""
    message = str(message or "").strip()
    if len(message) > FEEDBACK_MAX_CHARS:
        message = message[:FEEDBACK_MAX_CHARS]
    language = normalize_language(language)
    if not message:
        raise ValueError("message empty")

    row = fetch_all(
        "insert into user_feedback (language, message, created_at) "
        "values (%s, %s, now()) returning id, language, message, created_at",
        [language, message],
    )
    # `execute` là autocommit; dùng fetch_all để lấy `returning`.
    return row[0] if row else {}


def list_feedback(offset: int = 0, limit: int = FEEDBACK_BATCH) -> list[dict]:
    """Danh sách tin cho admin, mới trước. `limit <= FEEDBACK_BATCH * 5`."""
    limit = max(0, min(int(limit or FEEDBACK_BATCH), FEEDBACK_BATCH * 5))
    offset = max(0, int(offset or 0))
    rows = fetch_all(
        "select id, language, message, created_at from user_feedback "
        "order by created_at desc, id desc limit %s offset %s",
        [limit, offset],
    )
    for r in rows:
        r["id"] = str(r["id"])
        r["created_at"] = r["created_at"].isoformat() if r.get("created_at") else None
    return rows


def count_feedback() -> int:
    row = fetch_all("select count(*) as cnt from user_feedback")
    return int(row[0]["cnt"]) if row else 0


def clear_feedback() -> None:
    execute("truncate table user_feedback")


def delete_single_feedback(feedback_id: int) -> None:
    execute("delete from user_feedback where id = %s", [feedback_id])

