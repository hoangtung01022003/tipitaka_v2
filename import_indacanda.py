"""Nạp bản dịch tiếng Việt của Tỳ Khưu Indacanda (Tam Tạng Song Ngữ Pāli - Việt).

NGUỒN: tamtangpaliviet.net. Khách KHÔNG gửi file nào của bản này; đây là nguồn công khai
do chính nhóm dịch phát hành, phủ đúng phần đang trống: Tạng Luật và Tiểu Bộ
(Ngài Sujato không dịch, SuttaCentral cũng thiếu tiếng Việt).

CÁCH GHÉP - đây là nguồn tiếng Việt ghép được CHÍNH XÁC NHẤT
-------------------------------------------------------------
Sách in song ngữ theo trang đối diện, và đã kiểm tận mắt trên file thật:

    trang 44  PALI  câu 6,7,8,9,10,11   "Suttanipāte Uragavaggo | Uragasuttaṃ"
    trang 45  VIỆT  câu 6,7,8,9,10,11   "Kinh Tập - Phẩm Rắn    | Kinh Rắn"

Trang chẵn Pali, trang lẻ Việt, CÙNG số câu kệ. Nhờ vậy không phải đoán theo thứ tự:
- ghép cặp trang Pali <-> trang Việt kề nhau,
- trong cặp đó ghép câu kệ theo SỐ,
- rồi tìm câu Pali đó trong DB bằng chính nội dung (giống cách làm với bản Sujato).

Khác hẳn bản Minh Châu (chỉ ghép được cấp bài kinh) và bản Tịnh Sự (số đoạn lệch 19
so với bản gốc vì dịch từ ấn bản Thái).

Chạy:
    python import_indacanda.py sn          # Kinh Tập
    python import_indacanda.py --list      # xem các tập đã cấu hình
    python import_indacanda.py sn --dry-run --verbose
"""

import argparse
import urllib.parse
import logging
import re
import sys
import urllib.request
import warnings
from pathlib import Path

# line_buffering: chay nap mat vai phut moi tap, khong bat buoc phai xem qua terminal.
# De mac dinh thi Python dem stdout khi output khong phai terminal (ghi ra file, chay nen),
# nen nhin vao chi thay file rong va tuong la treo.
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
warnings.filterwarnings("ignore")
logging.getLogger("pypdf").setLevel(logging.CRITICAL)

from pypdf import PdfReader

from app.db import execute, fetch_all
from app.normalize import normalize_pali

BASE = "https://www.tamtangpaliviet.net/TTPV"
CACHE = Path(__file__).resolve().parent / ".indacanda_pdf"
SOURCE_ID = "indacanda"
LANGUAGE = "vi"

VIETNAMESE_CHARS = set("àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ")

# Tap nao ghep vao nhung tai lieu nao trong DB. Chi cau hinh nhung tap da doi chieu duoc.
VOLUMES: dict[str, dict] = {
    "sn": {"file": "29_Sn.pdf", "label": "Kinh Tập (Suttanipāta)", "docs": ["s0505m.mul.xml"]},
    "kn1": {"file": "28_Khp-Dh-Ud-It.pdf", "label": "Tiểu Tụng, Pháp Cú, Phật Tự Thuyết, Như Vậy",
            "docs": ["s0501m.mul.xml", "s0502m.mul.xml", "s0503m.mul.xml", "s0504m.mul.xml"]},
    "thag": {"file": "31_Thag_Thig.pdf", "label": "Trưởng Lão Kệ, Trưởng Lão Ni Kệ",
             "docs": ["s0508m.mul.xml", "s0509m.mul.xml"]},
    # TẠNG LUẬT - phần DB đang trống hoàn toàn: 12.957 đoạn, không có bản dịch tiếng Việt
    # nào (Sujato lẫn Minh Châu đều không dịch Luật). Đối chiếu tên mục trong DB để map:
    #   vin01m   Phân Tích Giới Bổn (Pārājika, Saṅghādisesa, Nissaggiya...)
    #   vin02m1  Pācittiya / Phân Tích Giới Tỳ Khưu Ni
    #   vin02m2  Đại Phẩm (Mahāvagga)
    #   vin02m3  Tiểu Phẩm (Cullavagga)
    #   vin02m4  Tập Yếu (Parivāra)
    # Chạy thử "pr" trước bằng --dry-run để biết tỉ lệ ghép thật của văn xuôi Luật, rồi
    # mới quyết có nạp 8 tập còn lại hay không.
    # Đã chạy thử "pr": 311/1461 = 21%, ngang mức Trường Bộ và vượt ngưỡng đáng nạp.
    "pr": {"file": "ttpv_01_Pr.pdf", "label": "Phân Tích Giới Bổn - Pārājika",
           "docs": ["vin01m.mul.xml"]},
    "pc1": {"file": "ttpv_02_Pc_I.pdf", "label": "Phân Tích Giới - Pācittiya I",
            "docs": ["vin02m1.mul.xml"]},
    "pc2": {"file": "ttpv_03_Pc_II.pdf", "label": "Phân Tích Giới - Pācittiya II",
            "docs": ["vin02m1.mul.xml"]},
    "mv1": {"file": "ttpv_04_Mv_I.pdf", "label": "Đại Phẩm - Mahāvagga I",
            "docs": ["vin02m2.mul.xml"]},
    "mv2": {"file": "ttpv_05_Mv_II.pdf", "label": "Đại Phẩm - Mahāvagga II",
            "docs": ["vin02m2.mul.xml"]},
    "cv1": {"file": "ttpv_06_Cv_I.pdf", "label": "Tiểu Phẩm - Cullavagga I",
            "docs": ["vin02m3.mul.xml"]},
    "cv2": {"file": "ttpv_07_Cv_II.pdf", "label": "Tiểu Phẩm - Cullavagga II",
            "docs": ["vin02m3.mul.xml"]},
    "par1": {"file": "ttpv_08_Par_I.pdf", "label": "Tập Yếu - Parivāra I",
             "docs": ["vin02m4.mul.xml"]},
    "par2": {"file": "ttpv_09_Par_II.pdf", "label": "Tập Yếu - Parivāra II",
             "docs": ["vin02m4.mul.xml"]},
    # TIỂU BỘ còn trống. Map lấy từ tiêu đề mục thật trong DB, không suy từ tên file:
    #   s0506m Thiên Cung Sự · s0507m Ngạ Quỷ Sự · s0510m1/m2 Thánh Nhân Ký Sự
    #   s0512m Hạnh Tạng · s0513m/s0514m Bổn Sanh · s0515m/s0516m Nghĩa Tích
    #   s0517m Phân Tích Đạo · s0518m Mi Tiên Vấn Đáp · s0519m Chỉ Đạo · s0520m Tạng Thích
    # Nhóm thơ kệ (Vv, Pv, Ap, Cp, Ja) đáng chạy TRƯỚC: đo trên Trưởng Lão Kệ và Kinh Tập
    # thì thơ kệ đạt 82-93%, còn văn xuôi chỉ 21%.
    "vvpv": {"file": "30_Vv_Pv.pdf", "label": "Thiên Cung Sự, Ngạ Quỷ Sự",
             "docs": ["s0506m.mul.xml", "s0507m.mul.xml"]},
    "ap1": {"file": "ttpv_39_Ap_I.pdf", "label": "Thánh Nhân Ký Sự I",
            "docs": ["s0510m1.mul.xml", "s0510m2.mul.xml"]},
    "ap2": {"file": "ttpv_40_Ap_II.pdf", "label": "Thánh Nhân Ký Sự II",
            "docs": ["s0510m1.mul.xml", "s0510m2.mul.xml"]},
    "ap3": {"file": "ttpv_41_Ap_III.pdf", "label": "Thánh Nhân Ký Sự III",
            "docs": ["s0510m1.mul.xml", "s0510m2.mul.xml"]},
    # Trang nguồn ghi nhãn là "Cp.pdf" nhưng href thật là "ttpv_42_Bv&Cp.pdf" - một tập
    # gộp Phật Sử và Hạnh Tạng, nên phủ được cả hai tài liệu.
    "bvcp": {"file": "ttpv_42_Bv&Cp.pdf", "label": "Phật Sử, Hạnh Tạng",
             "docs": ["s0511m.mul.xml", "s0512m.mul.xml"]},
    "ja1": {"file": "32_Ja_I.pdf", "label": "Bổn Sanh I",
            "docs": ["s0513m.mul.xml", "s0514m.mul.xml"]},
    "ja2": {"file": "33_Ja_II.pdf", "label": "Bổn Sanh II",
            "docs": ["s0513m.mul.xml", "s0514m.mul.xml"]},
    "ja3": {"file": "34_Ja_III.pdf", "label": "Bổn Sanh III",
            "docs": ["s0513m.mul.xml", "s0514m.mul.xml"]},
    # Văn xuôi Tiểu Bộ - tỉ lệ dự kiến thấp như Trường Bộ, chạy sau cùng.
    "nidd1": {"file": "35_Nidd_I.pdf", "label": "Đại Nghĩa Tích", "docs": ["s0515m.mul.xml"]},
    "nidd2": {"file": "36_Nidd_II.pdf", "label": "Tiểu Nghĩa Tích", "docs": ["s0516m.mul.xml"]},
    "pts1": {"file": "ttpv_37_Pts_I.pdf", "label": "Phân Tích Đạo I", "docs": ["s0517m.mul.xml"]},
    "pts2": {"file": "ttpv_38_Pts_II.pdf", "label": "Phân Tích Đạo II", "docs": ["s0517m.mul.xml"]},
    "net": {"file": "43_Net.pdf", "label": "Chỉ Đạo (Nettippakaraṇa)", "docs": ["s0519m.mul.xml"]},
    "pet": {"file": "ttpv_44_Pet.pdf", "label": "Tạng Thích (Peṭakopadesa)", "docs": ["s0520m.nrf.xml"]},
    "mil": {"file": "45_Mil.pdf", "label": "Mi Tiên Vấn Đáp", "docs": ["s0518m.nrf.xml"]},
    "dn1": {"file": "10_D_01.pdf", "label": "Trường Bộ tập 1", "docs": ["s0101m.mul.xml"]},
    "dn2": {"file": "11_D_02.pdf", "label": "Trường Bộ tập 2", "docs": ["s0102m.mul.xml"]},
    "dn3": {"file": "12_D_03.pdf", "label": "Trường Bộ tập 3", "docs": ["s0103m.mul.xml"]},
}

# So cau ke o dau dong. Sach dung ca "12." lan "12 ." tuy trang.
_VERSE = re.compile(r"^\s*(\d{1,4})\s*\.\s*", re.M)
# Do dai toi thieu de coi mot cau Pali la du dac trung ma di tim trong DB.
MIN_PALI_CHARS = 22
# Nguong giong nhau va khoang cach toi thieu voi ung vien thu hai.
SIMILARITY_MIN = 0.5
SIMILARITY_MARGIN = 0.1
# Chuoi do dai hon the nay thi trigram loang diem, cat bot truoc khi tra.
MAX_PROBE_CHARS = 200


def download(volume: dict) -> Path:
    CACHE.mkdir(exist_ok=True)
    target = CACHE / volume["file"]
    if target.exists() and target.stat().st_size > 10000:
        return target
    url = f"{BASE}/{urllib.parse.quote(volume['file'])}"
    print(f"  tải {volume['file']} ...")
    with urllib.request.urlopen(url, timeout=300) as response:
        target.write_bytes(response.read())
    return target


def is_vietnamese(text: str) -> bool:
    return sum(1 for ch in text.lower() if ch in VIETNAMESE_CHARS) > 30


def split_verses(text: str) -> dict[str, str]:
    """Tách các câu kệ đánh số trên một trang -> {số: nội dung}."""
    parts = _VERSE.split(text)
    verses: dict[str, str] = {}
    # parts = [phan dau, so, noi dung, so, noi dung, ...]
    for index in range(1, len(parts) - 1, 2):
        number = parts[index]
        body = re.sub(r"\s+", " ", parts[index + 1]).strip()
        if body and number not in verses:
            verses[number] = body
    return verses


# Bóc PDF hay chèn dấu cách ngay TRƯỚC nguyên âm mang dấu ("r ừng", "b ởi", "th ấy") -
# dính 71% số dòng, 15.128 chỗ. Chỉ nối lại khi mẩu đứng trước là phụ âm đầu THUẦN,
# tức không chứa nguyên âm nào. Nhờ ràng buộc đó mà "anh ấy" không bị nối thành "anhấy":
# mẩu "anh" có nguyên âm nên không khớp. Đổi lại, "quy ến" cũng không sửa được - chấp
# nhận bỏ sót còn hơn nối bừa làm hỏng chữ đúng.
_VN_VOWEL = "àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵ"
_ONSET = "ngh|ng|nh|ch|gh|gi|kh|ph|th|tr|qu|b|c|d|đ|g|h|k|l|m|n|p|q|r|s|t|v|x"
_SPLIT_WORD = re.compile(rf"(?<!\w)({_ONSET}) ([{_VN_VOWEL}])", re.IGNORECASE)

# Mẩu trước CÓ nguyên âm nên luật trên không với tới: "vi ệc", "bi ết", "quy ến",
# "thuy ết", "nhi ều", "ho ặc", "tu ệ".
#
# Phân biệt với chữ đúng bằng NGUYÊN ÂM ĐI SAU, không phải bằng mẩu đứng trước. Thống kê
# trên chính dữ liệu này cho thấy hai nhóm tách bạch hẳn:
#   đúng, phải giữ : "vị ấy" (889), "điều ấy" (326), "lợi ích" (169), "tham ái" (159),
#                    "xuống ở", "này ông" - đi sau là ấ/í/á/ở/ô/ý, đều mở đầu từ thật.
#   bị tách, phải nối: "vi ệc", "bi ết", "nhi ều", "ho ặc" - đi sau là ệ/ế/ề/ể/ặ, mà
#                    tiếng Việt không có từ nào bắt đầu bằng những âm này.
#
# Riêng "ế" phải có thêm chữ theo sau mới được nối, vì nó là từ đứng một mình được
# ("ế ẩm"): "bi ết" nối, còn "mua ế hàng" thì không. Bốn âm ệ/ể/ề/ặ không bao giờ đứng
# một mình nên không cần ràng buộc đó - nhờ vậy "trí tu ệ" mới nối lại được thành "tuệ".
_SPLIT_MEDIAL = re.compile(r"(?<!\w)(\w+) ((?:[ệểềặ]|ế\w))", re.IGNORECASE)

# "Bà -la-môn", "Sa- môn": dấu cách lọt vào cạnh gạch nối của từ ghép phiên âm. Hai vế
# đều phải dính liền chữ, nên dấu gạch ngang thật sự (" - " có cách hai bên, như
# "hữu biên - vô biên") không bị đụng tới.
_SPLIT_HYPHEN = re.compile(r"(\w) -(\w)|(\w)- (\w)")


# Thứ tự chạy cho `--all`, xếp theo GIÁ TRỊ GIẢM DẦN chứ không theo thứ tự trong tạng.
# Chạy cả bộ mất nhiều giờ; nếu phải dừng giữa chừng thì phần đã xong phải là phần đáng
# nhất. Đo được: thơ kệ đạt 82-93%, văn xuôi chỉ 21%.
#   1. thơ kệ Tiểu Bộ  - trống hoàn toàn, tỉ lệ cao nhất
#   2. Tạng Luật       - trống hoàn toàn, tỉ lệ ~21% nhưng không nguồn nào khác đụng tới
#   3. văn xuôi Tiểu Bộ- tỉ lệ thấp
#   4. các tập đã nạp  - để cuối, và mặc định bị bỏ qua
VOLUME_ORDER = (
    "vvpv", "ap1", "ap2", "ap3", "bvcp", "ja1", "ja2", "ja3",
    "pr", "pc1", "pc2", "mv1", "mv2", "cv1", "cv2", "par1", "par2",
    "nidd1", "nidd2", "pts1", "pts2", "net", "pet", "mil",
    "sn", "kn1", "thag", "dn1", "dn2", "dn3",
)


def already_imported() -> set[str]:
    """Các tập đã có bản ghi nạp thành công, để `--all` khỏi chạy lại.

    Chạy lại một tập là an toàn (`on conflict do update`) nhưng tốn hàng giờ mà không
    thêm được gì, nên mặc định bỏ qua. Dùng `--force` khi muốn nạp đè.
    """
    try:
        rows = fetch_all(
            "select distinct scope from human_translation_imports where source = %s", [SOURCE_ID]
        )
    except Exception:  # noqa: BLE001 - chưa có bảng thì coi như chưa nạp gì
        return set()
    return {str(r["scope"]) for r in rows}


def mend_spacing(text: str) -> str:
    """Nối lại những chữ bị bóc PDF tách đôi. Chạy tới khi không còn đổi."""
    for _ in range(4):
        fixed = _SPLIT_WORD.sub(r"\1\2", text)
        fixed = _SPLIT_MEDIAL.sub(r"\1\2", fixed)
        fixed = _SPLIT_HYPHEN.sub(lambda m: (m.group(1) or m.group(3)) + "-" + (m.group(2) or m.group(4)), fixed)
        if fixed == text:
            break
        text = fixed
    return text


def build_probes(pali: str) -> list[str]:
    """Các chuỗi Pali đem đi dò DB, thử lần lượt cho tới khi ra kết quả.

    Trước đây chỉ dò bằng mệnh đề đầu (phần trước dấu phẩy). Với thơ kệ thì đúng - dòng
    sau thường là điệp khúc lặp ở mọi câu nên lấy cả câu thì điệp khúc lấn át. Nhưng
    Trường Bộ là văn xuôi: đo trên tập 1 thì 24% số cặp trượt vì điểm thấp và 13% không
    tìm thấy dòng nào, phần lớn do mệnh đề đầu quá chung. Nên thử thêm cả câu và mệnh đề
    dài nhất, chỉ dùng tới khi cách đầu không ra gì.
    """
    probes: list[str] = []
    clauses = [normalize_pali(c) for c in re.split(r"[,;]", pali)]
    for candidate in (clauses[0] if clauses else "", normalize_pali(pali),
                      max(clauses, key=len) if clauses else ""):
        candidate = candidate[:MAX_PROBE_CHARS].strip()
        if len(candidate) >= MIN_PALI_CHARS and candidate not in probes:
            probes.append(candidate)
    return probes


# Nguong nhan mot doan lam UNG VIEN o che do can chinh toan cuc. Thap hon nguong ghep
# truc tiep, vi o day khong quyet dinh gi ca - chi thu thap ung vien roi de buoc can
# chinh chon, va viec chon co ca day lam chung cu chu khong xet rieng tung cap.
GLOBAL_CANDIDATE_MIN = 0.35
GLOBAL_CANDIDATE_LIMIT = 10
# Diem tong toi thieu de giu mot cap sau khi can chinh.
GLOBAL_ACCEPT_MIN = 0.45


# ĐÃ THỬ VA ĐA BO (lan hai) - "lap giua hai moc neo": lay cac cap khop chac lam moc,
# roi voi cac cap lot giua hai moc thi chon doan GIONG NHAT trong dung khoang do, con
# tro tien mot chieu bi nhot trong khoang nen khong the vot ra ngoai. Do phu tang manh:
# Truong Bo 1 19% -> 59%, Parajika 2% -> 26%.
#
# NHUNG SAI. Nap that roi soi tay: mau 6 dong ngau nhien co 2 dong gan nham, va muc
# `Sassatavado` ma khach bao co 5/11 doan thi 1 doan bi thay bang ban dich cua doan khac
# - doan do TRUOC KHI SUA VON DUNG. Kieu sai co he thong: cac doan MO DAU ("Santi,
# bhikkhave, eke samanabrahmana...") bi gan ban dich cua doan nam sau vai nhip.
#
# Vi sao: khi cap dung nam NGOAI khoang (con tro da chay qua, hoac moc neo qua thua),
# thuat toan van buoc phai chon mot doan trong khoang. Nguong va bien do khong cuu duoc,
# vi cac doan lien nhau o Truong Bo dung chung toi 70-80% so tu.
#
# Bai hoc ve cach do: phep thu "giau bot moc" cho 100% dung nen tuong la an. Sai o cho
# moc bi giau deu la cap KHOP CHAC (chu dac trung, de tim), khong dai dien cho dam cap
# nhap nhang ma luot lap thuc su phai xu ly - chon mau thien lech.
#
# Muon lam lai thi phai neo theo TUNG BAI KINH: dung `build_targets` ben import_sujato.py
# lay khoang sort_order cua tung bai, do bien bai trong PDF theo tieu de Pali, roi reset
# con tro o moi bai. Khong co diem neo do thi dung dong vao huong nay nua.


# Do dai toi thieu de mot tieu de muc du dac trung ma dem di do trong PDF. Ngan hon thi
# de trung ("Uddeso", "Nidanam" xuat hien khap noi).
SECTION_STEM_MIN = 8


def section_stem(title: str) -> str:
    """Rut tieu de ve dang so sanh duoc: bo so thu tu, bo dau, bo khoang trang."""
    return normalize_pali(re.sub(r"^[\d\.\(\)\s]+", "", title or "")).replace(" ", "")


def load_sections(doc_ids: list[str]) -> list[dict]:
    rows = fetch_all(
        """
        select s.id, s.title, s.document_id, min(p.sort_order) as first_sort
        from sections s join passages p on p.section_id = s.id
        where s.document_id = any(%s::uuid[])
        group by s.id, s.title, s.document_id
        order by s.document_id, min(p.sort_order)
        """,
        [doc_ids],
    )
    return [dict(row) for row in rows]


def tag_pages_with_sections(pages: list[str], sections: list[dict]) -> list[str | None]:
    """Moi trang Pali thuoc muc nao, doc theo tieu de Pali in tren trang.

    Ban song ngu in tieu de muc bang Pali o trang Pali ("Vinitavatthu", "2. Dutiyaparajikam").
    Do duoc chung thi moi cap cau ke biet minh thuoc muc nao, va viec tim doan thu tu "ca
    tap" xuong "may doan trong muc" - du hep de luat nghiem cu khong con bi hai ung vien
    giong het nhau o hai dau tap lam cho be tac.
    KHONG dung duoc cho Truong Bo: PDF bo do khong in tieu de muc (do duoc 1%), nen cac
    tap do van chay nhu cu.

    Chi cho tien: tieu de nao tro nguoc ve muc da di qua thi bo, vi do gan nhu chac chan
    la trung ten chu khong phai sach quay lai.
    """
    by_stem: dict[str, dict] = {}
    duplicated: set[str] = set()
    for section in sections:
        stem = section_stem(section["title"])
        if len(stem) < SECTION_STEM_MIN:
            continue
        if stem in by_stem:
            duplicated.add(stem)
            continue
        by_stem[stem] = section
    for stem in duplicated:
        by_stem.pop(stem, None)

    order = {str(section["id"]): index for index, section in enumerate(sections)}
    tags: list[str | None] = []
    current: dict | None = None
    for page in pages:
        if not is_vietnamese(page):
            for line in page.split("\n"):
                line = line.strip()
                if not line or len(line) > 70:
                    continue
                found = by_stem.get(section_stem(line))
                if found and (current is None or order[str(found["id"])] > order[str(current["id"])]):
                    current = found
        tags.append(str(current["id"]) if current else None)
    return tags


def strip_running_head(text: str) -> str:
    """Bỏ dòng tiêu đề chạy và số trang ở đầu mỗi trang."""
    lines = text.split("\n")
    while lines and (not lines[0].strip() or re.fullmatch(r"\s*\d{1,4}\s*", lines[0])):
        lines.pop(0)
    if lines and not re.match(r"^\s*\d{1,4}\s*\.", lines[0]):
        lines.pop(0)
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Nạp bản dịch tiếng Việt của TK. Indacanda.")
    parser.add_argument("volumes", nargs="*", help=f"Tập: {', '.join(VOLUMES)}")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--all", action="store_true",
                        help="chạy mọi tập chưa nạp, xếp theo giá trị giảm dần")
    parser.add_argument("--force", action="store_true",
                        help="với --all: nạp đè cả những tập đã nạp rồi")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--global-align", action="store_true",
                        help="căn chỉnh toàn cục: xét cả tập cùng lúc thay vì ghép từng cặp"
                             " (xem align_globally)")
    args = parser.parse_args()

    if args.list:
        for key, volume in VOLUMES.items():
            print(f"  {key:<6} {volume['label']:<46} {volume['file']}")
        return

    if args.all:
        done = set() if args.force else already_imported()
        args.volumes = [key for key in VOLUME_ORDER if key not in done]
        skipped = [key for key in VOLUME_ORDER if key in done]
        print(f"Sẽ chạy {len(args.volumes)} tập: {' '.join(args.volumes)}")
        if skipped:
            print(f"Bỏ qua {len(skipped)} tập đã nạp: {' '.join(skipped)}  (dùng --force để nạp đè)")
        print()
        if not args.volumes:
            print("Không còn tập nào chưa nạp.")
            return
    if not args.volumes:
        parser.error("cần chỉ định tập, hoặc dùng --all / --list")

    if not args.dry_run:
        migration = Path(__file__).resolve().parents[1] / "db" / "migrations" / "002_human_translations.sql"
        execute(migration.read_text(encoding="utf-8"))
        print(f"đã áp dụng {migration.name}\n")

    grand = {"pairs": 0, "matched": 0, "written": 0}

    for key in args.volumes:
        volume = VOLUMES.get(key)
        if not volume:
            print(f"!! không biết tập {key}\n")
            continue

        print(f"=== {volume['label']} ({volume['file']}) ===")
        path = download(volume)
        reader = PdfReader(str(path))
        pages = [strip_running_head(page.extract_text() or "") for page in reader.pages]

        # Giu dung thu tu nhu cau hinh: con tro tien-mot-chieu chay theo thu tu nay.
        by_name = {
            row["file_name"]: str(row["id"])
            for row in fetch_all(
                "select id, file_name from documents where file_name = any(%s)", [volume["docs"]]
            )
        }
        doc_ids = [by_name[name] for name in volume["docs"] if name in by_name]
        if not doc_ids:
            print("  !! không tìm thấy tài liệu tương ứng trong DB\n")
            continue

        sections = load_sections(doc_ids)
        page_section = tag_pages_with_sections(pages, sections)
        tagged_pages = sum(1 for tag in page_section if tag)

        # Ghep cap trang Pali <-> trang Viet ke nhau, roi ghep cau ke theo SO.
        aligned: list[tuple[str, str, str | None]] = []
        for index in range(len(pages) - 1):
            left, right = pages[index], pages[index + 1]
            if is_vietnamese(left) or not is_vietnamese(right):
                continue
            pali_verses = split_verses(left)
            viet_verses = split_verses(right)
            for number, pali in pali_verses.items():
                viet = viet_verses.get(number)
                if viet and len(pali) >= MIN_PALI_CHARS:
                    # Chi va ban Viet; ban Pali giu nguyen vi con dung de do chuoi trong DB
                    # va `_VN_VOWEL` khong dinh toi chu Pali.
                    aligned.append((pali, mend_spacing(viet), page_section[index]))

        print(f"  {len(pages)} trang · ghép được {len(aligned)} cặp câu kệ Pali-Việt")
        grand["pairs"] += len(aligned)

        if args.global_align:
            passages = []
            for document_id in doc_ids:
                passages.extend(
                    fetch_all(
                        "select id from passages where document_id = %s order by sort_order",
                        [document_id],
                    )
                )
            position_of = {str(row["id"]): index for index, row in enumerate(passages)}

            # Thu thap ung vien: nguong thap, KHONG doi bo xa ung vien nhi. O day chua
            # quyet dinh gi - viec chon de buoc can chinh lam, va no chon dua vao ca day.
            candidates: list[list[tuple[int, float]]] = []
            for pali, _viet, _section in aligned:
                best_by_position: dict[int, float] = {}
                for probe in build_probes(pali):
                    for row in fetch_all(
                        """
                        select id, similarity(normalized_pali, %s) as sim
                        from passages
                        where document_id = any(%s::uuid[]) and normalized_pali %% %s
                        order by sim desc limit %s
                        """,
                        [probe, doc_ids, probe, GLOBAL_CANDIDATE_LIMIT],
                    ):
                        if row["sim"] < GLOBAL_CANDIDATE_MIN:
                            continue
                        position = position_of.get(str(row["id"]))
                        if position is not None and row["sim"] > best_by_position.get(position, 0.0):
                            best_by_position[position] = float(row["sim"])
                candidates.append(sorted(best_by_position.items()))

            chosen = align_globally(candidates)
            written = 0
            for pair_index in sorted(chosen):
                pali, viet, _section = aligned[pair_index]
                position = chosen[pair_index]
                if dict(candidates[pair_index]).get(position, 0.0) < GLOBAL_ACCEPT_MIN:
                    continue
                if args.verbose and written < 4:
                    print(f"     PALI: {pali[:74]}")
                    print(f"     VIỆT: {viet[:74]}")
                if not args.dry_run:
                    execute(
                        """
                        insert into human_translations
                          (passage_id, source, language, translated_text, source_ref, segment_ids)
                        values (%s, %s, %s, %s, %s, %s)
                        on conflict (passage_id, source) do update
                          set translated_text = excluded.translated_text,
                              source_ref = excluded.source_ref,
                              updated_at = now()
                        """,
                        [str(passages[position]["id"]), SOURCE_ID, LANGUAGE, viet, key, []],
                    )
                written += 1

            rate = 100 * written // max(1, len(aligned))
            print(f"  căn chỉnh toàn cục: {written}/{len(aligned)} cặp ({rate}%)"
                  f" · phủ {100 * written // max(1, len(passages))}% số đoạn · ghi {written}")
            grand["matched"] += written
            grand["written"] += written
            print()
            continue

        matched = written = 0
        by_section = 0
        seen: set[str] = set()
        for pali, viet, section_id in aligned:
            hit = None
            # ĐÃ THỬ VA ĐA BO (lan ba) - thu hep pham vi tim vao dung MUC ma trang PDF do
            # thuoc ve, giu nguyen luat chon. Do duoc muc cho 640/735 trang Parajika va
            # them 35 dong (311 -> 346).
            #
            # NHUNG THU HEP PHAM VI TAO RA TU TIN GIA. Luat "phai bo xa ung vien nhi" chi
            # co nghia khi ung vien that nam trong tam nhin. Loai doi thu ra khoi tam nhin
            # thi mot doan khong lien quan trong muc bong thanh "ung vien duy nhat" va
            # vuot qua luat de dang. Soi tay 8 dong moi: 6 dung, 1 lech dau doan, 1 SAI
            # han (sort=1790 Puranacivara: Pali noi "bao giat thi pham dukkata", ban Viet
            # gan vao lai la nghi thuc xa bo y - doan khac trong cung muc).
            #
            # Do them: 35/38 ca nhap nhang co CA HAI ung vien trong cung mot muc (Tang Luat
            # liet ke hang loat vu viec chi khac vai chu ngay trong mot muc), nen thu hep
            # den cap muc vua khong go duoc phan lon, vua sinh loi moi. Muon di tiep thi
            # phai co diem neo den TUNG DOAN, khong phai tung muc.
            # Giu nguyen `section_id` o `aligned` va `tag_pages_with_sections` vi phan do
            # do dung (640/735 trang) va con dung duoc neu sau nay tim ra neo min hon.
            for scope in ("volume",):
                if scope == "section" and not section_id:
                    continue
                for probe in build_probes(pali):
                    # Khop mo bang index trigram: ban in noi tu khac ban goc
                    # ("jinnamiva tacam" vs "jinnamivattacam") nen khop chuoi chinh xac se truot.
                    if scope == "section":
                        rows = fetch_all(
                            """
                            select id, similarity(normalized_pali, %s) as sim
                            from passages
                            where section_id = %s and normalized_pali %% %s
                            order by sim desc limit 2
                            """,
                            [probe, section_id, probe],
                        )
                    else:
                        rows = fetch_all(
                            """
                            select id, similarity(normalized_pali, %s) as sim
                            from passages
                            where document_id = any(%s::uuid[]) and normalized_pali %% %s
                            order by sim desc limit 2
                            """,
                            [probe, doc_ids, probe],
                        )
                    # Phai vua giong du cao, vua bo xa ung vien thu hai. Sat nhau thi bo qua,
                    # tha thieu con hon gan nham cau ke ben canh.
                    if not rows or rows[0]["sim"] < SIMILARITY_MIN:
                        continue
                    if len(rows) > 1 and rows[0]["sim"] - rows[1]["sim"] < SIMILARITY_MARGIN:
                        continue
                    hit = rows[0]
                    break
                if hit:
                    if scope == "section":
                        by_section += 1
                    break
            if not hit:
                continue
            passage_id = str(hit["id"])
            # Cap DAU TIEN doi duoc mot doan thi giu doan do. Da thu doi sang cach chon
            # khac (giu day moc tang dan dai nhat): so dong tut 252 xuong 196 VA doan mo
            # dau cua Sassatavado bi thay bang ban dich cua doan khac - tuc chon lai la
            # hong ca cai dang dung. Xem ghi chu that bai o dau file.
            if passage_id in seen:
                continue
            seen.add(passage_id)
            matched += 1
            if args.verbose and matched <= 4:
                print(f"     PALI: {pali[:74]}")
                print(f"     VIỆT: {viet[:74]}")
            if args.dry_run:
                continue
            execute(
                """
                insert into human_translations
                  (passage_id, source, language, translated_text, source_ref, segment_ids)
                values (%s, %s, %s, %s, %s, %s)
                on conflict (passage_id, source) do update
                  set translated_text = excluded.translated_text,
                      source_ref = excluded.source_ref,
                      updated_at = now()
                """,
                [passage_id, SOURCE_ID, LANGUAGE, viet, key, []],
            )
            written += 1

        rate = 100 * matched // max(1, len(aligned))
        print(f"  tìm được đúng một chỗ trong DB: {matched}/{len(aligned)} ({rate}%) · ghi {written}"
              f"  [dò được mục cho {tagged_pages}/{len(pages)} trang · {by_section} cặp khớp nhờ giới hạn trong mục]")
        grand["matched"] += matched
        grand["written"] += written

        if not args.dry_run and matched:
            execute(
                """
                insert into human_translation_imports
                  (source, language, scope, segments_total, segments_matched, passages_written, notes)
                values (%s, %s, %s, %s, %s, %s, %s)
                """,
                [SOURCE_ID, LANGUAGE, key, len(aligned), matched, written, volume["label"]],
            )
        print()

    print(f"TỔNG: {grand['pairs']} cặp câu kệ · khớp DB {grand['matched']} · ghi {grand['written']}")


if __name__ == "__main__":

    main()
