"""Đo xem các bản `indacanda_full` "toàn bộ bài kinh" có bị cắt mất nội dung không.

Chạy: .venv\\Scripts\\python.exe dev_whole_unit_audit.py
      .venv\\Scripts\\python.exe dev_whole_unit_audit.py --limit 40
      .venv\\Scripts\\python.exe dev_whole_unit_audit.py --json bao_cao.json

CÂU HỎI SCRIPT NÀY TRẢ LỜI
--------------------------
`indacanda_full` được dựng bằng HAI cách, và chúng không đáng tin như nhau:

- `pdf_heading_boundary` (101 dòng): cắt từ đúng dòng tiêu đề in trong PDF tới tiêu đề
  bài kế tiếp. Đã đối chiếu tay với PDF trên `7. Mahāsamayasuttaṃ`: khớp ~100%, phần
  lệch còn lại là nhiễu của bộ lọc tiêu đề chạy, không phải nội dung.
- `whole_unit` (150 dòng): cắt span nằm GỌN BÊN TRONG hai đoạn neo khớp được, chấp nhận
  mất vài dòng mỗi đầu (`WHOLE_EDGE_SLACK` = 0.15). Đối chiếu tay `6. Pāsādikasuttaṃ`
  với PDF: chỉ còn **71%** nội dung. Nguyên cả đoạn 165 - khúc Sa-di Cunda đi gặp đại
  đức Ānanda - không có bản dịch, dù Pali vẫn hiện đủ bên cạnh.

Giao diện KHÔNG hé gì về chuyện đó: dòng kiểu "cả bài" chỉ in câu chung chung
`section.wholeOfficialHint`, không in tỉ lệ phủ như dòng ghép từ cấp đoạn. Nên cách duy
nhất để biết quy mô là đo.

CÁCH ĐO, VÀ VÌ SAO KHÔNG ĐỌC THẲNG PDF
--------------------------------------
Đọc PDF thì chính xác nhất, nhưng phải dò được dòng tiêu đề tiếng Việt của từng đơn vị -
đúng phần việc mà `indacanda_full_extract` mới làm được cho 3 tập (`sn`, `dn2`, `pts2`).
Ép nó chạy cho 30 tập là viết lại nguyên bộ dò tiêu đề, không phải một phép đo.

Nên ở đây đo gián tiếp bằng ĐỘ DÀI, và tự hiệu chuẩn:

    nền R của tập = trung vị (ký tự Việt / ký tự Pali) của các dòng cấp đoạn NẰM GIỮA MẠCH
                    (cả hai đoạn kề đều có bản dịch, tức chắc chắn không bị gắn dồn)
    độ đủ         = ký tự Việt thực có / (R × ký tự Pali của đơn vị)

Điểm mấu chốt làm phép đo này đứng vững: 101 dòng `pdf_heading_boundary` đóng vai NHÓM
ĐỐI CHỨNG. Chúng đã được kiểm là gần đủ 100%, nên điểm số của chúng cho biết thang đo
này đọc "đủ" ra bao nhiêu. Nếu nhóm `whole_unit` tụt hẳn xuống dưới nhóm đối chứng thì
đó là bằng chứng bị cắt trên diện rộng, chứ không phải tật của thước đo.

Vì vậy ĐỪNG đọc con số tuyệt đối của một dòng là "phủ N%" - hãy đọc nó so với trung vị
của nhóm đối chứng in ở cuối báo cáo. Chú thích chân trang trong PDF cũng được tính vào
ký tự Việt nên đẩy điểm lên; đó là một lý do nữa để so tương đối.

ĐIỂM MÙ PHẢI BIẾT: KHÔNG THẤY DÒNG LẤY THỪA
--------------------------------------------
Thước đo này chỉ bắt được dòng THIẾU chữ. Dòng lấy NHẦM chữ của bài khác thì dài ra,
nên nó chấm điểm CAO - đúng chiều ngược lại với sự thật.

Đã có ca thật: `1. Pāthikasuttaṃ` không hề lọt vào danh sách 50 dòng tệ nhất, nhưng dò
12 điểm rải đều trên nội dung của nó thì 0-33% mới là Pāthika, 58% là Udumbarikasutta,
66-91% là Cakkavattisutta. Quá nửa những gì người đọc thấy dưới nhan đề bài 1 là hai
bài kinh khác. Bản cắt từ PDF ngắn hơn 34% chính vì bỏ phần lấy nhầm đó đi.

Nên "50/150 dòng dưới 80%" là SÀN, không phải tổng số dòng có vấn đề. Muốn bắt loại lỗi
kia thì phải đối chiếu nội dung với PDF, không đo được bằng độ dài.

CHỈ ĐỌC. Không INSERT/UPDATE/DELETE.
"""

import argparse
import json
import statistics
import sys

sys.stdout.reconfigure(encoding="utf-8")

from app.db import fetch_all

SOURCE_WHOLE = "indacanda_full"
SOURCE_PASSAGE = "indacanda"


def volume_baselines() -> dict[str, float]:
    """Nền R của từng tập: trung vị Việt/Pali của các dòng cấp đoạn nằm giữa mạch.

    Phải lấy nền RIÊNG từng tập, không dùng một hằng số chung: văn kệ dịch ra dài hơn
    văn xuôi rất nhiều (đo được nền 1.30 ở Tạng Luật nhưng 3.37 ở tập kệ), nên một hằng
    số chung sẽ vu cho các tập kệ là thừa chữ và cho Tạng Luật là thiếu chữ.
    """
    rows = fetch_all(
        """
        select d.file_name, p.sort_order,
               length(p.pali_text) as pali_len,
               length(h.translated_text) as vi_len
        from documents d
        join passages p on p.document_id = d.id
        left join human_translations h
          on h.passage_id = p.id and h.source = %s
        where d.id in (
          select distinct p2.document_id
          from human_translations h2 join passages p2 on p2.id = h2.passage_id
          where h2.source = %s
        )
        order by d.file_name, p.sort_order
        """,
        [SOURCE_PASSAGE, SOURCE_PASSAGE],
    )
    by_doc: dict[str, list[dict]] = {}
    for row in rows:
        by_doc.setdefault(row["file_name"], []).append(row)

    baselines: dict[str, float] = {}
    for name, doc_rows in by_doc.items():
        interior: list[float] = []
        count = len(doc_rows)
        for i, row in enumerate(doc_rows):
            if not row["pali_len"] or row["vi_len"] is None:
                continue
            previous_missing = i - 1 >= 0 and doc_rows[i - 1]["vi_len"] is None
            next_missing = i + 1 < count and doc_rows[i + 1]["vi_len"] is None
            if not previous_missing and not next_missing:
                interior.append(row["vi_len"] / row["pali_len"])
        # Dưới 20 mẫu thì trung vị không nói lên gì; để tập đó dùng nền gộp.
        if len(interior) >= 20:
            baselines[name] = statistics.median(interior)
    return baselines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=25, help="số dòng tệ nhất in ra")
    parser.add_argument("--json", metavar="FILE", help="ghi toàn bộ kết quả ra JSON")
    args = parser.parse_args()

    baselines = volume_baselines()
    fallback = statistics.median(baselines.values()) if baselines else 1.35
    print(f"Nền R đo được cho {len(baselines)} tập (nền gộp dự phòng: {fallback:.2f})\n")

    # `distinct on (h.id)` là bắt buộc, không phải cho gọn: một khoảng `sort_order` có thể
    # ứng với NHIỀU mục (chương và bài kinh duy nhất trong chương thường trùng khít khoảng,
    # ví dụ `10. Ekādasanipāto` với `1. Kisāgotamītherīgāthā`). Thiếu nó thì cùng một dòng
    # dịch bị đếm hai lần - bản đầu tiên báo 160 dòng `whole_unit` trong khi DB chỉ có 150,
    # và mọi số thống kê phía sau đều lệch theo.
    rows = fetch_all(
        """
        select distinct on (h.id)
               h.id, h.match_method, h.start_sort_order, h.end_sort_order,
               d.file_name,
               length(h.translated_text) as vi_len,
               coalesce(s.title, '(không có mục khớp khoảng)') as title,
               (select sum(length(p.pali_text)) from passages p
                 where p.document_id = h.document_id
                   and p.sort_order between h.start_sort_order and h.end_sort_order) as pali_len,
               (select count(*) from passages p
                 where p.document_id = h.document_id
                   and p.sort_order between h.start_sort_order and h.end_sort_order) as passages
        from human_translations h
        join documents d on d.id = h.document_id
        left join sections s
          on s.document_id = h.document_id
         and s.start_sort_order = h.start_sort_order
         and coalesce(s.end_sort_order, s.start_sort_order) = h.end_sort_order
        where h.source = %s and h.start_sort_order is not null
        -- mục sâu nhất mô tả đúng đơn vị hơn chương bao ngoài, nên ưu tiên `level` lớn
        order by h.id, s.level desc nulls last, s.title
        """,
        [SOURCE_WHOLE],
    )

    results = []
    for row in rows:
        if not row["pali_len"]:
            continue
        base = baselines.get(row["file_name"], fallback)
        fullness = row["vi_len"] / (base * row["pali_len"])
        results.append(
            {
                "title": row["title"],
                "volume": row["file_name"],
                "method": row["match_method"],
                "passages": row["passages"],
                "pali_len": row["pali_len"],
                "vi_len": row["vi_len"],
                "baseline": round(base, 3),
                "fullness": round(fullness, 3),
            }
        )

    by_method: dict[str, list[float]] = {}
    for item in results:
        by_method.setdefault(str(item["method"]), []).append(float(item["fullness"]))

    control = by_method.get("pdf_heading_boundary") or []
    control_median = statistics.median(control) if control else None

    print(f"{'phương pháp':<24}{'số dòng':>9}{'trung vị độ đủ':>17}{'p25':>8}{'dưới 60% nền':>15}")
    print("-" * 74)
    for method, values in sorted(by_method.items(), key=lambda kv: -len(kv[1])):
        values = sorted(values)
        median = statistics.median(values)
        p25 = values[len(values) // 4]
        weak = (
            sum(1 for v in values if control_median and v < control_median * 0.6)
            if control_median
            else 0
        )
        note = "  <- NHÓM ĐỐI CHỨNG" if method == "pdf_heading_boundary" else ""
        print(f"{method:<24}{len(values):>9}{median:>17.0%}{p25:>8.0%}{weak:>15}{note}")

    if control_median:
        print(f"\nNhóm đối chứng (`pdf_heading_boundary`, đã kiểm tay là gần đủ 100%) đọc ra "
              f"{control_median:.0%}.")
        print("Đọc mọi con số dưới đây SO VỚI mốc đó, đừng đọc như phần trăm tuyệt đối.")

    # ── Đuôi TRÊN: bài lấy thừa văn của bài lân cận ───────────────────────────
    # Phần trên chỉ thấy bài THIẾU chữ. Bài lấy nhầm chữ bài khác thì dài ra nên được
    # điểm cao và trốn thoát - ca `1. Pāthikasuttaṃ` chứa hơn nửa là Udumbarika và
    # Cakkavatti mà chưa từng lọt vào danh sách tệ nhất.
    #
    # Tín hiệu bắt được nó: trong CÙNG một tập, tỉ lệ Việt/Pāli của các bài thường chụm
    # (Trường Bộ III đo được 1,45-1,71). So mỗi bài với TRUNG VỊ CỦA CHÍNH TẬP ĐÓ thì
    # bài lấy thừa vọt lên 1,42 lần còn bài bị cắt tụt xuống 0,50 lần, trong khi các bài
    # lành nằm gọn trong 0,94-1,10. Đây là phép so DUY NHẤT trong file bắt được kiểu
    # hỏng lấy thừa.
    #
    # NHƯNG ĐÂY LÀ DIỆN NGHI VẤN, KHÔNG PHẢI KẾT LUẬN. Giả định "trong một tập tỉ lệ
    # chụm" gãy ở tập nào mà bản Pāli lược đoạn không đều: Trường Bộ I viết đủ các đoạn
    # giới ở bài Phạm Võng rồi lược bằng `…pe…` ở các bài sau, còn bản dịch viết đủ mọi
    # lần, nên tỉ lệ trải 1,31-5,03 và gần như bài nào cũng bị cờ. Bài bị cờ phải đối
    # chiếu tay với PDF rồi mới kết luận.
    by_volume: dict[str, list[dict]] = {}
    for item in results:
        if item["pali_len"]:
            item["unit_ratio"] = float(item["vi_len"]) / float(item["pali_len"])
            by_volume.setdefault(str(item["volume"]), []).append(item)

    outliers: list[tuple[float, dict, float]] = []
    for volume, units in by_volume.items():
        if len(units) < 4:  # dưới 4 bài thì trung vị của tập không có nghĩa
            continue
        centre = statistics.median(float(u["unit_ratio"]) for u in units)
        for unit in units:
            relative = float(unit["unit_ratio"]) / centre
            if relative > 1.25 or relative < 0.75:
                outliers.append((relative, unit, centre))

    print("\nDIỆN NGHI VẤN - bài lệch khỏi dải của chính tập mình "
          "(>1.25x = nghi lấy thừa văn bài khác, <0.75x = nghi bị cắt).")
    print("Phải đối chiếu tay với PDF mới kết luận: tập nào bản Pāli lược đoạn không đều "
          "thì dải tự nó đã rộng.")
    if not outliers:
        print("  không có - mọi bài đều nằm trong dải tập của nó.")
    else:
        print(f"{'lệch':>7}  {'tỉ lệ':>6}{'nền tập':>9}  {'tập':<16}{'phương pháp':<22}tên đơn vị")
        print("-" * 100)
        for relative, unit, centre in sorted(outliers, key=lambda row: -abs(row[0] - 1)):
            flag = "THỪA" if relative > 1.25 else "CẮT"
            print(f"{relative:>6.2f}x  {float(unit['unit_ratio']):>6.2f}{centre:>9.2f}  "
                  f"{str(unit['volume']):<16}{str(unit['method']):<22}"
                  f"{str(unit['title'])[:36]}  <- {flag}")

    worst = sorted(results, key=lambda item: item["fullness"])[: args.limit]
    print(f"\n{args.limit} đơn vị điểm thấp nhất:")
    print(f"{'độ đủ':>7}  {'tập':<16}{'đoạn':>6}  {'phương pháp':<22}tên đơn vị")
    print("-" * 96)
    for item in worst:
        flag = ""
        if control_median and float(item["fullness"]) < control_median * 0.6:
            flag = "  <<"
        print(f"{float(item['fullness']):>6.0%}  {str(item['volume']):<16}{item['passages']:>6}  "
              f"{str(item['method']):<22}{str(item['title'])[:44]}{flag}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(
                {"baselines": baselines, "control_median": control_median, "units": results},
                handle,
                ensure_ascii=False,
                indent=2,
            )
        print(f"\nĐã ghi {len(results)} dòng ra {args.json}")


if __name__ == "__main__":
    main()
