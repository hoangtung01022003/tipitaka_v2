import os
import sys

# Đảm bảo đường dẫn import tới app
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import auth


def main():
    username = "capcomanh"
    password = "Nga03@!!"
    print(f"Đang kiểm tra và khởi tạo tài khoản '{username}'...")
    try:
        user = auth.ensure_user(username, password)
        print(f"Thành công! Tài khoản ID: {user.get('id')}, Username: {user.get('username')}")
        print("Tài khoản đã sẵn sàng đăng nhập và đọc toàn bộ bản dịch AI.")
    except Exception as exc:
        print(f"Lỗi khi tạo tài khoản: {exc}")


if __name__ == "__main__":
    main()
