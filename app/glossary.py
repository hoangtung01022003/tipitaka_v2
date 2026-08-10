from dataclasses import dataclass
import re

from .normalize import (
    normalize_pali,
    pali_content_tokens,
    pali_ratio,
    query_segments,
    strip_vietnamese,
    tokenize,
)

# Dưới ngưỡng này thì coi truy vấn là tiếng Việt, không lấy token của nó làm từ khóa Pali.
PALI_LIKE_RATIO = 0.5

# Khối chữ Myanmar. Dùng để biết khi nào phải bỏ luật ranh giới từ - xem `has_trigger`.
_MYANMAR = re.compile(r"[က-႟]")


@dataclass(frozen=True)
class Concept:
    id: str
    label: str
    triggers: tuple[str, ...]
    pali: tuple[str, ...]
    must: tuple[str, ...] = ()
    should: tuple[str, ...] = ()
    avoid: tuple[str, ...] = ()
    phrases: tuple[str, ...] = ()


CONCEPTS: tuple[Concept, ...] = (
    Concept(
        id="stealing",
        label="trộm cắp, lấy của không cho",
        triggers=("trom cap", "trom", "cap", "lay cua khong cho", "lay vat khong cho", "lay cua nguoi", "khong cho ma lay", "an cap", "dao tac", "stealing", "steal", "stolen", "theft", "taking what is not given", "not given", "robbery", "ခိုးယူ", "သူခိုး"),
        pali=("adinnadana", "adinna", "adana", "theyyam", "theyya", "cora", "avahara", "pariggaha", "sikkhapada", "veramani"),
        must=("adinnadana", "adinna", "theyyam", "theyya"),
        should=("cora", "avahara", "pariggaha", "sikkhapada", "veramani"),
        phrases=("adinnadana veramani", "adinnadana sikkhapada", "adinnam adiyati", "theyyasankhatam"),
    ),
    Concept(
        id="killing",
        label="sát sinh, giết hại",
        triggers=("sat sinh", "giet hai", "giet", "hai chung sinh", "doat mang", "sat hai", "killing", "kill", "taking life", "harming living beings", "murder", "သတ်ဖြတ်", "အသက်သတ်"),
        pali=("panatipata", "pana", "atipata", "vadha", "ghata", "himsa", "veramani", "sikkhapada"),
        must=("panatipata", "pana"),
        should=("atipata", "vadha", "ghata", "himsa", "veramani", "sikkhapada"),
        phrases=("panatipata veramani", "panam hanati", "panatipata sikkhapada"),
    ),
    Concept(
        id="false_speech",
        label="nói dối, vọng ngữ",
        triggers=("noi doi", "vong ngu", "noi sai su that", "noi la doi", "noi khong that", "lying", "lie", "false speech", "falsehood", "untruth", "မုသာ", "လိမ်ညာ"),
        pali=("musavada", "musa", "vada", "abhuta", "sampajanamusavada", "veramani", "sikkhapada"),
        must=("musavada", "musa"),
        should=("vada", "abhuta", "sampajanamusavada", "veramani", "sikkhapada"),
        phrases=("musavada veramani", "sampajanamusavada", "musavada sikkhapada"),
    ),
    Concept(
        id="sexual_misconduct",
        label="tà hạnh, không phạm hạnh",
        triggers=("ta hanh", "dam duc", "khong pham hanh", "hanh dam", "ngoai tinh", "bat tinh hanh", "sexual misconduct", "adultery", "celibacy", "unchastity", "ကာမေသုမိစ္ဆာစာရ"),
        pali=("kamesumicchacara", "abrahmacariya", "methuna", "kama", "micchacara", "veramani", "sikkhapada"),
        must=("kamesumicchacara", "abrahmacariya", "methuna"),
        should=("kama", "micchacara", "veramani", "sikkhapada"),
        phrases=("kamesumicchacara veramani", "abrahmacariya veramani", "methunadhamma"),
    ),
    Concept(
        id="intoxicants",
        label="rượu và chất say",
        triggers=("uong ruou", "ruou", "chat say", "say nghien", "men ruou", "ma tuy", "intoxicants", "intoxicant", "alcohol", "liquor", "beer", "wine", "drinking", "drugs", "အရက်", "သေသောက်"),
        pali=("surameraya", "majja", "pamadatthana", "meraya", "sura", "veramani", "sikkhapada"),
        must=("surameraya", "majja"),
        should=("pamadatthana", "meraya", "sura", "veramani", "sikkhapada"),
        phrases=("suramerayamajjapamadatthana veramani", "surameraya majja", "majja pamadatthana"),
    ),
    Concept(
        id="donation_result",
        label="bố thí/cúng dường và quả báo/phước báu",
        triggers=("bo thi", "cung duong", "qua bao", "qua phuoc", "phuoc bau", "cong duc", "xả thí", "cho tang", "giving", "generosity", "donation", "offering", "almsgiving", "merit", "fruit of giving", "ဒါန", "အလှူ"),
        pali=("dana", "dakkhina", "deyya", "caga", "vipaka", "phala", "punna", "anisamsa", "dakkhineyya"),
        must=("dana", "dakkhina", "deyya", "caga"),
        should=("vipaka", "phala", "punna", "anisamsa", "dakkhineyya"),
        phrases=("dana phala", "dana vipaka", "dakkhina phala", "punna anisamsa"),
    ),
    Concept(
        id="refuge",
        label="quy y Tam Bảo, nơi nương tựa",
        triggers=("quy y", "tam bao", "nuong tua", "noi nuong tua", "noi quy huong", "khong le thuoc", "khong dua vao ai", "refuge", "going for refuge", "take refuge", "three jewels", "triple gem", "သရဏဂုံ", "ရတနာသုံးပါး"),
        pali=("sarana", "saranagamana", "buddha", "dhamma", "sangha", "parayana", "aparappaccaya", "cittuppada"),
        must=("sarana", "saranagamana"),
        should=("buddha", "dhamma", "sangha", "parayana", "aparappaccaya", "cittuppada"),
        phrases=("saranagamanam", "esa me saranam", "esa me parayanam", "aparappaccayo cittuppado"),
    ),
    Concept(
        id="sangha_jewel",
        label="Tăng bảo, Tăng già, Thánh chúng",
        triggers=("tang bao", "tang gia", "tang chung", "chung tang", "thanh tang", "tang doan", "khai niem tang bao", "dinh nghia tang bao", "sangha", "noble sangha", "community of monks", "jewel of the sangha", "သံဃာ", "သံဃရတနာ"),
        pali=("sangharatana", "sangha", "ariyasangha", "savakasangha", "ratana", "ratanattaya", "supatipanna", "ujupatipanna", "nayapatipanna", "samicipatipanna"),
        must=("sangha",),
        should=("sangharatana", "ariyasangha", "savakasangha", "ratana", "ratanattaya", "supatipanna", "ujupatipanna", "nayapatipanna", "samicipatipanna"),
        phrases=("sangharatanam", "sangham saranam", "ariyasangha", "savakasangha", "supatipanno bhagavato savakasangho"),
    ),
    Concept(
        id="buddha_jewel",
        label="Phật bảo",
        triggers=("phat bao", "duc phat bao", "khai niem phat bao", "dinh nghia phat bao", "jewel of the buddha", "the buddha", "tathagata", "enlightened one", "ဗုဒ္ဓ", "ဘုရားရှင်"),
        pali=("buddharatana", "buddha", "bhagava", "tathagata", "araha", "sammasambuddha", "ratana", "ratanattaya"),
        must=("buddha",),
        should=("buddharatana", "bhagava", "tathagata", "araha", "sammasambuddha", "ratana", "ratanattaya"),
        phrases=("buddharatanam", "buddham saranam", "itipi so bhagava araham sammasambuddho"),
    ),
    Concept(
        id="dhamma_jewel",
        label="Pháp bảo",
        triggers=("phap bao", "giao phap", "khai niem phap bao", "dinh nghia phap bao", "jewel of the dhamma", "the dhamma", "dhamma jewel", "ဓမ္မရတနာ"),
        # KHONG dat "the teaching" hay "တရားတော်" lam trigger: trong tieng Myanmar
        # "တရားတော်" chi co nghia "bai phap", co mat trong gan nhu moi cau hoi ve kinh.
        # Do duoc la no keo `dhamma` - mot tu cuc rong - vao `must`, lam chim mat tu dac
        # trung: cau hoi ve niem hoi tho bi tra ve cac doan buddhanussati.
        pali=("dhammaratana", "dhamma", "svakkhata", "sanditthika", "akalika", "ehipassika", "opanayika", "paccattam", "ratana", "ratanattaya"),
        must=("dhamma",),
        should=("dhammaratana", "svakkhata", "sanditthika", "akalika", "ehipassika", "opanayika", "paccattam", "ratana", "ratanattaya"),
        phrases=("dhammaratanam", "dhammam saranam", "svakkhato bhagavata dhammo"),
    ),
    Concept(
        id="not_eating_after_noon",
        label="giới không ăn phi thời",
        triggers=("khong an phi thoi", "phi thoi", "an phi thoi", "qua ngo", "sau gio ngo", "sau bua trua", "an ban dem", "eating at the wrong time", "wrong time", "untimely eating", "after noon", "ဝိကာလဘောဇန"),
        pali=("vikalabhojana", "vikala", "bhojana", "veramani", "sikkhapada", "uposatha", "pacittiya", "rattibhojana"),
        must=("vikalabhojana", "vikala"),
        should=("bhojana", "veramani", "sikkhapada", "uposatha", "pacittiya", "rattibhojana"),
        phrases=("vikalabhojana veramani", "vikale bhojanam", "vikalabhojana sikkhapada"),
    ),
    Concept(
        id="virtue",
        label="giới hạnh/trì giới",
        triggers=("gioi", "gioi hanh", "tri gioi", "phong ho", "hoc gioi", "pham hanh", "virtue", "morality", "ethical conduct", "precepts", "training rules", "sila", "သီလ", "ကျင့်ဝတ်"),
        pali=("sila", "sikkhapada", "samvara", "patimokkha", "veramani", "vinaya", "brahmacariya"),
        should=("sila", "sikkhapada", "samvara", "patimokkha", "veramani", "vinaya"),
        phrases=("sila sikkhapada", "patimokkhasamvara", "veramani sikkhapada"),
    ),
    Concept(
        id="kamma_result",
        label="nghiệp và quả của nghiệp",
        triggers=("nghiep", "qua cua nghiep", "nhan qua", "nghiep bao", "di thuc", "kamma", "karma", "result of kamma", "action and result", "fruit of kamma", "ကံ", "ကံအကျိုး"),
        pali=("kamma", "vipaka", "phala", "hetu", "paccaya", "akusala", "kusala"),
        must=("kamma",),
        should=("vipaka", "phala", "hetu", "paccaya", "kusala", "akusala"),
        phrases=("kammassa phalam", "kammassa vipako", "kusalakamma", "akusalakamma"),
    ),
    Concept(
        id="impermanence",
        label="vô thường",
        triggers=("vo thuong", "khong thuong", "bien hoai", "sinh diet", "impermanence", "impermanent", "conditioned things", "anicca", "အနိစ္စ", "မမြဲ"),
        pali=("anicca", "viparinama", "udaya", "vaya", "uppada", "nirodha"),
        should=("anicca", "viparinama", "udaya", "vaya", "uppada", "nirodha"),
        phrases=("aniccam", "uppadavayadhammino", "viparinamadhamma"),
    ),
    Concept(
        id="suffering",
        label="khổ",
        triggers=("kho", "kho dau", "dukkha", "bat toai nguyen", "suffering", "unsatisfactoriness", "ဒုက္ခ", "ဆင်းရဲ"),
        pali=("dukkha", "dukkhata", "dukkhasamudaya", "dukkhanirodha"),
        should=("dukkha", "dukkhata"),
        phrases=("dukkham", "dukkhasamudaya", "dukkhanirodha"),
    ),
    Concept(
        id="non_self",
        label="vô ngã",
        triggers=("vo nga", "khong phai ta", "khong phai cua ta", "anatta", "non-self", "not self", "no self", "အနတ္တ"),
        pali=("anatta", "netam mama", "nesohamasmi", "na meso atta"),
        should=("anatta", "netam mama", "nesohamasmi", "na meso atta"),
        phrases=("anatta", "netam mama", "nesohamasmi", "na meso atta"),
    ),
    Concept(
        id="meditation",
        label="thiền định",
        triggers=("thien", "dinh", "tam dinh", "chanh niem", "niem xu", "meditation", "jhana", "concentration", "mindfulness", "absorption", "satipatthana", "foundations of mindfulness", "mindfulness of breathing", "breathing", "တရားထိုင်", "သမာဓိ", "သတိပဋ္ဌာန်", "အာနာပါန"),
        pali=("jhana", "samadhi", "sati", "satipatthana", "anapanasati", "bhavana"),
        should=("jhana", "samadhi", "sati", "satipatthana", "anapanasati", "bhavana"),
        phrases=("jhana", "samadhi", "satipatthana", "anapanasati", "bhavana"),
    ),
    # Ẩn dụ con rắn - chính là truy vấn khách gửi kèm ảnh chụp trong `toiuu_timkiem.docx`,
    # và cũng là ca kiểm định ở `dev_verify.py` lẫn `dev_http_check.py`.
    #
    # Vì sao phải thêm: mười bảy khái niệm phía trên đều là khái niệm GIÁO LÝ (giới, Tam
    # Bảo, nghiệp, vô thường...), không cái nào phủ hình ảnh ẩn dụ. Nên câu hỏi về con rắn
    # rơi hết vào tay Gemini, và đo được là chỉ 1/3 lần đoán ra `alagadda` - ba lần chạy
    # cho ba kết quả khác nhau. Có mục này thì tầng phân tích cục bộ luôn cấp sẵn từ khoá,
    # AI có trả về kém hay bị tắt hẳn cũng vẫn tìm đúng bài.
    #
    # `must` để trống có chủ ý: đặt `alagadda` vào must thì các câu hỏi về rắn nói chung
    # (sappa, uraga) sẽ bị nhánh must lọc sạch, đúng cái bẫy over-narrow đã ghi trong
    # CLAUDE.md.
    # Hai khái niệm dưới đây trước KHÔNG hề có, dù là loại cơ bản nhất và nằm ngay trong
    # bộ kiểm định. Truy vấn về lòng từ hay Tứ Diệu Đế vì thế phó mặc hoàn toàn cho Gemini;
    # đo với AI tắt thì cả hai trả về 0 kết quả.
    Concept(
        id="loving_kindness",
        label="lòng từ, từ bi hỷ xả",
        triggers=("long tu", "tu bi", "long tu bi", "tu tam", "bi man", "long thuong",
                  "tu bi hy xa", "bon vo luong tam", "loving-kindness", "loving kindness",
                  "compassion", "goodwill", "sympathetic joy", "equanimity",
                  "divine abidings", "brahmaviharas", "မေတ္တာ", "ကရုဏာ"),
        pali=("metta", "karuna", "mudita", "upekkha", "brahmavihara", "appamanna",
              "mettacitta", "mettasahagata"),
        # `must` phải có, không được để trống: `should` chứa toàn từ rất phổ biến, đo được
        # là truy vấn về lòng từ lại trả về các đoạn nói về samādhi/paññindriya vì chúng
        # khớp `sati`/`samadhi` ở hàng nghìn chỗ và dìm mất `metta`.
        must=("metta", "karuna"),
        should=("mudita", "upekkha", "brahmavihara", "appamanna"),
        phrases=("mettasahagatena cetasa", "karunasahagatena cetasa", "brahmavihara",
                 "appamanna", "mettacitta"),
    ),
    # Tách riêng khỏi `meditation`: khái niệm thiền nói chung quá rộng, `should` của nó
    # toàn từ phổ biến nên câu hỏi về niệm hơi thở bị kéo về các đoạn samādhi chung chung.
    Concept(
        id="breathing_meditation",
        label="niệm hơi thở, quán sổ tức",
        triggers=("niem hoi tho", "hoi tho", "quan hoi tho", "so tuc", "anapana",
                  "mindfulness of breathing", "breathing meditation", "in-breath",
                  "out-breath", "အာနာပါန", "ထွက်သက်ဝင်သက်"),
        pali=("anapanasati", "anapana", "assasa", "passasa", "assasapassasa", "sati"),
        must=("anapanasati", "anapana"),
        should=("assasa", "passasa", "assasapassasa"),
        phrases=("anapanassati", "anapanasati samadhi", "assasapassasa", "dighanam assasanto"),
    ),
    Concept(
        id="four_noble_truths",
        label="Tứ Diệu Đế, Bốn Thánh Đế",
        triggers=("tu dieu de", "tu thanh de", "bon su that", "bon thanh de",
                  "kho tap diet dao", "thanh de", "four noble truths", "noble truth",
                  "noble truths", "သစ္စာလေးပါး", "အရိယသစ္စာ"),
        pali=("ariyasacca", "sacca", "dukkha", "samudaya", "nirodha", "magga",
              "dukkhanirodhagamini", "patipada"),
        should=("ariyasacca", "sacca", "samudaya", "nirodha", "magga"),
        phrases=("cattari ariyasaccani", "dukkham ariyasaccam", "dukkhasamudayam",
                 "dukkhanirodham", "dukkhanirodhagamini patipada"),
    ),
    Concept(
        id="snake",
        label="con rắn, ẩn dụ bắt rắn",
        triggers=("ran", "con ran", "ran lon", "ran doc", "bat ran", "tim ran", "duoi ran", "snake", "serpent", "viper", "water snake", "simile of the snake", "မြွေ"),
        pali=("alagadda", "sappa", "uraga", "ahi", "asivisa"),
        # Phải có `must`, giống `loving_kindness`: để trống thì `should` không chặn được
        # gì, nhánh truy vấn bám vào từ chung và đo được là 0/3 lần tìm ra Alagaddūpama
        # dù `analyze_query` đã cấp đúng từ khoá. Bỏ "gaha" khỏi danh sách vì quá rộng
        # (vừa là "nắm giữ" vừa là "nhà"), đưa vào chỉ thêm nhiễu.
        must=("alagadda", "sappa", "uraga"),
        should=("ahi", "asivisa"),
        phrases=("alagaddupama", "alagaddatthiko", "alagaddagavesi", "uragavagga", "asivisopama"),
    ),
)


def _unique_normalized(values: list[str] | tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        normalized = normalize_pali(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            output.append(normalized)
    return output


def canonicalize_query(query: str) -> str:
    normalized = strip_vietnamese(query)
    normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()

    prefix_patterns = [
        r"^(toi\s+)?(muon|can)\s+tim\s+",
        r"^(hay\s+)?tim\s+(kiem\s+)?(cho\s+toi\s+)?",
        r"^(cho\s+toi\s+)?tim\s+(bai\s+kinh|doan\s+kinh|kinh|doan)\s+",
        r"^(bai\s+kinh|doan\s+kinh|kinh|doan)\s+",
        r"^(noi|noi\s+ve|ve)\s+",
        r"^(khai\s+niem|dinh\s+nghia|giai\s+thich)\s+(cua|ve)?\s*",
    ]
    changed = True
    while changed:
        changed = False
        for pattern in prefix_patterns:
            new_value = re.sub(pattern, "", normalized).strip()
            if new_value != normalized:
                normalized = new_value
                changed = True

    suffix_patterns = [
        r"\s+(la\s+gi|nghia\s+la\s+gi|la\s+sao|nhu\s+the\s+nao|the\s+nao)$",
        r"\s+(o\s+dau|trong\s+kinh\s+nao)$",
    ]
    for pattern in suffix_patterns:
        normalized = re.sub(pattern, "", normalized).strip()

    filler_words = {
        "cua",
        "ve",
        "la",
        "gi",
        "su",
        "cac",
        "nhung",
        "mot",
        "noi",
        "den",
        "trong",
    }
    tokens = [token for token in normalized.split() if token not in filler_words]
    return " ".join(tokens) or normalized or strip_vietnamese(query)


def analyze_query(query: str, corpus_types: list[str]) -> dict:
    clean_query = canonicalize_query(query)
    normalized_query = " ".join(dict.fromkeys([strip_vietnamese(query), clean_query]))

    def has_trigger(trigger: str) -> bool:
        normalized_trigger = strip_vietnamese(trigger)
        if " " in normalized_trigger or _MYANMAR.search(normalized_trigger):
            # Luat ranh gioi tu ben duoi gia dinh chu viet tach tu bang dau cach. Tieng
            # Myanmar viet dinh lien nhau, nen "ခိုးယူ" nam trong "ခိုးယူခြင်း" se bi luat
            # do chan lai - do duoc la truy van Myanmar ve trom cap tra ve 0 ket qua,
            # trong khi truy van ve quy y lai chay vi tinh co co dau cach theo sau.
            return normalized_trigger in normalized_query
        return re.search(rf"(^|\s){re.escape(normalized_trigger)}($|\s)", normalized_query) is not None

    matched = [
        concept
        for concept in CONCEPTS
        if any(has_trigger(trigger) for trigger in concept.triggers)
    ]

    pali: list[str] = []
    must: list[str] = []
    should: list[str] = []
    avoid: list[str] = []
    phrases: list[str] = []
    concepts: list[str] = []

    for concept in matched:
        concepts.append(concept.label)
        pali.extend(concept.pali)
        must.extend(concept.must)
        should.extend(concept.should or concept.pali)
        avoid.extend(concept.avoid)
        phrases.extend(concept.phrases)

    query_tokens = tokenize(query)
    clean_tokens = tokenize(clean_query)

    is_pali_like = pali_ratio(query) >= PALI_LIKE_RATIO
    segments = query_segments(query) if is_pali_like else []
    segment_terms = [terms for terms in (pali_content_tokens(segment) for segment in segments) if terms]
    segment_texts = [text for text in (normalize_pali(segment) for segment in segments) if text]

    return {
        "mainMeaning": query,
        "rawQuery": query,
        "queryIsPaliLike": is_pali_like,
        "queryTerms": pali_content_tokens(query) if is_pali_like else [],
        "querySegmentTerms": segment_terms,
        "querySegmentTexts": segment_texts,
        "cleanQuery": clean_query,
        "intent": "definition" if any(word in strip_vietnamese(query) for word in ["khai niem", "dinh nghia", "la gi", "giai thich"]) else "search",
        "vietnameseKeywords": list(dict.fromkeys([*clean_tokens, *query_tokens])),
        "relatedConcepts": concepts,
        "paliHints": _unique_normalized(pali),
        "mustHavePali": _unique_normalized(must),
        "shouldHavePali": _unique_normalized([*should, *pali]),
        "avoidPali": _unique_normalized(avoid),
        "expandedQueries": _unique_normalized([query, *phrases]),
        "preferredCorpus": corpus_types,
    }
