# Tipitaka Python Search

Python full-stack version: FastAPI + Jinja UI + PostgreSQL.

Search mặc định không dùng Gemini. Gemini chỉ dùng khi bấm dịch từng đoạn.

## Nạp bổ sung Phân Tích Đạo II (Indacanda `pts2`) an toàn

Tập này không đánh số đoạn như các PDF còn lại nên có parser riêng; **không** thêm
`--global-align` và không dùng `--all --force`. Chạy trong **CMD** theo đúng hai vòng:

```bat
cd /d D:\code_khach_hang\Lamnhatkhoi_code\nextjs\tipitaka\python_app
chcp 65001 >nul
.venv\Scripts\python.exe -m unittest test_import_pipeline.py
.venv\Scripts\python.exe import_indacanda.py pts2 --dry-run --verbose > kiem_tra_pts2.log 2>&1
type kiem_tra_pts2.log
```

Cổng chấp nhận hiện đã đo trên PDF thật: `122/134 cặp trang cân đoạn`, `258 passage
DB`, giữ lại `391` cặp chưa đủ chắc và `12` cặp trang lệch. Nếu dry-run lệch mạnh
những số này thì dừng, chưa nạp. Nếu đúng, mới chạy:

```bat
.venv\Scripts\python.exe import_indacanda.py pts2 > nap_pts2.log 2>&1
type nap_pts2.log
.venv\Scripts\python.exe dev_batch_stats.py --source indacanda
rem repair_indacanda_spacing.py xử lý indacanda + indacanda_full (nối chữ theo từ điển)
rem và minh_chau (CHỈ luật dấu câu - từ điển nối chữ phá nguồn này, xem CLAUDE.md).
.venv\Scripts\python.exe repair_indacanda_spacing.py
.venv\Scripts\python.exe repair_indacanda_spacing.py --apply
.venv\Scripts\python.exe repair_unicode_artifacts.py
.venv\Scripts\python.exe audit_indacanda_spacing.py --sample-size 30
```

Chỉ khi dòng cuối của kiểm toán là `ĐẠT` mới xuất SQL:

```bat
.venv\Scripts\python.exe export_data.py
dir export_data.sql
```

Importer không dựng `indacanda_full` từ đợt này: còn trang chưa ghép chắc nên gắn
nhãn “trọn bài” sẽ sai. Các cặp bị bỏ được lưu vào `human_translation_unresolved`
để xử lý ở đợt sau, không bị xóa.

## Tách thử bản Indacanda trọn bài theo từng PDF

Ba bộ được tách bằng ba lệnh độc lập; chúng chỉ đọc PDF/DB rồi sinh tệp TXT và
`manifest.json` trong `indacanda_full_preview`. **Không lệnh nào dưới đây ghi DB.**

```bat
cd /d D:\code_khach_hang\Lamnhatkhoi_code\nextjs\tipitaka\python_app
chcp 65001 >nul
.venv\Scripts\python.exe -m pip install -r requirements.txt

.venv\Scripts\python.exe extract_indacanda_full_dn2.py > test_full_dn2.log 2>&1
type test_full_dn2.log

.venv\Scripts\python.exe extract_indacanda_full_pts2.py > test_full_pts2.log 2>&1
type test_full_pts2.log

.venv\Scripts\python.exe extract_indacanda_full_sn.py > test_full_sn.log 2>&1
type test_full_sn.log
```

Mở các thư mục sau để đọc từng bài đã tách:

```txt
indacanda_full_preview\dn2
indacanda_full_preview\pts2
indacanda_full_preview\sn
```

`PASS` nghĩa là đã có tiêu đề đầu, dòng tiêu đề Việt trên trang đối diện, ranh giới
bài kế tiếp và cặp trang Pāli-Việt. `REVIEW` không được phép nạp tự động. Bộ tách cắt
được cả hai bài nằm chung một trang bằng vị trí dòng tiêu đề, giữ phần chữ nhỏ/chú
thích của PDF và bỏ đầu trang/số trang lặp. Không dùng `import_indacanda.py --force`
để nạp các preview này.

Mỗi trang Việt còn được kiểm toán độc lập bằng `pdfplumber`: so toàn bộ từ, riêng
những từ có font nhỏ hơn 85% font trung vị, glyph/Unicode lỗi và vùng ảnh/scan. Nếu
pypdf thiếu nhưng pdfplumber phủ đủ cả hai chiều, trang dùng `pdfplumber_fallback`;
nếu hai engine mâu thuẫn hoặc trang cần OCR thì bài bị hạ xuống `REVIEW`. Xem các số
`small_text_pages`, `fallback_text_pages`, `review_text_pages` và `page_text_audit`
trong manifest; không chỉ nhìn số bài `PASS`.

Sau khi cả ba manifest đã được duyệt, chạy dry-run DB (chỉ đọc):

```bat
.venv\Scripts\python.exe apply_indacanda_full_preview.py
```

Chỉ khi dry-run báo `bị chặn 0` mới ghi trong một transaction:

```bat
.venv\Scripts\python.exe apply_indacanda_full_preview.py --apply
```

Importer kiểm lại SHA-256 của PDF/TXT, section/range và passage neo ngay trước khi
ghi; chỉ nhận `PASS`, luôn bỏ `REVIEW`, không đè `manual`, lưu neo cũ cùng range vào
lịch sử rồi mới dọn. Nguồn đích duy nhất là `indacanda_full`; các dòng trích đoạn
`indacanda` không bị sửa.

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
.venv\Scripts\python.exe import_minhchau.py iti snp ud && .venv\Scripts\python.exe import_indacanda.py --all > import_all.log 2>&1
```
.\.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8002 --reload
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

Lưu ý: Không dùng cờ `-t` và không dùng dấu `>` để tránh lỗi hỏng cấu trúc file do Windows PowerShell. Nên export dưới dạng nén (Custom Format) để an toàn 100%.

```bat
:: Bước 1: Tạo file backup nén bên trong Docker container
docker exec tipitaka-postgres pg_dump -U postgres -d tipitaka_ai -F c --data-only -t documents -t sections -t passages -t translations -t search_logs -f /tmp/tipitaka-data.dump

:: Bước 2: Copy file backup ra ngoài máy host (Windows)
docker cp tipitaka-postgres:/tmp/tipitaka-data.dump .\tipitaka-data.dump
```

Kiểm tra file:

```bat
dir tipitaka-data.dump
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
