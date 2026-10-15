import os
import sys
import shutil
import zipfile
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

ROOT_DIR = Path(__file__).resolve().parent

def find_pg_dump() -> str | None:
    # 1. Kiểm tra trong PATH hệ thống
    which = shutil.which("pg_dump")
    if which:
        return which

    # 2. Tìm kiếm trong các thư mục cài đặt chuẩn của PostgreSQL trên Windows
    pg_bases = [
        Path(r"C:\Program Files\PostgreSQL"),
        Path(r"C:\Program Files (x86)\PostgreSQL"),
    ]
    for base in pg_bases:
        if base.exists():
            for ver_dir in sorted(base.glob("*"), reverse=True):
                candidate = ver_dir / "bin" / "pg_dump.exe"
                if candidate.exists():
                    return str(candidate)
    return None

def main():
    print("=" * 65)
    print("  TIẾN TRÌNH SAO LƯU DỮ LIỆU TOÀN DIỆN (SUTTASEARCH VPS)")
    print("=" * 65)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = ROOT_DIR / f"backup_temp_{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    zip_filename = ROOT_DIR / f"SUTTASEARCH_BACKUP_{timestamp}.zip"

    # 1. Đọc DATABASE_URL từ file .env
    env_file = ROOT_DIR / ".env"
    db_url = None
    if env_file.exists():
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    db_url = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break

    # 2. Thực hiện Dump Cơ sở dữ liệu PostgreSQL
    print("\n[1/3] Đang sao lưu Cơ sở dữ liệu PostgreSQL...")
    pg_dump_bin = find_pg_dump()
    db_dump_success = False

    if not pg_dump_bin:
        print("  [!] CẢNH BÁO: Không tìm thấy lệnh pg_dump trong hệ thống.")
        print("      Bạn có thể sao lưu DB bằng pgAdmin hoặc kiểm tra lại đường dẫn cài PostgreSQL.")
    elif not db_url:
        print("  [!] CẢNH BÁO: Không tìm thấy DATABASE_URL trong file .env.")
    else:
        try:
            parsed = urlsplit(db_url)
            host = parsed.hostname or "localhost"
            port = str(parsed.port or 5432)
            user = parsed.username or "postgres"
            password = parsed.password or ""
            dbname = parsed.path.lstrip("/") or "tipitaka_ai"

            dump_custom_file = backup_dir / f"{dbname}.dump"
            dump_sql_file = backup_dir / f"{dbname}.sql"

            env_copy = os.environ.copy()
            if password:
                env_copy["PGPASSWORD"] = password

            print(f"  -> Kết nối {host}:{port}, Database: '{dbname}', User: '{user}'...")
            
            # Xuất định dạng custom (.dump) nén tối ưu
            cmd_custom = [
                pg_dump_bin,
                "-h", host,
                "-p", port,
                "-U", user,
                "-d", dbname,
                "-F", "c",
                "-b",
                "-v",
                "-f", str(dump_custom_file)
            ]
            res1 = subprocess.run(cmd_custom, env=env_copy, capture_output=True, text=True)

            if res1.returncode == 0 and dump_custom_file.exists():
                size_mb = dump_custom_file.stat().st_size / (1024 * 1024)
                print(f"  [OK] Đã xuất file binary: {dump_custom_file.name} ({size_mb:.2f} MB)")
                db_dump_success = True
            else:
                print(f"  [!] Lỗi khi xuất file dump: {res1.stderr[:300]}")

            # Xuất thêm file plain SQL dự phòng
            cmd_sql = [
                pg_dump_bin,
                "-h", host,
                "-p", port,
                "-U", user,
                "-d", dbname,
                "-F", "p",
                "-f", str(dump_sql_file)
            ]
            res2 = subprocess.run(cmd_sql, env=env_copy, capture_output=True, text=True)
            if res2.returncode == 0 and dump_sql_file.exists():
                size_mb = dump_sql_file.stat().st_size / (1024 * 1024)
                print(f"  [OK] Đã xuất file script SQL: {dump_sql_file.name} ({size_mb:.2f} MB)")
        except Exception as exc:
            print(f"  [!] Xảy ra lỗi khi chạy pg_dump: {exc}")

    # 3. Thu thập các tệp dữ liệu quan trọng
    print("\n[2/3] Đang thu thập các file cấu hình và dữ liệu thư viện...")
    
    # Copy file .env
    if env_file.exists():
        shutil.copy2(env_file, backup_dir / ".env")
        print("  [OK] Đã sao lưu: .env")

    # Copy thư mục app/data (library_files, json settings, etc.)
    data_dir = ROOT_DIR / "app" / "data"
    if data_dir.exists():
        dest_data = backup_dir / "app_data"
        shutil.copytree(data_dir, dest_data, dirs_exist_ok=True)
        files_count = len(list(dest_data.rglob("*.*")))
        print(f"  [OK] Đã sao lưu: app/data/ ({files_count} files, gồm sách PDF và cài đặt)")

    # Copy chứng chỉ SSL nếu có
    certs_dir = ROOT_DIR / "nginx_config" / "certs"
    if certs_dir.exists():
        dest_certs = backup_dir / "ssl_certs"
        shutil.copytree(certs_dir, dest_certs, dirs_exist_ok=True)
        print("  [OK] Đã sao lưu: nginx_config/certs/ (chứng chỉ SSL)")

    # 4. Đóng gói thành file ZIP duy nhất
    print(f"\n[3/3] Đang nén thành file zip duy nhất...")
    with zipfile.ZipFile(zip_filename, "w", zipfile.ZIP_DEFLATED) as zipf:
        for file_path in backup_dir.rglob("*"):
            if file_path.is_file():
                arcname = file_path.relative_to(backup_dir)
                zipf.write(file_path, arcname)

    # Dọn dẹp thư mục tạm
    shutil.rmtree(backup_dir, ignore_errors=True)

    zip_size_mb = zip_filename.stat().st_size / (1024 * 1024)
    print("\n" + "=" * 65)
    print("  HOÀN TẤT SAO LƯU THÀNH CÔNG!")
    print(f"  File backup hoàn chỉnh: {zip_filename.name}")
    print(f"  Đường dẫn: {zip_filename}")
    print(f"  Dung lượng: {zip_size_mb:.2f} MB")
    print("=" * 65)
    print("\nBạn chỉ cần copy duy nhất file ZIP này về máy hoặc sang VPS mới!")

if __name__ == "__main__":
    main()
