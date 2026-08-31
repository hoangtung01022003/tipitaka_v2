"""Gợi ý từ khoá Pāḷi cho câu hỏi khó - tính năng ĐỘC LẬP với luồng tìm kiếm.

**ĐỌC TRƯỚC: file này có HAI bản, chỉ một bản đang chạy.**

Bản ĐANG CHẠY nằm ở CUỐI file (`_direct_keyword_prompt` -> `suggest_pali_keywords`): hỏi
Gemini bằng đúng câu lệnh khách tự viết rồi render thẳng kết quả, không lọc, không chấm
điểm. Bản CŨ - toàn bộ phần giữa file, từ khối hằng số điểm tới
`_suggest_pali_keywords_legacy` - đã TẮT sau khi khách dùng thử và báo kết quả không
chính xác; giữ lại nguyên vẹn để còn quay về nếu bản mới cũng không ổn.

Phần docstring còn lại dưới đây mô tả BẢN CŨ, để nguyên làm nhật ký thiết kế.

Khách vẫn tìm bằng tiếng Việt như cũ. Khi câu hỏi khó quá và cách tìm thường không ra,
khách bấm nút này để lấy cụm Pāḷi rồi tự dán vào ô tìm kiếm - đúng việc khách đang phải
làm tay bằng Gemini web ("hỏi Gemini lấy từ khoá rồi tìm lại").

**Để riêng một module chứ không nhét vào `search_engine`.** Đây không phải một bước của
pipeline xếp hạng: sửa phần gợi ý này không được phép làm xê dịch một kết quả tìm kiếm
nào. Chiều ngược lại cũng vậy - `search_engine` không import module này.

**"Cụm từ khoá chính" KHÔNG gọi `extract_search_keyword_with_ai` và KHÔNG lùi về nó khi
suy luận sâu thất bại.** Bản đầu có gọi lại đúng hàm đó - và hoá ra đó là lỗi thiết kế:
`search_engine._rank_candidates` đã TỰ ĐỘNG gọi CHÍNH hàm đó cho mọi câu tiếng Việt (không
phải Pāḷi) trước khi xếp hạng, rồi tiêm thẳng kết quả vào các trường quyết định kết quả
(`queryTerms`/`querySegmentTexts`). Vì cùng hàm + cùng khoá đệm (`query.strip()` + ngôn
ngữ - `clean_query` không hề nằm trong khoá), bấm "Tìm ngay" ở cụm chính chỉ tìm lại đúng
thứ pipeline đã tự thử ngầm - vô nghĩa với NHỮNG CÂU HỎI KHÓ mà tính năng này sinh ra để
giải quyết.

`extract_main_keyword_deep()` bên dưới thay vào chỗ đó bằng một lượt suy luận HOÀN TOÀN
ĐỘC LẬP: ngữ cảnh đầy đủ của câu hỏi (không rút gọn), yêu cầu model nhận diện câu
chuyện/bài kinh cụ thể trước khi đoán từ khoá, ra NHIỀU ứng viên rồi tự kiểm tra từng ứng
viên có thật trong kho hay không - chứ không tin mù một lượt đoán duy nhất. Khi không cụm
nào qua được cửa kiểm tra, hàm trả `None` và `suggest_pali_keywords` CHỈ ĐƠN GIẢN không
hiện "Cụm từ khoá chính" - không lùi về hàm cũ dưới bất kỳ hình thức nào, kể cả làm
phương án dự phòng. Tính năng này chỉ render đúng những gì lượt suy luận độc lập tự tìm
ra, không hơn. Đánh đổi: chậm hơn, tốn thêm lượt gọi Gemini, và một số câu hỏi sẽ không
có "cụm chính" để hiện - đã được khách xác nhận chấp nhận.

`expand_query_with_ai` cho danh sách "thuật ngữ liên quan" vẫn dùng lại như cũ (không
đổi) - phần đó chưa từng bị coi là vấn đề trong cuộc trao đổi dẫn tới thay đổi này, chỉ
"Cụm từ khoá chính" mới là chỗ được yêu cầu tách biệt.
"""

import re
import time
from functools import lru_cache

from google import genai
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb

from .config import settings
from .db import execute, fetch_all, fetch_one
from .glossary import analyze_query, canonicalize_query
from .i18n import DEFAULT_LANGUAGE, normalize_language, t
from .normalize import normalize_pali
from .query_expander import (
    _cache_key,
    _client,
    _is_retryable_error,
    _keyword_models,
    expand_query_with_ai,
)

# Số thuật ngữ hiện ra cho khách, KHÔNG TÍNH cụm chính - khách yêu cầu "khoảng 5 kết quả
# (bao gồm cả key chính)", nên 4 + 1 cụm chính = 5.
MAX_TERMS = 4

# Số ứng viên tối đa đem đi kiểm tra sự tồn tại. `_is_usable_term` chạy một truy vấn cho
# mỗi mục (có `lru_cache`), nên phải chặn trần trước khi lọc - dư ra so với `MAX_TERMS` vì
# vài ứng viên đầu bảng có thể là từ AI bịa, không tồn tại trong kho.
MAX_TERM_CANDIDATES = 10

# Bỏ hư từ Pāḷi. Cụm AI trả về hay kèm `vā`, `ca`, `hi`, `pi`, `na` - có mặt khắp nơi
# trong kho nên qua được cửa kiểm tra tồn tại, nhưng làm từ khoá thì vô dụng.
MIN_TERM_LENGTH = 4

# Ngưỡng cho CÂU PĀḶI TRỌN VẸN - xem `_resolve_full_sentence` để biết vì sao để lỏng.
# Dưới 4 chữ nội dung thì không còn là một câu, chỉ là cụm - đã có mục riêng cho cụm rồi.
FULL_SENTENCE_MIN_WORDS = 4
FULL_SENTENCE_MIN_REAL_RATIO = 0.6

# Số cụm ứng viên xin AI đưa ra cho "cụm chính". Xin NHIỀU (6) chứ không ít, vì bộ lọc
# nguyên văn bên dưới loại rất mạnh tay: đo thật thì 2/3 ứng viên của prompt cũ không hề
# tồn tại nguyên văn trong kho. Xin dư để còn cái mà chọn, chi phí chỉ là vài chục chữ
# trong CÙNG MỘT lượt gọi Gemini, không phải thêm lượt gọi nào.
DEEP_CANDIDATE_COUNT = 6

# Trần đếm tần suất. Chỉ cần phân biệt "hiếm" với "phổ thông", không cần con số chính xác.
PHRASE_FREQ_CAP = 400

# Từ ĐƠN phổ thông hơn mức này thì coi như không đặc trưng. Đo thật: `titthiya` (500+ đoạn)
# trả về sai bài, `jaccandha` (100 đoạn) trả về đúng bài.
GENERIC_TERM_MAX = 250

# Bốn thang điểm TÁCH RỜI nhau, không phải bốn mức trong cùng một thang: hạng trên luôn
# thắng hạng dưới bất kể độ hiếm chênh nhau bao nhiêu.
#
# **CỤM TRÍCH THÂN KINH ĐỨNG TRÊN TÊN BÀI KINH.** Bản trước ngược lại (tên bài kinh 1000
# điểm, luôn chiếm ô "Cụm từ khoá chính"), và đó là lỗi thiết kế do tôi đo sai câu hỏi:
# tôi đo "có ra đúng BÀI KINH không" (tên bài thắng 3/4), trong khi cái người đọc cần là
# "có ra đúng ĐOẠN KINH không". Với thước đúng thì kết luận đảo hẳn.
#
# Nguyên nhân nằm ở `search_engine._names_the_concept`: nó cộng `CONCEPT_TITLE_BONUS_SUTTA`
# (1.50) cho MỌI đoạn có tên đó trong `sourcePath`. Tìm bằng tên bài kinh nghĩa là cả 200+
# đoạn của bài được cộng ĐỀU NHAU, nên đoạn nào nổi lên là do điểm nền quyết định - hoàn
# toàn ngẫu nhiên so với ý người hỏi. Đo thật với câu "lý do Đức Phật niết bàn tại Kusinārā":
#
#     mahaparinibbanasutta (tên bài)                      2,287  -> đoạn HỎI ĐÁP VỀ bài kinh,
#                                                                   hạng 2 còn là bài về địa ngục
#     bhutapubbam ananda raja mahasudassano nama ahosi    4,809  -> ĐÚNG đoạn cần tìm
#     ma hevam ananda avaca khuddakanagarakam ...         3,885  -> ĐÚNG đoạn cần tìm
#
# Khách nói thẳng: "cụm nội dung là mặc định, tên bài kinh là ngoại lệ". Tên bài kinh vẫn
# giữ ở hạng dưới chứ không xoá - đo thật, ca "mũi tên độc" thì câu/cụm thân kinh SAI mà
# tên bài kinh ĐÚNG, hai loại bù nhau.
MULTIWORD_BASE_SCORE = 100.0
# Nhỏ, và có trần 4 chữ: chỉ để phá hoà giữa các cụm AI xếp NGANG NHAU, không được phép
# vượt một bậc thứ tự của AI. Bản trước để 10.0 nên một cụm dài AI xếp thứ 3 vẫn đè được
# cụm ngắn AI xếp thứ nhất.
MULTIWORD_LENGTH_BONUS = 0.1
SUTTA_NAME_BASE_SCORE = 50.0
SINGLE_WORD_BASE_SCORE = 10.0

# Tên bài kinh phải đủ dài mới mang thông tin. Đo thật: `culamalunkyovada` (16) và
# `alagaddupama` (12) tìm ra đúng bài, còn `tittha` (6) - cũng là tên thật - lại quá phổ
# thông (400+ đoạn) và trả về sai bài. Dưới 8 ký tự thì lợi bất cập hại.
SUTTA_NAME_MIN_LENGTH = 8

# Số ký tự đầu dùng để đối chiếu tên với tiêu đề thật trong DB. Phải là GỐC chứ không so
# nguyên chuỗi: ấn bản khác nhau viết tên khác nhau, `culamalunkyovada` của AI không hề
# xuất hiện nguyên văn ở đâu (0 đoạn), nhưng gốc `culamalu` khớp đúng
# `Cūḷamālukyasuttavaṇṇanā` trong DB.
SUTTA_NAME_STEM_LENGTH = 8

# **DANH SÁCH TRẮNG: một cái tên chỉ được nhận nếu KẾT THÚC bằng một trong các đuôi này.**
#
# Trước đây tôi làm ngược - liệt kê các từ CẤM (`nikaya`, `pitaka`, `atthakatha`...) - và
# sai hai lần liên tiếp vì danh sách đen không bao giờ đủ: lần đầu lọt `Aṅguttaranikāya`
# (đã vá bằng cách dò chuỗi con), lần sau lọt tiếp `visuddhimagga` (Thanh Tịnh Đạo, cả một
# bộ luận), `abhidhammatthasangaha`, `atthasalini` - không tên nào chứa từ cấm nào. Cứ có
# tên sách mới là lại sót.
#
# Đảo lại thành danh sách trắng thì bền: tên BỘ/SÁCH kết thúc bằng `magga`, `saṅgaha`,
# `nikāya`, `piṭaka`, `ṭīkā`, `sālinī`, `purāṇī`, `pasādikā`... - không cái nào trùng đuôi
# của một bài kinh, nên tên sách mới xuất hiện bao nhiêu cũng tự động bị loại mà không phải
# sửa gì. Đo trên 13 tên: loại đúng 8/8 tên bộ sách, giữ đúng 5/5 tên bài kinh, 0 sai.
#
# Đã thử dùng cột `sections.level` thay cho cách này và KHÔNG được: `visuddhimagga` nằm ở
# cấp 3 còn `kutadantasutta` ở cấp 4 - hai loại chồng lấn cấp nhau, không có ngưỡng nào cắt.
SUTTA_NAME_TAILS = (
    "sutta", "suttam", "suttanta", "suttantam",
    "jataka", "jatakam",
    "vatthu", "vatthum",
    "sikkhapada", "sikkhapadam",
    "gatha", "gatham",
    "puccha", "pucchā",
    "parajika", "parajikam",
    "cariya", "cariyam",
)

# **KHÔNG ĐỆM lượt suy luận này - mỗi lần bấm là một lượt hỏi Gemini mới.** Đã đi qua ba
# bản, ghi lại để đừng ai quay về bản giữa:
#
# 1. Đệm MỘT bộ: bấm lại luôn ra y hệt - khách báo "hỏi đúng câu đó cứ bị lặp lại".
# 2. Đệm BA bộ rồi xoay vòng: đỡ hơn nhưng hỏng cùng kiểu, chỉ chậm hơn - sau 3 lần bấm là
#    hết bộ mới, từ đó chỉ xoay lại ba bộ cũ nên "Cụm từ khoá chính" đứng im. Khách phát
#    hiện đúng chỗ này.
# 3. Hiện tại - luôn hỏi mới.
#
# Lý do bản 2 không đáng giữ: nó tiết kiệm một thứ vốn không tốn thêm gì. **Cả ba phần hiển
# thị (Câu Pāḷi trọn vẹn, Cụm từ khoá chính, Thuật ngữ liên quan) đều đến từ CÙNG MỘT lượt
# gọi**, nên hỏi mới cũng chỉ đúng một lượt - bằng đúng cái mà xoay vòng đang tránh.
#
# Cái giá phải chấp nhận: mỗi lần bấm mất 3-10 giây và một lượt gọi Gemini, và chất lượng
# lên xuống giữa hai lần bấm liền kề (đo thật trên câu "voi mù": có lần ra `jaccandhasutta`
# đúng, có lần ra `tittirajataka` sai hẳn). Đây là đánh đổi khách đã chọn.
#
# `expand_query_with_ai` (danh sách "thuật ngữ liên quan") VẪN đệm như cũ và không được
# đụng tới: đệm đó dùng chung với pipeline tìm kiếm chính, bỏ nó đi là làm kết quả tìm
# kiếm mất ổn định - đúng thứ đệm đó sinh ra để chặn.
KEYWORD_VARIANT_COUNT = 3
DEEP_KEYWORD_KIND = "deep_keyword"
DEEP_KEYWORD_PROMPT_VERSION = "v5-body-phrase-first"


class _DeepKeywordResult(BaseModel):
    identified: str = ""
    reasoning: str = ""
    # MỘT câu / bài kệ Pāḷi trọn vẹn. Đo thật, đây là loại từ khoá cho kết quả tốt NHẤT -
    # xem `_resolve_full_sentence`.
    fullSentence: str = ""
    # Tên bài kinh, XIN NHIỀU CÁCH VIẾT vì mỗi ấn bản đặt tên một khác: câu "người mù sờ
    # voi" được AI gọi là "Tittha Sutta" (tên bên SuttaCentral) trong khi CST đặt là
    # `Paṭhamanānātitthiyasuttaṃ` - chỉ xin một cách viết là hụt mất bài đúng.
    #
    # Đây là trường PHỤ, xếp dưới `candidates` - xem khối hằng số điểm để biết vì sao.
    suttaNames: list[str] = Field(default_factory=list)
    candidates: list[str] = Field(default_factory=list)


@lru_cache(maxsize=1)
def _normalized_section_titles() -> tuple[str, ...]:
    """Toàn bộ tiêu đề section đã chuẩn hoá, nạp MỘT LẦN cho cả tiến trình.

    Phải so ở Python chứ không so thẳng bằng SQL: cột `title` còn nguyên dấu Pāḷi
    (`Cūḷamālukyasutta`) trong khi tên AI đưa ra đã bỏ dấu, mà Postgres ở đây không có
    `unaccent`. Nạp một lần rồi tra trong bộ nhớ thì mỗi lần kiểm tra chỉ còn là quét
    chuỗi, không phải một lượt truy vấn.
    """
    rows = fetch_all("select distinct title from sections where title is not null", [])
    return tuple(
        normalized
        for normalized in (normalize_pali(str(row["title"])) for row in rows)
        if normalized
    )


def _sutta_name_is_real(name: str) -> bool:
    """Tên này có phải TÊN MỘT BÀI KINH có thật trong kho không.

    Hai cửa, mỗi cửa chặn một loại lỗi khác nhau:

    1. **Kết thúc bằng đuôi của tên bài kinh** (`SUTTA_NAME_TAILS`) - chặn tên BỘ/SÁCH.
       Xem lời giải thích ở chỗ khai báo hằng số để biết vì sao phải là danh sách trắng
       chứ không phải danh sách đen.
    2. **Gốc khớp một tiêu đề thật trong DB** - chặn tên AI bịa. Phải so GỐC chứ không so
       nguyên chuỗi: `culamalunkyovada` của AI không xuất hiện nguyên văn ở đâu cả, nhưng
       gốc `culamalu` khớp đúng `Cūḷamālukyasuttavaṇṇanā`.

    Đo trên 4 tên thật + 4 tên bịa ở cửa 2: nhận đúng cả 4 tên thật, loại 3/4 tên bịa. Ca
    lọt lưới duy nhất là `brahmajalupama` khớp gốc `brahmaja` của `Brahmajālasuttaṃ` - vẫn
    trỏ về một bài kinh có thật nên tác hại giới hạn.
    """
    if len(name) < SUTTA_NAME_MIN_LENGTH:
        return False
    if not name.endswith(SUTTA_NAME_TAILS):
        return False
    stem = name[:SUTTA_NAME_STEM_LENGTH]
    return any(stem in title for title in _normalized_section_titles())


def _resolve_sutta_name(raw_name: str) -> str | None:
    """Rút từ khoá TÊN BÀI KINH ra khỏi một chuỗi tên AI đưa ra, nếu có.

    Đây là hạng từ khoá TỐT NHẤT, đo thật trên các câu hỏi khó: `culamalukyasutta` trả về
    đúng `Cūḷamālukyasutta`, `alagaddupamasutta` trả về đúng `Alagaddūpamasuttaṃ`, trong
    khi cả câu hỏi tiếng Việt đầy đủ lẫn mọi cụm trích từ thân kinh đều trả về SAI bài. Lý
    do là xếp hạng có sẵn điểm thưởng riêng khi từ khoá khớp tiêu đề bài kinh
    (`search_engine.CONCEPT_TITLE_BONUS_SUTTA`), mạnh hơn hẳn mọi tín hiệu trùng chữ.

    Chỉ so độ dài để chọn giữa các CHỮ TRONG CÙNG một chuỗi tên ("Alagaddūpama Sutta" ->
    `alagaddupama` chứ không phải `sutta`). Việc xếp hạng giữa các TÊN KHÁC NHAU thì
    KHÔNG được dùng độ dài - xem chỗ gọi.
    """
    best: str | None = None
    for token in normalize_pali(raw_name).split():
        token = token.strip("-")
        if not _sutta_name_is_real(token):
            continue
        if best is None or len(token) > len(best):
            best = token
    return best


@lru_cache(maxsize=4096)
def _phrase_frequency(phrase: str) -> int:
    """Số đoạn chứa cụm này NGUYÊN VĂN, đếm có chặn trần ở `PHRASE_FREQ_CAP`.

    Chặn trần chứ không `count(*)`: câu hỏi cần trả lời chỉ là "hiếm hay phổ biến", mà
    `count(*)` trên 463k đoạn với `like '%...%'` thì phải quét hết mọi đoạn khớp - một từ
    phổ thông như `titthiya` có hàng nghìn đoạn, đếm đủ chỉ để biết "nhiều quá" là lãng
    phí. `limit` cho Postgres dừng ngay khi đủ số dòng.
    """
    if not phrase:
        return 0
    rows = fetch_all(
        "select 1 as ok from passages where normalized_pali like %s limit %s",
        [f"%{phrase}%", PHRASE_FREQ_CAP],
    )
    return len(rows)


def _rarity(frequency: int) -> float:
    """0..1, càng hiếm càng cao. Dùng để so hai cụm CÙNG hạng, không quyết định hạng."""
    return (PHRASE_FREQ_CAP - min(frequency, PHRASE_FREQ_CAP)) / PHRASE_FREQ_CAP


@lru_cache(maxsize=2048)
def _cooccurrence_count(phrase: str) -> int:
    """Số đoạn chứa TẤT CẢ các từ của cụm, KHÔNG đòi chúng nằm liền nhau.

    Đây mới là phép đo đúng, vì công cụ tìm kiếm cũng làm y như vậy: nó AND các token
    rời (`_tsquery_for_quote`), không đòi kề nhau. Đo bằng "nguyên văn liền nhau" là đo
    sai thứ - xem `_resolve_candidate`.
    """
    tokens = [word for word in phrase.split() if len(word) >= MIN_TERM_LENGTH]
    if not tokens:
        return 0
    where = " and ".join(["normalized_pali like %s"] * len(tokens))
    rows = fetch_all(
        f"select 1 as ok from passages where {where} limit %s",
        [*[f"%{token}%" for token in tokens], PHRASE_FREQ_CAP],
    )
    return len(rows)


def _resolve_candidate(candidate: str, index: int = 0) -> tuple[float, str] | None:
    """Chấm điểm MỘT ứng viên AI, và trả về dạng thực sự dùng được của nó.

    **GIỮ NGUYÊN CỤM NHIỀU TỪ.** Bản trước đòi cụm phải xuất hiện NGUYÊN VĂN LIỀN NHAU
    trong kho, không thì bóp xuống còn một từ đơn. Luật đó sai, và sai nặng: đo trên chính
    các cụm Gemini đưa ra cho khách, **5/6 cụm không hề tồn tại nguyên văn** dù ba trong số
    đó tìm kiếm rất tốt:

        buddho pi buddhassa bhaneyya vannam   liền nhau 0 · AND 23 -> Namakkāraṭīkā (1,540)
        buddho pi buddhassa bhaneyya          liền nhau 0 · AND 23 -> Buddhaguṇakathā (1,507)
        khiyetha kappo                        liền nhau 23 · AND 26 -> Kappavināsakaṇḍo (1,246)
        kappam pi ce titthati dighamayum      liền nhau 0 · AND  0 -> không truy hồi được
        khiyyayyati kappo na hi buddhavanno   liền nhau 0 · AND  0 -> không truy hồi được
        na tveva vanno sugatassa khiyyati     liền nhau 0 · AND  0 -> không truy hồi được

    Ranh giới dùng được / không dùng được nằm ở cột **AND** chứ không phải cột liền nhau,
    vì công cụ tìm kiếm cũng AND các token rời. Đòi liền nhau thì giết 5/6 cụm chất lượng
    Gemini và biến chúng thành từ đơn kiểu `dhamma` - đúng cái khách phàn nàn "từ đơn mang
    nghĩa rộng quá".

    Vẫn phải LỌC TỪ BỊA: bỏ riêng những chữ không tồn tại trong kho rồi mới đo lại, thay vì
    vứt cả cụm. `kappaṃ pi ce tiṭṭhati dīghamāyuṃ` chết cả cụm chỉ vì `dighamayum` là dạng
    AI tự chia; bỏ chữ đó đi thì phần còn lại vẫn tìm được.

    **`index` (thứ tự AI xếp) là khoá chính trong hạng cụm nhiều chữ, độ hiếm chỉ phá hoà.**
    Đo thật: cụm ở vị trí 0 của AI đúng ở CẢ HAI ca thử, trong khi điểm cũ (chỉ gồm số chữ
    + độ hiếm) cho gần như hoà nhau - `kullūpamaṁ vo bhikkhave` (ĐÚNG) 120,97 so với
    `nittharaṇatthāya no gahaṇatthāya` (SAI) cũng 120,97 - nên chỉ cần cụm sai hiếm hơn một
    chút là nó lật ngược được thứ tự AI và chiếm ô "cụm chính". Đây đúng bài học đã rút ra
    ở `suttaNames`: độ hiếm đo được "đặc trưng đến đâu", KHÔNG đo được "có liên quan tới
    câu hỏi không" - chỉ AI biết điều đó, và nó thể hiện qua thứ tự nó xếp.

    Trả `None` khi không cứu được gì.
    """
    normalized = normalize_pali(candidate)
    if not normalized:
        return None
    words = [word for word in normalized.split() if len(word) >= MIN_TERM_LENGTH]
    if not words:
        return None

    # Bỏ những chữ AI tự chia sai / bịa ra. Giữ THỨ TỰ gốc để cụm còn đọc được như một
    # dòng kinh, không sắp xếp lại.
    existing = [(word, _phrase_frequency(word)) for word in words]
    existing = [(word, freq) for word, freq in existing if freq > 0]
    if not existing:
        return None

    # Hạng 1 - CỤM NHIỀU TỪ. Cụm càng nhiều chữ càng thu hẹp kết quả nên càng đặc trưng;
    # `_cooccurrence_count` là số đoạn chứa đủ mọi chữ, ít hơn thì đúng trọng tâm hơn.
    #
    # Không đòi đủ CẢ cụm ngay: một chữ phổ thông trong đó cũng đủ kéo phép AND về rỗng dù
    # phần còn lại rất đúng. Đo thật với `kullaṁ upamaṁ katvā` - cả ba chữ đều có thật
    # (`kullam` 30 đoạn, `upamam` và `katva` mỗi chữ 400+), nhưng không đoạn nào chứa đủ ba,
    # nên bản trước bóp cả cụm xuống còn mỗi `kullam`. Bỏ dần chữ PHỔ THÔNG NHẤT rồi thử
    # lại thì giữ được phần đặc trưng của cụm; chỉ khi xuống dưới 2 chữ mới chịu thua.
    remaining = list(existing)
    while len(remaining) >= 2:
        phrase = " ".join(word for word, _ in remaining)
        together = _cooccurrence_count(phrase)
        if together > 0:
            # Thứ tự AI (`index`) là khoá chính; số chữ và độ hiếm chỉ phá hoà giữa các cụm
            # AI xếp ngang nhau. Cả hai cộng lại phải NHỎ HƠN 1 để không bao giờ vượt được
            # một bậc `index`, nếu không lại rơi vào đúng lỗi cũ.
            score = (
                MULTIWORD_BASE_SCORE
                - index
                + min(len(remaining), 4) * MULTIWORD_LENGTH_BONUS
                + _rarity(together) * 0.1
            )
            return score, phrase
        commonest = max(remaining, key=lambda item: item[1])
        remaining.remove(commonest)

    # Hạng 2 - từ đơn có thật và chưa quá phổ thông. `titthiya` (500+ đoạn) bị loại ở đây:
    # từ phổ thông kéo kết quả về những bài chỉ trùng chủ đề chứ không phải bài đang tìm.
    specific = [(word, freq) for word, freq in existing if freq <= GENERIC_TERM_MAX]
    if specific:
        word, frequency = min(specific, key=lambda item: item[1])
        return SINGLE_WORD_BASE_SCORE + _rarity(frequency), word

    # Hạng 3 - chỉ còn toàn từ phổ thông. Vẫn trả về từ hiếm nhất trong đám, nhưng ở thang
    # điểm thấp nhất để bất kỳ ứng viên nào khá hơn đều vượt mặt được.
    word, frequency = min(existing, key=lambda item: item[1])
    return _rarity(frequency), word


def _deep_keyword_prompt(query: str) -> str:
    return "\n".join(
        [
            "Bạn là học giả Pāḷi chuyên sâu Tam Tạng, Chú giải và Phụ chú giải Theravāda.",
            "Người đọc kể lại một câu hỏi/câu chuyện bằng trí nhớ, có thể dài dòng, sai chi",
            "tiết hoặc không nhớ chính xác - không phải một câu tìm kiếm đã chọn lọc từ khoá.",
            "",
            f'Câu hỏi: "{query}"',
            "",
            "Làm theo đúng thứ tự:",
            "1. Suy nghĩ kỹ bằng hiểu biết chung của bạn về Tam Tạng: đây có phải một ẩn dụ,",
            "   câu chuyện hoặc đoạn giáo lý CỤ THỂ mà bạn nhận ra không - dù người hỏi không",
            "   nhớ tên bài kinh? Đừng chỉ tóm tắt lại câu hỏi.",
            "",
            "2. `fullSentence` - MỘT CÂU PĀḶI TRỌN VẸN, chép nguyên văn từ chính đoạn kinh đó.",
            "   Phải là một câu hoàn chỉnh hoặc một bài kệ trọn vẹn (khoảng 6-20 chữ), không",
            "   phải mẩu cụt. Đây là câu đặc trưng nhất, đáng nhớ nhất của đoạn kinh - câu mà",
            "   người đọc kinh sẽ nhận ra ngay.",
            "   Ví dụ dạng mong muốn:",
            "     \"kullūpamaṁ vo, bhikkhave, dhammaṁ desessāmi nittharaṇatthāya no gahaṇatthāya\"",
            "     \"tena hi, bhaṇe, jaccandhānaṁ hatthiṁ dassehīti\"",
            "   Không nhớ chắc cả câu thì cứ đưa phần bạn nhớ chắc nhất, miễn còn đọc ra một",
            "   câu; để trống chỉ khi hoàn toàn không nhớ được câu nào.",
            "",
            f"3. `candidates` - PHẦN QUAN TRỌNG NHẤT. {DEEP_CANDIDATE_COUNT} CỤM TỪ KHOÁ PĀḶI",
            "   trích từ THÂN đoạn kinh, khác nhau thật sự (không phải biến thể chính tả của",
            "   cùng một cụm), xếp theo độ tin cậy giảm dần.",
            "",
            "   Vì sao đây là phần quan trọng nhất: người đọc cần tìm ĐÚNG ĐOẠN KINH mang ý",
            "   nghĩa họ hỏi, không phải chỉ tìm ra tên bài kinh. Một cụm chữ nằm ngay trong",
            "   đoạn đó sẽ trỏ thẳng vào đoạn đó; còn tên bài kinh thì trỏ đều vào cả trăm đoạn",
            "   của bài, và đoạn hiện ra thường không phải đoạn họ cần.",
            "",
            "BA YÊU CẦU BẮT BUỘC cho mỗi cụm ở mục 3, quan trọng hơn mọi thứ khác:",
            "a. PHẢI TỪ 2 ĐẾN 4 CHỮ. TUYỆT ĐỐI KHÔNG đưa từ đơn lẻ. Một chữ đứng một mình mang",
            "   nghĩa quá rộng, khớp hàng trăm bài không liên quan; hai chữ đi cùng nhau mới đủ",
            "   thu hẹp về đúng đoạn kinh cần tìm.",
            "   ĐÚNG:  \"sallaṁ āharissāmi\" · \"bhisakko sallakatto\" · \"jaccandhānaṁ hatthiṁ dassesi\"",
            "   SAI:   \"salla\" · \"assamedho\" · \"mahāyañño\" · \"dhammapariyāyaṁ\"",
            "   Nếu chỉ nghĩ ra một chữ, hãy ghép nó với chữ ĐỨNG NGAY CẠNH nó trong câu kinh.",
            "b. NGUYÊN VĂN. Cụm phải là chuỗi chữ XUẤT HIỆN Y NGUYÊN, LIỀN NHAU trong bản Pāḷi,",
            "   đúng dạng đã chia (không phải dạng từ điển, không phải cụm bạn tự ghép cho xuôi",
            "   tai). Hãy hình dung bạn đang trích một mẩu ra khỏi trang kinh rồi chép lại - nếu",
            "   không nhớ chắc một chữ nào trong cụm, hãy thay cả cụm bằng mẩu khác mà bạn nhớ",
            "   chắc là có thật, còn hơn một cụm nghe hợp lý nhưng bạn tự dựng nên.",
            "c. ĐẶC TRƯNG. Ưu tiên chữ chỉ riêng câu chuyện/ẩn dụ này mới có (tên nhân vật, con",
            "   vật, đồ vật, hình ảnh ẩn dụ cụ thể). TRÁNH thuật ngữ giáo lý phổ thông xuất hiện",
            "   khắp Tam Tạng - chúng khớp hàng trăm bài không liên quan và làm chìm mất bài đúng.",
            "",
            "4. `suttaNames` - PHẦN PHỤ, chỉ điền khi bạn thực sự nhận ra bài kinh. Cho 2-4 CÁCH",
            "   VIẾT PĀḶI khác nhau của TÊN bài kinh, chỉ tên bài thôi.",
            "   TUYỆT ĐỐI KHÔNG đưa tên BỘ/TẠNG/SÁCH LỚN - `Visuddhimagga`, `Aṅguttaranikāya`,",
            "   `Abhidhammatthasaṅgaha`, `Atthasālinī`, `Suttapiṭaka` đều SAI ở đây: chúng bao cả",
            "   nghìn đoạn nên vô dụng làm từ khoá. Chỉ tên MỘT bài kinh cụ thể mới được.",
            "   Ví dụ dạng mong muốn: [\"Alagaddūpamasutta\", \"Alagaddūpama\"];",
            "   [\"Cūḷamālukyasutta\", \"Cūḷamālunkyovādasutta\"].",
            "   Không nhận ra bài nào thì để trống - thà trống còn hơn đưa tên một bộ sách.",
            "",
            "Không dịch, không giải thích ngoài JSON.",
            "Trả JSON thuần:",
            '{"identified":"","reasoning":"","fullSentence":"",'
            '"suttaNames":["",""],"candidates":["",""]}',
        ]
    )


def _variant_cache_key(query: str, language: str, index: int) -> str:
    """Mỗi BỘ một dòng riêng trong `query_ai_cache`, phân biệt bằng số thứ tự ở cuối khoá."""
    return _cache_key(query.strip(), language, DEEP_KEYWORD_PROMPT_VERSION, str(index))


def _load_variants(query: str, language: str) -> list[dict]:
    """Các bộ đã sinh cho câu hỏi này, theo đúng thứ tự đã sinh."""
    keys = [_variant_cache_key(query, language, i) for i in range(KEYWORD_VARIANT_COUNT)]
    try:
        rows = fetch_all(
            "select cache_key, payload from query_ai_cache "
            "where cache_key = any(%s) and kind = %s and pipeline_version = %s",
            [keys, DEEP_KEYWORD_KIND, DEEP_KEYWORD_PROMPT_VERSION],
        )
    except Exception:  # noqa: BLE001 - chua chay migration thi coi nhu chua co bo nao
        return []
    by_key = {row["cache_key"]: row["payload"] for row in rows}
    return [by_key[key] for key in keys if key in by_key]


def _store_variant(query: str, language: str, index: int, payload: dict) -> None:
    try:
        execute(
            "insert into query_ai_cache (cache_key, kind, pipeline_version, payload) "
            "values (%s, %s, %s, %s) "
            "on conflict (cache_key, kind, pipeline_version) do nothing",
            [
                _variant_cache_key(query, language, index),
                DEEP_KEYWORD_KIND,
                DEEP_KEYWORD_PROMPT_VERSION,
                Jsonb(payload),
            ],
        )
    except Exception:  # noqa: BLE001 - khong ghi duoc dem thi van tra ket qua cho khach
        pass


def _ask_deep_keywords(query: str) -> dict:
    """MỘT lượt hỏi Gemini. Trả `{}` khi mọi model đều hỏng."""
    prompt = _deep_keyword_prompt(query)
    client = _client()
    for model in _keyword_models():
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
            return _DeepKeywordResult.model_validate_json(response.text or "{}").model_dump()
        except Exception as exc:
            if not _is_retryable_error(exc):
                break
    return {}


def _deep_keyword_payload(query: str, language: str) -> dict:
    """Trả về kết quả AI cho câu hỏi này - **LUÔN hỏi mới, không đệm gì cả**.

    Bản trước đệm 3 bộ rồi xoay vòng, nhằm cho khách bộ khác nhau mỗi lần bấm mà không tốn
    thêm lượt gọi. Nó hỏng ở chỗ đơn giản: sau 3 lần bấm là hết bộ mới, từ đó chỉ xoay lại
    ba bộ cũ - khách bấm lấy lại từ khoá mà "Cụm từ khoá chính" vẫn y nguyên.

    Và cái nó tiết kiệm hoá ra không đáng: **cả ba phần hiển thị (Câu Pāḷi trọn vẹn, Cụm từ
    khoá chính, Thuật ngữ liên quan) đều lấy từ CÙNG MỘT lượt gọi này**, nên hỏi mới cũng
    chỉ tốn đúng một lượt - bằng đúng cái mà xoay vòng đang tránh. Đổi lại, mỗi lần bấm là
    một phương án thật sự mới.

    `_load_variants` / `_store_variant` giữ lại nhưng không còn ai gọi: bảng `query_ai_cache`
    vẫn còn các dòng `deep_keyword` cũ, và nếu sau này muốn quay lại cơ chế đệm thì đã có
    sẵn. Hiện tại chúng là mã chết, đừng đọc nhầm thành "vẫn đang đệm".
    """
    return _ask_deep_keywords(query)


def extract_main_keyword_deep(
    query: str, language: str
) -> tuple[str | None, list[str], str | None]:
    """Suy luận sâu để tìm "cụm từ khoá chính" - HOÀN TOÀN ĐỘC LẬP với pipeline tìm kiếm
    chính (không gọi, không lùi về `extract_search_keyword_with_ai`), xem docstring đầu
    file để biết vì sao cần tách riêng.

    Trả `(cụm_tốt_nhất, các_cụm_khác, câu_trọn_vẹn)`. Vế thứ hai được
    `suggest_pali_keywords` gộp vào nhóm "cụm dài" của danh sách gợi ý phụ, tận dụng luôn
    những ứng viên đã trả tiền cho một lượt gọi AI thay vì bỏ phí. Vế thứ ba là loại từ
    khoá cho kết quả tốt nhất - xem `_resolve_full_sentence`.

    Trả `(None, [])` khi AI không đưa ra được cụm nào tồn tại thật trong kho - KHÔNG có
    lưới an toàn nào khác. `suggest_pali_keywords` khi đó chỉ đơn giản không hiện "Cụm từ
    khoá chính", đúng yêu cầu "tách biệt ra đừng liên quan gì, nó chỉ việc là render ra
    từ khoá thôi".

    **Bấm hai lần trên cùng câu hỏi thì ra hai bộ từ khoá khác nhau**, nhưng không phải vì
    hỏi lại AI mỗi lần - xem `_deep_keyword_payload` và khối hằng số đầu file.
    """
    cached = _deep_keyword_payload(query, language)
    full_sentence = _resolve_full_sentence(str(cached.get("fullSentence") or ""))

    # `_resolve_*` trả về DẠNG DÙNG ĐƯỢC của ứng viên, có thể chỉ là một phần của thứ AI
    # đưa ra - nên phải bỏ trùng SAU khi rút gọn: hai ứng viên khác nhau của AI hoàn toàn
    # có thể cùng rút về một chuỗi.
    resolved: list[tuple[float, str]] = []
    seen: set[str] = set()

    def collect(outcome: tuple[float, str] | None) -> None:
        if not outcome:
            return
        score, phrase = outcome
        if phrase in seen:
            return
        seen.add(phrase)
        resolved.append((score, phrase))

    # TÊN BÀI KINH trước - hạng từ khoá tốt nhất, xem `_resolve_sutta_name`.
    #
    # Xếp theo ĐÚNG THỨ TỰ AI ĐƯA RA, không theo độ dài. Đo thật trên 4 câu hỏi khó, tên
    # đầu danh sách của AI đúng cả 4 lần (`Titthiyasutta`, `Cūḷamālukyasutta`,
    # `Alagaddūpamasutta`, `Kūṭadantasutta`), trong khi luật "tên dài hơn thì đặc trưng
    # hơn" chọn nhầm 2 lần: `Mahādukkhakkhandhasutta` (23 ký tự, AI xếp thứ 3) đè
    # `Alagaddūpamasutta` (17 ký tự, AI xếp thứ 1) và trả về sai bài.
    for index, raw_name in enumerate(cached.get("suttaNames") or []):
        name = _resolve_sutta_name(str(raw_name))
        if name:
            collect((SUTTA_NAME_BASE_SCORE - index, name))

    # `identified` là trường tự do, model hay viết cả một đoạn văn vào đó (đo thật: một
    # đoạn 600 ký tự lẫn tên bộ, tên phẩm, tên bài khác). Chỉ đụng tới nó khi `suttaNames`
    # không cho được gì, và luôn xếp sau mọi tên trong danh sách.
    if not resolved:
        name = _resolve_sutta_name(str(cached.get("identified") or ""))
        if name:
            collect((SUTTA_NAME_BASE_SCORE - len(cached.get("suttaNames") or []), name))

    # `index` truyền vào để giữ ĐÚNG thứ tự AI xếp - xem `_resolve_candidate`.
    for index, raw_candidate in enumerate(cached.get("candidates") or []):
        collect(_resolve_candidate(str(raw_candidate), index))

    if not resolved:
        # Không ứng viên nào lấy một chữ có thật trong kho. AI bịa hoàn toàn cho câu hỏi
        # này, không phải một cụm gần đúng còn cứu được. Câu trọn vẹn vẫn trả về nếu qua
        # được cửa của nó - hai thứ được xác thực độc lập với nhau.
        return None, [], full_sentence

    resolved.sort(key=lambda item: item[0], reverse=True)
    best = resolved[0][1]
    others = [phrase for _, phrase in resolved[1:]]
    return best, others, full_sentence


def _normalized_set(values: list[str]) -> set[str]:
    return {term for term in (normalize_pali(str(value)) for value in values) if term}


def _pick_terms(values: list[str], avoid: set[str]) -> list[str]:
    """Chuẩn hoá, bỏ trùng, bỏ từ quá ngắn và bỏ từ nằm trong danh sách nên tránh.

    Giữ NGUYÊN thứ tự đầu vào vì thứ tự đó chính là độ tin cậy giảm dần - xem chỗ gọi.
    """
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        term = normalize_pali(str(value))
        if not term or len(term) < MIN_TERM_LENGTH:
            continue
        if term in seen or term in avoid:
            continue
        seen.add(term)
        output.append(term)
    return output


def _resolve_full_sentence(raw_sentence: str) -> str | None:
    """Xác thực CÂU PĀḶI TRỌN VẸN do AI chép ra, trả về nguyên văn để khách copy.

    Đây là loại từ khoá cho kết quả TỐT NHẤT trong mọi loại đã thử. Đo thật bằng cách tìm
    kiếm bằng chính câu đó, so với dùng tên bài kinh của cùng câu hỏi:

        kullūpamaṁ vo, bhikkhave, dhammaṁ desessāmi...  3,315  (tên bài kinh: 2,576)
        na tāvāhaṁ imaṁ sallaṁ āharissāmi...            3,283  (tên bài kinh: 2,259)
        tena hi, bhaṇe, jaccandhānaṁ hatthiṁ dassehīti  2,765  (đúng bài ở CẢ top 3)

    **Ngưỡng để lỏng là có cơ sở, không phải dễ dãi.** Cả câu chỉ cần phần lớn chữ có thật
    là đủ, vì pipeline tìm kiếm chịu lỗi rất tốt với chuỗi dài: đo thật, câu trên khi bị
    nhét thêm một chữ bịa hoàn toàn (`xyzabca`) VẪN trả về đúng `Alagaddūpamasuttaṃ` ở
    hạng 1, và khi chia sai một vĩ tố (`desessāmi` -> `desemi`) cũng vậy. Bắt câu phải
    đúng 100% thì loại oan gần hết, trong khi hại của một chữ sai gần như bằng không.

    Giữ NGUYÊN dấu Pāḷi và dấu câu của AI, không chuẩn hoá: đây là thứ khách đọc và copy
    như một dòng kinh, `kullūpamaṁ vo, bhikkhave` dễ đọc hơn hẳn `kullupamam vo bhikkhave`.
    Tìm kiếm tự chuẩn hoá đầu vào nên không ảnh hưởng gì.
    """
    sentence = " ".join(str(raw_sentence or "").split())
    if not sentence:
        return None

    words = [word for word in normalize_pali(sentence).split() if len(word) >= MIN_TERM_LENGTH]
    if len(words) < FULL_SENTENCE_MIN_WORDS:
        return None

    existing = sum(1 for word in words if _phrase_frequency(word) > 0)
    if existing / len(words) < FULL_SENTENCE_MIN_REAL_RATIO:
        return None
    return sentence


def _is_usable_term(term: str) -> bool:
    """Từ khoá này có tìm ra được cái gì trong kho không.

    Phải phân biệt theo SỐ CHỮ, không dùng chung một phép kiểm tra: `search_engine`
    `_existing_pali_terms` so bằng `like '%term%'`, tức đòi các chữ nằm LIỀN NHAU. Với từ
    đơn thì đúng, với cụm nhiều chữ thì sai và sai âm thầm - `jaccandha hatthim dassesi`
    là cụm đặc trưng nhất của bài kinh nhưng dạng chia trong kinh là `jaccandhānaṃ hatthiṃ
    dassesi`, nên phép so liền nhau trả về 0 và cụm bị loại ngay trước khi hiện ra. Đó là
    lý do danh sách gợi ý cứ rụng hết cụm dài và chỉ còn từ lẻ.
    """
    if " " in term:
        return _cooccurrence_count(term) > 0
    return _phrase_frequency(term) > 0


def _interleave(short_terms: list[str], long_terms: list[str]) -> list[str]:
    """Xen kẽ hai danh sách để kết quả có ĐỦ CẢ từ đơn ngắn lẫn cụm dài, theo đúng yêu
    cầu khách "đủ loại key ngắn dài tuỳ vào câu hỏi" - không phải danh sách toàn từ đơn
    (như trước) hay toàn cụm dài. Bắt đầu bằng một cụm dài vì `mainKeyword` hiển thị
    riêng đã "dùng" vị trí cụm dài đầu tiên, nên 4 gợi ý còn lại nên có phương án dài
    khác sớm chứ không dồn hết xuống cuối bảng.

    Nguồn nào cạn trước thì lấy tiếp nguồn còn lại, không dừng giữa chừng - một câu hỏi
    mà AI không ghép được cụm dài nào vẫn phải trả đủ số từ đơn, và ngược lại.
    """
    merged: list[str] = []
    seen: set[str] = set()
    i = j = 0
    want_long = True
    while i < len(short_terms) or j < len(long_terms):
        if want_long and j < len(long_terms):
            term = long_terms[j]
            j += 1
        elif i < len(short_terms):
            term = short_terms[i]
            i += 1
        elif j < len(long_terms):
            term = long_terms[j]
            j += 1
        else:
            break
        want_long = not want_long
        if term not in seen:
            seen.add(term)
            merged.append(term)
    return merged


# ---------------------------------------------------------------------------
# BẢN CŨ - ĐANG TẮT, GIỮ NGUYÊN ĐỂ CÒN QUAY LẠI
#
# Toàn bộ phần trên (`_deep_keyword_prompt`, `extract_main_keyword_deep`, các hằng số
# điểm, `_resolve_*`, `_interleave` và `_suggest_pali_keywords_legacy` ngay dưới đây) là
# bản CŨ: hỏi Gemini một lượt rồi TỰ CHẤM ĐIỂM và TỰ LỌC ứng viên bằng cách đối chiếu với
# kho (`_phrase_frequency`, `_cooccurrence_count`, danh sách trắng đuôi tên bài kinh...).
#
# Khách trải nghiệm và báo kết quả KHÔNG CHÍNH XÁC. Khách tự hỏi Gemini web bằng câu lệnh
# của mình thì ra đúng, nên yêu cầu là: dùng thẳng câu lệnh đó, render thẳng kết quả
# Gemini trả về, không lọc, không chấm điểm - xem `_direct_keyword_prompt` bên dưới.
#
# KHÔNG XOÁ khối này: nếu bản mới cũng không ổn thì đổi lại `suggest_pali_keywords` gọi
# `_suggest_pali_keywords_legacy` là quay về nguyên trạng. Hiện tại không nơi nào gọi
# `_suggest_pali_keywords_legacy` nữa - đây là mã chết có chủ đích.
# ---------------------------------------------------------------------------
def _suggest_pali_keywords_legacy(query: str, language: str = DEFAULT_LANGUAGE) -> dict:
    """Trả cụm Pāḷi chính + tối đa `MAX_TERMS` gợi ý khác cho một câu hỏi - tổng khoảng 5
    kết quả (kể cả cụm chính), đủ cả từ đơn ngắn lẫn cụm dài chứ không chỉ một loại.

    Không ghi `search_logs`: đây không phải một lượt tìm kiếm, và lượt tìm thật sau đó
    (khi khách bấm "Tìm ngay" hoặc tự dán) mới là thứ đáng vào lịch sử.
    """
    query = str(query or "").strip()
    language = normalize_language(language)
    if not query:
        return {
            "ok": False,
            "query": "",
            "fullSentence": None,
            "mainKeyword": None,
            "terms": [],
        }

    clean_query = canonicalize_query(query) or query

    # Cụm chính: HOÀN TOÀN từ lượt suy luận sâu độc lập - không lùi về hàm nào khác.
    # AI suy luận sâu không ra được cụm nào tồn tại thật trong kho thì `main_keyword` là
    # `None`, và kết quả trả về đơn giản không có "Cụm từ khoá chính" - xem docstring của
    # `extract_main_keyword_deep`.
    main_keyword, deep_alt_candidates, full_sentence = extract_main_keyword_deep(query, language)

    expansion = expand_query_with_ai(query, clean_query, language) or {}
    local = analyze_query(query, ["all"])

    avoid = _normalized_set(
        [*(expansion.get("avoidPali") or []), *(local.get("avoidPali") or [])]
    )

    # Nguồn NGẮN: thuật ngữ đơn lẻ - thứ tự trong `_pick_terms` là độ tin cậy giảm dần:
    # thuật ngữ trọng tâm của AI > thuật ngữ của bảng khái niệm tự soạn > thuật ngữ mở
    # rộng. KHÔNG lấy từng chữ tách rời của `main_keyword` như trước nữa: giờ mainKeyword
    # đã ở NGUYÊN CỤM trong kết quả, tách rời nó ra làm từ đơn chỉ tạo thêm hàng trùng ý.
    #
    # Các ứng viên của lượt suy luận sâu phải được TÁCH theo số chữ trước khi xếp vào
    # nguồn. Bản trước đổ nguyên cả `deep_alt_candidates` vào nguồn DÀI mà không lọc, mà
    # trong đó có lẫn từ đơn - đo thật trên câu "voi mù" của khách, nguồn dài nhận
    # `['titthiyavagga', 'jaccandha hatthim dassesi', ...]` nên phần tử ĐẦU của "nguồn dài"
    # lại là một từ đơn. `_interleave` bắt đầu bằng nguồn dài, nên chính chỗ đáng lẽ là cụm
    # đặc trưng nhất (`jaccandha hatthim dassesi`) bị `titthiyavagga` chiếm mất, và danh
    # sách hiện ra gần như toàn từ lẻ - đúng cái khách phàn nàn.
    deep_terms = _pick_terms(deep_alt_candidates, avoid)
    deep_multiword = [term for term in deep_terms if " " in term]
    deep_single = [term for term in deep_terms if " " not in term]

    short_pool = deep_single + [
        term
        for term in _pick_terms(
            [
                *(expansion.get("paliExactTerms") or []),
                *(local.get("mustHavePali") or []),
                *(expansion.get("paliRelatedTerms") or []),
                *(local.get("shouldHavePali") or []),
                *(expansion.get("paliHints") or []),
            ],
            avoid,
        )
        if " " not in term
    ]

    # Nguồn DÀI: CHỈ những mục thật sự nhiều chữ. Ưu tiên cụm của lượt suy luận sâu (đã trả
    # tiền cho một lượt gọi AI, không lấy thêm thì phí), rồi mới tới cụm nhiều chữ của
    # `expandedQueries` - cùng loại với cụm chính nhưng là phương án khác, hợp khi cụm chính
    # không khớp đúng cách chia trong kinh.
    long_pool = deep_multiword + [
        term for term in _pick_terms(expansion.get("expandedQueries") or [], avoid) if " " in term
    ]

    candidates = _interleave(short_pool, long_pool)[:MAX_TERM_CANDIDATES]
    terms = [term for term in candidates if _is_usable_term(term)][:MAX_TERMS]

    return {
        "ok": bool(main_keyword or terms or full_sentence),
        "fullSentence": full_sentence,
        "query": query,
        "mainKeyword": main_keyword or None,
        "terms": terms,
    }


# ---------------------------------------------------------------------------
# BẢN ĐANG DÙNG: hỏi Gemini bằng ĐÚNG câu lệnh của khách rồi RENDER THẲNG
#
# Khách đã tự hỏi Gemini web bằng câu lệnh dưới đây và hài lòng với kết quả, trong khi
# bản cũ (tự chấm điểm + tự lọc theo kho, xem khối "BẢN CŨ" phía trên) bị khách báo là
# không chính xác. Nên ở đây KHÔNG chấm điểm, KHÔNG đối chiếu kho, KHÔNG loại bỏ gì:
# Gemini trả về sao thì hiện đúng vậy.
#
# Mỗi mục gồm HAI phần tách rời: `pali` (chỉ chữ Pāḷi) và `meaning` (nghĩa trong ngoặc).
# Tách như vậy vì khách nói rõ "khi tìm kiếm thì chỉ lấy từ khoá pali thôi, dịch từ khoá
# để lấy từ khoá phù hợp thôi" - phần nghĩa chỉ để người đọc chọn đúng cụm, hai nút
# "Sao chép"/"Tìm ngay" chỉ nhận `pali`.
#
# Cũng KHÔNG đệm: mỗi lần bấm là một lượt hỏi mới, cùng lý do đã ghi ở phần bản cũ
# (khách phàn nàn bấm lại ra y hệt).
# ---------------------------------------------------------------------------

# Trần cho vừa một bảng gợi ý, không phải để lọc chất lượng.
#
# **ĐÚNG 2 mục mỗi nhóm, và con số này do GIAO DIỆN quyết định chứ không phải nội dung.**
# `.keywordList` là lưới `auto-fill` với cột tối thiểu 420px, nên ở khổ máy tính nó xếp
# ĐÚNG 2 cột. Để 3 mục thì mỗi nhóm thành một hàng đủ cộng một ô lẻ nằm chơ vơ bên trái -
# khách nhìn thấy ngay và phàn nàn "sao cứ lẻ lẻ mỗi cái 3 key vậy". 2 mục thì mọi nhóm
# đều là một hàng kín, không còn ô lẻ nào. Đổi con số này thì phải xem lại `.keywordList`.
DIRECT_MAX_ITEMS_PER_GROUP = 2

# Còn số nhóm là TRẦN THỜI GIAN: xin 4 nhóm x 4 mục (mỗi mục còn kèm một dòng nghĩa) làm
# model phải viết dài gấp đôi, và `gemini-3.6-flash` bắt đầu trả 504 DEADLINE_EXCEEDED.
DIRECT_MAX_GROUPS = 3

# Lượt hỏi này CHẬM hơn hẳn mọi lượt gọi Gemini khác của app - khách bấm một nút rồi ngồi
# xem con quay, chứ không phải một bước ngầm trong lúc tìm kiếm. Nên nó có timeout riêng
# thay vì dùng `gemini_request_timeout_ms` (12s, đặt cho các lượt gọi ngầm của tìm kiếm).
# Cùng cách xử lý với `translator.py` cho phần tóm tắt dài (timeout 60s riêng).
#
# Vì sao phải nới: 12s là QUÁ NGẮN cho lượt hỏi này. Đo thật trên câu "người mù sờ voi",
# hai lần bấm mỗi model:
#
#     gemini-3.6-flash        24.0s / 37.9s   (trả lời tốt, 3 nhóm)
#     gemini-3.5-flash        27.0s / 18.1s   (trả lời tốt)
#     gemini-3.5-flash-lite    1.7s /  1.7s   (nhanh nhưng có lần JSON hỏng)
#     gemini-3.1-flash-lite    2.6s /  3.7s
#
# Tức hai model tốt nhất KHÔNG BAO GIỜ kịp trong 12s - khách hỏi một câu rất rõ ràng mà
# vẫn bị báo "chưa gợi ý được từ khoá". Đây chính là lỗi khách gặp.
DIRECT_REQUEST_TIMEOUT_MS = 40000

# Trần cho CẢ vòng thử, không phải cho từng model. Bắt buộc phải có: timeout 40s nhân với
# 4 model trong danh sách là 160s, trong khi nginx trước app cắt ở 60s - khách sẽ nhận 504
# của nginx (một trang lỗi, không phải câu nhắn) trước khi vòng lặp kịp chạy xong.
#
# 50s để còn chỗ cho phần ghi nhật ký và độ trễ mạng. Model đầu ăn hết 40s thì model sau
# chỉ còn 10s - vẫn thừa cho hai model `lite` (1.7-3.7s), tức chuỗi dự phòng vẫn chạy.
DIRECT_TOTAL_BUDGET_MS = 50000

# Còn ít hơn ngần này thì thôi, không thử thêm model nào: một lượt gọi chắc chắn hỏng chỉ
# làm khách chờ thêm mà không đổi được kết quả.
DIRECT_MIN_ATTEMPT_MS = 4000

# Ngôn ngữ viết phần nghĩa trong ngoặc - chuỗi này nằm trong câu tiếng Việt của prompt.
_MEANING_LANGUAGE = {"vi": "tiếng Việt", "en": "tiếng Anh", "my": "tiếng Myanmar (Miến Điện)"}


class _DirectKeywordItem(BaseModel):
    pali: str = ""
    meaning: str = ""


class _DirectKeywordGroup(BaseModel):
    label: str = ""
    items: list[_DirectKeywordItem] = Field(default_factory=list)


class _DirectKeywordResult(BaseModel):
    groups: list[_DirectKeywordGroup] = Field(default_factory=list)


def _direct_keyword_prompt(query: str, language: str) -> str:
    """Câu lệnh của khách, giữ gần như nguyên văn - chỉ thêm phần mô tả JSON.

    Ba đoạn đầu là câu lệnh khách vẫn dán vào Gemini web. Phần còn lại chỉ nói CÁCH TRÌNH
    BÀY (chia nhóm, tách `pali` khỏi `meaning`) chứ không thêm ràng buộc nào về nội dung -
    đúng tinh thần "chủ yếu là cách đặt câu lệnh khéo léo cho AI".
    """
    return "\n".join(
        [
            "Hãy đóng vai chuyên gia Pāḷi kinh điển Tam tạng.",
            f'Tôi muốn tìm bài kinh về chủ đề: "{query}"',
            "Cung cấp cho tôi từ khóa ngắn gọn bằng tiếng Pāḷi đã được chia cách, chia thì,",
            "chia ngôi chuẩn xác 100% như trong Tam tạng kinh điển để tôi dùng làm từ khóa",
            "tìm kiếm trong 1 công cụ tìm kiếm kinh điển Pāḷi bằng AI.",
            "",
            "Cách trình bày:",
            f"- Chia thành 2-{DIRECT_MAX_GROUPS} NHÓM, xếp nhóm dễ tìm ra nhất lên đầu.",
            "- `label` của nhóm nói rõ đó là loại cụm gì và lấy từ đâu. Ví dụ:",
            '    "Cụm câu kinh văn chuẩn xác và dễ tìm ra nhất (Chánh văn Mahāparinibbānasutta & Udāna)"',
            '    "Cụm câu kinh văn đối thoại và chịu đựng cơn đau (Chánh văn Mahāparinibbānasutta)"',
            '    "Từ khóa và cụm Chú giải giải thích tên chứng bệnh"',
            f"- Mỗi nhóm ĐÚNG {DIRECT_MAX_ITEMS_PER_GROUP} mục - không hơn không kém, kể cả khi bạn",
            "  nghĩ ra nhiều hơn: chỗ hiển thị chỉ vừa từng ấy. Nghĩ ra nhiều thì giữ lại hai",
            "  cụm chắc chắn nhất. Mỗi mục có đúng hai trường:",
            "    `pali`   : CHỈ chữ Pāḷi, nguyên văn như trong kinh, đúng dạng đã chia.",
            "               Không kèm dấu ngoặc, không kèm bản dịch, không đánh số.",
            f"    `meaning`: nghĩa ngắn gọn bằng {_MEANING_LANGUAGE[language]} - đúng phần",
            "               vẫn viết trong ngoặc đơn ngay dưới cụm Pāḷi.",
            "- Ví dụ hai mục đúng:",
            '    {"pali":"kharo ābādho uppajji lohitapakkhandikā",',
            '     "meaning":"Cơn trọng bệnh khốc liệt khởi lên, chứng kiết lỵ ra máu"}',
            '    {"pali":"lohitapakkhandikābādho","meaning":"Chứng bệnh kiết lỵ đi tiêu ra máu"}',
            "",
            "Không viết gì ngoài JSON.",
            "Trả JSON thuần:",
            '{"groups":[{"label":"","items":[{"pali":"","meaning":""}]}]}',
        ]
    )


def _direct_client(timeout_ms: int) -> genai.Client:
    """Client riêng CHỈ để đặt timeout - xem `DIRECT_REQUEST_TIMEOUT_MS`.

    Không dùng `query_expander._client()` được vì hàm đó khoá cứng
    `gemini_request_timeout_ms`, và nới biến môi trường đó lên thì nới cho cả các lượt gọi
    ngầm của tìm kiếm - đúng chỗ 12s đang bảo vệ người dùng khỏi phải chờ.

    Dựng MỘT client cho mỗi lượt thử chứ không dùng lại: `timeout` nằm trong `http_options`
    của client, mà mỗi model lại được cấp một hạn giờ khác nhau tuỳ phần ngân sách còn lại.
    """
    api_key = str(settings()["gemini_api_key"])
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")
    return genai.Client(api_key=api_key, http_options={"timeout": timeout_ms})


def _ask_direct_keywords(query: str, language: str) -> dict | None:
    """MỘT lượt hỏi Gemini, thử lần lượt các model trong danh sách.

    Trả `None` khi MỌI model đều hỏng (hết giờ, hết quota, Google lỗi) - khác hẳn với việc
    model trả lời nhưng không nêu được từ khoá nào. Hai trường hợp này phải nói với khách
    bằng hai câu khác nhau: một câu bảo "thử lại sau", một câu bảo "diễn đạt khác đi". Bản
    đầu gộp cả hai thành `{}` và khách gặp đúng cảnh vô lý - hỏi một câu rất rõ ràng (câu
    chuyện người mù sờ voi) mà bị bảo là "hãy thử diễn đạt ngắn gọn hơn", trong khi lỗi
    thật chỉ là hai model đầu bảng hết 12 giây.
    """
    prompt = _direct_keyword_prompt(query, language)
    deadline = time.monotonic() + DIRECT_TOTAL_BUDGET_MS / 1000

    for model in _keyword_models():
        remaining_ms = int((deadline - time.monotonic()) * 1000)
        if remaining_ms < DIRECT_MIN_ATTEMPT_MS:
            break
        try:
            response = _direct_client(min(DIRECT_REQUEST_TIMEOUT_MS, remaining_ms)).models.generate_content(
                model=model,
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
        except Exception as exc:
            if not _is_retryable_error(exc):
                break
            continue

        # JSON hỏng thì LUÔN thử model sau, không tính là lỗi dừng vòng. Đo thật:
        # `gemini-3.5-flash-lite` có lần trả JSON lệch dấu ngoặc (`"items"` nằm sai cấp) -
        # `_is_retryable_error` đọc câu báo lỗi của pydantic thì không thấy từ khoá nào nên
        # coi là lỗi chết người và dừng cả vòng, dù model kế tiếp trả lời tốt trong 3 giây.
        try:
            return _DirectKeywordResult.model_validate_json(response.text or "{}").model_dump()
        except Exception:  # noqa: BLE001 - model tra JSON hong, thu model khac
            continue

    return None


def _clean_pali(value: str) -> str:
    """Dọn phần `pali` về ĐÚNG chuỗi đem đi tìm kiếm được, không sửa chính tả Pāḷi.

    Chỉ chống lại thói quen trình bày của model chứ không phải một bộ lọc chất lượng:
    model hay trả `kharo ābādho (cơn bệnh khốc liệt)` hoặc `1. lohitapakkhandikā` dù prompt
    đã dặn tách riêng `meaning`. Phần trong ngoặc bị cắt vì khách yêu cầu "khi tìm kiếm thì
    chỉ lấy từ khoá pali thôi" - để nguyên thì nó chui thẳng vào ô tìm kiếm.
    """
    text = " ".join(str(value or "").split())
    text = re.sub(r"^\d+[.)]\s*", "", text)
    text = re.sub(r"\s*[(（\[].*$", "", text)
    return text.strip().strip('"“”').strip()


def _empty_keyword_payload(query: str, error: str | None = None) -> dict:
    """`error` chỉ đặt khi lượt gọi Gemini HỎNG. Để trống thì giao diện dùng câu mặc định
    "chưa gợi ý được... thử diễn đạt ngắn gọn hơn" - câu đó chỉ đúng khi model đã trả lời."""
    payload = {
        "ok": False,
        "query": query,
        "groups": [],
        "fullSentence": None,
        "mainKeyword": None,
        "terms": [],
    }
    if error:
        payload["error"] = error
    return payload


def suggest_pali_keywords(query: str, language: str = DEFAULT_LANGUAGE) -> dict:
    """Trả các nhóm từ khoá Pāḷi cho một câu hỏi - **render thẳng những gì Gemini đưa ra**.

    Không ghi `search_logs`: đây không phải một lượt tìm kiếm, và lượt tìm thật sau đó
    (khi khách bấm "Tìm ngay" hoặc tự dán) mới là thứ đáng vào lịch sử.

    Ba khoá `fullSentence` / `mainKeyword` / `terms` vẫn còn trong kết quả nhưng CHỈ để
    tương thích ngược, không phải để hiển thị: `log_keyword_request` và trang
    `admin_keyword_history` đọc đúng ba khoá đó. Giao diện tìm kiếm đọc `groups`.
    """
    query = str(query or "").strip()
    language = normalize_language(language)
    if not query:
        return _empty_keyword_payload("")

    data = _ask_direct_keywords(query, language)
    if data is None:
        return _empty_keyword_payload(query, t(language, "keywords.failed"))

    groups: list[dict] = []
    flat: list[str] = []
    seen: set[str] = set()
    for raw_group in (data.get("groups") or [])[:DIRECT_MAX_GROUPS]:
        items: list[dict] = []
        # Duyệt HẾT các mục model đưa ra rồi mới cắt còn 2 ở cuối, chứ không cắt trước:
        # model vẫn có lúc trả 3 mục dù prompt dặn 2, và một mục có thể bị loại vì trùng.
        # Cắt trước thì nhóm đó chỉ còn 1 ô - đúng cái ô lẻ đang muốn tránh.
        taken: set[str] = set()
        for raw_item in raw_group.get("items") or []:
            if len(items) >= DIRECT_MAX_ITEMS_PER_GROUP:
                break
            pali = _clean_pali(str(raw_item.get("pali") or ""))
            if not pali:
                continue
            # Bỏ trùng theo dạng đã chuẩn hoá - hai nhóm hay lặp lại cùng một cụm chỉ khác
            # dấu câu. Đây là chỗ DUY NHẤT loại bỏ mục, và không phải vì chê chất lượng.
            key = normalize_pali(pali)
            if not key or key in seen or key in taken:
                continue
            taken.add(key)
            items.append({"pali": pali, "meaning": " ".join(str(raw_item.get("meaning") or "").split())})
        if not items:
            continue

        # NHÓM KHÔNG CÓ TIÊU ĐỀ THÌ BỎ, trừ khi nó là nhóm đầu tiên.
        #
        # Giao diện chỉ vẽ tiêu đề khi `label` có chữ, nên một nhóm không tiêu đề nằm ngay
        # dưới nhóm trước sẽ DÍNH LIỀN vào nhóm đó - khách nhìn thấy một tiêu đề với 3 thẻ
        # bên dưới và tưởng trần 2 mục bị hỏng, dù mỗi nhóm vẫn đúng 2. Đây chính là ca
        # "vẫn hiển thị 3 kết quả" khách báo. Trần 2 mục là trần MỖI NHÓM, nên nó chỉ giữ
        # đúng lời hứa khi ranh giới giữa các nhóm còn nhìn thấy được.
        label = " ".join(str(raw_group.get("label") or "").split())
        if not label and groups:
            continue

        # `seen` chỉ nhận những mục THẬT SỰ hiện ra. Ghi cả mục đã bị cắt bỏ vào đây thì
        # một cụm không ai nhìn thấy vẫn chặn mất chính nó ở nhóm sau.
        seen |= taken
        flat.extend(item["pali"] for item in items)
        groups.append({"label": label, "items": items})

    if not flat:
        return _empty_keyword_payload(query)

    return {
        "ok": True,
        "query": query,
        "groups": groups,
        "fullSentence": None,
        "mainKeyword": flat[0],
        "terms": flat[1:],
    }


# ---------------------------------------------------------------------------
# Nhật ký "Lấy Từ khoá Pāḷi" - xem `db/migrations/013_pali_keyword_logs.sql`
#
# Ghi MỌI lượt bấm, không đợi ai bấm lưu gì - cùng triết lý với `search_logs` và
# `dictionary_search_logs`. Khách hỏi thẳng: "em muốn xem câu hỏi gốc của mn là gì", nên
# thứ phải lưu là CÂU HỎI NGUYÊN VĂN cộng bộ từ khoá đã trả về, chứ không phải chỉ thống kê.
# ---------------------------------------------------------------------------

# Câu hỏi ở đây có thể là cả một đoạn kinh dán vào (đo thật: khách đã dán một đoạn chú
# giải ~2.000 ký tự), nên trần rộng hơn hẳn `dictionary.HISTORY_MAX_QUERY_CHARS` (500) -
# cắt ở 500 là mất luôn phần đuôi mà admin cần đọc để hiểu người ta đang hỏi gì.
LOG_MAX_QUERY_CHARS = 4000


def log_keyword_request(query: str, language: str, user_id: str | None, result: dict) -> None:
    """Ghi lại một lượt lấy từ khoá. Không bao giờ làm hỏng request của khách."""
    query = str(query or "").strip()[:LOG_MAX_QUERY_CHARS]
    if not query:
        return
    payload = {
        "fullSentence": result.get("fullSentence"),
        "mainKeyword": result.get("mainKeyword"),
        "terms": result.get("terms") or [],
    }
    has_result = bool(payload["fullSentence"] or payload["mainKeyword"] or payload["terms"])
    try:
        execute(
            "insert into pali_keyword_logs (user_id, query, language, keywords, has_result) "
            "values (%s, %s, %s, %s, %s)",
            [user_id, query, normalize_language(language), Jsonb(payload), has_result],
        )
    except Exception:  # noqa: BLE001 - chua chay migration thi van phai tra tu khoa cho khach
        pass


def history_rows(
    keyword: str,
    limit: int,
    before_time: str | None = None,
    before_id: str | None = None,
    only_empty: bool = False,
) -> list[dict]:
    """Một mẻ lịch sử, cũ dần kể từ mốc `before`.

    Phân trang theo CON TRỎ `(created_at, id)` chứ không theo `offset`, vì bảng vẫn được
    ghi thêm trong lúc admin đang cuộn - cùng lý do đã áp dụng ở `dictionary.history_rows`.
    """
    conditions: list[str] = []
    params: list[object] = []
    if keyword:
        conditions.append("k.query ilike %s")
        params.append(f"%{keyword}%")
    if only_empty:
        conditions.append("k.has_result = false")
    if before_time and before_id:
        conditions.append("(k.created_at, k.id) < (%s::timestamptz, %s::uuid)")
        params.extend([before_time, before_id])
    where_sql = ("where " + " and ".join(conditions)) if conditions else ""
    return fetch_all(
        f"""
        select k.id, k.query, k.language, k.keywords, k.has_result, k.created_at, u.username
        from pali_keyword_logs k
        left join users u on k.user_id = u.id
        {where_sql}
        order by k.created_at desc, k.id desc
        limit %s
        """,
        [*params, limit],
    )


def history_counts(keyword: str, only_empty: bool) -> dict:
    conditions: list[str] = []
    params: list[object] = []
    if keyword:
        conditions.append("query ilike %s")
        params.append(f"%{keyword}%")
    if only_empty:
        conditions.append("has_result = false")
    where_sql = ("where " + " and ".join(conditions)) if conditions else ""
    filtered = fetch_one(f"select count(*) as cnt from pali_keyword_logs {where_sql}", params)
    total = fetch_one("select count(*) as cnt from pali_keyword_logs")
    empty = fetch_one("select count(*) as cnt from pali_keyword_logs where has_result = false")
    return {
        "filtered": int(filtered["cnt"]) if filtered else 0,
        "total": int(total["cnt"]) if total else 0,
        "empty": int(empty["cnt"]) if empty else 0,
    }


def clear_history() -> None:
    execute("delete from pali_keyword_logs")
