# Bàn giao mở rộng `indacanda_full` cho toàn bộ PDF

> Cập nhật: 2026-08-15 (Asia/Saigon)
>
> Đọc file này trước khi tiếp tục phần dữ liệu Indacanda. Sau đó đọc toàn bộ
> `CLAUDE.md`, đặc biệt các mục **Human translations** và **Indacanda PDF importer**.

## 1. Mục tiêu người dùng

Người dùng muốn đạt quy tắc dữ liệu sau trên web:

> Khi kết quả có bản dịch Tỳ Khưu Indacanda dạng **trích đoạn ngắn**, người đọc phải
> có chỗ mở bản dịch **toàn bộ bài kinh/đơn vị tương ứng**.

Không được dựng “toàn bộ bài” từ vài đoạn khớp ở giữa. Phần full phải được cắt từ
đầu đến cuối theo ranh giới in thật trong PDF. Phải giữ chữ nhỏ/chú thích/cước chú
thuộc nội dung PDF và sửa lỗi text layer kiểu `Ch ánh`, `thu ộc`, `kho ảng`.

Người dùng chạy lệnh ghi DB/VPS. AI được tự đọc, test, tạo preview và chạy dry-run,
nhưng không tự ý nạp VPS. Lệnh gửi cho người dùng phải là **CMD**, dùng `cd /d`,
không dùng `Set-Location`, `Get-Item`, `Select-Object` trong lệnh họ phải chạy.

## 2. Trạng thái hiện tại - phần đã hoàn tất

Ba PDF thử nghiệm đã tách theo ranh giới tiêu đề in, kiểm toán và nạp DB local:

| Volume | PDF | PASS | REVIEW | Trang Việt audit | Trang có chữ nhỏ |
|---|---|---:|---:|---:|---:|
| `dn2` | `11_D_02.pdf` | 10/10 | 0 | 282 | 116 |
| `pts2` | `ttpv_38_Pts_II.pdf` | 20/20 | 0 | 134 | 2 |
| `sn` | `29_Sn.pdf` | 71/73 | 2 | 176 | 48 |

Hai mục SN vẫn REVIEW và **không được nạp**:

- `16. Piṅgiyamāṇavapucchā`: không có ranh giới kết thúc độc lập vì mục kế tiếp
  không dò được tiêu đề riêng.
- `Pārāyanatthutigāthā`: không dò được một tiêu đề in duy nhất trong PDF.

Kết quả audit ba tập:

- 592 trang Việt được so bằng `pypdf` và `pdfplumber`.
- 166 trang có chữ nhỏ (font tối thiểu đã thấy: 6.48 pt).
- 100% word coverage và small-word coverage ở các trang được nhận PASS.
- 0 trang fallback, 0 trang text REVIEW, 0 trang ảnh cần OCR, 0 Unicode hazard.
- Sau vòng làm sạch cuối: 0 mẫu còn sót của `kho ảng`, `chư ớng`, `trư ờng`,
  `"( "` và `" )"` trong preview mới.

Unit test hiện tại:

```text
Ran 22 tests
OK
```

## 3. DB local sau khi đã nạp ba tập

Đợt nạp thành công:

```text
pdf_heading_boundary-20260815-013512-284691
```

Kết quả apply:

```text
89 insert + 12 update = 101 dòng thay đổi
46 neo cũ cùng exact range đã lưu lịch sử rồi xóa
0 blocked
2 REVIEW skipped
```

Kiểm tra sau apply:

| Chỉ số | Giá trị |
|---|---:|
| `indacanda_full` tổng | 251 |
| `match_method=pdf_heading_boundary` | 101 |
| `match_method=whole_unit` cũ | 150 |
| range trùng | 0 |
| range null/đảo đầu-cuối | 0 |
| history do batch mới | 58 |

58 history = 46 stale anchors bị dọn + 12 dòng được update (trigger archive tự lưu).

Passage-level hiện có:

```text
indacanda: 30,590 dòng
```

Trong đó chỉ 5,919 passage-level rows đang nằm trong một range `indacanda_full`
(khoảng 19.35%). Còn 24,671 dòng trích đoạn chưa được full cover. Vì thế hiện tại
**chưa thể khẳng định** “có trích đoạn thì chắc chắn có full” cho toàn kho.

DB local đã cập nhật, nhưng dữ liệu mới **chưa được export và chưa nạp lên VPS** tại
thời điểm viết file này.

## 4. Các file đã tạo/sửa và vai trò

### Bộ tách preview

- `indacanda_full_extract.py`
  - Đọc PDF song ngữ theo trang Pāli/Việt.
  - Map DB section tới tiêu đề Pāli in trong PDF.
  - Dò đúng dòng tiêu đề Việt trên trang đối diện.
  - Cắt từ tiêu đề Việt của mục hiện tại tới tiêu đề Việt mục kế tiếp.
  - Xử lý được hai mục cùng một trang.
  - Giữ chữ nhỏ/cước chú; bỏ running header và số trang rõ ràng.
  - So toàn bộ text bằng `pypdf` + `pdfplumber`.
  - Ghi SHA-256 của PDF và từng TXT vào manifest.
  - Chỉ preview, tuyệt đối không có INSERT/UPDATE/DELETE DB.

- Wrappers hiện có:
  - `extract_indacanda_full_dn2.py`
  - `extract_indacanda_full_pts2.py`
  - `extract_indacanda_full_sn.py`

- Output bị `.gitignore`:
  - `indacanda_full_preview/<volume>/manifest.json`
  - `indacanda_full_preview/<volume>/*.txt`

### Bộ nạp preview đã duyệt

- `apply_indacanda_full_preview.py`
  - Không có `--apply`: read-only dry-run.
  - Có `--apply`: ghi trong một transaction.
  - Recheck PDF hash, TXT hash, Unicode, spacing, section, document, range, anchor.
  - Chỉ nhận `status=PASS`; luôn bỏ REVIEW.
  - Chỉ ghi source `indacanda_full`; không sửa `indacanda`.
  - Không ghi đè `match_method=manual`.
  - Nếu có full row chồng range nhưng khác exact range: BLOCK toàn transaction.
  - Exact-range stale anchor được archive rồi mới delete.
  - Chạy lại idempotent: sau apply thành công sẽ là 101 `unchanged`, không nhân bản.

### Provenance/migration/export

- `../db/migrations/006_pdf_heading_boundary.sql`
  - Thêm rank cho phương pháp mới.
- `../db/migrations/004_match_provenance.sql`
  - Cũng đã được sửa để biết phương pháp mới, tránh importer cũ chạy lại 004 làm
    mất rank của 006.
- Thứ hạng hiện tại:

```text
manual 50
pdf_heading_boundary 45
strict_unique 40
global_align 30
whole_unit 20
heuristic 10
null/khác 0
```

- `import_indacanda.py`
  - Apply thêm migration 006.
  - `mend_spacing()` bổ sung các ca audit cuối và làm sạch khoảng trắng sát dấu câu.
- `export_data.py`
  - Export cả migration 006.
- `test_import_pipeline.py`
  - 22 test, có test SHA-256/tampered TXT và spacing cuối.
- `README.md` và `CLAUDE.md`
  - Đã cập nhật quy trình preview/dry-run/apply.

## 5. Lệnh đã dùng và có thể chạy lại

Chạy test:

```bat
cd /d D:\code_khach_hang\Lamnhatkhoi_code\nextjs\tipitaka\python_app
chcp 65001 >nul
.venv\Scripts\python.exe -m unittest test_import_pipeline.py
```

Tách ba tập hiện có:

```bat
.venv\Scripts\python.exe extract_indacanda_full_dn2.py
.venv\Scripts\python.exe extract_indacanda_full_pts2.py
.venv\Scripts\python.exe extract_indacanda_full_sn.py
```

Dry-run/apply ba tập:

```bat
.venv\Scripts\python.exe apply_indacanda_full_preview.py
.venv\Scripts\python.exe apply_indacanda_full_preview.py --apply
```

Không cần apply lại batch ba tập. Nếu vô tình chạy lại, script phải báo 101 unchanged
và không tạo translation trùng.

Sau khi toàn bộ phần cần nạp đã xong mới export:

```bat
cd /d D:\code_khach_hang\Lamnhatkhoi_code\nextjs\tipitaka\python_app && chcp 65001 >nul && .venv\Scripts\python.exe export_data.py && dir export_data.sql
```

Không đưa lệnh nạp VPS cho người dùng trước khi kiểm tra số dòng/file SQL mới.

## 6. Yêu cầu mới nhất: chạy toàn bộ 30 PDF

Người dùng xác nhận ba tập thử nghiệm đã thành công và muốn mở rộng sang toàn bộ
PDF. Tất cả 30 file hiện đã có trong `.indacanda_pdf`; không cần tải lại.

Các volume trong `import_indacanda.VOLUMES`:

```text
sn kn1 thag
pr pc1 pc2 mv1 mv2 cv1 cv2 par1 par2
vvpv ap1 ap2 ap3 bvcp ja1 ja2 ja3
nidd1 nidd2 pts1 pts2 net pet mil
dn1 dn2 dn3
```

Đã xong: `dn2 pts2 sn`.

Còn cần làm: 27 volume còn lại.

### PDF cache hiện có

```text
10_D_01.pdf, 11_D_02.pdf, 12_D_03.pdf
28_Khp-Dh-Ud-It.pdf, 29_Sn.pdf, 30_Vv_Pv.pdf, 31_Thag_Thig.pdf
32_Ja_I.pdf, 33_Ja_II.pdf, 34_Ja_III.pdf
35_Nidd_I.pdf, 36_Nidd_II.pdf, 43_Net.pdf, 45_Mil.pdf
ttpv_01_Pr.pdf ... ttpv_09_Par_II.pdf
ttpv_37_Pts_I.pdf, ttpv_38_Pts_II.pdf
ttpv_39_Ap_I.pdf, ttpv_40_Ap_II.pdf, ttpv_41_Ap_III.pdf
ttpv_42_Bv&Cp.pdf, ttpv_44_Pet.pdf
```

## 7. Vì sao không được chỉ thêm toàn bộ volume vào `SUPPORTED_VOLUMES`

Ba tập thử nghiệm không đại diện cho tất cả bố cục. Nếu bật vòng lặp mù, có thể ghi
“toàn bộ bài” nhưng thật ra là cả chương hoặc nửa bài.

Các vấn đề đã đo từ DB:

1. Một số PDF dùng chung document DB, nhưng mỗi PDF chỉ chứa một đoạn của document:
   - `pc1` và `pc2` cùng `vin02m1.mul.xml`.
   - `ap1`, `ap2`, `ap3` cùng hai document Apadāna.
   - `ja1`, `ja2`, `ja3` cùng hai document Jātaka.
   Nếu load mọi section của document cho mỗi PDF, mỗi tập sẽ đòi lại toàn bộ 603/150
   mục và sinh hàng trăm REVIEW giả hoặc map sai mục cùng tên.

2. `_is_whole_unit_title()` cũ chỉ nhận các suffix được audit:

```text
sutta/suttam, jatakam, apadanam, gatha, vatthu,
cariya, vamso, sikkhapadam, parajikam
```

Nó cố tình không nhận `vaggo`, `nipato`, `kandam`, `khandhakam`, `bhanavaro`,
`katha`, `vibhango` vì đa số là chương. Nhưng nhiều tập như `cv1/cv2`, `nidd1`,
`pts1`, `net`, `pet`, `mil` không có candidate theo suffix cũ. Phải xác định đúng
đơn vị người đọc của từng tập, không nới suffix toàn cục.

3. Counts candidate từ DB bằng selector cũ (chỉ để profile, không phải target cuối):

```text
sn 54; kn1 197; thag 337
pr 49; pc1 217; pc2 217; mv1 58; mv2 58
cv1 0; cv2 0; par1 2; par2 2
vvpv 135; ap1/ap2/ap3 mỗi tập nhìn thấy 603
bvcp 60; ja1/ja2/ja3 mỗi tập nhìn thấy 150
nidd1 0; nidd2 2; pts1 0; pts2 selector cũ 0
net 0; pet 0; mil 0
dn1 13; dn2 10; dn3 11
```

Riêng `sn` extractor mới dùng level 5 và thêm māṇavapucchā/gāthā nên target thật là
73, không phải 54. `pts2` có rule level 5 + start sort >=1167 nên target thật là 20.

4. Tiêu đề cùng tên có thể xuất hiện nhiều lần trong một hoặc nhiều phẩm. Phải ghép
theo thứ tự in khi số lần xuất hiện khớp, không chọn ngẫu nhiên một trang.

5. Running headers cũng viết hoa và có thể giống tiêu đề. `find_headings()` chỉ được
nhận một trang duy nhất hoặc mapping tuần tự có đủ bằng chứng.

## 8. Công việc profiling vừa bắt đầu nhưng bị dừng

Một lệnh read-only đã bắt đầu đọc 27 PDF để đo:

- tổng số trang;
- số trang Việt;
- candidate units theo selector cũ;
- số tiêu đề map được;
- level và page span.

Người dùng dừng turn khi lệnh còn chạy nên **không có output profile hoàn chỉnh được
lưu lại**. Không coi profiling đó là đã xong. Lần sau nên chạy theo nhóm 3-5 volume
và ghi kết quả ra một file JSON/MD trong workspace để không mất khi turn bị ngắt.

Không có DB write trong lệnh profiling vừa bị dừng.

## 9. Thiết kế khuyến nghị để mở rộng an toàn

### 9.1 Tạo cấu hình riêng, không nhồi thêm `if volume == ...`

Nên thêm một cấu trúc kiểu `FULL_VOLUME_CONFIG`, ví dụ:

```python
FULL_VOLUME_CONFIG = {
    "dn1": {
        "unit_rule": "numbered_suffix",
        "levels": [4],
        "suffixes": ["sutta", "suttam"],
        "body_start": ...,       # nếu đã audit
        "body_end": ...,
        "heading_aliases": {},
    },
}
```

Mỗi exception/alias phải scope theo volume. Không đưa alias của một sách vào fuzzy
matching toàn cục.

### 9.2 Chia volume theo nhóm bố cục

Thứ tự nên triển khai:

1. `dn1`, `dn3`: gần bố cục `dn2`; khả năng tái sử dụng cao nhất.
2. `kn1`, `thag`, `vvpv`, `bvcp`: nhiều document trong một PDF nhưng unit suffix rõ.
3. `ap1-3`, `ja1-3`, `pc1-2`: shared DB documents; phải lọc subset bằng tiêu đề/page
   span của chính PDF.
4. `pr`, `mv1-2`, `par1-2`: Vinaya suffix có một phần rõ.
5. `cv1-2`, `nidd1-2`, `pts1`, `net`, `pet`, `mil`: unit semantic không nằm trong
   suffix cũ; phải đọc hierarchy và nhìn trực quan trang chuyển mục trước.

### 9.3 Chọn subset cho shared-document volumes

Không lấy toàn bộ candidate DB rồi buộc tất cả phải có heading trong từng PDF.

Hướng an toàn:

1. Tạo toàn bộ candidate có thể có của document.
2. Quét tiêu đề Pāli in thật trong PDF.
3. Chỉ chọn một chuỗi DB section liên tục, tăng dần `start_sort_order`, có tiêu đề
   map rõ và nằm giữa heading đầu/cuối của file.
4. Section giữa hai heading có thể được nhận chỉ khi title/order/hierarchy xác nhận;
   không suy từ số trang đơn thuần.
5. Nếu hai candidate cạnh tranh một printed heading hoặc chuỗi bị đảo order: REVIEW
   cả khu vực, không đoán.

### 9.4 Xác định đơn vị cho các sách không có suffix cũ

Với `cv`, `nidd`, `pts1`, `net`, `pet`, `mil`:

1. Query toàn bộ `sections` theo `level`, `title`, `start_sort_order`, `end_sort_order`,
   `source_path`.
2. Đếm level/suffix và kiểm tra parent-child containment.
3. Render các trang đầu một đơn vị, trang chuyển đơn vị và trang cuối để nhìn bố cục.
4. Chọn level sao cho:
   - các range anh em không chồng lấn;
   - phủ đúng nội dung thân sách;
   - không phải chapter quá lớn nếu bên trong còn các đơn vị người đọc rõ;
   - không phải paragraph con quá nhỏ.
5. Ghi rule riêng theo volume và test regression bằng expected unit count.

### 9.5 Cổng PASS bắt buộc cho mọi volume

Một item chỉ PASS khi đồng thời:

- DB section/document/range hợp lệ;
- heading Pāli đầu map rõ và duy nhất;
- dòng heading Việt đầu map rõ;
- heading mục kế tiếp hoặc printed end marker xác định được điểm cuối;
- page parity/paired Vietnamese ratio đạt ngưỡng;
- text không quá ngắn;
- tất cả Vietnamese pages đạt text audit;
- `mend_spacing(text) == text` sau khi sinh preview;
- không còn `unicode_artifacts()`;
- TXT/PDF có SHA-256 trong manifest.

Không hạ chuẩn để làm số PASS đẹp hơn. REVIEW là kết quả hợp lệ và phải được giữ.

### 9.6 Kiểm tra trực quan bắt buộc

Với mỗi nhóm bố cục mới, render ít nhất:

- trang mở mục đầu;
- một trang có chữ nhỏ/cước chú;
- trang hai mục dùng chung nếu có;
- ranh giới cuối một mục/đầu mục tiếp theo;
- trang kết thúc thân sách.

Đối chiếu TXT preview với trang render. Không chỉ tin text extraction.

## 10. Lệnh chạy toàn bộ nên được thiết kế thế nào

Sau khi 27 volume đã có config và test, nên tạo một wrapper duy nhất, ví dụ:

```text
extract_indacanda_full_all.py
```

Yêu cầu wrapper:

- Mặc định chạy mọi volume theo thứ tự đã cấu hình.
- Có thể nhận subset để resume: `... dn1 dn3 kn1`.
- Mỗi volume có log/status độc lập.
- Crash kỹ thuật phải trả exit code khác 0.
- Volume có REVIEW không làm mất preview của volume PASS khác, nhưng summary cuối
  phải liệt kê rõ PASS/REVIEW và tuyệt đối không apply REVIEW.
- Nên có `--resume` hoặc kiểm hash để bỏ qua manifest còn hợp lệ.
- Tạo `indacanda_full_preview/all_summary.json` chứa tổng hợp unit/page/audit/hash.

Sau đó mở rộng `apply_indacanda_full_preview.py`:

- Không hardcode default `dn2 pts2 sn` nữa.
- Đọc các volume đã xuất hiện trong all-summary hoặc nhận danh sách rõ.
- Recheck tất cả manifest/hash ở đầu.
- Build toàn bộ DB plan trước; nếu bất kỳ BLOCKED nào thì không ghi một phần.
- Chỉ `--apply` mới transaction.

Người dùng cuối muốn một lệnh CMD. Chỉ gửi lệnh sau khi dry-run toàn bộ báo 0 blocked.

## 11. Kiểm thử phải bổ sung

Tối thiểu thêm test cho:

- unit selection/count của mỗi volume;
- shared document chỉ chọn subset đúng của từng PDF;
- duplicated titles ghép theo order;
- running header không bị chọn làm boundary;
- mục đầu/cuối cùng;
- hai mục cùng trang;
- printed end marker;
- trang có footnote font nhỏ;
- PDF/hash/TXT tampering;
- range overlap làm apply BLOCK;
- `manual` không bị đè;
- apply lại -> unchanged, không duplicate;
- 004 chạy sau 006 vẫn giữ rank 45.

Sau mỗi đợt:

```bat
.venv\Scripts\python.exe -m unittest test_import_pipeline.py
.venv\Scripts\python.exe apply_indacanda_full_preview.py
```

Không chạy `--apply` cho các volume mới trước khi người dùng xem dry-run.

## 12. Các lỗi đã gặp - không lặp lại

1. **PowerShell/CMD lẫn lộn**
   - Người dùng dùng CMD.
   - `Set-Location`, `Get-Item`, `Select-Object` gây lỗi trong CMD.
   - Dùng `cd /d`, `dir`, `type`.

2. **Xóa cả preview dir trên Windows**
   - `shutil.rmtree(volume_dir)` từng lỗi WinError 5 vì antivirus/editor giữ file.
   - Đã sửa: không xóa cả thư mục; ghi đè file được manifest tham chiếu.
   - Không đưa `rmtree` trở lại.

3. **Thiếu `pdfplumber`**
   - Đã thêm `pdfplumber==0.11.9` vào requirements.
   - Nếu thiếu, chạy `pip install -r requirements.txt` trước.

4. **Spacing trong text layer**
   - Không nối mọi cặp chữ theo regex mù; sẽ phá cụm đúng như `mà ra`, `Tissa Metteyya`.
   - Dùng từ điển + confirmed exceptions + regression tests.
   - Các ca đã sửa gồm `Ch ánh`, `Sikh ī`, `thu ộc`, `kho ảng`, `chư ớng`, `trư ờng`.

5. **Anchor assembler cũ không phải full thật**
   - `write_whole_suttas()` cũ dựng span từ passage đã match và cho phép hụt mép 15%.
   - Không dùng output `whole_unit` cũ làm chuẩn để tuyên bố “toàn bộ”.
   - Phương pháp mới phải là `pdf_heading_boundary`.

6. **Không xóa dữ liệu cũ trước khi nạp**
   - Upsert/rank/archive đã xử lý cộng dồn.
   - Không truncate `human_translations`.
   - Không dùng `--force` theo nghĩa delete/rebuild.

## 13. Git/deployment cần chú ý

Có hai Git repository lồng nhau:

- Root `tipitaka/.git`.
- `tipitaka/python_app/.git` riêng.

`python_app` được deploy độc lập lên Windows VPS, nhưng migration nằm ở sibling
`../db/migrations` thuộc root repository. Hiện root Git nhìn nhiều file `python_app`
như untracked/modified, còn nested Git có status riêng. Không stage/commit/reset các
thay đổi không liên quan của người dùng.

Migration 004/006 phải có mặt khi export/deploy. Nếu chỉ đẩy nested `python_app` mà
không mang sibling migration sang môi trường chạy, `export_data.py` sẽ thiếu file.
Kiểm tra quy trình thực tế trước khi commit/push.

## 14. Việc nên làm ngay ở phiên tiếp theo

1. Kiểm tra không còn tiến trình profiling cũ; turn trước bị abort giữa lệnh read-only.
2. Tạo profiler có output bền vững (`json`), chạy theo nhóm 3-5 volume.
3. Làm `dn1` + `dn3` trước và so trực quan với `dn2`.
4. Sau khi hai tập Dīgha đạt, trừu tượng hóa config thay vì thêm nhiều `if`.
5. Làm các nhóm suffix rõ, sau đó shared-document, cuối cùng các sách không có suffix.
6. Chạy full preview + text audit, tạo all-summary.
7. Chạy DB dry-run toàn bộ và báo người dùng số insert/update/unchanged/blocked.
8. Chỉ khi người dùng đồng ý mới gửi một lệnh `--apply`.
9. Sau apply kiểm tra:
   - counts by method/source;
   - duplicate ranges = 0;
   - invalid ranges = 0;
   - coverage passage-level -> full tăng bao nhiêu;
   - batch/history đúng kế hoạch.
10. Cuối cùng mới export SQL và nạp VPS, rồi test web/browser.

## 15. Tiêu chí hoàn thành thật sự

Không dùng “đã chạy hết 30 script” làm tiêu chí. Chỉ hoàn thành khi:

- mọi PDF đã được profile;
- mỗi volume có unit rule được giải thích/test;
- toàn bộ item PASS đã qua boundary + text audit + hash;
- REVIEW được báo rõ, không bị nạp;
- DB dry-run 0 blocked;
- apply không tạo range trùng/lỗi;
- coverage mới được đo;
- SQL được export/nạp VPS;
- web được test rằng full button chỉ hoạt động khi range full thật sự tồn tại.

