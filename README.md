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

## 1. Vào project

```bat
cd /d D:\code_khach_hang\Lamnhatkhoi_code\nextjs\tipitaka
```

## 2. Rollback/xóa sạch local Docker

```bat
docker exec tipitaka-postgres psql -U postgres -d tipitaka_ai -c "truncate table search_logs, translations, passages, sections, documents restart identity cascade;"
```

## 3. Import XML lại vào local Docker

```bat
set DATABASE_URL=postgresql://postgres:postgres@localhost:5434/tipitaka_ai
npm run db:migrate
npm run import:xml
```

## 4. Kiểm tra local

```bat
docker exec tipitaka-postgres psql -U postgres -d tipitaka_ai -c "select count(*) from public.documents; select count(*) from public.sections; select count(*) from public.passages; select count(*) from public.translations; select count(*) from public.search_logs;"
```

Kỳ vọng gần như:

```txt
documents    217
sections     ...
passages     ~379692
translations 0
search_logs  0
```

## 5. Export SQL mới từ local

Lưu ý: lần này không dùng `--disable-triggers`.

```bat
docker exec -t tipitaka-postgres pg_dump -U postgres -d tipitaka_ai --data-only --table=documents --table=sections --table=passages --table=translations --table=search_logs > tipitaka-data.sql
```

Kiểm tra file:

```bat
dir tipitaka-data.sql
```

## 6. Rollback/xóa sạch Supabase

Dùng pooler để tránh lỗi IPv6:

```bat
docker exec -it tipitaka-postgres psql "postgresql://postgres.sygsiuqpldtaysgxoosj:%40Tung0355881907@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres?sslmode=require" -c "truncate table search_logs, translations, passages, sections, documents restart identity cascade;"
```

## 7. Copy SQL vào Docker

```bat
docker cp tipitaka-data.sql tipitaka-postgres:/tmp/tipitaka-data.sql
```

## 8. Import SQL mới lên Supabase

```bat
docker exec -it tipitaka-postgres psql "postgresql://postgres.sygsiuqpldtaysgxoosj:%40Tung0355881907@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres?sslmode=require" -v ON_ERROR_STOP=1 -f /tmp/tipitaka-data.sql
```

## 9. Kiểm tra Supabase

```bat
docker exec -it tipitaka-postgres psql "postgresql://postgres.sygsiuqpldtaysgxoosj:%40Tung0355881907@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres?sslmode=require" -c "select count(*) from public.documents; select count(*) from public.sections; select count(*) from public.passages; select count(*) from public.translations; select count(*) from public.search_logs;"
```

## 10. Test nhanh đoạn bị lỗi

Sau khi import xong, search lại đoạn `adinnādānaṃ` hoặc `saṅgharatanaṃ`. Bản đúng phải không còn kiểu chữ in đậm nhảy lên đầu câu nữa.