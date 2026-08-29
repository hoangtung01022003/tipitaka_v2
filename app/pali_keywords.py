"""Gợi ý từ khoá Pāḷi cho câu hỏi khó - tính năng ĐỘC LẬP với luồng tìm kiếm.

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

from functools import lru_cache

from pydantic import BaseModel, Field

from .db import fetch_all
from .glossary import analyze_query, canonicalize_query
from .i18n import DEFAULT_LANGUAGE, normalize_language
from .normalize import normalize_pali
from .query_expander import (
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

# Bốn thang điểm TÁCH RỜI nhau, không phải bốn mức trong cùng một thang: một TÊN BÀI KINH
# đã xác thực phải luôn thắng mọi cụm trích từ thân kinh, cụm nhiều từ phải luôn thắng từ
# đơn, và từ đơn đặc trưng phải luôn thắng từ phổ thông - bất kể độ hiếm chênh nhau bao
# nhiêu. Xem `_resolve_candidate` và `_resolve_sutta_name`.
SUTTA_NAME_BASE_SCORE = 1000.0
MULTIWORD_BASE_SCORE = 100.0
MULTIWORD_LENGTH_BONUS = 10.0
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

# Tên bộ/tạng/ấn bản - không phải tên bài kinh, và khớp lung tung khắp nơi. Đo thật:
# `majjhima` trả về `Vicayahārasampāto`, `nikaya` trả về `Paṭhamamahāsaṅgītikathā`.
COLLECTION_WORDS = {
    "sutta", "suttam", "suttanta", "vagga", "vaggo", "nikaya", "pitaka", "pitake",
    "majjhima", "digha", "samyutta", "anguttara", "khuddaka", "tipitaka", "udana",
    "vinaya", "abhidhamma", "atthakatha", "tika", "pali", "palic", "kinh", "trung",
    "truong", "tuong", "tang", "chi", "tieu", "bo",
}

# **KHÔNG ĐỆM lượt suy luận này** - cố ý, theo yêu cầu của khách: "hỏi đúng câu đó cứ bị
# lặp lại". Bản trước đệm cả trong tiến trình lẫn dưới DB (`query_ai_cache`), nên bấm lại
# nút trên cùng một câu hỏi luôn ra y hệt bộ từ khoá cũ - trong khi khách bấm lại CHÍNH LÀ
# để xin một bộ khác mà thử.
#
# Đây là đánh đổi ngược hẳn với phần còn lại của dự án, nên nói rõ để đừng ai "sửa lại cho
# nhất quán": ở chỗ khác Gemini phải được đệm để cùng câu hỏi cho cùng kết quả (xem
# `003_query_cache.sql`), còn ở đây tính bất định của Gemini chính là thứ đang được dùng.
# Cái giá là mỗi lần bấm tốn một lượt gọi Gemini, và bộ từ khoá có thể tốt xấu khác nhau
# giữa hai lần bấm liền kề.
#
# `expand_query_with_ai` (danh sách "thuật ngữ liên quan") VẪN đệm như cũ và không được
# đụng tới: đệm đó dùng chung với pipeline tìm kiếm chính, bỏ nó đi là làm kết quả tìm
# kiếm mất ổn định - đúng thứ đệm đó sinh ra để chặn.


class _DeepKeywordResult(BaseModel):
    identified: str = ""
    reasoning: str = ""
    # Tên bài kinh viết bằng Pāḷi, XIN NHIỀU CÁCH VIẾT. Đây là trường cho từ khoá tốt nhất
    # (xem `_resolve_sutta_name`), nhưng mỗi ấn bản đặt tên một khác nên phải xin vài cách:
    # câu "người mù sờ voi" được AI gọi là "Tittha Sutta" (tên bên SuttaCentral) trong khi
    # CST đặt là `Paṭhamanānātitthiyasuttaṃ` - chỉ xin một cách viết là hụt mất bài đúng.
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
    """Tên này có ứng với một tiêu đề thật trong kho không - chặn tên AI bịa.

    Đo thật trên 4 tên có thật và 4 tên bịa: nhận đúng cả 4 tên thật (kể cả
    `culamalunkyovada`, thứ không xuất hiện nguyên văn trong BẤT KỲ đoạn kinh nào nên
    phép kiểm tra theo thân kinh sẽ loại oan), và loại 3/4 tên bịa. Ca lọt lưới duy nhất
    là `brahmajalupama` khớp gốc `brahmaja` của `Brahmajālasuttaṃ` - vẫn trỏ về một bài
    kinh có thật, nên tác hại giới hạn.
    """
    if len(name) < SUTTA_NAME_MIN_LENGTH or name in COLLECTION_WORDS:
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


def _resolve_candidate(candidate: str) -> tuple[float, str] | None:
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
            score = (
                MULTIWORD_BASE_SCORE
                + len(remaining) * MULTIWORD_LENGTH_BONUS
                + _rarity(together)
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
            "2. `suttaNames` - PHẦN QUAN TRỌNG NHẤT. Nếu nhận ra, hãy cho 2-4 CÁCH VIẾT PĀḶI",
            "   khác nhau của TÊN bài kinh đó, chỉ tên thôi, KHÔNG kèm tên bộ/tạng/số hiệu.",
            "   Mỗi ấn bản đặt tên một khác, nên hãy liệt kê cả tên ngắn lẫn tên dài đầy đủ,",
            "   cả cách viết của ấn bản Miến (CST/Chaṭṭha Saṅgāyana) lẫn cách viết phổ biến.",
            "   Ví dụ dạng mong muốn: [\"Alagaddūpamasutta\", \"Alagaddūpama\"];",
            "   [\"Cūḷamālukyasutta\", \"Cūḷamālunkyovādasutta\"].",
            "   Không chắc thì cứ đưa phỏng đoán tốt nhất; để trống chỉ khi hoàn toàn mù tịt.",
            "",
            f"3. `candidates` - {DEEP_CANDIDATE_COUNT} CỤM TỪ KHOÁ PĀḶI trích từ THÂN bài kinh,",
            "   khác nhau thật sự (không phải biến thể chính tả của cùng một cụm), xếp theo độ",
            "   tin cậy giảm dần.",
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
            "Không trả lời nội dung kinh, không dịch, không giải thích ngoài JSON.",
            "Trả JSON thuần:",
            '{"identified":"","reasoning":"","suttaNames":["",""],"candidates":["",""]}',
        ]
    )


def extract_main_keyword_deep(query: str, language: str) -> tuple[str | None, list[str]]:
    """Suy luận sâu để tìm "cụm từ khoá chính" - HOÀN TOÀN ĐỘC LẬP với pipeline tìm kiếm
    chính (không gọi, không lùi về `extract_search_keyword_with_ai`), xem docstring đầu
    file để biết vì sao cần tách riêng.

    Trả `(cụm_tốt_nhất_đã_kiểm_chứng, các_cụm_khác_còn_dùng_được)` - vế thứ hai được
    `suggest_pali_keywords` gộp vào nhóm "cụm dài" của danh sách gợi ý phụ, tận dụng luôn
    những ứng viên đã trả tiền cho một lượt gọi AI thay vì bỏ phí.

    Trả `(None, [])` khi AI không đưa ra được cụm nào tồn tại thật trong kho - KHÔNG có
    lưới an toàn nào khác. `suggest_pali_keywords` khi đó chỉ đơn giản không hiện "Cụm từ
    khoá chính", đúng yêu cầu "tách biệt ra đừng liên quan gì, nó chỉ việc là render ra
    từ khoá thôi".

    **Mỗi lần gọi là một lượt hỏi Gemini MỚI, không đệm** - xem lời giải thích ở khối hằng
    số đầu file. Bấm nút hai lần trên cùng một câu hỏi thì ra hai bộ từ khoá khác nhau, và
    đó là chủ ý.
    """
    prompt = _deep_keyword_prompt(query)
    client = _client()
    parsed: _DeepKeywordResult | None = None
    for model in _keyword_models():
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
            parsed = _DeepKeywordResult.model_validate_json(response.text or "{}")
            break
        except Exception as exc:
            if not _is_retryable_error(exc):
                break
    cached = parsed.model_dump() if parsed else {}

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

    for raw_candidate in cached.get("candidates") or []:
        collect(_resolve_candidate(str(raw_candidate)))

    if not resolved:
        # Không ứng viên nào lấy một chữ có thật trong kho. AI bịa hoàn toàn cho câu hỏi
        # này, không phải một cụm gần đúng còn cứu được.
        return None, []

    resolved.sort(key=lambda item: item[0], reverse=True)
    best = resolved[0][1]
    others = [phrase for _, phrase in resolved[1:]]
    return best, others


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


def suggest_pali_keywords(query: str, language: str = DEFAULT_LANGUAGE) -> dict:
    """Trả cụm Pāḷi chính + tối đa `MAX_TERMS` gợi ý khác cho một câu hỏi - tổng khoảng 5
    kết quả (kể cả cụm chính), đủ cả từ đơn ngắn lẫn cụm dài chứ không chỉ một loại.

    Không ghi `search_logs`: đây không phải một lượt tìm kiếm, và lượt tìm thật sau đó
    (khi khách bấm "Tìm ngay" hoặc tự dán) mới là thứ đáng vào lịch sử.
    """
    query = str(query or "").strip()
    language = normalize_language(language)
    if not query:
        return {"ok": False, "query": "", "mainKeyword": None, "terms": []}

    clean_query = canonicalize_query(query) or query

    # Cụm chính: HOÀN TOÀN từ lượt suy luận sâu độc lập - không lùi về hàm nào khác.
    # AI suy luận sâu không ra được cụm nào tồn tại thật trong kho thì `main_keyword` là
    # `None`, và kết quả trả về đơn giản không có "Cụm từ khoá chính" - xem docstring của
    # `extract_main_keyword_deep`.
    main_keyword, deep_alt_candidates = extract_main_keyword_deep(query, language)

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
        "ok": bool(main_keyword or terms),
        "query": query,
        "mainKeyword": main_keyword or None,
        "terms": terms,
    }
