"""Bài kinh khách đã lưu (⭐) - xem `db/migrations/011_saved_items.sql`.

`content_html` là snapshot HTML do chính server dựng ra (thẻ kết quả hoặc popup "Xem
toàn bộ bài kinh"), client chụp lại nguyên trạng lúc bấm lưu rồi gửi lên - không phải
văn bản người dùng tự gõ, nên hiển thị lại được thẳng bằng `| safe` mà không cần lọc.
"""

from app.db import execute, fetch_all, fetch_one

SAVED_ITEM_KINDS = ("result", "section", "favorite_result", "favorite_section")
# Rộng rãi có chủ đích: cắt ngang chừng sẽ để lại HTML dở dang (thẻ không đóng) - trang
# "Bài đã lưu" hiển thị hỏng còn tệ hơn từ chối lưu. Mốc này cao hơn hẳn trang đọc dài
# nhất từng đo (~700k ký tự Pali + bản dịch, xem CLAUDE.md phần "trang đọc khổng lồ"),
# chỉ để chặn payload bất thường chứ không nhằm cắt bớt nội dung thật.
SAVED_ITEM_MAX_HTML_CHARS = 3_000_000


def save_item(user_id: str, kind: str, title: str, excerpt: str, content_html: str) -> dict:
    if kind not in SAVED_ITEM_KINDS:
        raise ValueError("kind không hợp lệ.")
    content_html = str(content_html or "").strip()
    if not content_html:
        raise ValueError("Không có nội dung để lưu.")
    if len(content_html) > SAVED_ITEM_MAX_HTML_CHARS:
        raise ValueError("Nội dung quá lớn để lưu.")

    row = fetch_one(
        "insert into saved_items (user_id, kind, title, excerpt, content_html) "
        "values (%s, %s, %s, %s, %s) returning id, kind, title, excerpt, created_at",
        [user_id, kind, str(title or "").strip()[:300], str(excerpt or "").strip()[:500], content_html],
    )
    return row


def list_saved_items(user_id: str, kind_group: str = 'history') -> list[dict]:
    if kind_group == 'favorites':
        kinds = ('favorite_result', 'favorite_section')
    else:
        kinds = ('result', 'section')
        
    return fetch_all(
        "select id, kind, title, excerpt, created_at from saved_items "
        "where user_id = %s and kind = ANY(%s) order by created_at desc",
        [user_id, list(kinds)],
    )


def get_saved_item(item_id: str, user_id: str | None = None) -> dict | None:
    """`user_id=None` cho phép admin xem bất kỳ mục nào; trang người dùng luôn truyền
    `user_id` để không ai đọc được mục của người khác qua id."""
    if user_id is None:
        return fetch_one("select * from saved_items where id = %s", [item_id])
    return fetch_one("select * from saved_items where id = %s and user_id = %s", [item_id, user_id])


def delete_saved_item(item_id: str, user_id: str) -> None:
    execute("delete from saved_items where id = %s and user_id = %s", [item_id, user_id])
