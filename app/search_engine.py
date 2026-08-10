import copy
import math
import re
import time
import unicodedata
from collections import OrderedDict
from functools import lru_cache
from typing import Any

from psycopg.types.json import Jsonb

from .config import settings
from .db import execute, fetch_all
from .glossary import analyze_query
from .i18n import DEFAULT_LANGUAGE, normalize_language, t
from .normalize import normalize_pali
from .query_expander import expand_query_with_ai, merge_expansion, rerank_candidates_with_ai
from .translator import public_translation_error, translate_passage, translate_text


PITAKA_PREFIXES = {
    "vinaya": ["vin%"],
    "sutta": ["s%"],
    "abhidhamma": ["abh%"],
}

ALL_CORPUS_TYPES = ["mul", "att", "tik", "nrf"]
SEARCH_ALL = "all"


def resolve_corpus_types(value: str | list[str] | None) -> list[str]:
    """Chuẩn hoá lựa chọn phần dữ liệu, trong đó "all" nghĩa là tìm toàn bộ kinh tạng."""
    if value is None:
        return list(ALL_CORPUS_TYPES)
    values = [value] if isinstance(value, str) else list(value)
    if not values or SEARCH_ALL in values:
        return list(ALL_CORPUS_TYPES)
    valid = [item for item in values if item in ALL_CORPUS_TYPES]
    return valid or list(ALL_CORPUS_TYPES)


def resolve_pitaka_type(value: str | None) -> str | None:
    """"all" hoặc rỗng đều nghĩa là không lọc theo Tạng."""
    candidate = str(value or "").strip()
    if not candidate or candidate == SEARCH_ALL:
        return None
    return candidate

SNIPPET_MIN_CHARS = 900
SNIPPET_MAX_CHARS = 2600

PITAKA_SOURCE_ROOTS = {
    "vinaya": {"vinayapitaka", "vinayapitake"},
    "sutta": {"suttapitaka", "suttapitake", "suttantapitaka", "suttantapitake"},
    "abhidhamma": {"abhidhammapitaka", "abhidhammapitake"},
    "abhidhammapitaka": {"abhidhammapitaka", "abhidhammapitake"},
}

HEADING_SUFFIXES = (
    "vaggo",
    "vagga",
    "nikayo",
    "nikaya",
    "pitaka",
    "pali",
    "patho",
    "suttam",
    "sutta",
    "suttavannana",
    "vannana",
    "katha",
    "niddeso",
    "niddesa",
    "nipato",
    "bhago",
)


def _regex_for_terms(terms: list[str]) -> str:
    escaped = [re.escape(normalize_pali(term)) for term in terms if normalize_pali(term) and " " not in normalize_pali(term)]
    if not escaped:
        return r"$.^"
    return rf"(^|[[:space:]])({'|'.join(escaped)})[[:alpha:]]*($|[[:space:]])"


def _phrase_patterns(terms: list[str]) -> list[str]:
    phrases: list[str] = []
    for term in terms:
        normalized = normalize_pali(term)
        if " " in normalized:
            phrases.append(f"%{normalized}%")
    return sorted(set(phrases))


def _single_terms(terms: list[str]) -> list[str]:
    return sorted({normalize_pali(term) for term in terms if normalize_pali(term) and " " not in normalize_pali(term)})


def _term_like_patterns(terms: list[str]) -> list[str]:
    return sorted({f"%{term}%" for term in _single_terms(terms)})


# Pali ghép từ rất nhiều: cùng một khái niệm xuất hiện dưới nhiều hợp từ khác nhau
# (alagaddūpama / alagaddatthika / alagaddagavesī). Khớp theo nguyên từ sẽ trượt hết,
# nên với thuật ngữ dài ta cắt lấy phần gốc chung để bắt được cả họ hợp từ.
COMPOUND_STEM_MIN_LENGTH = 9
COMPOUND_STEM_LENGTH = 7


def _compound_stems(terms: list[str]) -> list[str]:
    stems: list[str] = []
    for term in _single_terms(terms):
        if len(term) < COMPOUND_STEM_MIN_LENGTH:
            continue
        stem = term[:COMPOUND_STEM_LENGTH]
        if stem not in stems and stem not in _single_terms(terms):
            stems.append(stem)
    return stems


def _tsquery_for_terms(terms: list[str]) -> str:
    parts = []
    for term in _single_terms(terms):
        safe = re.sub(r"[^a-z0-9]+", "", term)
        if safe:
            parts.append(f"{safe}:*")
    return " | ".join(parts)


# Một chuỗi `<->` dài chỉ khớp khi toàn bộ token nằm gọn trong đúng một dòng `passages`.
# Câu kệ lại được lưu mỗi dòng một row, nên đoạn dài phải cắt thành nhiều cửa sổ ngắn.
PHRASE_CHAIN_MAX_TOKENS = 8
PHRASE_WINDOW_SIZE = 6
PHRASE_WINDOW_STRIDE = 4
PHRASE_MAX_WINDOWS_PER_SEGMENT = 4
PHRASE_MAX_CHAINS = 16
QUOTE_MAX_TOKENS_PER_SEGMENT = 8
QUOTE_MAX_SEGMENTS = 6


def _adjacency_chain(tokens: list[str]) -> str:
    return "(" + " <-> ".join(f"{token}:*" for token in tokens) + ")"


def _chains_for_tokens(tokens: list[str]) -> list[str]:
    tokens = [token for token in tokens if len(token) >= 2]
    if len(tokens) < 2:
        return []
    if len(tokens) <= PHRASE_CHAIN_MAX_TOKENS:
        return [_adjacency_chain(tokens)]

    windows: list[str] = []
    for start in range(0, len(tokens) - 1, PHRASE_WINDOW_STRIDE):
        window = tokens[start : start + PHRASE_WINDOW_SIZE]
        if len(window) >= 3:
            windows.append(_adjacency_chain(window))
        if len(windows) >= PHRASE_MAX_WINDOWS_PER_SEGMENT:
            break
    return windows


def _tsquery_for_phrases(terms: list[str], segment_terms: list[list[str]] | None = None) -> str:
    chains: list[str] = []
    for term in terms:
        normalized = normalize_pali(term)
        if " " not in normalized:
            continue
        tokens = [re.sub(r"[^a-z0-9]+", "", token) for token in normalized.split()]
        chains.extend(_chains_for_tokens(tokens))

    for tokens in segment_terms or []:
        chains.extend(_chains_for_tokens(tokens))

    return " | ".join(list(dict.fromkeys(chains))[:PHRASE_MAX_CHAINS])


def _tsquery_for_quote(segment_terms: list[list[str]]) -> str:
    """AND các từ đặc trưng trong từng câu, rồi OR các câu lại với nhau.

    Bắt được đoạn trích dán nguyên văn kể cả khi thứ tự/khoảng cách từ bị lệch,
    và kể cả khi đoạn dán trải qua nhiều dòng `passages` khác nhau.
    """
    parts: list[str] = []
    for tokens in segment_terms[:QUOTE_MAX_SEGMENTS]:
        picked = sorted(dict.fromkeys(tokens), key=len, reverse=True)[:QUOTE_MAX_TOKENS_PER_SEGMENT]
        if len(picked) >= 2:
            parts.append("(" + " & ".join(f"{token}:*" for token in picked) + ")")
    return " | ".join(dict.fromkeys(parts))


def _has_stem(text: str, term: str) -> bool:
    normalized = normalize_pali(term)
    if not normalized:
        return False
    if " " in normalized:
        return normalized in text
    return re.search(rf"(^|\s){re.escape(normalized)}[a-z]*($|\s)", text) is not None


def _hit_score(text: str, terms: list[str]) -> float:
    normalized_terms = sorted({normalize_pali(term) for term in terms if normalize_pali(term)})
    if not normalized_terms:
        return 0.0
    hits = sum(1 for term in normalized_terms if _has_stem(text, term))
    return hits / len(normalized_terms)


def _proximity_score(text: str, terms: list[str]) -> float:
    normalized_terms = [normalize_pali(term) for term in terms if normalize_pali(term) and " " not in normalize_pali(term)]
    positions: list[int] = []
    words = text.split()
    for idx, word in enumerate(words):
        if any(word.startswith(term) for term in normalized_terms):
            positions.append(idx)
    if len(positions) < 2:
        return 0.0
    span = max(positions) - min(positions) + 1
    return 1 / math.log(span + 2)


def _content_words(text: str) -> set[str]:
    stopwords = {
        "atha", "bhante", "bhagava", "bhagavā", "bhikkhave", "hoti", "honti",
        "idha", "kho", "pana", "tasmā", "tattha", "tena", "vutta", "vuccati",
        "eva", "evam", "evaṃ", "iti", "santi", "ahosi",
    }
    return {word for word in normalize_pali(text).split() if len(word) >= 5 and word not in stopwords}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _source_family(source_path: str) -> str:
    parts = [part.strip().lower() for part in source_path.split("->") if part.strip()]
    return " -> ".join(parts[:4])


def _is_near_duplicate(candidate: dict, selected: dict) -> bool:
    lexical_similarity = _jaccard(candidate.get("_contentWords") or set(), selected.get("_contentWords") or set())
    same_source_family = _source_family(candidate.get("sourcePath", "")) == _source_family(selected.get("sourcePath", ""))
    return lexical_similarity >= 0.42 or (same_source_family and lexical_similarity >= 0.26)


ADJACENT_COLLAPSE_DISTANCE = 2


def _collapse_adjacent(candidates: list[dict]) -> list[dict]:
    """Gộp các đoạn nằm sát nhau trong cùng một mục thành một kết quả.

    Dán một câu kệ nhiều dòng sẽ khớp nhiều row liền kề của cùng bài kinh; giữ cả
    hai chỉ làm kết quả bị trùng vì phần mở rộng ngữ cảnh phía sau vốn đã ghép
    các dòng hàng xóm vào rồi.
    """
    best_by_section: dict[str, list[dict]] = {}
    dropped: set[str] = set()

    for candidate in candidates:
        section_id = candidate.get("sectionId")
        sort_order = candidate.get("_sortOrder")
        if not section_id or sort_order is None:
            continue
        kept = best_by_section.setdefault(section_id, [])
        neighbour = next(
            (
                item
                for item in kept
                if abs(int(item["_sortOrder"]) - int(sort_order)) <= ADJACENT_COLLAPSE_DISTANCE
            ),
            None,
        )
        if neighbour is None:
            kept.append(candidate)
        else:
            dropped.add(candidate["id"])

    return [item for item in candidates if item["id"] not in dropped]


def _diversify_results(candidates: list[dict]) -> list[dict]:
    selected: list[dict] = []
    delayed: list[dict] = []
    for candidate in candidates:
        candidate["_contentWords"] = _content_words(candidate.get("paliText", ""))
        if any(_is_near_duplicate(candidate, item) for item in selected):
            delayed.append(candidate)
        else:
            selected.append(candidate)
    return [*selected, *delayed]


def _source_path_from_value(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _source_path(hierarchy: dict[str, Any]) -> list[str]:
    source_path = hierarchy.get("sourcePath")
    return _source_path_from_value(source_path)


def _source_label(corpus_type: str) -> str:
    return {
        "mul": "Tipiṭaka Mūla",
        "att": "Aṭṭhakathā",
        "tik": "Ṭīkā",
        "nrf": "Añña",
    }.get(corpus_type, corpus_type)


def _pitaka_label(pitaka_type: str | None, corpus_type: str) -> str | None:
    if not pitaka_type:
        return None
    base = {
        "vinaya": "Vinayapiṭaka",
        "sutta": "Suttapiṭaka",
        "abhidhammapitaka": "Abhidhammapiṭaka",
        "abhidhamma": "Abhidhammapiṭaka",
    }.get(pitaka_type, pitaka_type)
    if corpus_type == "mul":
        return base
    return f"{base} ({_source_label(corpus_type)})"


def _source_key(label: str) -> str:
    decomposed = unicodedata.normalize("NFD", label.lower())
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    without_marks = without_marks.replace("ṃ", "m").replace("ṁ", "m")
    without_marks = re.sub(r"[^\w\s().-]", " ", without_marks, flags=re.UNICODE)
    return re.sub(r"\s+", " ", without_marks).strip()


def _looks_like_source_noise(label: str) -> bool:
    normalized = _source_key(label)
    words = normalized.split()
    compact = normalized.replace(" ", "")
    if not normalized:
        return True
    if "nitthita" in normalized or "samatt" in normalized:
        return True
    is_known_heading_shape = any(compact.endswith(suffix) for suffix in HEADING_SUFFIXES)
    if len(label) > 180:
        return True
    sentence_markers = ["ti ", " tattha ", " vutta ", " hoti ", " yatha ", " ettha ", " iti "]
    if len(label) > 90 and sum(1 for marker in sentence_markers if marker in f" {normalized} ") >= 2:
        return True
    if re.match(r"^\d+[\.-]\s+", normalized) and not is_known_heading_shape:
        if len(label) > 70 or label.count(".") >= 2 or any(marker in f" {normalized} " for marker in sentence_markers):
            return True
    if label.endswith(".") and not is_known_heading_shape:
        if len(words) > 1 or any(marker in f" {normalized} " for marker in sentence_markers):
            return True
    if len(words) > 18 and label.count(".") >= 1:
        return True
    return False


def _is_display_hidden_source_level(label: str) -> bool:
    normalized = _source_key(label)
    return re.fullmatch(r"\(?\s*(pathamo|dutiyo|tatiyo|catuttho|pancamo|chattho|sattamo|atthamo|navamo|dasamo)\s+bhago\s*\)?", normalized) is not None


def _looks_like_heading_title(label: str) -> bool:
    normalized = _source_key(label)
    if _looks_like_source_noise(label):
        return False
    words = normalized.split()
    if not words or len(words) > 10:
        return False
    compact = normalized.replace(" ", "")
    if any(compact.endswith(suffix) for suffix in HEADING_SUFFIXES):
        return True
    if re.match(r"^\(?\d+\)?\s+", normalized):
        return True
    return False


def _nearby_heading_title(row: dict) -> str | None:
    document_id = row.get("document_id")
    sort_order = row.get("sort_order")
    if not document_id or sort_order is None:
        return None

    rows = fetch_all(
        """
        select pali_text
        from passages
        where document_id = %s
          and sort_order < %s
          and sort_order >= greatest(0, %s - 140)
        order by sort_order desc
        limit 140
        """,
        [document_id, sort_order, sort_order],
    )
    for candidate in rows:
        title = str(candidate.get("pali_text") or "").strip()
        if _looks_like_heading_title(title):
            return title
    return None


def _clean_source_items(source: list[str], pitaka_type: str | None) -> list[str]:
    noisy = {
        "Namo tassa bhagavato arahato sammāsambuddhassa",
        "Nidānavaṇṇanā niṭṭhitā.",
        "Paṭhamavaggavaṇṇanā niṭṭhitā.",
        "Dutiyavaggavaṇṇanā niṭṭhitā.",
    }
    clean: list[str] = []

    for item in source:
        label = str(item or "").strip()
        if not label or label in noisy:
            continue
        normalized = normalize_pali(label)
        if "nitthita" in normalized or "samatt" in normalized:
            continue
        clean.append(label)

    return clean


def _display_source(row: dict, corpus_types: list[str], pitaka_type: str | None) -> str:
    section_source = _source_path_from_value(row.get("section_source_path"))
    passage_source = _source_path(row.get("hierarchy") or {})
    source = section_source or passage_source
    clean = _clean_source_items(source, pitaka_type)
    # Ưu tiên corpus thật của chính dòng này. Nếu lấy corpus_types[0] thì ở chế độ
    # "tìm tất cả" mọi kết quả đều bị gán nhãn theo lựa chọn đầu tiên (sai nguồn).
    corpus = str(row.get("corpus_type") or (corpus_types[0] if corpus_types else "mul"))
    prefix = [_source_label(corpus)]
    merged: list[str] = []
    for item in [*prefix, *clean]:
        if item:
            merged.append(item)
    return " -> ".join(merged)


def _source_has_noisy_item(row: dict) -> bool:
    del row
    return False


def _append_nearby_heading_to_source(item: dict) -> None:
    if not item.get("_needsNearbyHeading"):
        return
    nearby_heading = _nearby_heading_title(
        {
            "document_id": item.get("_documentId"),
            "sort_order": item.get("_sortOrder"),
        }
    )
    if not nearby_heading:
        return
    source_path = str(item.get("sourcePath") or "")
    parts = [part.strip() for part in source_path.split(" -> ") if part.strip()]
    if all(_source_key(nearby_heading) != _source_key(part) for part in parts):
        item["sourcePath"] = " -> ".join([*parts, nearby_heading])


def _pitaka_sql(pitaka_type: str | None) -> tuple[str, list[str]]:
    if not pitaka_type:
        return "", []
    return "and lower(d.file_name) like any(%s)", PITAKA_PREFIXES.get(pitaka_type, [])


def _document_ids(corpus_types: list[str], pitaka_type: str | None) -> list[str]:
    pitaka_sql, pitaka_params = _pitaka_sql(pitaka_type)
    rows = fetch_all(
        f"""
        select id
        from documents d
        where d.corpus_type = any(%s)
          {pitaka_sql}
        """,
        [corpus_types, *([pitaka_params] if pitaka_params else [])],
    )
    return [str(row["id"]) for row in rows]


def _params_with_pitaka(base: list[Any], pitaka_params: list[str], tail: list[Any]) -> list[Any]:
    if pitaka_params:
        return [*base, pitaka_params, *tail]
    return [*base, *tail]


def _quote_score(text: str, analysis: dict) -> float:
    """Mức phủ của chính chữ người dùng gõ, tính theo từng câu.

    Lấy câu khớp tốt nhất làm chính (đoạn dán nhiều dòng thì mỗi row chỉ chứa được
    một dòng), cộng thêm phần thưởng nhỏ cho row phủ được nhiều câu.
    """
    segments = analysis.get("querySegmentTerms") or []
    if not segments:
        terms = analysis.get("queryTerms") or []
        return _hit_score(text, terms) if terms else 0.0
    coverages = [_hit_score(text, terms) for terms in segments]
    return max(coverages) * 0.7 + (sum(coverages) / len(coverages)) * 0.3


def _score(
    row: dict,
    analysis: dict,
    base: float,
    semantic_weight: float = 0.0,
    quote_weight: float = 0.0,
) -> tuple[float, float, float]:
    text = row["normalized_pali"]
    hints = analysis["paliHints"]
    must = analysis["mustHavePali"]
    should = analysis["shouldHavePali"]
    avoid = analysis["avoidPali"]
    keyword = _hit_score(text, hints)
    must_score = _hit_score(text, must) if must else 1.0
    should_score = _hit_score(text, should)
    concept = min(1.0, must_score * 0.55 + should_score * 0.45)
    proximity = _proximity_score(text, [*must, *should])
    penalty = _hit_score(text, avoid) * 0.3
    semantic = float(row.get("semantic_score") or 0)
    quote = _quote_score(text, analysis) if quote_weight else 0.0
    db_hits = float(row.get("term_hits") or row.get("phrase_hits") or 0)
    db_hit_score = min(1.0, db_hits / max(1, len(set([*hints, *must, *should]))))
    score = (
        base
        + keyword * 0.22
        + concept * 0.38
        + proximity * 0.17
        + db_hit_score * 0.16
        + semantic * semantic_weight
        + quote * quote_weight
        - penalty
    )
    return max(0.0, score), keyword, concept


def _match_reason(score: float, keyword: float, concept: float, semantic: float, language: str = DEFAULT_LANGUAGE) -> str:
    if concept >= 0.75:
        return t(language, "match.concept")
    if keyword >= 0.35:
        return t(language, "match.keyword")
    if semantic > 0:
        return t(language, "match.semantic")
    return t(language, "match.threshold")


# Số từ tối thiểu để coi một câu là "trích dẫn nguyên văn" chứ không phải câu hỏi khái niệm.
# Để 4 thì những câu kệ ba từ quen thuộc nhất ("Sabbe saṅkhārā aniccā") không được coi là
# trích dẫn, cả nhánh dò nguyên văn lẫn điểm thưởng đều không chạy. Hạ xuống 3 an toàn vì
# `querySegmentTexts` chỉ có giá trị khi câu hỏi trông như tiếng Pali (`analyze_query` chặn
# bằng `pali_ratio`), nên câu hỏi tiếng Việt ba chữ không đụng tới đây; và cơ chế này tự
# giới hạn - không có đoạn nào chứa đúng chuỗi ký tự đó thì không ai được cộng điểm.
EXACT_QUOTE_MIN_TOKENS = 3
EXACT_QUOTE_MAX_SEGMENTS = 4
EXACT_QUOTE_ROW_LIMIT = 40


def _quote_segments(analysis: dict) -> list[str]:
    return [
        segment
        for segment in (analysis.get("querySegmentTexts") or [])
        if len(segment.split()) >= EXACT_QUOTE_MIN_TOKENS
    ]


def _exact_quote_rank(row: dict, analysis: dict) -> float:
    """Đếm số câu người dùng dán xuất hiện nguyên văn trong đoạn này.

    Dán nguyên văn là thao tác tra cứu đúng đoạn, không phải hỏi khái niệm, nên
    đoạn chứa nguyên văn phải đứng trên kết quả do AI rerank chấm điểm ngữ nghĩa.
    """
    segments = _quote_segments(analysis)
    if not segments:
        return 0.0
    text = row["normalized_pali"]
    matched = sum(1 for segment in segments if segment in text)
    # Cộng thêm cho câu đầu tiên vì đó là câu người dùng dẫn dắt truy vấn.
    leading_bonus = 0.5 if segments[0] in text else 0.0
    return matched + leading_bonus


def _exact_quote_density(row: dict, analysis: dict) -> float:
    """Câu trích chiếm bao nhiêu phần của đoạn: tách BÀI KỆ khỏi đoạn TRÍCH LẠI bài kệ.

    Một câu kệ nổi tiếng nằm nguyên văn trong 140 đoạn, riêng chánh tạng đã 60 đoạn, nên
    `EXACT_QUOTE_BONUS` lẫn ưu tiên chánh tạng đều cộng y hệt nhau cho tất cả và không
    phân định được gì; `_quote_score` thì bão hoà ở 1.0 ngay khi đoạn chứa đủ chữ, dài
    ngắn như nhau. Hết tín hiệu, điểm nền quyết định - mà điểm nền ưu ái đoạn DÀI vì
    nhiều term hits hơn, đúng ngược với thao tác tra nguyên văn: đoạn ngắn nhất chứa câu
    kệ CHÍNH LÀ câu kệ. Đo tỉ lệ độ dài tách được hai loại rất dứt khoát - bài kệ gốc
    trong Pháp Cú dài 44 ký tự đạt ~0.5, còn đoạn Nghĩa Tích 1.200 ký tự trích lại nó chỉ
    ~0.02.
    """
    text = row["normalized_pali"]
    if not text:
        return 0.0
    matched = sum(len(segment) for segment in _quote_segments(analysis) if segment in text)
    return min(1.0, matched / len(text))


def _paragraph_no(row: dict, fallback_rank: int) -> str:
    del fallback_rank
    return str(row.get("display_paragraph_no") or row.get("xml_paragraph_no") or row.get("paragraph_no") or "N/A")


def _candidate(row: dict, score: float, keyword: float, concept: float, corpus_types: list[str], pitaka_type: str | None, rank: int, analysis: dict, language: str = DEFAULT_LANGUAGE) -> dict:
    section_id = row.get("section_id")
    return {
        "id": str(row["id"]),
        "rank": rank,
        "score": round(score, 4),
        "sourcePath": _display_source(row, corpus_types, pitaka_type),
        "paragraphNo": _paragraph_no(row, rank),
        "paliText": row["pali_text"],
        "contextExpanded": False,
        "translation": {"vi": None, "fromCache": False},
        "matchReason": _match_reason(score, keyword, concept, float(row.get("semantic_score") or 0), language),
        "sectionId": str(section_id) if section_id else None,
        "sectionTitle": row.get("section_title"),
        "canOpenSection": bool(section_id),
        "_documentId": row.get("document_id"),
        "_needsNearbyHeading": _source_has_noisy_item(row),
        "_exactQuote": _exact_quote_rank(row, analysis),
        "_exactQuoteDensity": _exact_quote_density(row, analysis),
        "_corpusType": row.get("corpus_type"),
        "_textHash": row["text_hash"],
        "_keyword": keyword,
        "_concept": concept,
        "_sortOrder": row.get("sort_order"),
        "_originalPaliText": row["pali_text"],
    }


def _insert_log(query: str, corpus_types: list[str], pitaka_type: str | None, analysis: dict, result_ids: list[str]) -> None:
    execute(
        """
        insert into search_logs (query, filters, expanded_query, result_passage_ids)
        values (%s, %s::jsonb, %s::jsonb, %s::uuid[])
        """,
        [
            query,
            Jsonb({"corpusType": corpus_types, "pitakaType": pitaka_type}),
            Jsonb(analysis),
            result_ids,
        ],
    )


def _candidate_columns() -> str:
    return """
      p.id,
      p.document_id,
      p.section_id,
      p.sort_order,
      p.paragraph_no,
      p.xml_paragraph_no,
      p.display_paragraph_no,
      p.pali_text,
      p.normalized_pali,
      p.hierarchy,
      p.text_hash,
      d.file_name,
      d.corpus_type,
      s.title as section_title,
      s.source_path as section_source_path
    """


@lru_cache(maxsize=1)
def _has_embeddings() -> bool:
    """Bật vector search mà bảng chưa có embedding nào thì mỗi lượt tìm vẫn tốn
    một lần gọi embedding của Gemini rồi trả về rỗng. Kiểm tra một lần rồi nhớ luôn."""
    row = fetch_all("select 1 from passages where embedding is not null limit 1")
    return bool(row)


def _retrieve_candidates(query: str, corpus_types: list[str], pitaka_type: str | None, analysis: dict, limit: int) -> list[dict]:
    doc_ids = _document_ids(corpus_types, pitaka_type)
    if not doc_ids:
        return []
    candidates: list[dict] = []
    segment_terms = analysis.get("querySegmentTerms") or []
    query_terms = analysis.get("queryTerms") or []
    keyword_terms = sorted({*analysis["paliHints"], *analysis["mustHavePali"], *analysis["shouldHavePali"]})
    keyword_single_terms = _single_terms(keyword_terms)
    if not keyword_single_terms:
        # Không khớp khái niệm nào trong glossary và Gemini cũng không trả về thuật ngữ.
        # Trước đây nhánh từ khóa bị bỏ qua hoàn toàn nên truy vấn kiểu "Sona" trả 0 kết quả
        # dù trong DB có hàng trăm đoạn chứa từ đó.
        keyword_single_terms = _single_terms(query_terms)

    # Nhánh trích dẫn nguyên văn: dò thẳng chuỗi ký tự nhờ index trigram, lấy đoạn NGẮN
    # NHẤT trước. Không có nhánh này thì với truy vấn dán nguyên văn, các đoạn Chú giải
    # dài chiếm hết chỗ trong giới hạn ứng viên và chính bài kinh gốc bị loại từ đầu.
    for segment in (analysis.get("querySegmentTexts") or [])[:EXACT_QUOTE_MAX_SEGMENTS]:
        if len(segment.split()) < EXACT_QUOTE_MIN_TOKENS:
            continue
        rows = fetch_all(
            f"""
            select
              {_candidate_columns()},
              0::float as semantic_score,
              0 as term_hits
            from passages p
            join documents d on d.id = p.document_id
            left join sections s on s.id = p.section_id
            where p.document_id = any(%s::uuid[])
              and p.normalized_pali like %s
            order by length(p.normalized_pali) asc
            limit %s
            """,
            [doc_ids, f"%{segment}%", EXACT_QUOTE_ROW_LIMIT],
        )
        for row in rows:
            score, keyword, concept = _score(row, analysis, base=0.50, quote_weight=0.34)
            candidates.append({"row": row, "score": score, "keyword": keyword, "concept": concept})

    quote_tsquery = _tsquery_for_quote(segment_terms)
    if quote_tsquery:
        rows = fetch_all(
            f"""
            select
              {_candidate_columns()},
              0::float as semantic_score,
              ts_rank_cd(to_tsvector('simple', p.normalized_pali), to_tsquery('simple', %s)) as phrase_hits
            from passages p
            join documents d on d.id = p.document_id
            left join sections s on s.id = p.section_id
            where p.document_id = any(%s::uuid[])
              and to_tsvector('simple', p.normalized_pali) @@ to_tsquery('simple', %s)
            order by phrase_hits desc, p.sort_order asc
            limit %s
            """,
            [quote_tsquery, doc_ids, quote_tsquery, limit],
        )
        for row in rows:
            score, keyword, concept = _score(row, analysis, base=0.42, quote_weight=0.34)
            candidates.append({"row": row, "score": score, "keyword": keyword, "concept": concept})

    phrase_tsquery = _tsquery_for_phrases(
        [*analysis["expandedQueries"], *analysis["mustHavePali"], *analysis["shouldHavePali"]],
        segment_terms,
    )
    if phrase_tsquery:
        rows = fetch_all(
            f"""
            select
              {_candidate_columns()},
              0::float as semantic_score,
              ts_rank_cd(to_tsvector('simple', p.normalized_pali), to_tsquery('simple', %s)) as phrase_hits
            from passages p
            join documents d on d.id = p.document_id
            left join sections s on s.id = p.section_id
            where p.document_id = any(%s::uuid[])
              and to_tsvector('simple', p.normalized_pali) @@ to_tsquery('simple', %s)
            order by phrase_hits desc, p.sort_order asc
            limit %s
            """,
            [phrase_tsquery, doc_ids, phrase_tsquery, limit],
        )
        for row in rows:
            score, keyword, concept = _score(row, analysis, base=0.52, quote_weight=0.18)
            candidates.append({"row": row, "score": score, "keyword": keyword, "concept": concept})

    # Cổng lọc vẫn neo theo mustHavePali nhưng nới thêm gốc hợp từ, nếu không thì
    # thuật ngữ AI đưa ra ("alagaddupama") sẽ chặn mất chính đoạn kinh ("alagaddatthiko").
    gate_terms = _single_terms([*analysis["mustHavePali"], *(analysis.get("paliStems") or [])]) or keyword_single_terms
    if keyword_single_terms:
        term_sql = f"""
            select
              {_candidate_columns()},
              0::float as semantic_score,
              (
                select count(*)
                from unnest(%s::text[]) term
                where p.normalized_pali ~* ('(^|[[:space:]])' || term || '[[:alpha:]]*($|[[:space:]])')
              ) as term_hits
            from passages p
            join documents d on d.id = p.document_id
            left join sections s on s.id = p.section_id
            where p.document_id = any(%s::uuid[])
              and to_tsvector('simple', p.normalized_pali) @@ to_tsquery('simple', %s)
            order by term_hits desc, p.sort_order asc
            limit %s
        """
        rows: list[dict] = []
        # Gemini đôi khi trả về mustHavePali là một từ ghép không có trong DB.
        # Khi đó cổng lọc theo must sẽ chặn sạch, nên phải nới ra tập từ khóa rộng hơn.
        for gate in dict.fromkeys([tuple(gate_terms), tuple(keyword_single_terms)]):
            gate_tsquery = _tsquery_for_terms(list(gate))
            if not gate_tsquery:
                continue
            rows = fetch_all(term_sql, [keyword_single_terms, doc_ids, gate_tsquery, limit * 2])
            if rows:
                break

        for row in rows:
            score, keyword, concept = _score(row, analysis, base=0.18, quote_weight=0.18)
            candidates.append({"row": row, "score": score, "keyword": keyword, "concept": concept})

    if settings()["search_enable_vector"] and _has_embeddings():
        from .translator import embed_query_vector

        vector = embed_query_vector(" ".join([query, *analysis["expandedQueries"], *keyword_terms]))
        if vector:
            rows = fetch_all(
                f"""
                select
                  {_candidate_columns()},
                  1 - (p.embedding <=> %s::vector) as semantic_score,
                  0 as term_hits
                from passages p
                join documents d on d.id = p.document_id
                left join sections s on s.id = p.section_id
                where p.embedding is not null
                  and p.document_id = any(%s::uuid[])
                order by p.embedding <=> %s::vector
                limit %s
                """,
                [vector, doc_ids, vector, limit],
            )
            for row in rows:
                score, keyword, concept = _score(row, analysis, base=0.05, semantic_weight=0.45)
                candidates.append({"row": row, "score": score, "keyword": keyword, "concept": concept})

    return candidates


def _expand_short_snippets(results: list[dict]) -> None:
    for item in results:
        if len(item.get("paliText") or "") >= SNIPPET_MIN_CHARS:
            continue
        section_id = item.get("sectionId")
        sort_order = item.get("_sortOrder")
        if not section_id or sort_order is None:
            continue

        rows = fetch_all(
            """
            select id, sort_order, coalesce(display_paragraph_no, xml_paragraph_no, paragraph_no) as paragraph_no, pali_text
            from passages
            where section_id = %s
              and sort_order between %s and %s
            order by sort_order asc
            """,
            [section_id, max(0, int(sort_order) - 1), int(sort_order) + 3],
        )
        if not rows:
            continue

        parts: list[str] = []
        snippet_paragraphs: list[dict] = []
        total = 0
        for row in rows:
            text = str(row["pali_text"])
            if total + len(text) > SNIPPET_MAX_CHARS and parts:
                break
            label = row.get("paragraph_no")
            parts.append(text)
            snippet_paragraphs.append({"id": str(row["id"]), "paragraphNo": label})
            total += len(text)
            if total >= SNIPPET_MIN_CHARS:
                break

        if len(parts) > 1:
            item["paliText"] = "\n\n".join(parts)
            item["snippetParagraphs"] = snippet_paragraphs
            item["contextExpanded"] = True


def _attach_translations(results: list[dict], language: str = DEFAULT_LANGUAGE) -> None:
    for item in results:
        try:
            if item.get("paliText") != item.get("_originalPaliText"):
                item["translation"] = translate_text(item["paliText"], language)
            else:
                item["translation"] = translate_passage(item["id"], language)
        except Exception:
            item["translation"] = {
                "vi": None,
                "fromCache": False,
                "error": public_translation_error(),
            }

# Bấm "Hiển thị thêm" phải cắt tiếp DANH SÁCH CŨ chứ không được xếp hạng lại: AI rerank
# không tất định, chạy lại là ra thứ tự khác. Nhớ lại danh sách đã xếp hạng theo
# (câu hỏi, bộ lọc, ngôn ngữ) trong ít phút để mọi trang cắt từ cùng một danh sách.
_RANKED_TTL_SECONDS = 600
_RANKED_CACHE_MAX = 32
_RANKED_CACHE: "OrderedDict[tuple, tuple[float, tuple[dict, list[dict]]]]" = OrderedDict()


def _ranked_cache_key(query: str, corpus_types: list[str], pitaka_type: str | None, language: str) -> tuple:
    return (" ".join(query.split()).lower(), tuple(corpus_types), pitaka_type, language)


def _ranked_cache_get(key: tuple) -> tuple[dict, list[dict]] | None:
    entry = _RANKED_CACHE.get(key)
    if not entry:
        return None
    stamp, payload = entry
    if time.monotonic() - stamp > _RANKED_TTL_SECONDS:
        _RANKED_CACHE.pop(key, None)
        return None
    _RANKED_CACHE.move_to_end(key)
    return payload


def _ranked_cache_put(key: tuple, payload: tuple[dict, list[dict]]) -> None:
    _RANKED_CACHE[key] = (time.monotonic(), payload)
    _RANKED_CACHE.move_to_end(key)
    while len(_RANKED_CACHE) > _RANKED_CACHE_MAX:
        _RANKED_CACHE.popitem(last=False)


def _page_results(candidate_results: list[dict], page: int, page_size: int) -> list[dict]:
    start = (page - 1) * page_size
    # Bản sao sâu: danh sách xếp hạng còn phải phục vụ các trang sau, mà mọi bước phía
    # dưới (nối ngữ cảnh, gắn bản dịch, xoá trường nội bộ) đều sửa thẳng vào từng item.
    results = copy.deepcopy(candidate_results[start : start + page_size])
    for idx, item in enumerate(results):
        item["rank"] = start + idx + 1
    return results


# Đủ lớn để thắng cả khoảng cách điểm do AI rerank tạo ra (rerank trộn 70% relevance).
EXACT_QUOTE_BONUS = 0.45

# Một câu kệ nổi tiếng được hàng chục bộ Chú giải trích lại nguyên văn. Ở chế độ tìm
# tất cả, chúng lấn át chính bài kinh gốc (Theragāthā từng tụt xuống hạng 11 vì bị 23
# đoạn Chú giải chen lên). Dán nguyên văn là thao tác tra cứu đúng đoạn, nên chánh tạng
# phải đứng trước. Chỉ áp dụng cho đoạn khớp nguyên văn, không đụng tới truy vấn khái niệm.
EXACT_QUOTE_CORPUS_BONUS = {"mul": 0.80, "att": 0.20, "tik": 0.10, "nrf": 0.0}

# Ưu tiên đoạn mà câu trích chiếm phần lớn, xem `_exact_quote_density`. Phải đủ lớn để
# thắng khoảng dao động của điểm nền: đo trên "Sabbe saṅkhārā aniccā", 25 kết quả đầu
# chen nhau trong 0.29 điểm, nên 1.20 cho chênh lệch ~0.55 giữa bài kệ gốc và đoạn trích
# lại nó - đủ dứt khoát mà không nuốt mất các tín hiệu khác. Không nhân theo số câu khớp:
# đây là tỉ lệ, dán nhiều dòng thì phần khớp cũng lớn lên theo.
EXACT_QUOTE_DENSITY_BONUS = 1.20


# Ở chế độ tìm tất cả, Chú giải hay lấn lướt vì chúng định nghĩa thuật ngữ tường minh
# nên AI rerank chấm điểm cao. Nhưng "tìm tất cả" nghĩa là người dùng KHÔNG chọn, mà
# mặc định hợp lý khi không chọn là bản văn gốc; ai cần Chú giải thì đã có lựa chọn riêng.
# Mức chênh phải lớn hơn biên độ dao động của AI rerank (đo được ~0.15-0.2), nếu không
# thì cùng một câu hỏi lúc ra Chánh tạng lúc ra Chú giải. Chú giải vẫn vượt lên được khi
# thật sự sát ý hơn hẳn. Tìm trong một bộ đơn lẻ thì đây là hằng số, không đổi thứ hạng.
CORPUS_PREFERENCE_BONUS = {"mul": 0.28, "att": 0.06, "tik": 0.03, "nrf": 0.0}


# Bài kinh MANG TÊN khái niệm đang hỏi thì gần như chắc chắn là bài người ta muốn tìm.
# Hỏi "bốn niệm xứ" mà Mahāsatipaṭṭhānasuttaṃ tụt hạng 186 vì các đoạn ĐỊNH NGHĨA thuật
# ngữ (Vibhaṅga, Peṭakopadesa, Niddesa) nhắc từ khoá dày đặc hơn - mỗi đoạn của chính bài
# kinh chỉ nhắc cụm từ một lần rồi giảng dài. Điểm nền đo mật độ từ khoá nên xếp ngược.
# Mức 0.70 lấy từ khoảng cách đo được: bài kinh đúng 0.58, hạng 5 lúc đó 1.22.
CONCEPT_TITLE_BONUS = 0.70
# Cộng thêm khi tên CHÍNH BÀI KINH mang thuật ngữ, không phải chỉ tên chương chứa nó.
# Phải đủ lớn để thắng dao động của AI rerank: ở mức 1.10 thì Đại Niệm Xứ hơn nhóm sau
# đúng 0.09 điểm, chạy lúc được lúc mất. Mức này cho biên ~0.5, ổn định qua nhiều lượt.
CONCEPT_TITLE_BONUS_SUTTA = 1.50
# Thuật ngữ ngắn ("sati", "jhana") nằm lọt trong quá nhiều tên mục nên không tính.
CONCEPT_TITLE_MIN_TERM = 6
_SUTTA_NAME_TAILS = ("suttam", "sutta", "suttantam", "suttanta")


def _names_the_concept(item: dict, analysis: dict) -> float:
    """Tên thuật ngữ đang hỏi nằm ở đâu trong đường dẫn nguồn của đoạn này.

    Xét cả đường dẫn chứ không riêng tên mục: thân bài Mahāsatipaṭṭhāna nằm trong các mục
    con ("Kāyānupassanā ānāpānapabbaṃ"), tên bài kinh chỉ xuất hiện ở nhánh cha.

    Tách hai mức, vì gộp chung thì hỏng: cả trăm bài trong Satipaṭṭhānasaṃyutta đều có
    "satipaṭṭhāna" ở nhánh chương, cộng đều nhau thì chính bài Đại Niệm Xứ vẫn không nổi
    lên. Bài kinh mang tên khái niệm NGAY TRONG TÊN CỦA NÓ mới là bài người ta muốn tìm.
    """
    path = str(item.get("sourcePath") or "")
    if not path:
        return 0.0
    terms = [
        term.replace(" ", "")
        for term in {*(analysis.get("mustHavePali") or []), *(analysis.get("shouldHavePali") or [])}
        if len(term) >= CONCEPT_TITLE_MIN_TERM
    ]
    if not terms:
        return 0.0

    best = 0.0
    for part in path.split("->"):
        piece = normalize_pali(part).replace(" ", "")
        if not any(term in piece for term in terms):
            continue
        best = max(best, CONCEPT_TITLE_BONUS_SUTTA if piece.endswith(_SUTTA_NAME_TAILS) else CONCEPT_TITLE_BONUS)
    return best


def _apply_corpus_and_quote_bonus(
    candidates: list[dict], language: str = DEFAULT_LANGUAGE, analysis: dict | None = None
) -> None:
    """Cộng ưu tiên nguồn văn bản, và ưu tiên mạnh cho đoạn khớp nguyên văn.

    Cộng thẳng vào score thay vì sắp xếp riêng, để thứ hạng và điểm hiển thị khớp nhau.
    """
    for item in candidates:
        corpus = str(item.get("_corpusType") or "")
        bonus = CORPUS_PREFERENCE_BONUS.get(corpus, 0.0)

        if analysis is not None:
            bonus += _names_the_concept(item, analysis)

        exact = float(item.get("_exactQuote") or 0.0)
        if exact > 0:
            bonus += (EXACT_QUOTE_BONUS + EXACT_QUOTE_CORPUS_BONUS.get(corpus, 0.0)) * exact
            bonus += EXACT_QUOTE_DENSITY_BONUS * float(item.get("_exactQuoteDensity") or 0.0)
            item["matchReason"] = t(language, "match.exactQuote")

        if bonus:
            item["score"] = round(item["score"] + bonus, 4)


def _strip_internal_fields(results: list[dict]) -> None:
    for item in results:
        item.pop("_documentId", None)
        item.pop("_needsNearbyHeading", None)
        item.pop("_exactQuote", None)
        item.pop("_exactQuoteDensity", None)
        item.pop("_corpusType", None)
        item.pop("_textHash", None)
        item.pop("_keyword", None)
        item.pop("_concept", None)
        item.pop("_contentWords", None)
        item.pop("_sortOrder", None)
        item.pop("_originalPaliText", None)


# Chú giải trích lại nguyên văn Chánh tạng, nên cùng một đoạn văn xuất hiện y hệt ở
# nhiều bộ và có chung text_hash. Khi khử trùng lặp mà chỉ so điểm thì bản Chú giải
# thường thắng, và phần "Trích nguồn" chỉ về Chú giải thay vì về chính bài kinh gốc.
CORPUS_PROVENANCE_RANK = {"mul": 3, "att": 2, "tik": 1, "nrf": 0}


def _prefer_duplicate(candidate: dict, current: dict) -> bool:
    """Trong các bản sao giống hệt nhau, giữ bản thuộc bộ gốc nhất; hoà thì xét điểm."""
    new_rank = CORPUS_PROVENANCE_RANK.get(str(candidate["row"].get("corpus_type") or ""), 0)
    old_rank = CORPUS_PROVENANCE_RANK.get(str(current["row"].get("corpus_type") or ""), 0)
    if new_rank != old_rank:
        return new_rank > old_rank
    return candidate["score"] > current["score"]


def _candidate_limit(page_size: int, analysis: dict) -> int:
    """Cỡ rổ ứng viên, KHÔNG phụ thuộc số trang.

    Trước đây rổ phình theo `page`, nên trang 2 lấy 800 ứng viên trong khi trang 1 lấy
    600. Rổ khác nhau thì khử trùng lặp, gộp đoạn liền kề và giãn kết quả cho ra thứ
    tự khác - trang 2 thôi không còn là phần tiếp của trang 1, sinh ra đoạn hiện hai
    lần và đoạn không bao giờ hiện. Đo thực tế: ở cỡ rổ này danh sách xếp hạng đã có
    234-388 dòng, tức 46-77 trang, nên không có lý do gì phải phình thêm.
    """
    strong_terms = [
        *(analysis.get("mustHavePali") or []),
        *(analysis.get("paliExactTerms") or []),
    ]
    has_strong_pali_signal = bool(strong_terms)
    per_page_factor = 30 if has_strong_pali_signal else 80
    base_limit = 150 if has_strong_pali_signal else 600
    return max(base_limit, page_size * per_page_factor)


def _rerank_limit(ranked: list[dict], analysis: dict) -> int:
    configured = min(50, max(5, int(settings()["search_rerank_limit"])))
    if not ranked:
        return configured

    strong_terms = [
        *(analysis.get("mustHavePali") or []),
        *(analysis.get("paliExactTerms") or []),
    ]
    if strong_terms and ranked[0].get("score", 0) >= 0.78:
        return min(configured, 30)
    return configured


def _run_query_shortening_fallback(
    query: str,
    corpus_types: list[str],
    pitaka_type: str | None,
    page_size: int,
    include_translations: bool,
    language: str,
) -> dict | None:
    from .fallback_search import run_fallback

    def search_step(step_query: str, step_corpus: list[str], step_pitaka: str | None, step_page: int, step_size: int) -> dict:
        return search_passages(
            step_query,
            step_corpus,
            step_pitaka,
            step_page,
            step_size,
            include_translations=include_translations,
            allow_fallback=False,
            log_search=False,
            language=language,
        )

    return run_fallback(query, corpus_types, pitaka_type, page_size, search_step)


def search_passages(
    query: str,
    corpus_types: list[str],
    pitaka_type: str | None,
    page: int = 1,
    page_size: int = 5,
    include_translations: bool = True,
    allow_fallback: bool = True,
    log_search: bool = True,
    language: str = DEFAULT_LANGUAGE,
) -> dict:
    language = normalize_language(language)
    corpus_types = resolve_corpus_types(corpus_types)
    pitaka_type = resolve_pitaka_type(pitaka_type)

    cache_key = _ranked_cache_key(query, corpus_types, pitaka_type, language)
    cached = _ranked_cache_get(cache_key) if page > 1 else None
    if cached is not None:
        analysis, candidate_results = cached
    else:
        analysis, candidate_results = _rank_candidates(query, corpus_types, pitaka_type, page_size, language)
        _ranked_cache_put(cache_key, (analysis, candidate_results))

    results = _page_results(candidate_results, page, page_size)
    _expand_short_snippets(results)
    if include_translations:
        _attach_translations(results, language)
    else:
        for item in results:
            item["translation"] = {"vi": None, "fromCache": False, "pending": True}
    _strip_internal_fields(results)

    if not results and allow_fallback and page == 1:
        fallback = _run_query_shortening_fallback(query, corpus_types, pitaka_type, page_size, include_translations, language)
        if fallback:
            fallback_result = fallback["result"]
            _insert_log(query, corpus_types, pitaka_type, analysis, [item["id"] for item in fallback_result["results"]])
            fallback_result["query"] = query
            fallback_result["fallback"] = {
                "used": True,
                "usedQuery": fallback["usedQuery"],
                "triedQueries": fallback["triedQueries"],
                "ladder": fallback["ladder"],
            }
            return fallback_result

    if log_search:
        _insert_log(query, corpus_types, pitaka_type, analysis, [item["id"] for item in results])

    start = (page - 1) * page_size
    return {
        "query": query,
        "analysis": analysis,
        "results": results,
        "fallback": {"used": False},
        "pagination": {
            "page": page,
            "pageSize": page_size,
            "hasMore": len(candidate_results) > start + page_size,
        },
        "warning": t(language, "translation.aiWarning"),
    }


def _rank_candidates(
    query: str,
    corpus_types: list[str],
    pitaka_type: str | None,
    page_size: int,
    language: str,
) -> tuple[dict, list[dict]]:
    """Chạy trọn pipeline một lần, trả về phân tích truy vấn và TOÀN BỘ danh sách đã xếp hạng.

    Tách riêng khỏi `search_passages` để các trang sau cắt lát từ đúng danh sách này
    thay vì xếp hạng lại - xem `_RANKED_CACHE`.
    """
    local_analysis = analyze_query(query, corpus_types)
    clean_query = str(local_analysis.get("cleanQuery") or query)
    # Truyen ngon ngu xuong: prompt mo rong truy van co ban rieng cho vi/en/my, khong con
    # noi cung la "cau hoi tieng Viet" nhu truoc.
    analysis = merge_expansion(local_analysis, expand_query_with_ai(query, clean_query, language))
    # Gốc hợp từ vào luôn shouldHavePali để vừa dùng cho truy vấn vừa dùng cho chấm điểm.
    analysis["paliStems"] = _compound_stems([*(analysis.get("mustHavePali") or []), *(analysis.get("paliExactTerms") or [])])
    if analysis["paliStems"]:
        analysis["shouldHavePali"] = [*(analysis.get("shouldHavePali") or []), *analysis["paliStems"]]
    retrieval_query = str(analysis.get("cleanQuery") or clean_query or query)
    limit = _candidate_limit(page_size, analysis)
    candidates = _retrieve_candidates(retrieval_query, corpus_types, pitaka_type, analysis, limit)

    min_score = float(settings()["search_min_score"])
    by_hash: dict[str, dict] = {}
    by_hash_all: dict[str, dict] = {}
    for item in candidates:
        text_hash = item["row"]["text_hash"]
        if text_hash not in by_hash_all or _prefer_duplicate(item, by_hash_all[text_hash]):
            by_hash_all[text_hash] = item
        if item["score"] >= min_score:
            if text_hash not in by_hash or _prefer_duplicate(item, by_hash[text_hash]):
                by_hash[text_hash] = item

    ranked = sorted((by_hash or by_hash_all).values(), key=lambda item: item["score"], reverse=True)
    rerank_limit = _rerank_limit(ranked, analysis)
    rerank_window = ranked[:rerank_limit]
    rerank_candidates = [
        _candidate(item["row"], item["score"], item["keyword"], item["concept"], corpus_types, pitaka_type, idx + 1, analysis, language)
        for idx, item in enumerate(rerank_window)
    ]

    reranked = rerank_candidates_with_ai(query, analysis, rerank_candidates, language)
    if reranked:
        by_id = {item["id"]: item for item in rerank_candidates}
        used: set[str] = set()
        reranked_items: list[dict] = []
        for item in reranked:
            candidate = by_id.get(str(item.get("id")))
            if not candidate:
                continue
            used.add(candidate["id"])
            relevance = float(item.get("relevance") or 0)
            adjusted = dict(candidate)
            adjusted["score"] = round(adjusted["score"] * 0.3 + relevance * 0.7, 4)
            adjusted["matchReason"] = item.get("reason") or t(language, "match.aiRerank")
            reranked_items.append(adjusted)

        remaining_candidates = [item for item in rerank_candidates if item["id"] not in used]
        tail = [
            _candidate(item["row"], item["score"], item["keyword"], item["concept"], corpus_types, pitaka_type, rerank_limit + idx + 1, analysis, language)
            for idx, item in enumerate(ranked[rerank_limit:])
        ]
        candidate_results = [*reranked_items, *remaining_candidates, *tail]
    else:
        candidate_results = [
            _candidate(item["row"], item["score"], item["keyword"], item["concept"], corpus_types, pitaka_type, idx + 1, analysis, language)
            for idx, item in enumerate(ranked)
        ]

    _apply_corpus_and_quote_bonus(candidate_results, language, analysis)
    candidate_results.sort(key=lambda item: item["score"], reverse=True)
    return analysis, _diversify_results(_collapse_adjacent(candidate_results))
