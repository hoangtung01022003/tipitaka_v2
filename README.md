# Tipitaka Python Search

Python full-stack version: FastAPI + Jinja UI + PostgreSQL.

Search mặc định không dùng Gemini. Gemini chỉ dùng khi bấm dịch từng đoạn.

## Chạy local

```bat
cd /d D:\code_khach_hang\Lamnhatkhoi_code\nextjs\tipitaka
docker compose up -d
cd python_app
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --port 8001
```
.\python_app\.venv\Scripts\uvicorn.exe app.main:app --app-dir python_app --host 127.0.0.1 --port 8002 --reload
Mở:

```txt
http://localhost:8001
```

Nếu muốn dùng Supabase, đổi `DATABASE_URL` trong `python_app/.env`.
