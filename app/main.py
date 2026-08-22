import json
import re
import secrets
from collections import defaultdict
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware



from .config import settings
from .db import execute, fetch_all, fetch_one
from .help_guide import (
    HELP_GUIDE_BATCH,
    get_help_config,
    get_help_page,
    get_help_sutta,
    save_help,
)
from .i18n import (
    DEFAULT_LANGUAGE,
    LANGUAGES,
    corpus_options,
    language_options,
    normalize_language,
    pitaka_options,
    t,
    ui_strings,
)
from .notice import get_notice, get_notice_config, save_notice
from .normalize import normalize_pali
from .search_engine import resolve_corpus_types, resolve_pitaka_type, search_passages, _display_source
from .translation_sources import (
    AI_SOURCE,
    SOURCE_ORDER,
    normalize_source,
    official_translations_merged,
    source_label,
    sources_for_sections,
    resolve_human_translation,
    unavailable_translation,
)
from .translator import (
    public_translation_error,
    summarize_plain_pali_text,
    translate_passage,
    translate_text,
    translate_text_cached,
)
from .user_feedback import (
    FEEDBACK_BATCH,
    FEEDBACK_MAX_CHARS,
    add_feedback,
    clear_feedback,
    count_feedback,
    list_feedback,
)


APP_DIR = Path(__file__).resolve().parent
SECTION_TRANSLATION_MAX_CHARS = 18000
SECTION_TRANSLATION_CHUNK_CHARS = 12000
SECTION_TRANSLATION_STREAM_CHUNK_CHARS = 3600

app = FastAPI(title="Tipiṭaka Python Search")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
app.add_middleware(SessionMiddleware, secret_key=settings().get("secret_key", "default_secret_key"))
templates = Jinja2Templates(directory=APP_DIR / "templates")


GATHA_RENDS = {"gatha1", "gatha2", "gatha3", "gathalast"}
LANGUAGE_COOKIE = "lang"
LANGUAGE_COOKIE_MAX_AGE = 60 * 60 * 24 * 365
# Do dai cua so trich cua ban dich cap bai kinh trong the ket qua. Cua so nay TRUOT toi
# vi tri tuong doi cua doan dang xem (xem `_excerpt`), khong phai cat tu dau bai.
# Do tren cac moc kiem chung duoc, ti le trung khuc dich dung: 1.000 chu -> 31%,
# 2.000 -> 40%, 3.000 -> 56%, 5.000 -> 65%. Chon 2.000: gap doi muc cu theo yeu cau,
# van chua lap mat phan Pali. Khong co muc nao dat do tin cay that su - muon chinh xac
# thi phai ghep duoc cap doan, xem `align_minhchau.py`.
WHOLE_SUTTA_EXCERPT_CHARS = 2000

# Đơn vị đọc hoàn chỉnh. Kinh dùng hậu tố sutta/suttanta; Tiểu Bộ và Luật dùng
# những hậu tố khác. Khandhaka là đơn vị đọc hoàn chỉnh của phần Luật dạng chương;
# không nhận vagga/nipata vì chúng thường chỉ là nhóm chứa nhiều bài độc lập.
_READER_SUTTA_SUFFIXES = ("sutta", "suttam", "suttanta", "suttantam")
_READER_NUMBERED_SUFFIXES = (
    "jatakam",
    "apadanam",
    "gatha",
    "vatthu",
    "cariya",
    "vamso",
    "sikkhapadam",
    "parajikam",
    "khandhako",
    "khandhakam",
    "puccha",
    "katha",
)

# Số thứ tự in trong ngoặc ở CUỐI tiêu đề, ví dụ `543. Bhūridattajātakaṃ (6)` - số 6 là
# vị trí trong Mahānipāta, không phải một phần của tên bài. Nó che mất đuôi thật khiến
# `jatakam` không khớp, nên toàn bộ Jātaka bị coi là không có đơn vị đọc: đo được 4.885
# đoạn (2.717 ở s0514m + 2.168 ở s0513m) mở nút "Xem toàn bộ bài kinh" ra một mẩu.
#
# CLAUDE.md từng ghi giả thuyết "s0514m không có section cấp jātaka, chỉ có nipāta" và
# tự đánh dấu là chưa kiểm chứng. Giả thuyết đó SAI - các section ấy có đủ, chỉ bị luật
# tiêu đề loại oan.
#
# Bỏ đuôi này chỉ làm THÊM tiêu đề được nhận, không nới danh sách đuôi: `22. Mahānipāto
# (3)` sau khi bỏ vẫn kết thúc bằng `nipato` nên vẫn bị loại đúng như trước.
# Nhận cả dạng NHIỀU CẤP `(2-1-1)` (nipāta-phẩm-vị trí), không chỉ `(6)`. Bản sửa trước chỉ
# xử lý dạng một số nên 264 mục Jātaka vẫn bị đuôi ngoặc che mất hậu tố `jātakaṃ` và trượt
# bậc 1 - `151. Rājovādajātakaṃ (2-1-1)` mở ra cả `1. Daḷhavaggo` 72 đoạn (khoảng 10 bổn
# sanh) thay vì chính bổn sanh 8 đoạn. Đo khi nới: thêm 264 mục, **mất 0 mục**, toàn bộ ở `mul`.
_READER_TRAILING_ORDINAL = re.compile(r"\s*\(\d+(?:[-–]\d+)*\)\s*$")


# Chú giải và Phụ chú giải đặt tên theo lối "phần GIẢI THÍCH của X": `Mettāsahagatasutta`
# thành `Mettāsahagatasuttavaṇṇanā`. Đuôi `vaṇṇanā` che mất hậu tố thật, nên TRƯỚC bản sửa
# này KHÔNG mục nào của Chú giải/Phụ chú giải được nhận là đơn vị đọc - đo được: att chỉ 374
# mục trên toàn bộ, tik 538. Kết quả là ca khách vừa báo: đoạn 235 nằm trong
# `4. Mettāsahagatasuttavaṇṇanā` (8 đoạn) nhưng bậc 1 trượt, bậc 2 leo lên
# `6. Sākacchavaggo` (66 đoạn) và mục lục hiện ra cả 6 bài chú giải của phẩm.
#
# Bỏ đuôi rồi áp LẠI đúng luật cũ trên phần lõi, không nới danh sách hậu tố. Nhờ vậy:
#   `Mettāsahagatasuttavaṇṇanā` -> `...sutta`  -> nhận, lớp dứt khoát
#   `Suddhabrahmacariyakathāvaṇṇanā` -> `...kathā` -> nhận, lớp phụ thuộc ngữ cảnh
#   `1. Bhūmivaggavaṇṇanā` -> `...vagga` -> vẫn TỪ CHỐI, vì chương thì vẫn là chương
# Đo trên toàn kho: att 374 -> 2.953, tik 538 -> 2.169, nrf 688 -> 707, **mul không đổi**
# (chánh kinh không dùng lối đặt tên này nên không có rủi ro), và **0 tên chương bị nhận oan**
# - đã kiểm riêng các đuôi `vagga`/`nipāta`/`saṃyutta`/`kaṇḍa`/`paṇṇāsaka` + `vaṇṇanā`.
_READER_COMMENTARY_SUFFIXES = ("vannana",)


def _reader_core(title: str) -> tuple[str, str]:
    """Trả về (tiêu đề đã bỏ số thứ tự cuối, phần lõi đã bỏ đuôi giải thích).

    Phần lõi là thứ đem so với danh sách hậu tố; `raw` vẫn cần để kiểm "có số ở đầu".
    """
    raw = _READER_TRAILING_ORDINAL.sub("", str(title or "").strip())
    normalized = normalize_pali(raw).replace(" ", "")
    for suffix in _READER_COMMENTARY_SUFFIXES:
        if normalized.endswith(suffix) and len(normalized) > len(suffix):
            return raw, normalized[: -len(suffix)]
    return raw, normalized


def _is_commentary_section(title: str) -> bool:
    """Tiêu đề có phải một MỤC CHÚ GIẢI (`...vaṇṇanā`) - tức `_reader_core` đã bỏ đuôi ấy."""
    raw = _READER_TRAILING_ORDINAL.sub("", str(title or "").strip())
    return normalize_pali(raw).replace(" ", "").endswith(_READER_COMMENTARY_SUFFIXES)


def _is_reader_unit_title(title: str) -> bool:
    raw, core = _reader_core(title)
    if core.endswith(_READER_SUTTA_SUFFIXES):
        return True
    return bool(re.match(r"^\(?\d", raw)) and core.endswith(_READER_NUMBERED_SUFFIXES)


# Hai LỚP hậu tố, vì `kathā` và `khandhaka` đổi nghĩa theo chỗ chúng đứng:
#
# - Trong Trường Bộ, `kathā` là TIỂU MỤC bên trong một bài kinh (`Sīlakkhandhakathā` nằm
#   trong `1. Brahmajālasuttaṃ`) - nhận nó làm đơn vị đọc thì người đọc bấm "toàn bộ bài
#   kinh" lại ra một mẩu, đúng lỗi khách từng báo.
# - Trong Mahāvagga, `kathā` LÀ đơn vị: `1. Mahākhandhako` chỉ gồm 69 mục con và tất cả
#   đều là `kathā`/`vatthu`, từ 1 đoạn (`42. Sikkhāpadakathā`) tới 69 đoạn
#   (`5. Brahmayācanakathā`). Không có đơn vị nào nhỏ hơn để leo tới.
# - `khandhaka` cũng vậy: là đơn vị đọc khi đoạn rơi thẳng vào nó, nhưng là CHƯƠNG khi bên
#   trong còn `kathā`/`vatthu` mang tên riêng.
#
# Nên phân biệt theo NGỮ CẢNH, không theo KÍCH THƯỚC. Bản trước dùng size floor 20 đoạn
# cho `kathā` và chính nó tạo ra lỗi khách báo lại: `42. Sikkhāpadakathā` (1 đoạn) bị floor
# loại ở bậc 1 nên vòng lặp leo tiếp tới `1. Mahākhandhako` - 616 đoạn, 234k ký tự Pāli,
# 76 lượt dịch AI, và bản ghép Indacanda chỉ phủ 255/616 đoạn. Kích thước là chỉ dấu sai:
# `41. Rāhulavatthu` (3 đoạn) và `43. Daṇḍakammavatthu` (4 đoạn) ngay cạnh đó vẫn hiện
# riêng vì đuôi `vatthu` không có floor, nên ba mục in liền nhau lại xử sự khác nhau.
#
# Đo trước khi đổi: 501 mục đổi đơn vị đọc (2.989 đoạn), tập trung ở đúng những sách mà
# `kathā` là đơn vị thật - `abh03m3` (Kathāvatthu) 197, `vin12t` 166, `vin02m2` 91,
# `vin02m3` 14, `s0517m` (Paṭisambhidāmagga) 13. Trường Bộ (`s0101m`/`s0102m`/`s0103m`)
# KHÔNG có mục nào đổi: `kathā` trong đó luôn có tổ tiên lớp A là bài kinh, nên vẫn leo
# đúng lên bài kinh. Đó là phép kiểm quan trọng nhất của luật này.
_READER_CONTEXT_SUFFIXES = ("katha", "khandhako", "khandhakam", "niddeso", "niddesa", "niddesam")


# Hai từ này kết thúc bằng `kathā` nhưng KHÔNG phải đơn vị đọc: `aṭṭhakathā` là tên bộ chú
# giải (`Dasakanipāta-aṭṭhakathā`, 167 đoạn) và `ārambhakathā` là lời tựa
# (`Ganthārambhakathā`, 3.815 đoạn - phủ trọn tài liệu). Không loại chúng thì việc nới
# `kathā` không số biến tên sách thành trang đọc: đo được 837 mục bị đẩy lên trang TO HƠN,
# trong đó có những mục 9-16 đoạn bị thay bằng trang 3.815 đoạn.
_READER_NOT_UNIT_CORES = ("atthakatha", "arambhakatha")


def _is_reader_unit_row(row: dict) -> bool:
    """Như `_is_reader_unit_title` nhưng biết cả ĐỘ DÀI, nên nhận thêm `kathā` KHÔNG SỐ.

    `_READER_NUMBERED_SUFFIXES` đòi tiêu đề bắt đầu bằng chữ số, vì các đuôi đó
    (`kathā`/`vatthu`/`gāthā`...) là đuôi từ thường gặp, nhận bừa thì kéo cả tên sách vào.
    Nhưng Kathāvatthu và các bộ chú giải đặt tên mục con KHÔNG SỐ: ca khách báo là
    `Atītacakkhurūpādikathā` (6 đoạn) nằm trong `5. Sabbamatthītikathā` (101 đoạn) - mục con
    không được nhận nên trang đọc mở ra cả 101 đoạn.

    Chỉ nới cho lớp PHỤ THUỘC NGỮ CẢNH, và đó là điều làm bản nới này an toàn về mặt cấu
    trúc: lớp ngữ cảnh chỉ được xét khi không có đơn vị lớp dứt khoát nào, nên một `kathā`
    không số KHÔNG BAO GIỜ thắng được một bài kinh - các ca kiểu Trường Bộ không thể hỏng.

    Hai chốt chặn, cả hai đều do đo mà có (xem `_READER_NOT_UNIT_CORES` và số bên dưới):
    tên sách/lời tựa bị loại theo từ, và phần còn lại phải nằm trong
    `READER_FALLBACK_MAX_PASSAGES` - `Nidānakathā` 3.985 đoạn vẫn là một khoảng phủ trọn
    tài liệu, không phải mục để đọc.

    Cân đo trên toàn kho: **1.162 mục có trang NHỎ ĐI (6.436 đoạn), 35 mục to ra (641 đoạn)**
    - lợi gấp 10 lần hại, và phần "to ra" còn sót phần lớn chỉ là đổi nhãn sang bản chú giải
    cùng độ dài (`6. Gatikathā` 13 đoạn -> `Gatikathāvaṇṇanā` 13 đoạn).
    """
    title = str(row.get("title") or "")
    if title in ("Paṭhamavaggo", "Dutiyavaggo", "Tatiyavaggo", "Catutthavaggo"):
        # Trong Khaggavisāṇasuttaniddeso (Tiểu Nghĩa Tích), các phẩm con không có số
        # và là đơn vị đọc nhỏ nhất mà bản dịch hỗ trợ (cỡ 100 đoạn mỗi phẩm).
        span = row["end_sort_order"] - row["start_sort_order"] + 1
        if span <= READER_FALLBACK_MAX_PASSAGES:
            return True
    if _is_reader_unit_title(title):
        return True
    core = _reader_core(title)[1]
    if core.endswith(_READER_NOT_UNIT_CORES):
        return False
    # Bỏ yêu cầu "phải có số" trong hai trường hợp:
    #
    # a) lõi mang hậu tố LỚP NGỮ CẢNH (`kathā`...) - ca Kathāvatthu ở trên;
    # b) tiêu đề là MỘT MỤC CHÚ GIẢI (`...vaṇṇanā`) và lõi mang bất kỳ hậu tố đơn vị nào.
    #
    # (b) có lý do riêng: đuôi `vaṇṇanā` tự nó đã là dấu người biên tập đánh "đây là một mục
    # chú giải riêng", và chú giải được đọc theo từng mục nhỏ - mịn hơn đơn vị của bản gốc.
    # Ca khách báo: `Vaggumudātīriyabhikkhuvatthuvaṇṇanā` (14 đoạn) bị bỏ qua vì lõi kết thúc
    # `vatthu` (nhóm đòi số) và không có số, nên trang đọc mở ra cả `4. Catutthapārājikaṃ`
    # (109 đoạn). Tệ hơn: trong CÙNG một mục lục, `Suddhikavārakathāvaṇṇanā` lại được nhận vì
    # lõi kết thúc `kathā` - hai mục anh em xử sự khác nhau chỉ vì hậu tố của lõi.
    #
    # Đo trên toàn kho: **165 mục có trang nhỏ đi (1.540 đoạn), 0 ca to ra thật** - 3 ca đổi
    # nhãn nhưng GIỮ NGUYÊN độ dài (`Dvepabbajitavatthuvaṇṇanā` 5->5, 7->7, 10->10). Chỉ ảnh
    # hưởng `att`/`tik`; `mul` không có tiêu đề nào kiểu này nên chánh kinh không chạm tới.
    unnumbered_ok = core.endswith(_READER_CONTEXT_SUFFIXES) or (
        _is_commentary_section(title)
        and (core.endswith(_READER_SUTTA_SUFFIXES) or core.endswith(_READER_NUMBERED_SUFFIXES))
    )
    if not unnumbered_ok:
        return False
    span = row["end_sort_order"] - row["start_sort_order"] + 1
    return span <= READER_FALLBACK_MAX_PASSAGES


def _is_context_dependent_reader_title(title: str) -> bool:
    # Xét trên phần LÕI, cùng một phép bỏ đuôi giải thích: `...kathāvaṇṇanā` phải rơi vào
    # lớp phụ thuộc ngữ cảnh giống `...kathā`, nếu không thì một tiểu mục trong bài chú giải
    # sẽ thắng chính bài chú giải đó.
    return _reader_core(title)[1].endswith(_READER_CONTEXT_SUFFIXES)


def _source_path_is_prefix(candidate: object, selected: object) -> bool:
    left = [str(item) for item in candidate] if isinstance(candidate, (list, tuple)) else []
    right = [str(item) for item in selected] if isinstance(selected, (list, tuple)) else []
    return bool(left and len(left) <= len(right) and right[: len(left)] == left)


# Trần cho bậc rút lui ở dưới. Đo trên kho hiện có: leo lên tổ tiên gần nhất cho trung
# vị 101 đoạn và p90 499 - đúng cỡ một bài kinh thật (Mahāpadānasutta ~204 đoạn) - nhưng
# đuôi vươn tới 13.618 đoạn ở Jātaka Mahānipāta, nơi tổ tiên duy nhất là cả một nipāta.
# Đổ ngần ấy vào một trang thì vừa sai nghĩa "bài kinh" vừa nặng trang, nên chặn lại và
# thà giữ mục con còn hơn.
READER_FALLBACK_MAX_PASSAGES = 1500

# Chỉ leo khi mục con thật sự là MẨU VỤN. Leo vô điều kiện là sai: có những mục con vốn
# đã là một đơn vị lớn và đúng, leo lên chỉ đổi tên hiển thị thành thứ rộng hơn mà không
# thêm được nội dung nào.
#
# Ca thật đã dựng lại: câu uddāna mở đầu Trường Bộ tập 2 (`sort_order` 1, s0102m) nằm ở
# `Mahāvaggapāḷi` (1.361 đoạn). Không bài kinh nào chứa nó - uddāna là câu kệ tóm tắt cả
# tập - nên bậc 1 trượt là ĐÚNG. Bậc 2 khi ấy bỏ qua chính nó rồi leo lên `Dīghanikāyo`,
# vốn cũng đúng 1.361 đoạn vì hai mục trùng khít: không thêm gì, chỉ khiến người đọc thấy
# tiêu đề "Dīghanikāyo" thay vì "Mahāvaggapāḷi".
#
# Ngưỡng 20 do đo mà ra, không phải ước lượng: nó giảm tỉ lệ đoạn rơi vào mẩu vụn từ 5,2%
# xuống 0,7% - y hệt phương án leo vô điều kiện - nhưng chỉ sinh 76 trang trên 500 đoạn
# thay vì 106, và 19 trang trên 1.000 đoạn thay vì 28. Nới lên 50/100/200 không giảm thêm
# được mẩu vụn nào mà chỉ đẻ thêm trang nặng.
READER_FALLBACK_MIN_PASSAGES = 20

# Các corpus lấy thẳng mục sâu nhất làm đơn vị đọc - xem chú thích trong
# `_canonical_reader_section`. `mul` (chánh kinh) KHÔNG nằm đây: ở đó đơn vị đọc là bài kinh.
_READER_DEEPEST_CORPUS = ("att", "tik", "nrf")

# Bậc 2 chỉ leo khi KHÔNG có bằng chứng nào về đơn vị đọc. Số thứ tự đầu tiêu đề chính là
# bằng chứng đó: người biên tập đã xếp mục này vào một DÃY ĐÁNH SỐ, tức nó là một đơn vị
# của dãy, không phải mẩu cắt giữa bài.
#
# Ca khách báo: đoạn 659 nằm ở `13. Appamaññāvibhaṅgo -> 1. Suttantabhājanīyaṃ -> 2. Karuṇā`.
# `1. Suttantabhājanīyaṃ` (53 đoạn) chứa đúng bốn mục con `1. Mettā` / `2. Karuṇā` /
# `3. Muditā` / `4. Upekkhā`, mỗi mục 13 đoạn - tứ vô lượng tâm. `2. Karuṇā` là đơn vị trọn
# vẹn, nhưng vì 13 < 20 nên bậc 2 leo lên và người đọc nhận cả bốn.
#
# **KHÔNG kèm ngưỡng kích thước ở đây, và đó là bài học vừa phải trả giá.** Ngưỡng sẽ tái
# tạo đúng lỗi bất nhất mà `_READER_SIZE_SENSITIVE_SUFFIXES` từng gây ra: đo được
# `2. Vedanākkhandhaniddeso` (16 đoạn) sẽ hiện riêng còn `4. Saṅkhārakkhandhaniddeso`
# (1 đoạn) lại leo lên, dù hai mục là ANH EM cùng một dãy năm uẩn - chỉ khác độ dài vì chú
# giải chỗ đó viết ngắn. Nhãn khớp đúng nội dung vẫn là nhãn đúng, kể cả khi chỉ có 1 đoạn.
#
# Đo trên toàn kho: 5.910 mục dừng lại (21.287 đoạn), trong đó 2.436 mục chỉ 1 đoạn; trước
# đó chúng mở ra trang trung vị 73 đoạn. Trang hẹp không còn là vấn đề tìm kiếm nữa vì
# `revealMatchedPassage` đưa người đọc tới đúng đoạn, và mục lục vẫn còn đó để đi tiếp.
_ENUMERATED_TITLE = re.compile(r"^\s*\(?\d+\)?\s*[.\-]")


def _is_enumerated_title(title: str) -> bool:
    return bool(_ENUMERATED_TITLE.match(_READER_TRAILING_ORDINAL.sub("", str(title or "").strip())))


def _canonical_reader_section(section: dict) -> dict:
    """Đưa một mục con lên đơn vị đọc hoàn chỉnh gần nhất trong cây section.

    Đây là bộ suy luận CŨ, hiện chỉ còn được giữ cho các phép audit/import đã viết dựa
    trên nó. Trang đọc thật không gọi hàm này nữa: quy tắc hiện hành là dùng nguyên
    `passages.section_id`, tức lớp nguồn sâu nhất mà đoạn đang trực tiếp thuộc về.

    `passages.section_id` trỏ vào mục sâu nhất. Ví dụ đoạn 11 của DN 14 trỏ vào
    `Pubbenivāsapaṭisaṃyuttakathā` (4-35), trong khi bài kinh thật là
    `Mahāpadānasuttaṃ` (4-207). Dựa vào tiền tố `source_path` giúp loại các section
    bao trùm do XML nhiễu nhưng không phải tổ tiên thật.

    HAI BẬC, và bậc thứ hai mới là chỗ đáp ứng yêu cầu của khách:

    1. Tổ tiên có tiêu đề qua được `_is_reader_unit_title` - đây là bài kinh thật, và xét
       theo HAI LỚP (xem `_READER_CONTEXT_SUFFIXES`): ưu tiên tổ tiên NHỎ NHẤT thuộc lớp
       dứt khoát (`sutta`/`sikkhāpadaṃ`/`jātaka`/`vatthu`...); chỉ khi không có mới nhận
       tổ tiên nhỏ nhất thuộc lớp phụ thuộc ngữ cảnh (`kathā`/`khandhaka`). Nhờ vậy
       `kathā` bên trong một bài kinh Trường Bộ vẫn leo lên bài kinh, còn `kathā` là mục
       con trực tiếp của một khandhaka Luật thì được hiện riêng - đúng "lấy nhãn gần nhất"
       mà khách chốt, không phải chọn theo số đoạn.
    2. Không có, VÀ mục con nhỏ hơn `READER_FALLBACK_MIN_PASSAGES`, thì leo lên tổ tiên
       GẦN NHẤT bất kể tiêu đề, miễn không vượt `READER_FALLBACK_MAX_PASSAGES`. Khách
       chốt "không có trọn bài thì lấy gần nhất cũng được". Bậc này kéo tỉ lệ đoạn rơi
       vào mẩu vụn từ 5,2% xuống 0,7%. Trước khi có nó, code rơi thẳng về mục con sâu
       nhất - trung vị chỉ 10 đoạn, tức người đọc bấm "toàn bộ bài kinh" mà nhận một mẩu.

       Điều kiện "mục con quá bé" là bắt buộc, không phải tinh chỉnh: xem chú thích ở
       `READER_FALLBACK_MIN_PASSAGES` về ca uddāna Trường Bộ.

    Kết quả mang theo cờ `_readerUnitExact` cho biết bậc nào đã trúng. Giao diện KHÔNG
    dùng cờ này: khách chốt cột Pali luôn ghi "Bản gốc Pali trọn bài kinh" ở mọi trường
    hợp. Giữ cờ lại vì nó là ranh giới hành vi thật, có test ghim, và đợt sửa importer
    cho Jātaka sắp tới cần đọc nó.

    Còn 2.267 đoạn vốn đã không có tổ tiên nào để leo - mục đó đã là cấp cao nhất của
    tài liệu, tức đang hiện hết mức có thể chứ không phải hỏng.
    """
    # CHÚ GIẢI / PHỤ CHÚ GIẢI / SÁCH PHỤ: lấy thẳng mục sâu nhất, không leo.
    #
    # Lý lẽ nằm ở cách người ta đọc: chú giải được đọc theo từng mục nhỏ bám sát bản gốc,
    # nên đơn vị đọc của nó MỊN HƠN đơn vị của chánh kinh - `Vaggumudātīriyabhikkhu-
    # vatthuvaṇṇanā` (14 đoạn) là một mục để đọc, không phải mẩu cắt của
    # `4. Catutthapārājikaṃ`. Chánh kinh thì ngược lại: `Pubbenivāsapaṭisaṃyuttakathā` là
    # mẩu bên trong `1. Mahāpadānasuttaṃ` và khách chốt phải leo lên bài kinh.
    #
    # Vì sao thành luật theo CORPUS chứ không kể thêm hậu tố: bốn ca khách báo liên tiếp đều
    # là cùng một vấn đề dưới bốn quy ước đặt tên khác nhau (`kathā` có số, `kathā` không số,
    # phạm vi ghi sai, `vaṇṇanā` + lõi thuộc nhóm đòi số). Cách kể tên từng quy ước không
    # bao giờ đủ với kho này; luật theo corpus thì dứt điểm cho cả tầng chú giải.
    #
    # Đo được: đoạn mở ra trang dưới 50 đoạn tăng 162.681 -> 178.373; trang 500-1499 giảm
    # 48.723 -> 43.590. Nhóm 1500+ không đổi (35.704) vì đó là các sách `nrf` KHÔNG có mục
    # con nào trong dữ liệu - chỉ nạp lại XML mới chia nhỏ được, không phải việc của luật này.
    #
    # `corpus_type` thiếu (fixture test, hoặc caller cũ) thì coi như chánh kinh, tức giữ
    # nguyên hành vi cũ - không được đoán sang nhánh mới.
    if str(section.get("corpus_type") or "") in _READER_DEEPEST_CORPUS:
        section["_readerUnitExact"] = _is_reader_unit_row(section)
        return _clip_overreaching_range(section)

    candidates = fetch_all(
        """
        select id, document_id, title, source_path, start_sort_order,
               coalesce(end_sort_order, start_sort_order) as end_sort_order
        from sections
        where document_id = %s
          and start_sort_order <= %s
          and coalesce(end_sort_order, start_sort_order) >= %s
        order by coalesce(end_sort_order, start_sort_order) - start_sort_order,
                 cardinality(source_path) desc
        """,
        [section["document_id"], section["start_sort_order"], section["end_sort_order"]],
    )
    within_tree = [
        row
        for row in candidates
        if _source_path_is_prefix(row.get("source_path"), section.get("source_path"))
    ]

    # `within_tree` đã sắp theo độ dài TĂNG DẦN, nên lượt nào cũng lấy được mục nhỏ nhất
    # thoả điều kiện. Hai lượt chứ không một: lượt đầu chỉ nhận lớp dứt khoát, nên một
    # `kathā` nhỏ nằm trong bài kinh không thể thắng chính bài kinh dù nó đứng trước trong
    # danh sách. Xem `_READER_CONTEXT_SUFFIXES`.
    unit_rows = [row for row in within_tree if _is_reader_unit_row(row)]
    for row in unit_rows:
        if _is_context_dependent_reader_title(str(row.get("title") or "")):
            continue
        row["_readerUnitExact"] = True
        return _clip_overreaching_range(row)
    if unit_rows:
        unit_rows[0]["_readerUnitExact"] = True
        return _clip_overreaching_range(unit_rows[0])

    own_span = section["end_sort_order"] - section["start_sort_order"] + 1
    if own_span < READER_FALLBACK_MIN_PASSAGES and not _is_enumerated_title(section.get("title")):
        for row in within_tree:
            if str(row["id"]) == str(section["id"]):
                continue
            span = row["end_sort_order"] - row["start_sort_order"] + 1
            if span <= READER_FALLBACK_MAX_PASSAGES:
                row["_readerUnitExact"] = False
                return _clip_overreaching_range(row)
            # `candidates` đã sắp theo độ dài tăng dần, nên tổ tiên đầu tiên đã là nhỏ
            # nhất; vượt trần thì mọi tổ tiên còn lại đều lớn hơn, không cần xét tiếp.
            break

    # Không nâng được: hoặc mục này đã là cấp cao nhất của tài liệu, hoặc tổ tiên duy
    # nhất quá lớn. Cả hai đều KHÔNG phải bài kinh đã xác thực nên nhãn phải nói khác.
    section["_readerUnitExact"] = False
    return _clip_overreaching_range(section)


# Đuôi nhận ra một section là TÊN CÔNG TRÌNH (bộ chú giải / phụ chú giải).
_WORK_TITLE_SUFFIX = re.compile(r"(ṭīkā|tika|aṭṭhakathā|atthakatha)$", re.I)


def _book_stem(title: str) -> str:
    """Gốc sách: phần trước dấu gạch đầu tiên. `Vibhaṅga-anuṭīkā` -> `vibhaṅga`."""
    return re.split(r"[-–]", str(title or "").strip(), 1)[0].strip().casefold()


def _fix_source_path(section: dict, siblings: list[dict]) -> list[str]:
    """Sửa `source_path` lúc HIỂN THỊ: bỏ phần tử không phải tổ tiên, sửa tên công trình.

    Bản import XML để lại hai loại rác trong đường dẫn trích dẫn, và cả hai đều làm địa chỉ
    KHÔNG dò được - khách báo đúng ca này: đi theo
    `Vajirabuddhi-Ṭīkā -> Ganthārambhakathā -> Vinayapiṭake -> Vajirabuddhi-ṭīkā` thì không
    tìm ra `4. Catutthapārājikaṃ` ở đâu cả.

    A. **Phần tử LẶP LẠI** một phần tử trước đó: `Vajirabuddhi-ṭīkā` lặp `Vajirabuddhi-Ṭīkā`
       (chỉ khác hoa/thường). So khớp CHÍNH XÁC (không phân biệt hoa/thường) nên an toàn.
       (788 ca)
    B. **Tên công trình bị dán theo FILE.** `abh02t.tik.xml` chứa hai công trình -
       `Vibhaṅga-mūlaṭīkā` (0-863) và `Vibhaṅga-anuṭīkā` (864-1649) - nhưng cả hai nửa đều
       mang nhãn `Vibhaṅga-Mūlaṭīkā`, tức nửa sau bị gọi sai tên. Tên thật có sẵn trong DB
       dưới dạng section cấp công trình, nên thay vào chứ không phải đoán. (208 ca)

    Chốt chặn cho (B): chỉ thay khi hai tên **cùng gốc sách** (`Vibhaṅga`) và phần tử đang có
    cũng mang đuôi tên công trình. Không có chốt này thì một tên bộ hợp lệ có thể bị thay bằng
    tên công trình chứa nó ở tài liệu khác.

    **LUẬT THỨ BA ĐÃ BỊ THU HỒI - đừng cài lại.** "Bỏ phần tử KHÔNG CHỨA section này theo
    phạm vi" chữa được 2.515 phần tử rác (`Ganthārambhakathā` 0-34 với section ở 529-530),
    nhưng nó **xoá luôn tên sách hợp lệ có phạm vi ghi thiếu**: `Milindapañhapāḷi` ghi 16-117
    trong khi mục con `1. Vessantarapañho` ở 1074-1109, và `Abhidhammatthasaṅgaho` ghi 0-743
    với mục con ở 1188-1225. Khách phát hiện cả hai.

    Đã thử cứu bằng phạm vi hiệu chỉnh (chạy tới trước section cấp bằng/nông hơn kế tiếp) -
    KHÔNG cứu được: cả hai vẫn ra `16-117` và `0-743` vì có section cùng cấp ngay sau. Với dữ
    liệu hiện có **không có tín hiệu nào tách được "sách bị ghi thiếu" khỏi "lời tựa rác"** -
    hai thứ có hình dạng y hệt nhau. Nên thà để lại một phần tử dư còn hơn xoá tên sách.

    Phần tử KHÔNG khớp section nào thì luôn giữ - đó là nhãn do importer gán (tên corpus, tên
    bộ), không kiểm được bằng phạm vi nên không được đoán.
    """
    start, end = section["start_sort_order"], section["end_sort_order"]
    path = [str(x) for x in (section.get("source_path") or [])]
    ranges_by_title: dict[str, list[tuple[int, int]]] = {}
    works: list[dict] = []
    for row in siblings:
        title = str(row.get("title") or "")
        ranges_by_title.setdefault(title, []).append(
            (row["start_sort_order"], row["end_sort_order"])
        )
        if _WORK_TITLE_SUFFIX.search(title):
            works.append(row)
    return _prune_path(path, start, end, str(section.get("title") or ""), ranges_by_title, works)


def _prune_path(
    path: list[str],
    start: int,
    end: int,
    own_title: str,
    ranges_by_title: dict[str, list[tuple[int, int]]],
    works: list[dict],
) -> list[str]:
    """Phần thuần tính toán của `_fix_source_path`, để gọi được theo lô mà không truy vấn lại."""
    section = {"start_sort_order": start, "end_sort_order": end, "title": own_title}
    # CHỈ bỏ phần tử LẶP. Phép kiểm "phần tử không chứa section này thì bỏ" đã được cài,
    # đo, rồi THU HỒI - xem chú thích ở `_fix_source_path`.
    del ranges_by_title
    kept: list[str] = []
    seen: set[str] = set()
    for index, element in enumerate(path):
        last = index == len(path) - 1
        key = element.casefold()
        if not last and key in seen:
            continue
        kept.append(element)
        seen.add(key)

    containing = [
        row
        for row in works
        if row["start_sort_order"] <= start
        and row["end_sort_order"] >= end
        and str(row.get("title") or "").casefold() != str(section.get("title") or "").casefold()
    ]
    if containing:
        real = min(containing, key=lambda row: row["end_sort_order"] - row["start_sort_order"])
        real_title = str(real.get("title") or "")
        if real_title.casefold() not in {x.casefold() for x in kept}:
            stem = _book_stem(real_title)
            for index in range(len(kept) - 1):
                element = kept[index]
                if _book_stem(element) == stem and _WORK_TITLE_SUFFIX.search(element):
                    kept[index] = real_title
                    break
    return kept


def _fixed_paths_for_sections(section_ids: list[str]) -> dict[str, list[str]]:
    """Đường dẫn đã sửa cho một LÔ section - hai truy vấn cho cả trang, không phải mỗi dòng.

    Cố ý chỉ tra những tiêu đề CÓ trong đường dẫn, thay vì nạp mọi section của tài liệu: một
    tài liệu Paṭṭhāna có hàng nghìn section, mà mỗi đường dẫn chỉ có dưới 10 phần tử.
    """
    ids = [str(x) for x in section_ids if x]
    if not ids:
        return {}
    metas = fetch_all(
        """
        select id, document_id, title, source_path, start_sort_order,
               coalesce(end_sort_order, start_sort_order) as end_sort_order
        from sections where id = any(%s)
        """,
        [ids],
    )
    if not metas:
        return {}
    documents = sorted({str(row["document_id"]) for row in metas})
    titles = sorted({
        str(element)
        for row in metas
        for element in (row["source_path"] or [])
    })
    ranges = defaultdict(list)
    if titles:
        for row in fetch_all(
            """
            select document_id, title, start_sort_order,
                   coalesce(end_sort_order, start_sort_order) as end_sort_order
            from sections
            where document_id = any(%s) and title = any(%s)
            """,
            [documents, titles],
        ):
            ranges[(str(row["document_id"]), str(row["title"]))].append(
                (row["start_sort_order"], row["end_sort_order"])
            )
    works = defaultdict(list)
    for row in fetch_all(
        """
        select document_id, title, start_sort_order,
               coalesce(end_sort_order, start_sort_order) as end_sort_order
        from sections
        where document_id = any(%s)
          and (title ~* %s or title ~* %s)
        """,
        [documents, "ṭīkā$|tika$", "aṭṭhakathā$|atthakatha$"],
    ):
        works[str(row["document_id"])].append(row)

    out: dict[str, list[str]] = {}
    for row in metas:
        doc = str(row["document_id"])
        path = [str(x) for x in (row["source_path"] or [])]
        by_title = {
            title: ranges.get((doc, title), [])
            for title in path
        }
        out[str(row["id"])] = _prune_path(
            path,
            row["start_sort_order"],
            row["end_sort_order"],
            str(row["title"] or ""),
            {k: v for k, v in by_title.items() if v},
            works.get(doc, []),
        )
    return out


def _duplicate_path_ranks(section_ids: list[str]) -> dict[str, tuple[int, int]]:
    """Với section nào bị TRÙNG cả tiêu đề lẫn `source_path` trong cùng tài liệu, trả về
    (thứ tự xuất hiện, tổng số). Không trùng thì không có khoá.

    Vì sao cần: `abh02t.tik.xml` chứa HAI bộ chú giải hoàn chỉnh nối nhau (đoạn 0-863 và
    864-1649), mỗi bộ đi hết 18 vibhaṅga. Nên có hai section `Viññāṇapadaniddesavaṇṇanā`
    (308-351 và 1188-1225) với nội dung KHÁC NHAU nhưng `source_path` giống hệt từng chữ -
    người đọc thấy hai văn bản khác nhau dưới cùng một địa chỉ trích dẫn, không cách nào
    biết mình đang ở bộ nào.

    Không phải ca lẻ: đo toàn kho có **1.760 nhóm** section trùng cả tài liệu + tiêu đề +
    đường dẫn, nặng nhất là Paṭṭhāna với `1. Paccayānulomaṃ` xuất hiện **72 lần** cùng một
    đường dẫn trong `abh03m10.mul.xml`.

    Thứ tự lấy theo `start_sort_order` - tức thứ tự in trong sách, không phải thứ tự tuỳ ý.
    Một truy vấn cho cả trang; gọi từng dòng thì trang kết quả tốn 10 lượt.
    """
    ids = [str(x) for x in section_ids if x]
    if not ids:
        return {}
    rows = fetch_all(
        """
        with target as (
            select distinct document_id, title, source_path
            from sections where id = any(%s)
        ),
        peers as (
            select s.id,
                   row_number() over (
                       partition by s.document_id, s.title, s.source_path
                       order by s.start_sort_order
                   ) as idx,
                   count(*) over (
                       partition by s.document_id, s.title, s.source_path
                   ) as total
            from sections s
            join target t
              on t.document_id = s.document_id
             and t.title is not distinct from s.title
             and t.source_path is not distinct from s.source_path
        )
        select id, idx, total from peers where total > 1 and id = any(%s)
        """,
        [ids, ids],
    )
    return {str(row["id"]): (int(row["idx"]), int(row["total"])) for row in rows}


def _with_path_occurrence(source_path: str, rank: tuple[int, int] | None, language: str) -> str:
    """Gắn "bộ N/M" vào cuối đường dẫn khi địa chỉ bị trùng.

    Gắn vào CUỐI chuỗi vì tiêu đề của chính section luôn là phần tử cuối của `source_path`.
    """
    if not rank:
        return source_path
    index, total = rank
    return f"{source_path} ({t(language, 'results.pathOccurrence', index=index, total=total)})"


def _clip_overreaching_range(row: dict) -> dict:
    """Cắt phạm vi ghi QUÁ RỘNG, tại chỗ section không-phải-con-cháu bắt đầu.

    Một section không thể chứa một section không phải con cháu của nó. Bản import XML lại
    ghi cho một số section phạm vi trọn cả tài liệu: `Ganthārambhakathā` trong `s0506a` ghi
    là 0-3814 (3.815 đoạn) trong khi lời tựa ấy chỉ có **29 đoạn** - nội dung thật bắt đầu
    ở `1. Itthivimānaṃ`/`1. Pīṭhavaggo` từ đoạn 29. Người đọc rơi vào lời tựa thì nhận cả
    bộ sách, kèm mục lục 7 phẩm không liên quan - đúng lỗi khách báo.

    Cắt tại đoạn mở đầu của section KHÔNG PHẢI con cháu đầu tiên nằm trong phạm vi. Đây là
    suy ra từ dữ liệu, không phải phỏng đoán, và đã đối chiếu: phạm vi sau khi cắt trùng
    KHỚP với số đoạn thực sự trỏ vào section đó ở cả ba ca đã biết -
    `Ganthārambhakathā` 3.815 -> 29 (29 đoạn thật), `Suttanipātapāḷi` 3.892 -> 90 (90),
    `Pācittiyapāḷi` 3.048 -> 1 (1). Đo toàn kho: 525 section có phạm vi quá rộng, trong đó
    110 section người đọc có thể rơi vào.

    Section cha HỢP LỆ không bị ảnh hưởng: mục con của nó LÀ con cháu nên không tính là
    section lạ - `1. Suttantabhājanīyaṃ` (53 đoạn, 4 mục con) giữ nguyên 53.
    """
    rows = fetch_all(
        """
        select source_path, start_sort_order
        from sections
        where document_id = %s
          and start_sort_order > %s
          and start_sort_order <= %s
        order by start_sort_order asc
        """,
        [row["document_id"], row["start_sort_order"], row["end_sort_order"]],
    )
    for other in rows:
        if _source_path_is_prefix(row.get("source_path"), other.get("source_path")):
            continue
        row["end_sort_order"] = other["start_sort_order"] - 1
        row["_readerRangeClipped"] = True
        break
    return row


def _reader_section_by_id(section_id: str) -> dict | None:
    """Lấy đúng section được yêu cầu, không tự nâng lên một tổ tiên có tên "hợp lệ".

    `passages.section_id` đã là khóa của lớp sâu nhất trong trích nguồn. Dùng trực tiếp
    khóa này làm phạm vi đọc vừa nhất quán cho mọi Tạng, vừa không cần danh sách hậu tố
    hay ngoại lệ theo nhan đề. Vẫn giữ phép cắt phạm vi XML ghi quá rộng để một section
    lỗi không nuốt sang section kế tiếp.
    """
    section = fetch_one(
        """
        select s.id, s.document_id, s.title, s.source_path, s.start_sort_order,
               coalesce(s.end_sort_order, s.start_sort_order) as end_sort_order,
               d.corpus_type
        from sections s join documents d on d.id = s.document_id
        where s.id = %s
        """,
        [section_id],
    )
    return _clip_overreaching_range(section) if section else None


def _language_from_header(header: str) -> str | None:
    for part in header.split(","):
        code = part.split(";")[0].strip().lower()[:2]
        if code in LANGUAGES:
            return code
    return None


def request_language(request: Request, override: str | None = None) -> str:
    """Ngôn ngữ giao diện: tham số của request > cookie > Accept-Language > tiếng Việt."""
    if override and str(override).strip().lower()[:2] in LANGUAGES:
        return normalize_language(override)
    cookie = request.cookies.get(LANGUAGE_COOKIE)
    if cookie and str(cookie).strip().lower()[:2] in LANGUAGES:
        return normalize_language(cookie)
    return _language_from_header(request.headers.get("accept-language", "")) or DEFAULT_LANGUAGE


def _template_context(request: Request, language: str, **extra: object) -> dict:
    context = {
        "request": request,
        "lang": language,
        "t": lambda key, **kwargs: t(language, key, **kwargs),
    }
    context.update(extra)
    return context


def _admin_filter_labels() -> tuple[dict[str, str], dict[str, str]]:
    """Nhãn bộ lọc cho trang admin, luôn dùng tiếng Việt."""
    return (
        {item["value"]: item["label"] for item in corpus_options(DEFAULT_LANGUAGE)},
        {item["value"]: item["label"] for item in pitaka_options(DEFAULT_LANGUAGE)},
    )


@app.get("/", response_class=HTMLResponse)
def index(request: Request, lang: str | None = Query(None)):
    language = request_language(request, lang)
    response = templates.TemplateResponse(
        "index.html",
        _template_context(
            request,
            language,
            strings=ui_strings(language),
            corpus_options=corpus_options(language),
            pitaka_options=pitaka_options(language),
            language_options=language_options(),
            notice=get_notice(language),
            default_query="",
            ga_measurement_id=settings().get("ga_measurement_id", ""),
        ),
    )
    response.set_cookie(
        LANGUAGE_COOKIE,
        language,
        max_age=LANGUAGE_COOKIE_MAX_AGE,
        samesite="lax",
        httponly=False,
    )
    return response


@app.post("/search")
def search_api(payload: dict, request: Request):
    query = str(payload.get("query", "")).strip()
    filters = payload.get("filters") or {}
    corpus_types = resolve_corpus_types(filters.get("corpusType"))
    pitaka_type = resolve_pitaka_type(filters.get("pitakaType"))
    page = int(payload.get("page") or 1)
    page_size = min(20, int(payload.get("pageSize") or 5))
    if not query:
        raise HTTPException(status_code=400, detail="Missing query.")
    include_translations = bool(payload.get("includeTranslations", True))
    language = request_language(request, payload.get("language"))
    return search_passages(
        query,
        corpus_types,
        pitaka_type,
        page,
        page_size,
        include_translations=include_translations,
        language=language,
    )

@app.post("/log-timeout")
def log_timeout(
    query: str = Form(...),
    corpus_type: str = Form(...),
    pitaka_type: str | None = Form(None),
):
    from .db import execute
    from psycopg.types.json import Jsonb
    try:
        execute(
            """
            insert into search_logs (query, filters, expanded_query, result_passage_ids, status)
            values (%s, %s::jsonb, %s::jsonb, %s::uuid[], 'timeout')
            """,
            [
                query,
                Jsonb({"corpusType": resolve_corpus_types(corpus_type), "pitakaType": resolve_pitaka_type(pitaka_type)}),
                Jsonb({}),
                [],
            ],
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
    return {"status": "ok"}

@app.post("/search-page", response_class=HTMLResponse)
def search_page(
    request: Request,
    query: str = Form(...),
    corpus_type: str = Form(...),
    pitaka_type: str | None = Form(None),
    page: int = Form(1),
    lang: str | None = Form(None),
):
    language = request_language(request, lang)
    result = search_passages(
        query,
        resolve_corpus_types(corpus_type),
        resolve_pitaka_type(pitaka_type),
        page,
        5,
        include_translations=False,
        language=language,
    )
    # Bản dịch của dịch giả đọc thẳng từ DB nên hiển thị được ngay cùng kết quả,
    # không phải chờ tải sau như bản dịch AI.
    # Phải lấy theo TẤT CẢ các đoạn đang hiển thị: đoạn trích hay được mở rộng ngữ cảnh,
    # chỉ lấy đoạn neo thì phần dịch không phủ hết phần Pali, nhìn vào tưởng ghép lệch.
    # Lấy cả bản cấp bài kinh (Minh Châu) theo yêu cầu của khách - hiện đủ ba dịch giả
    # ngay tại kết quả. Bản cấp bài kinh chỉ in đoạn đầu; muốn đọc trọn thì đã có nút
    # "Xem toàn bộ bài kinh" ngay dưới thẻ, mở trang đọc với đủ nguyên văn.
    results = result.get("results") or []
    for item in results:
        passage_ids = [part["id"] for part in (item.get("snippetParagraphs") or [])] or [item["id"]]
        item["officialTranslations"] = official_translations_merged(
            passage_ids, language, whole_sutta_excerpt_chars=WHOLE_SUTTA_EXCERPT_CHARS
        )
        # Nguon nao khong co ban dich cho dung doan nay thi noi thang ra, thay vi im lang
        # bo qua - im lang khien nguoi doc tuong nguon do khong ton tai.
        present = {str(entry["source"]) for entry in item["officialTranslations"]}
        item["missingTranslations"] = [
            source_label(source_id, language)
            for source_id in SOURCE_ORDER
            if source_id != AI_SOURCE and source_id not in present
        ]

        # `sectionId` do truy vấn passage trả về đã là lớp sâu nhất trong trích nguồn.
        # Dùng thẳng nó cho trang đọc: không phân tích tên, không nâng lên bài/chương cha.
        item["readerSectionId"] = item.get("sectionId")

    # Địa chỉ trích dẫn phải chỉ được MỘT chỗ. Một tài liệu có thể chứa hai bộ chú giải nối
    # nhau, sinh ra hai section trùng cả tiêu đề lẫn đường dẫn - xem `_duplicate_path_ranks`.
    # Làm theo lô cho cả trang: một truy vấn thay vì một truy vấn mỗi dòng.
    section_ids = [item.get("sectionId") for item in results]
    path_ranks = _duplicate_path_ranks(section_ids)
    # Đường dẫn đã lọc rác + sửa tên công trình, xem `_fix_source_path`. Giữ nhãn corpus mà
    # `_display_source` gắn ở đầu (`Ṭīkā`...) - nó không thuộc `source_path`.
    fixed_paths = _fixed_paths_for_sections(section_ids)
    for item in results:
        fixed = fixed_paths.get(str(item.get("sectionId")))
        if fixed:
            parts = str(item.get("sourcePath") or "").split(" -> ")
            prefix = parts[:1] if parts and parts[0] not in fixed else []
            item["sourcePath"] = " -> ".join([*prefix, *fixed])
        item["sourcePath"] = _with_path_occurrence(
            str(item.get("sourcePath") or ""),
            path_ranks.get(str(item.get("sectionId"))),
            language,
        )

    # Tra độ phủ theo đúng lớp nguồn sâu nhất sẽ được mở trong trang đọc.
    section_sources = sources_for_sections([item.get("readerSectionId") for item in results], language)
    for item in results:
        section_entries = section_sources.get(str(item.get("readerSectionId")), [])
        with_data = {str(entry["source"]) for entry in section_entries}
        here = {str(entry["source"]) for entry in item["officialTranslations"]}
        translations_by_source = {
            str(entry["source"]): entry for entry in item["officialTranslations"]
        }
        is_abhidhamma = "abhidhammapitaka" in normalize_pali(str(item.get("sourcePath") or ""))
        entries = []
        for source_id in SOURCE_ORDER:
            if source_id == AI_SOURCE:
                continue
            if source_id == "brahmali" and source_id not in with_data:
                unavailable_reason = t(language, "translation.brahmaliVinayaOnly")
            elif is_abhidhamma and source_id not in with_data:
                unavailable_reason = t(language, "translation.noAbhidhammaCoverage")
            else:
                unavailable_reason = t(language, "translation.noOfficial")
            entries.append(
                {
                    "source": source_id,
                    "label": source_label(source_id, language),
                    "translation": translations_by_source.get(source_id),
                    "available": source_id in with_data,
                    "elsewhereOnly": source_id in with_data and source_id not in here,
                    "unavailableReason": unavailable_reason,
                }
            )
        item["translationEntries"] = entries
        # Giữ field cũ cho các đoạn HTML/cache cũ đang mở trong trình duyệt; template
        # mới dùng `translationEntries` để đặt nút ngay dưới đúng bản dịch.
        item["sectionSources"] = entries

    return templates.TemplateResponse(
        "results.html",
        _template_context(
            request,
            language,
            result=result,
            query=query,
            corpus_type=corpus_type,
            pitaka_type=pitaka_type,
            append_mode=page > 1,
        ),
    )


def _translation_or_error(passage_id: str, language: str = DEFAULT_LANGUAGE) -> dict:
    try:
        return translate_passage(passage_id, language)
    except Exception:
        return {
            "vi": None,
            "text": None,
            "fromCache": False,
            "error": public_translation_error(),
        }


@app.post("/api/translate-result")
def translate_result_api(payload: dict, request: Request):
    passage_id = str(payload.get("passageId") or "").strip()
    pali_text = str(payload.get("paliText") or "").strip()
    use_passage_cache = bool(payload.get("usePassageCache"))
    language = request_language(request, payload.get("language"))
    source = normalize_source(payload.get("source"))

    if not passage_id and not pali_text:
        raise HTTPException(status_code=400, detail="Missing passageId or paliText.")

    warning = t(language, "translation.aiWarning")

    if source != AI_SOURCE:
        # Bản dịch của dịch giả thật: có dữ liệu thì trả về, chưa có thì báo rõ
        # thay vì lặng lẽ chuyển sang bản dịch AI.
        human = resolve_human_translation(source, passage_id or None, pali_text, language)
        if human:
            return {"ok": True, "translation": human, "warning": None, "source": source}
        return {"ok": False, "translation": unavailable_translation(language), "warning": None, "source": source}

    try:
        if use_passage_cache and passage_id:
            translation = translate_passage(passage_id, language)
        elif pali_text:
            translation = translate_text_cached(pali_text, language)
        else:
            translation = translate_passage(passage_id, language)
        return {"ok": True, "translation": translation, "warning": warning, "source": source}
    except Exception:
        return {
            "ok": False,
            "translation": {
                "vi": None,
                "text": None,
                "fromCache": False,
                "error": public_translation_error(),
            },
            "warning": warning,
            "source": source,
        }


def _split_long_paragraph_safely(paragraph: str, max_chars: int) -> list[str]:
    pieces: list[str] = []
    remaining = paragraph.strip()

    while len(remaining) > max_chars:
        window = remaining[:max_chars]
        cut_at = -1

        sentence_matches = list(re.finditer(r"[.!?;:।॥](?:[’”'\")\]]+)?\s+", window))
        if sentence_matches:
            cut_at = sentence_matches[-1].end()

        if cut_at < int(max_chars * 0.55):
            soft_matches = list(re.finditer(r"[,–—-](?:[’”'\")\]]+)?\s+", window))
            if soft_matches:
                cut_at = soft_matches[-1].end()

        if cut_at < int(max_chars * 0.45):
            whitespace = window.rfind(" ")
            if whitespace > int(max_chars * 0.45):
                cut_at = whitespace + 1

        if cut_at <= 0:
            cut_at = max_chars

        pieces.append(remaining[:cut_at].strip())
        remaining = remaining[cut_at:].strip()

    if remaining:
        pieces.append(remaining)

    return pieces


def _chunk_section_text(pali_text: str, max_chars: int = SECTION_TRANSLATION_CHUNK_CHARS) -> list[str]:
    paragraphs = [part.strip() for part in pali_text.split("\n\n") if part.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush_current() -> None:
        nonlocal current, current_len
        if current:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0

    for paragraph in paragraphs:
        paragraph_pieces = (
            [paragraph]
            if len(paragraph) <= max_chars
            else _split_long_paragraph_safely(paragraph, max_chars)
        )

        for piece in paragraph_pieces:
            piece_len = len(piece)
            if piece_len > max_chars:
                flush_current()
                chunks.append(piece)
                continue

            next_len = current_len + piece_len + (2 if current else 0)
            if current and next_len > max_chars:
                flush_current()
                current = [piece]
                current_len = piece_len
            else:
                current.append(piece)
                current_len = next_len

    flush_current()

    return chunks


def _translate_section_text(pali_text: str, language: str = DEFAULT_LANGUAGE) -> tuple[dict, bool]:
    chunks = _chunk_section_text(pali_text)
    if not chunks:
        return {"vi": "", "text": "", "notes": None, "model": None, "fromCache": False}, False

    try:
        if len(chunks) == 1 and len(pali_text) <= SECTION_TRANSLATION_MAX_CHARS:
            return translate_text_cached(pali_text, language), True

        translated_parts: list[str] = []
        failed_parts: list[int] = []
        models: list[str] = []

        for index, chunk in enumerate(chunks, start=1):
            try:
                translated = translate_text_cached(chunk, language)
                text = str(translated.get("vi") or "").strip()
                if text:
                    translated_parts.append(text)
                else:
                    failed_parts.append(index)

                model = translated.get("model")
                if model and model not in models:
                    models.append(str(model))
            except Exception:
                failed_parts.append(index)

        if not translated_parts:
            return (
                {
                    "vi": None,
                    "fromCache": False,
                    "error": public_translation_error(),
                },
                True,
            )

        notes = f"Dịch theo {len(chunks)} phần lớn rồi ghép lại để giữ mạch văn."
        if failed_parts:
            notes += f" Một số phần chưa dịch được: {', '.join(map(str, failed_parts))}."

        joined = "\n\n".join(translated_parts)
        return (
            {
                "vi": joined,
                "text": joined,
                "notes": notes,
                "model": ", ".join(models) if models else None,
                "fromCache": False,
                "chunkCount": len(chunks),
                "failedChunks": failed_parts,
            },
            True,
        )
    except Exception:
        return (
            {
                "vi": None,
                "fromCache": False,
                "error": public_translation_error(),
            },
            True,
        )


def _passage_rend(row: dict) -> str:
    hierarchy = row.get("hierarchy") or {}
    if isinstance(hierarchy, dict):
        return str(hierarchy.get("rend") or "")
    return ""


def _paragraph_label(row: dict, language: str) -> str | None:
    paragraph_no = row.get("paragraph_no")
    if paragraph_no and row.get("xml_paragraph_no"):
        return f"{t(language, 'results.paragraph')} {paragraph_no}"
    return None


def _section_paragraphs(rows: list[dict], language: str = DEFAULT_LANGUAGE) -> list[dict]:
    """Gom các đoạn thành khối hiển thị, NHƯNG giữ `passage_id` để neo được từng khối.

    Cùng luật gộp gāthā như trước: nhiều dòng kệ liền nhau là MỘT khối đọc. Vì thế một
    khối có thể chứa nhiều `passage_id`, và phải trả ra CẢ danh sách - đoạn khớp tìm kiếm
    có thể là dòng kệ thứ ba trong khối, mà người đọc cần được đưa tới đầu khối.

    `_join_section_passages` dựng chuỗi từ chính hàm này để hai bên không lệch nhau: luật
    gộp kệ mà nằm ở hai chỗ thì bản dịch AI (chia theo chuỗi) và phần hiển thị (chia theo
    khối) sẽ đánh số đoạn khác nhau, và nút nhảy sẽ trỏ sai chỗ.
    """
    blocks: list[dict] = []
    previous_rend = ""

    for row in rows:
        text = str(row["pali_text"]).strip()
        if not text:
            continue

        rend = _passage_rend(row)
        label = _paragraph_label(row, language)
        line = f"{label}\n{text}" if label else text

        if blocks and previous_rend in GATHA_RENDS and rend in GATHA_RENDS:
            blocks[-1]["text"] = f"{blocks[-1]['text']}\n{line}"
            blocks[-1]["passageIds"].append(str(row["id"]))
        else:
            blocks.append(
                {
                    "anchor": f"doan-{len(blocks) + 1}",
                    "passageIds": [str(row["id"])],
                    "sortOrder": row.get("sort_order"),
                    "label": label,
                    "text": line,
                }
            )

        previous_rend = rend

    return blocks


def _join_section_passages(rows: list[dict], language: str = DEFAULT_LANGUAGE) -> str:
    return "\n\n".join(block["text"] for block in _section_paragraphs(rows, language))


# Trang ngắn không cần mục lục - cả trang đã nằm trong một màn hình. Ngưỡng trùng với
# `READER_FALLBACK_MIN_PASSAGES` vì cùng một câu hỏi: "trang này có đủ dài để người đọc bị
# lạc không".
SECTION_OUTLINE_MIN_PASSAGES = 20


def _section_outline(section: dict) -> list[dict]:
    """Mục lục một tầng cho trang đọc, dựng TỪ CÂY SECTION - không gọi AI, không đoán.

    Đây là chỗ trả lời yêu cầu "tóm tắt từng phân đoạn rồi tạo nút nhảy" của khách mà
    không phải tóm tắt gì: các mục con này là tiêu đề do chính người biên tập sách in ra
    (`1. Bodhikathā`, `42. Sikkhāpadakathā`...). Đo trên `mul`: 295 trong 888 trang đọc từ
    50 đoạn trở lên có sẵn mục con đặt tên - `1. Ñāṇakathā` (499 đoạn) có 48 mục, và
    `3. Paccayānulomapaccanīyaṃ` (480 đoạn) có 152 mục. Nhờ AI tóm tắt thân bài các trang
    ấy tốn ~8.957 lượt gọi Gemini chỉ riêng `mul`, để nói lại đúng điều tiêu đề đã nói -
    kèm rủi ro bịa nội dung kinh điển.

    Chỉ lấy CẤP NÔNG NHẤT để mục lục là một tầng phẳng, không lồng: `Paccayānulomapaccanīyaṃ`
    có 152 mục ở cấp nông nhất, đổ cả cây con vào thì mục lục dài hơn chính bài.
    """
    own_span = section["end_sort_order"] - section["start_sort_order"] + 1
    if own_span < SECTION_OUTLINE_MIN_PASSAGES:
        return []
    rows = fetch_all(
        """
        select id, title, source_path, start_sort_order,
               coalesce(end_sort_order, start_sort_order) as end_sort_order
        from sections
        where document_id = %s
          and start_sort_order >= %s
          and coalesce(end_sort_order, start_sort_order) <= %s
          and id <> %s
        order by start_sort_order asc
        """,
        [
            section["document_id"],
            section["start_sort_order"],
            section["end_sort_order"],
            section["id"],
        ],
    )
    smaller = [
        row
        for row in rows
        if (row["end_sort_order"] - row["start_sort_order"] + 1) < own_span
    ]
    children = [
        row
        for row in smaller
        if _source_path_is_prefix(section.get("source_path"), row.get("source_path"))
    ]
    layer = _flat_outline_layer(children)
    if layer:
        return layer
    # DỰ PHÒNG cho "section rác" của bản import XML: có những section phủ TRỌN tài liệu
    # nhưng `source_path` lại nằm DƯỚI một mục con thật và tên trùng tổ tiên - `s0505m` có
    # section 0-3891 tên `Khuddakanikāye` với path [... 'Suttanipātapāḷi', '1. Uragavaggo',
    # 'Khuddakanikāye'], tức một khoảng cả-sách tự nhận nằm trong phẩm đầu tiên. Các câu kệ
    # uddāna trỏ section_id vào đúng mấy section này, nên trang đọc của chúng là cả bộ sách.
    #
    # `_source_path_is_prefix` từ chối chúng là ĐÚNG (xem `_canonical_reader_section`), nên
    # không dựng được mục lục theo quan hệ cha-con. Nhưng cấu trúc thật vẫn nằm cùng tài
    # liệu, nên vẫn lấy được một tầng mục lục: chỉ THÊM đường điều hướng, không đổi một
    # đoạn nào đang hiển thị.
    return _flat_outline_layer(smaller, require_disjoint=True)


def _flat_outline_layer(rows: list[dict], require_disjoint: bool = False) -> list[dict]:
    """Chọn MỘT tầng mục lục phẳng: cùng độ sâu `source_path`, xếp theo thứ tự đọc.

    Lấy tầng NÔNG NHẤT có từ 2 mục - mục lục một tầng, không lồng.

    `require_disjoint` CHỈ bật cho nhánh dự phòng (section rác), và có lý do đo được: ở đó
    quan hệ cha-con đã đổ nên các mục cùng độ sâu có thể chồng nhau hoặc vượt ra ngoài - đo
    trên `s0510m2` (Therīapadāna), tầng nông nhất cho ra các khoảng cộng lại **10.015 đoạn
    trong một trang 4.042 đoạn**.

    Cố tình KHÔNG bật cho nhánh con cháu đã xác thực prefix: đo trên toàn kho, bật ở đó làm
    19 trang mục lục bị cắt bớt dòng và 1 trang mất hẳn mục lục (`e1007n/Sūrassatīnīti`
    2 dòng -> 0). Ở đó quan hệ cây là thật, nên hai mục chồng nhau vẫn là hai mục thật và
    nút nhảy vẫn tới đúng đoạn của mình; bỏ chúng là mất đường điều hướng chứ không sửa gì.
    """
    by_depth: dict[int, list[dict]] = {}
    for row in rows:
        by_depth.setdefault(len(row.get("source_path") or []), []).append(row)
    for depth in sorted(by_depth):
        ordered = sorted(
            by_depth[depth],
            key=lambda row: (row["start_sort_order"], row["end_sort_order"]),
        )
        if require_disjoint:
            kept: list[dict] = []
            for row in ordered:
                if kept and row["start_sort_order"] <= kept[-1]["end_sort_order"]:
                    continue
                kept.append(row)
            ordered = kept
        if len(ordered) >= 2:
            return ordered
    return []


def _outline_entries(section: dict, paragraphs: list[dict]) -> list[dict]:
    """Gắn mỗi mục của mục lục vào KHỐI đầu tiên của nó, để nút nhảy có đích thật.

    Neo theo `sort_order`, KHÔNG theo thứ tự mục: `_section_paragraphs` bỏ các đoạn rỗng
    và gộp dòng kệ, nên "khối thứ N" không nhất thiết là mục con thứ N. Mục nào không tìm
    được khối nào bắt đầu trong phạm vi của nó thì bỏ hẳn - một nút nhảy không có đích còn
    tệ hơn không có nút.
    """
    entries: list[dict] = []
    for row in _section_outline(section):
        anchor = next(
            (
                block["anchor"]
                for block in paragraphs
                if block.get("sortOrder") is not None
                and row["start_sort_order"] <= block["sortOrder"] <= row["end_sort_order"]
            ),
            None,
        )
        if not anchor:
            continue
        entries.append(
            {
                "title": str(row["title"] or "").strip(),
                "anchor": anchor,
                "passageCount": row["end_sort_order"] - row["start_sort_order"] + 1,
            }
        )
    return entries if len(entries) >= 2 else []


def _section_payload(
    section_id: str,
    include_translation: bool = True,
    language: str = DEFAULT_LANGUAGE,
    source: str = AI_SOURCE,
) -> dict:
    selected = normalize_source(source)
    section = fetch_one(
        """
        select s.id, s.document_id, s.title, s.source_path, s.start_sort_order,
               coalesce(s.end_sort_order, s.start_sort_order) as end_sort_order,
               d.corpus_type
        from sections s join documents d on d.id = s.document_id
        where s.id = %s
        """,
        [section_id],
    )
    if not section:
        raise HTTPException(status_code=404, detail="Section not found.")

    # Phạm vi đọc là đúng section sâu nhất đã nhận từ `passages.section_id`; không nâng
    # lên tổ tiên dựa trên hậu tố nhan đề. Phép cắt chỉ sửa những khoảng XML ghi lấn sang
    # section không phải hậu duệ, không thay đổi cấp của section.
    section = _clip_overreaching_range(section)

    rows = fetch_all(
        """
        select
          id,
          sort_order,
          coalesce(display_paragraph_no, xml_paragraph_no, paragraph_no) as paragraph_no,
          xml_paragraph_no,
          pali_text,
          hierarchy
        from passages
        where document_id = %s
          and sort_order between %s and %s
        order by sort_order asc
        """,
        [section["document_id"], section["start_sort_order"], section["end_sort_order"]],
    )
    paragraphs = _section_paragraphs(rows, language)
    pali_text = "\n\n".join(block["text"] for block in paragraphs)
    if include_translation:
        translation, attempted_translation = _translate_section_text(pali_text, language)
    else:
        translation, attempted_translation = {"vi": None, "text": None, "fromCache": False, "pending": True}, False
    source_path = section.get("source_path") or []
    # Bản dịch của dịch giả cho toàn bộ section đang đọc, ghép theo đúng thứ tự các đoạn.
    # Tên tham số `covers_whole_sutta` là hợp đồng cũ của tầng nguồn dịch; tại đây nó có
    # nghĩa là đã truyền đủ mọi đoạn của phạm vi đang hiển thị.
    official_list = official_translations_merged(
        [str(row["id"]) for row in rows], language, covers_whole_sutta=True
    )
    # Liet ke DU moi dich gia chu khong chi nguon co du lieu, dung nhu khach yeu cau:
    # nguon nao chua co ban dich cho muc nay van hien tab, bam vao thi bao "Hien khong co
    # ban dich chinh thuc nao". Truoc day chi dung tab tu `official_list` nen tab
    # Indacanda bien mat o moi bo kinh chua nap - giong het loi ben trang ket qua.
    with_data = {str(item["source"]) for item in official_list}
    is_abhidhamma = "abhidhammapitaka" in normalize_pali(" ".join(map(str, source_path)))
    # Tab AI đứng đầu, LUÔN available: AI dịch được mọi đoạn nên không có trạng thái
    # "chưa có dữ liệu" như các dịch giả. Thiếu tab này thì trang chỉ vào được AI ở lần
    # tải đầu (mặc định `source=ai`); bấm sang bất kỳ dịch giả nào là hết đường quay lại,
    # vì JS chỉ điều hướng qua nút có `data-section-tab`.
    available = [
        {
            "source": AI_SOURCE,
            "label": source_label(AI_SOURCE, language),
            "available": True,
            "unavailableReason": "",
        }
    ]
    for source_id in SOURCE_ORDER:
        if source_id == AI_SOURCE:
            continue
        if source_id == "brahmali" and source_id not in with_data:
            unavailable_reason = t(language, "translation.brahmaliVinayaOnly")
        elif is_abhidhamma and source_id not in with_data:
            unavailable_reason = t(language, "translation.noAbhidhammaCoverage")
        else:
            unavailable_reason = t(language, "translation.noOfficial")
        available.append(
            {
                "source": source_id,
                "label": source_label(source_id, language),
                "available": source_id in with_data,
                "unavailableReason": unavailable_reason,
            }
        )
    if selected != AI_SOURCE and selected not in {item["source"] for item in available}:
        selected = AI_SOURCE
    chosen = next((item for item in official_list if item["source"] == selected), None)

    return {
        "sectionId": str(section["id"]),
        "officialTranslations": official_list,
        "availableSources": available,
        "selectedSource": selected,
        "selectedTranslation": chosen,
        "title": section["title"],
        # Cùng lý do như trang kết quả: địa chỉ phải chỉ được một chỗ duy nhất, và phải sạch
        # rác + gọi đúng tên công trình (xem `_fix_source_path`).
        "sourcePath": _with_path_occurrence(
            " -> ".join(
                _fixed_paths_for_sections([str(section["id"])]).get(str(section["id"]))
                or (source_path if isinstance(source_path, list) else [])
            ),
            _duplicate_path_ranks([str(section["id"])]).get(str(section["id"])),
            language,
        ),
        "passageCount": len(rows),
        "paliText": pali_text,
        # Khối có neo, để nhảy tới đúng đoạn vừa khớp tìm kiếm. `paliText` giữ nguyên vì
        # bản dịch AI chia theo chuỗi ký tự - đổi nó là đổi cách chia chunk.
        "paragraphs": paragraphs,
        "outline": _outline_entries(section, paragraphs),
        "translation": translation,
        "attemptedTranslation": attempted_translation,
        "warning": t(language, "translation.aiWarning"),
    }


def _outline_labels(section: dict, language: str) -> dict[str, str]:
    """Dịch NHAN ĐỀ mục lục - gộp một lượt gọi cho cả trang, rồi cache theo hash.

    Khách xin "AI tóm tắt từng phân đoạn rồi tạo nút bấm". Dịch tiêu đề đạt đúng phần
    khách cần (đọc được bằng tiếng Việt) mà rẻ hơn hai bậc: tóm tắt thân bài các trang dài
    `mul` tốn ~8.957 lượt gọi Gemini, còn cách này tốn MỘT lượt cho mỗi trang có mục lục
    (~295 trang `mul`), và `translate_text_cached` giữ theo hash nên chỉ tốn một lần đầu.
    Quan trọng hơn: tiêu đề là chữ của người biên tập sách, nên không có chỗ cho AI bịa
    nội dung kinh điển - việc của nó chỉ là dịch một cụm từ.

    Gộp cả danh sách vào MỘT lượt gọi chứ không dịch từng dòng: 68 dòng của
    `1. Mahākhandhako` mà gọi riêng là 68 lượt, và mô hình cũng mất ngữ cảnh chuỗi mục.

    Sai số dòng thì trả về rỗng để giao diện giữ nguyên tiêu đề Pāli. Gán lệch nhãn còn tệ
    hơn không có nhãn: người đọc sẽ bấm vào "phần nói về X" rồi tới đoạn nói về chuyện khác.
    """
    titles = [str(row["title"] or "").strip() for row in _section_outline(section)]
    titles = [title for title in titles if title]
    if not titles:
        return {}
    payload = translate_text_cached("\n".join(titles), language)
    text = str(payload.get("text") or payload.get("vi") or "").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != len(titles):
        return {}
    return dict(zip(titles, lines))


@app.get("/api/sections/{section_id}/outline-labels")
def section_outline_labels_api(
    section_id: str, request: Request, lang: str | None = Query(None)
):
    """Nhãn tiếng Việt cho mục lục. Gọi RIÊNG và chỉ khi người đọc mở mục lục ra.

    Tách khỏi `/section-page` có chủ đích: nhúng vào đó thì mọi lần mở bài kinh dài đều
    kéo theo một lượt gọi Gemini, kể cả khi người đọc không thèm nhìn mục lục.
    """
    language = request_language(request, lang)
    section = _reader_section_by_id(section_id)
    if not section:
        raise HTTPException(status_code=404, detail="Section not found.")
    return {"sectionId": str(section["id"]), "labels": _outline_labels(section, language)}


@app.get("/api/sections/{section_id}")
def section_api(section_id: str, request: Request, lang: str | None = Query(None)):
    return _section_payload(section_id, include_translation=False, language=request_language(request, lang))


@app.get("/api/sections/{section_id}/summary")
def section_summary_api(section_id: str, request: Request, lang: str | None = Query(None)):
    language = request_language(request, lang)
    section = _section_payload(section_id, include_translation=False, language=language)
    from app.translator import summarize_section_text
    summary = summarize_section_text(section, language)
    return summary


@app.get("/api/sections/{section_id}/translate")
def section_translate_api(section_id: str, request: Request, lang: str | None = Query(None)):
    language = request_language(request, lang)
    section = _section_payload(section_id, include_translation=True, language=language)
    return {
        "ok": bool(section.get("translation", {}).get("vi")),
        "sectionId": section["sectionId"],
        "translation": section["translation"],
        "warning": section["warning"],
    }


@app.get("/api/sections/{section_id}/translate-chunk")
def section_translate_chunk_api(
    section_id: str,
    request: Request,
    chunk: int = Query(0, ge=0),
    lang: str | None = Query(None),
    source: str | None = Query(None),
):
    language = request_language(request, lang)
    section = _section_payload(section_id, include_translation=False, language=language)
    translation_source = normalize_source(source)
    chunks = _chunk_section_text(
        str(section.get("paliText") or ""),
        max_chars=SECTION_TRANSLATION_STREAM_CHUNK_CHARS,
    )
    total_chunks = len(chunks)
    if total_chunks == 0:
        return {
            "ok": True,
            "sectionId": section["sectionId"],
            "chunkIndex": 0,
            "totalChunks": 0,
            "hasMore": False,
            "translation": {"vi": "", "text": "", "fromCache": False},
            "warning": section["warning"],
        }
    if chunk >= total_chunks:
        raise HTTPException(status_code=404, detail="Translation chunk not found.")

    if translation_source != AI_SOURCE:
        human = resolve_human_translation(translation_source, None, chunks[chunk], language)
        return {
            "ok": bool(human),
            "sectionId": section["sectionId"],
            "chunkIndex": chunk,
            "totalChunks": total_chunks,
            "hasMore": False,
            "translation": human or unavailable_translation(language),
            "warning": None,
        }

    try:
        translation = translate_text_cached(chunks[chunk], language)
        ok = bool(translation.get("vi"))
    except Exception:
        translation = {
            "vi": None,
            "text": None,
            "fromCache": False,
            "error": public_translation_error(),
        }
        ok = False

    return {
        "ok": ok,
        "sectionId": section["sectionId"],
        "chunkIndex": chunk,
        "totalChunks": total_chunks,
        "hasMore": chunk + 1 < total_chunks,
        "translation": translation,
        "warning": section["warning"],
    }


@app.get("/section-page/{section_id}", response_class=HTMLResponse)
def section_page(
    request: Request,
    section_id: str,
    lang: str | None = Query(None),
    source: str | None = Query(None),
):
    language = request_language(request, lang)
    section = _section_payload(section_id, include_translation=False, language=language, source=source or AI_SOURCE)
    return templates.TemplateResponse(
        "section.html",
        _template_context(request, language, section=section),
    )


@app.get("/api/passages/{passage_id}")
def passage_api(passage_id: str, request: Request):
    row = fetch_one(
        "select id, document_id, sort_order, paragraph_no, pali_text, hierarchy from passages where id = %s",
        [passage_id],
    )
    if not row:
        raise HTTPException(status_code=404, detail="Passage not found.")

    nearby = fetch_all(
        """
        select id, paragraph_no, pali_text
        from passages
        where document_id = %s and sort_order between %s and %s and id <> %s
        order by sort_order asc
        """,
        [row["document_id"], row["sort_order"] - 2, row["sort_order"] + 2, row["id"]],
    )
    source_path = row.get("hierarchy", {}).get("sourcePath")
    return {
        "id": str(row["id"]),
        "sourcePath": " -> ".join(source_path) if isinstance(source_path, list) else "",
        "paragraphNo": row["paragraph_no"],
        "paliText": row["pali_text"],
        "translation": _translation_or_error(passage_id, request_language(request)),
        "nearbyPassages": nearby,
    }


@app.get("/help", response_class=HTMLResponse)
def help_page_route(request: Request, lang: str | None = Query(None)):
    """Trang người dùng: đọc hướng dẫn (theo lô)."""
    language = request_language(request, lang)
    return templates.TemplateResponse(
        "help.html",
        _template_context(
            request,
            language,
            help_page=get_help_page(language, 1, HELP_GUIDE_BATCH),
            help_batch=HELP_GUIDE_BATCH,
            notice=get_notice(language),
            ga_measurement_id=settings().get("ga_measurement_id", ""),
        ),
    )


@app.get("/feedback", response_class=HTMLResponse)
def feedback_page_route(request: Request, lang: str | None = Query(None)):
    """Trang người dùng: gửi góp ý / xin trợ giúp."""
    language = request_language(request, lang)
    return templates.TemplateResponse(
        "feedback.html",
        _template_context(
            request,
            language,
            feedback_max_chars=FEEDBACK_MAX_CHARS,
            notice=get_notice(language),
            ga_measurement_id=settings().get("ga_measurement_id", ""),
        ),
    )


@app.get("/api/help")
def help_api(request: Request, page: int = Query(1, ge=1), lang: str | None = Query(None)):
    """Một mẻ nội dung hướng dẫn, để client cuộn trang nạp thêm."""
    language = request_language(request, lang)
    return get_help_page(language, page, HELP_GUIDE_BATCH)


@app.get("/help-sutta/{item_id}", response_class=HTMLResponse)
def help_sutta_page(request: Request, item_id: str, lang: str | None = Query(None)):
    """Trang riêng đọc bài kinh Pali do admin gắn vào một mục hướng dẫn."""
    language = request_language(request, lang)
    sutta = get_help_sutta(item_id)
    if not sutta:
        raise HTTPException(status_code=404, detail="Manual sutta not found.")
    if not sutta["title"]:
        sutta["title"] = t(language, "manualSutta.defaultTitle")
    return templates.TemplateResponse(
        "help_sutta.html",
        _template_context(
            request,
            language,
            sutta=sutta,
            strings=ui_strings(language),
            warning=t(language, "translation.aiWarning"),
            ga_measurement_id=settings().get("ga_measurement_id", ""),
        ),
    )


@app.get("/api/help-sutta/{item_id}/summary")
def help_sutta_summary_api(item_id: str, request: Request, lang: str | None = Query(None)):
    sutta = get_help_sutta(item_id)
    if not sutta:
        raise HTTPException(status_code=404, detail="Manual sutta not found.")
    return summarize_plain_pali_text(sutta["pali_text"], request_language(request, lang))


@app.get("/api/help-sutta/{item_id}/translate-chunk")
def help_sutta_translate_chunk_api(
    item_id: str,
    request: Request,
    chunk: int = Query(0, ge=0),
    lang: str | None = Query(None),
):
    sutta = get_help_sutta(item_id)
    if not sutta:
        raise HTTPException(status_code=404, detail="Manual sutta not found.")
    language = request_language(request, lang)
    chunks = _chunk_section_text(
        sutta["pali_text"], max_chars=SECTION_TRANSLATION_STREAM_CHUNK_CHARS
    )
    if chunk >= len(chunks):
        raise HTTPException(status_code=404, detail="Translation chunk not found.")
    try:
        translation = translate_text_cached(chunks[chunk], language)
        ok = bool(translation.get("text") or translation.get("vi"))
    except Exception:
        translation = {
            "vi": None,
            "text": None,
            "fromCache": False,
            "error": public_translation_error(),
        }
        ok = False
    return {
        "ok": ok,
        "itemId": sutta["id"],
        "chunkIndex": chunk,
        "totalChunks": len(chunks),
        "hasMore": chunk + 1 < len(chunks),
        "translation": translation,
        "warning": t(language, "translation.aiWarning"),
    }


@app.post("/api/feedback")
def feedback_submit(payload: dict, request: Request):
    """Gửi một góp ý / yêu cầu hỗ trợ về tìm kiếm."""
    message = str(payload.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message trống.")
    if len(message) > FEEDBACK_MAX_CHARS:
        raise HTTPException(status_code=400, detail="message quá dài.")
    language = request_language(request, payload.get("language"))
    add_feedback(message, language)
    return {"ok": True}


@app.exception_handler(Exception)
def handle_exception(_request: Request, exc: Exception):
    status = exc.status_code if isinstance(exc, HTTPException) else 500
    detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
    return JSONResponse({"error": detail}, status_code=status)


def get_current_admin(request: Request):
    if not request.session.get("admin_logged_in"):
        raise HTTPException(status_code=status.HTTP_302_FOUND, headers={"Location": "/admin/login"})
    return True


@app.get("/admin", include_in_schema=False)
def admin_root(request: Request):
    if request.session.get("admin_logged_in"):
        return RedirectResponse(url="/admin/history", status_code=status.HTTP_302_FOUND)
    return RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request):
    if request.session.get("admin_logged_in"):
        return RedirectResponse(url="/admin/history", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse("admin_login.html", {"request": request})


@app.post("/admin/login")
def admin_login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    current_username_bytes = username.encode("utf8")
    correct_username_bytes = str(settings().get("admin_username", "")).encode("utf8")
    is_correct_username = secrets.compare_digest(current_username_bytes, correct_username_bytes)

    current_password_bytes = password.encode("utf8")
    correct_password_bytes = str(settings().get("admin_password", "")).encode("utf8")
    is_correct_password = secrets.compare_digest(current_password_bytes, correct_password_bytes)

    if not (is_correct_username and is_correct_password):
        return templates.TemplateResponse("admin_login.html", {"request": request, "error": "Sai tên đăng nhập hoặc mật khẩu"})
    
    request.session["admin_logged_in"] = True
    return RedirectResponse(url="/admin/history", status_code=status.HTTP_302_FOUND)


@app.get("/admin/logout")
def admin_logout(request: Request):
    request.session.pop("admin_logged_in", None)
    return RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)


# Không còn trần: cuộn tới đâu tải tới đó, mỗi lượt một mẻ nhỏ. Trước đây phải chọn số
# dòng mỗi trang rồi bấm chuyển trang; khách muốn xem hết nên bỏ hẳn phân trang.
ADMIN_HISTORY_BATCH = 20
# Chặn trên cho tham số của client, đề phòng ai đó gọi thẳng API xin vài trăm nghìn dòng.
ADMIN_HISTORY_MAX_BATCH = 100


def _admin_history_where(keyword: str, only_empty: bool, only_timeout: bool) -> tuple[str, list[object]]:
    conditions: list[str] = []
    params: list[object] = []
    if keyword:
        conditions.append("query ilike %s")
        params.append(f"%{keyword}%")
    if only_timeout:
        conditions.append("status = 'timeout'")
    elif only_empty:
        # Đúng các lượt tìm không ra kết quả nào - chính là nhóm khách muốn soi.
        conditions.append("coalesce(array_length(result_passage_ids, 1), 0) = 0 and (status is null or status != 'timeout')")
    return (("where " + " and ".join(conditions)) if conditions else ""), params


def _admin_history_rows(keyword: str, only_empty: bool, only_timeout: bool, limit: int,
                        before_time: str | None, before_id: str | None) -> list[dict]:
    """Một mẻ lịch sử, cũ dần kể từ mốc `before`.

    Phân trang theo CON TRỎ chứ không theo `offset`: `search_logs` được ghi thêm sau mỗi
    lượt tìm kiếm, nên trong lúc người dùng cuộn thì offset bị đẩy lệch - dòng đã xem lại
    hiện lại, dòng chưa xem thì trượt mất. Mốc `(created_at, id)` không bị ảnh hưởng.
    """
    where_sql, params = _admin_history_where(keyword, only_empty, only_timeout)
    if before_time and before_id:
        cursor_sql = "(created_at, id) < (%s::timestamptz, %s::uuid)"
        where_sql = f"{where_sql} and {cursor_sql}" if where_sql else f"where {cursor_sql}"
        params = [*params, before_time, before_id]
    return fetch_all(
        f"""
        select id, query, filters,
               coalesce(array_length(result_passage_ids, 1), 0) as result_count,
               status,
               created_at
        from search_logs
        {where_sql}
        order by created_at desc, id desc
        limit %s
        """,
        [*params, limit],
    )


@app.get("/admin/history", response_class=HTMLResponse)
def admin_history(
    request: Request,
    q: str = Query(""),
    only_empty: bool = Query(False),
    only_timeout: bool = Query(False),
    _: str = Depends(get_current_admin),
):
    keyword = q.strip()
    where_sql, params = _admin_history_where(keyword, only_empty, only_timeout)

    # Chỉ mẻ đầu; phần còn lại do trình duyệt xin thêm khi cuộn tới đáy.
    logs = _admin_history_rows(keyword, only_empty, only_timeout, ADMIN_HISTORY_BATCH, None, None)

    total_row = fetch_one(f"select count(*) as cnt from search_logs {where_sql}", params)
    total_logs = total_row["cnt"] if total_row else 0

    all_row = fetch_one("select count(*) as cnt from search_logs")
    all_logs = all_row["cnt"] if all_row else 0

    empty_row = fetch_one(
        "select count(*) as cnt from search_logs where coalesce(array_length(result_passage_ids, 1), 0) = 0 and (status is null or status != 'timeout')"
    )
    empty_logs = empty_row["cnt"] if empty_row else 0

    timeout_row = fetch_one("select count(*) as cnt from search_logs where status = 'timeout'")
    timeout_logs = timeout_row["cnt"] if timeout_row else 0

    top_queries = fetch_all(
        """
        select query, count(*) as count
        from search_logs
        group by query
        order by count desc
        limit 10
        """
    )

    corpus_labels, pitaka_labels = _admin_filter_labels()

    return templates.TemplateResponse(
        "admin_history.html",
        {
            "request": request,
            "logs": logs,
            "total_logs": total_logs,
            "all_logs": all_logs,
            "empty_logs": empty_logs,
            "timeout_logs": timeout_logs,
            "corpus_options": corpus_labels,
            "pitaka_options": pitaka_labels,
            "top_queries": top_queries,
            "batch": ADMIN_HISTORY_BATCH,
            "q": keyword,
            "only_empty": only_empty,
            "only_timeout": only_timeout,
            "ga_measurement_id": settings().get("ga_measurement_id", ""),
        },
    )


@app.get("/api/admin/history/rows")
def api_admin_history_rows(
    q: str = Query(""),
    only_empty: bool = Query(False),
    only_timeout: bool = Query(False),
    limit: int = Query(ADMIN_HISTORY_BATCH, ge=1, le=ADMIN_HISTORY_MAX_BATCH),
    before_time: str = Query(""),
    before_id: str = Query(""),
    _: str = Depends(get_current_admin),
):
    """Mẻ lịch sử tiếp theo cho việc cuộn vô hạn ở `/admin/history`.

    Xin dư MỘT dòng rồi cắt bỏ, để biết còn dữ liệu phía sau hay không mà không phải chạy
    thêm một câu `count(*)` cho mỗi lần cuộn.
    """
    corpus_labels, pitaka_labels = _admin_filter_labels()
    rows = _admin_history_rows(
        q.strip(), only_empty, only_timeout, limit + 1, before_time or None, before_id or None
    )
    has_more = len(rows) > limit
    rows = rows[:limit]

    payload = []
    for row in rows:
        filters = row.get("filters") or {}
        badges = [corpus_labels.get(c, c) for c in (filters.get("corpusType") or [])]
        pitaka = filters.get("pitakaType")
        if pitaka:
            badges.append(pitaka_labels.get(pitaka, pitaka))
        created = row.get("created_at")
        payload.append(
            {
                "id": str(row["id"]),
                "time": created.strftime("%H:%M:%S %d/%m/%Y") if created else "N/A",
                "createdAt": created.isoformat() if created else None,
                "query": row.get("query") or "",
                "badges": badges,
                "resultCount": int(row.get("result_count") or 0),
                "status": row.get("status") or "success",
            }
        )
    return {"rows": payload, "hasMore": has_more}


@app.get("/admin/notice", response_class=HTMLResponse)
def admin_notice_page(request: Request, saved: bool = Query(False), _: str = Depends(get_current_admin)):
    return templates.TemplateResponse(
        "admin_notice.html",
        {
            "request": request,
            "notice": get_notice_config(),
            "languages": LANGUAGES,
            "language_options": language_options(),
            "saved": saved,
            "ga_measurement_id": settings().get("ga_measurement_id", ""),
        },
    )


@app.post("/admin/notice")
async def admin_notice_save(request: Request, _: str = Depends(get_current_admin)):
    form = await request.form()
    enabled = str(form.get("enabled") or "").strip() in {"1", "on", "true"}
    content = {
        code: {
            "title": str(form.get(f"title_{code}") or ""),
            "body": str(form.get(f"body_{code}") or ""),
        }
        for code in LANGUAGES
    }
    save_notice(enabled, content)
    return RedirectResponse(url="/admin/notice?saved=1", status_code=status.HTTP_302_FOUND)


@app.get("/api/admin/history/{log_id}")
def api_admin_history_detail(log_id: str, _: str = Depends(get_current_admin)):
    log = fetch_one("select * from search_logs where id = %s", [log_id])
    if not log:
        raise HTTPException(status_code=404, detail="Không tìm thấy lịch sử này.")

    passage_ids = log.get("result_passage_ids") or []
    passages = []
    
    if passage_ids:
        rows = fetch_all(
            """
            select 
                p.id, p.pali_text, p.paragraph_no, p.display_paragraph_no, p.xml_paragraph_no, p.hierarchy,
                d.file_name, d.corpus_type,
                s.title as section_title, s.source_path as section_source_path,
                t.translated_text
            from passages p
            join documents d on d.id = p.document_id
            left join sections s on s.id = p.section_id
            left join translations t on t.passage_id = p.id
            where p.id = any(%s::uuid[])
            """,
            [passage_ids]
        )
        passage_map = {str(row["id"]): row for row in rows}
        passages = [passage_map[str(pid)] for pid in passage_ids if str(pid) in passage_map]

    pitaka_type = None
    if log.get("filters"):
        pitaka_type = log["filters"].get("pitakaType")

    return {
        "log": {
            "id": log["id"],
            "query": log["query"],
            "created_at": log["created_at"].strftime('%H:%M:%S %d/%m/%Y') if log["created_at"] else "N/A",
            "filters": log["filters"],
        },
        "passages": [
            {
                "file_name": p["file_name"],
                "section_title": p["section_title"],
                "paragraph_no": p.get("display_paragraph_no") or p.get("xml_paragraph_no") or p.get("paragraph_no"),
                "pali_text": p["pali_text"],
                "translated_text": p["translated_text"],
                "breadcrumb": _display_source(p, [p["corpus_type"]], pitaka_type)
            } for p in passages
        ]
    }


@app.post("/api/admin/history/clear")
def clear_admin_history(_: str = Depends(get_current_admin)):
    execute("truncate table search_logs restart identity;")
    return {"ok": True, "message": "Đã xóa toàn bộ lịch sử tìm kiếm."}


@app.get("/admin/help-feedback", response_class=HTMLResponse)
def admin_help_feedback_page(
    request: Request,
    saved: bool = Query(False),
    _: str = Depends(get_current_admin),
):
    """Trang admin: soạn hướng dẫn và đọc góp ý của người dùng trên cùng một trang."""
    return templates.TemplateResponse(
        "admin_help_feedback.html",
        {
            "request": request,
            "help": get_help_config(),
            "languages": LANGUAGES,
            "language_options": language_options(),
            "feedback_batch": FEEDBACK_BATCH,
            "saved": saved,
            "ga_measurement_id": settings().get("ga_measurement_id", ""),
        },
    )


@app.post("/admin/help-feedback/help")
async def admin_help_feedback_save_help(request: Request, _: str = Depends(get_current_admin)):
    """Lưu nội dung hướng dẫn cho từng ngôn ngữ."""
    form = await request.form()
    content = {}
    for code in LANGUAGES:
        try:
            items = json.loads(str(form.get(f"items_{code}") or "[]"))
        except json.JSONDecodeError:
            items = []
        content[code] = {
            "heading": str(form.get(f"heading_{code}") or ""),
            "body": str(form.get(f"body_{code}") or ""),
            "items": items if isinstance(items, list) else [],
            "font_size": str(form.get(f"font_size_{code}") or "16"),
            "font_color": str(form.get(f"font_color_{code}") or "#333333"),
        }
    save_help(content)
    return RedirectResponse(url="/admin/help-feedback?saved=1", status_code=status.HTTP_302_FOUND)


@app.get("/api/admin/feedback")
def api_admin_feedback(
    offset: int = Query(0, ge=0),
    limit: int = Query(FEEDBACK_BATCH, ge=1, le=FEEDBACK_BATCH * 5),
    _: str = Depends(get_current_admin),
):
    """Mẻ góp ý tiếp theo cho việc cuộn vô hạn ở trang admin."""
    rows = list_feedback(offset, limit)
    return {"rows": rows, "total": count_feedback()}


@app.post("/api/admin/feedback/clear")
def clear_admin_feedback(_: str = Depends(get_current_admin)):
    clear_feedback()
    return {"ok": True, "message": "Đã xóa toàn bộ góp ý."}


@app.delete("/api/admin/feedback/{feedback_id}")
def delete_admin_single_feedback(feedback_id: int, _: str = Depends(get_current_admin)):
    from app.user_feedback import delete_single_feedback
    delete_single_feedback(feedback_id)
    return {"ok": True}



@app.get("/admin/analytics", response_class=HTMLResponse)
def admin_analytics_page(request: Request, _: str = Depends(get_current_admin)):
    return templates.TemplateResponse(
        "admin_analytics.html",
        {
            "request": request,
            "ga_measurement_id": settings().get("ga_measurement_id", ""),
        },
    )

