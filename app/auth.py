"""Tài khoản người dùng (khách tìm kiếm) - tách khỏi phiên đăng nhập admin.

Đăng nhập bằng username + mật khẩu, không xác thực email, không đăng nhập mạng xã hội -
đúng theo yêu cầu của khách. Phiên lưu trong `request.session["user_id"]`, dùng chung
`SessionMiddleware` với admin nhưng khác key nên không lẫn vào nhau.
"""

import re
import secrets

import bcrypt
from fastapi import HTTPException, Request, status

from .config import settings
from .db import execute, fetch_one

USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]{3,32}$")


def verify_admin_credentials(username: str, password: str) -> bool:
    """Dùng chung cho form đăng nhập chung (`/login`): gõ đúng tài khoản admin (biến môi
    trường `ADMIN_USERNAME`/`ADMIN_PASSWORD`) thì vào thẳng trang quản trị, không cần một
    UI đăng nhập riêng cho admin nữa."""
    correct_username = str(settings().get("admin_username", "")).encode("utf8")
    correct_password = str(settings().get("admin_password", "")).encode("utf8")
    return secrets.compare_digest(username.encode("utf8"), correct_username) and secrets.compare_digest(
        password.encode("utf8"), correct_password
    )


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def get_user_by_username(username: str) -> dict | None:
    return fetch_one("select * from users where username = %s", [username.strip().lower()])


def get_user_by_id(user_id: str) -> dict | None:
    return fetch_one("select * from users where id = %s", [user_id])


def create_user(username: str, password: str) -> dict:
    username = username.strip().lower()
    if not USERNAME_PATTERN.match(username):
        raise ValueError("Tên đăng nhập chỉ gồm 3-32 ký tự chữ/số/._- ")
    if len(password) < 6:
        raise ValueError("Mật khẩu cần ít nhất 6 ký tự.")
    if get_user_by_username(username):
        raise ValueError("Tên đăng nhập đã tồn tại.")
    row = fetch_one(
        "insert into users (username, password_hash) values (%s, %s) returning *",
        [username, hash_password(password)],
    )
    return row


def get_or_create_admin_user() -> dict:
    """Tìm hoặc tạo một bản ghi user tương ứng với tài khoản admin.

    Admin vốn chỉ có session["admin_logged_in"] mà không có user_id, nên không
    dùng được các tính năng yêu cầu user (lưu bài kinh, v.v.). Hàm này đảm bảo
    admin cũng có một dòng trong bảng `users` để liên kết dữ liệu cá nhân."""
    admin_username = str(settings().get("admin_username", "admin")).strip().lower()
    user = get_user_by_username(admin_username)
    if user:
        return user
    # Tạo mới với mật khẩu ngẫu nhiên (admin đăng nhập bằng biến môi trường,
    # không bao giờ dùng mật khẩu trong bảng users).
    random_pw = secrets.token_hex(32)
    row = fetch_one(
        "insert into users (username, password_hash) values (%s, %s) returning *",
        [admin_username, hash_password(random_pw)],
    )
    return row


def ensure_user(username: str, password: str) -> dict:
    """Đảm bảo tài khoản tồn tại với mật khẩu được chỉ định. Nếu chưa có thì tạo mới,
    nếu đã có nhưng mật khẩu khác thì cập nhật lại mật khẩu mới."""
    username = username.strip().lower()
    user = get_user_by_username(username)
    if not user:
        return create_user(username, password)
    if not verify_password(password, user["password_hash"]):
        updated = fetch_one(
            "update users set password_hash = %s where id = %s returning *",
            [hash_password(password), user["id"]],
        )
        return updated or user
    return user


def get_current_user(request: Request) -> dict | None:
    if hasattr(request.state, "current_user"):
        return request.state.current_user
    user_id = request.session.get("user_id")
    if not user_id:
        user = None
    else:
        user = get_user_by_id(user_id)
    try:
        request.state.current_user = user
    except Exception:
        pass
    return user


def require_user_page(request: Request) -> dict:
    """Dùng cho các trang HTML: chưa đăng nhập thì đưa về /login."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_302_FOUND,
            headers={"Location": f"/login?next={request.url.path}"},
        )
    return user


def require_user_api(request: Request) -> dict:
    """Dùng cho API gọi bằng fetch(): chưa đăng nhập thì trả 401 JSON."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Chưa đăng nhập.")
    return user


def count_saved_items(user_id: str) -> int:
    row = fetch_one("select count(*) as count from saved_items where user_id = %s", [user_id])
    return int(row["count"]) if row else 0


def list_users_with_counts() -> list[dict]:
    from .db import fetch_all

    return fetch_all(
        """
        select u.id, u.username, u.created_at, count(s.id) as saved_count
        from users u
        left join saved_items s on s.user_id = u.id
        group by u.id
        order by u.created_at desc
        """
    )
