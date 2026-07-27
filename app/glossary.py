from dataclasses import dataclass
import re

from .normalize import normalize_pali, strip_vietnamese, tokenize


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
        triggers=("trom cap", "trom", "cap", "lay cua khong cho", "lay vat khong cho", "lay cua nguoi", "khong cho ma lay", "an cap", "dao tac"),
        pali=("adinnadana", "adinna", "adana", "theyyam", "theyya", "cora", "avahara", "pariggaha", "sikkhapada", "veramani"),
        must=("adinnadana", "adinna", "theyyam", "theyya"),
        should=("cora", "avahara", "pariggaha", "sikkhapada", "veramani"),
        phrases=("adinnadana veramani", "adinnadana sikkhapada", "adinnam adiyati", "theyyasankhatam"),
    ),
    Concept(
        id="killing",
        label="sát sinh, giết hại",
        triggers=("sat sinh", "giet hai", "giet", "hai chung sinh", "doat mang", "sat hai"),
        pali=("panatipata", "pana", "atipata", "vadha", "ghata", "himsa", "veramani", "sikkhapada"),
        must=("panatipata", "pana"),
        should=("atipata", "vadha", "ghata", "himsa", "veramani", "sikkhapada"),
        phrases=("panatipata veramani", "panam hanati", "panatipata sikkhapada"),
    ),
    Concept(
        id="false_speech",
        label="nói dối, vọng ngữ",
        triggers=("noi doi", "vong ngu", "noi sai su that", "noi la doi", "noi khong that"),
        pali=("musavada", "musa", "vada", "abhuta", "sampajanamusavada", "veramani", "sikkhapada"),
        must=("musavada", "musa"),
        should=("vada", "abhuta", "sampajanamusavada", "veramani", "sikkhapada"),
        phrases=("musavada veramani", "sampajanamusavada", "musavada sikkhapada"),
    ),
    Concept(
        id="sexual_misconduct",
        label="tà hạnh, không phạm hạnh",
        triggers=("ta hanh", "dam duc", "khong pham hanh", "hanh dam", "ngoai tinh", "bat tinh hanh"),
        pali=("kamesumicchacara", "abrahmacariya", "methuna", "kama", "micchacara", "veramani", "sikkhapada"),
        must=("kamesumicchacara", "abrahmacariya", "methuna"),
        should=("kama", "micchacara", "veramani", "sikkhapada"),
        phrases=("kamesumicchacara veramani", "abrahmacariya veramani", "methunadhamma"),
    ),
    Concept(
        id="intoxicants",
        label="rượu và chất say",
        triggers=("uong ruou", "ruou", "chat say", "say nghien", "men ruou", "ma tuy"),
        pali=("surameraya", "majja", "pamadatthana", "meraya", "sura", "veramani", "sikkhapada"),
        must=("surameraya", "majja"),
        should=("pamadatthana", "meraya", "sura", "veramani", "sikkhapada"),
        phrases=("suramerayamajjapamadatthana veramani", "surameraya majja", "majja pamadatthana"),
    ),
    Concept(
        id="donation_result",
        label="bố thí/cúng dường và quả báo/phước báu",
        triggers=("bo thi", "cung duong", "qua bao", "qua phuoc", "phuoc bau", "cong duc", "xả thí", "cho tang"),
        pali=("dana", "dakkhina", "deyya", "caga", "vipaka", "phala", "punna", "anisamsa", "dakkhineyya"),
        must=("dana", "dakkhina", "deyya", "caga"),
        should=("vipaka", "phala", "punna", "anisamsa", "dakkhineyya"),
        phrases=("dana phala", "dana vipaka", "dakkhina phala", "punna anisamsa"),
    ),
    Concept(
        id="refuge",
        label="quy y Tam Bảo, nơi nương tựa",
        triggers=("quy y", "tam bao", "nuong tua", "noi nuong tua", "noi quy huong", "khong le thuoc", "khong dua vao ai"),
        pali=("sarana", "saranagamana", "buddha", "dhamma", "sangha", "parayana", "aparappaccaya", "cittuppada"),
        must=("sarana", "saranagamana"),
        should=("buddha", "dhamma", "sangha", "parayana", "aparappaccaya", "cittuppada"),
        phrases=("saranagamanam", "esa me saranam", "esa me parayanam", "aparappaccayo cittuppado"),
    ),
    Concept(
        id="not_eating_after_noon",
        label="giới không ăn phi thời",
        triggers=("khong an phi thoi", "phi thoi", "an phi thoi", "qua ngo", "sau gio ngo", "sau bua trua", "an ban dem"),
        pali=("vikalabhojana", "vikala", "bhojana", "veramani", "sikkhapada", "uposatha", "pacittiya", "rattibhojana"),
        must=("vikalabhojana", "vikala"),
        should=("bhojana", "veramani", "sikkhapada", "uposatha", "pacittiya", "rattibhojana"),
        phrases=("vikalabhojana veramani", "vikale bhojanam", "vikalabhojana sikkhapada"),
    ),
    Concept(
        id="virtue",
        label="giới hạnh/trì giới",
        triggers=("gioi", "gioi hanh", "tri gioi", "phong ho", "hoc gioi", "pham hanh"),
        pali=("sila", "sikkhapada", "samvara", "patimokkha", "veramani", "vinaya", "brahmacariya"),
        should=("sila", "sikkhapada", "samvara", "patimokkha", "veramani", "vinaya"),
        phrases=("sila sikkhapada", "patimokkhasamvara", "veramani sikkhapada"),
    ),
    Concept(
        id="kamma_result",
        label="nghiệp và quả của nghiệp",
        triggers=("nghiep", "qua cua nghiep", "nhan qua", "nghiep bao", "di thuc"),
        pali=("kamma", "vipaka", "phala", "hetu", "paccaya", "akusala", "kusala"),
        must=("kamma",),
        should=("vipaka", "phala", "hetu", "paccaya", "kusala", "akusala"),
        phrases=("kammassa phalam", "kammassa vipako", "kusalakamma", "akusalakamma"),
    ),
    Concept(
        id="impermanence",
        label="vô thường",
        triggers=("vo thuong", "khong thuong", "bien hoai", "sinh diet"),
        pali=("anicca", "viparinama", "udaya", "vaya", "uppada", "nirodha"),
        should=("anicca", "viparinama", "udaya", "vaya", "uppada", "nirodha"),
        phrases=("aniccam", "uppadavayadhammino", "viparinamadhamma"),
    ),
    Concept(
        id="suffering",
        label="khổ",
        triggers=("kho", "kho dau", "dukkha", "bat toai nguyen"),
        pali=("dukkha", "dukkhata", "dukkhasamudaya", "dukkhanirodha"),
        should=("dukkha", "dukkhata"),
        phrases=("dukkham", "dukkhasamudaya", "dukkhanirodha"),
    ),
    Concept(
        id="non_self",
        label="vô ngã",
        triggers=("vo nga", "khong phai ta", "khong phai cua ta", "anatta"),
        pali=("anatta", "netam mama", "nesohamasmi", "na meso atta"),
        should=("anatta", "netam mama", "nesohamasmi", "na meso atta"),
        phrases=("anatta", "netam mama", "nesohamasmi", "na meso atta"),
    ),
    Concept(
        id="meditation",
        label="thiền định",
        triggers=("thien", "dinh", "tam dinh", "chanh niem", "niem xu"),
        pali=("jhana", "samadhi", "sati", "satipatthana", "anapanasati", "bhavana"),
        should=("jhana", "samadhi", "sati", "satipatthana", "anapanasati", "bhavana"),
        phrases=("jhana", "samadhi", "satipatthana", "anapanasati", "bhavana"),
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


def analyze_query(query: str, corpus_types: list[str]) -> dict:
    normalized_query = strip_vietnamese(query)

    def has_trigger(trigger: str) -> bool:
        normalized_trigger = strip_vietnamese(trigger)
        if " " in normalized_trigger:
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

    return {
        "mainMeaning": query,
        "vietnameseKeywords": query_tokens,
        "relatedConcepts": concepts,
        "paliHints": _unique_normalized(pali),
        "mustHavePali": _unique_normalized(must),
        "shouldHavePali": _unique_normalized([*should, *pali]),
        "avoidPali": _unique_normalized(avoid),
        "expandedQueries": _unique_normalized([query, *phrases]),
        "preferredCorpus": corpus_types,
    }
