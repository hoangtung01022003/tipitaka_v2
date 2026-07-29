import math
import re
import unicodedata
from typing import Any

from psycopg.types.json import Jsonb

from .config import settings
from .db import execute, fetch_all
from .glossary import analyze_query
from .normalize import normalize_pali
from .query_expander import expand_query_with_ai, merge_expansion, rerank_candidates_with_ai
from .translator import public_translation_error, translate_passage, translate_text


PITAKA_PREFIXES = {
    "vinaya": ["vin%"],
    "sutta": ["s%"],
    "abhidhamma": ["abh%"],
}

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
    pitaka_roots = PITAKA_SOURCE_ROOTS.get(pitaka_type or "", set())
    clean: list[str] = []
    skipped_pitaka_root = False

    for item in source:
        label = str(item or "").strip()
        if not label or label in noisy:
            continue
        if _is_display_hidden_source_level(label):
            continue
        normalized = normalize_pali(label)
        if _looks_like_source_noise(label):
            continue
        if pitaka_roots and not skipped_pitaka_root and normalized in pitaka_roots:
            skipped_pitaka_root = True
            continue
        if clean and normalize_pali(clean[-1]) == normalized:
            continue
        clean.append(label)

    return clean


def _display_source(row: dict, corpus_types: list[str], pitaka_type: str | None) -> str:
    section_source = _source_path_from_value(row.get("section_source_path"))
    passage_source = _source_path(row.get("hierarchy") or {})
    source = section_source or passage_source
    clean = _clean_source_items(source, pitaka_type)
    corpus = corpus_types[0] if corpus_types else "mul"
    prefix = [_source_label(corpus)]
    pitaka = _pitaka_label(pitaka_type, corpus)
    if pitaka:
        prefix.append(pitaka)
    merged: list[str] = []
    for item in [*prefix, *clean]:
        if item and item not in merged:
            merged.append(item)
    return " -> ".join(merged)


def _source_has_noisy_item(row: dict) -> bool:
    section_source = _source_path_from_value(row.get("section_source_path"))
    passage_source = _source_path(row.get("hierarchy") or {})
    source = section_source or passage_source
    return any(_looks_like_source_noise(str(item or "")) for item in source if item)


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


def _params_with_pitaka(base: list[Any], pitaka_params: list[str], tail: list[Any]) -> list[Any]:
    if pitaka_params:
        return [*base, pitaka_params, *tail]
    return [*base, *tail]


def _score(row: dict, analysis: dict, base: float, semantic_weight: float = 0.0) -> tuple[float, float, float]:
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
    db_hits = float(row.get("term_hits") or row.get("phrase_hits") or 0)
    db_hit_score = min(1.0, db_hits / max(1, len(set([*hints, *must, *should]))))
    score = base + keyword * 0.22 + concept * 0.38 + proximity * 0.17 + db_hit_score * 0.16 + semantic * semantic_weight - penalty
    return max(0.0, score), keyword, concept


def _match_reason(score: float, keyword: float, concept: float, semantic: float) -> str:
    if concept >= 0.75:
        return "Khớp mạnh với nhóm thuật ngữ Pali trọng tâm và điều kiện ý nghĩa."
    if keyword >= 0.35:
        return "Có nhiều thuật ngữ Pali liên quan trực tiếp, đã được xếp hạng lại theo ngữ cảnh."
    if semantic > 0:
        return "Có độ gần nghĩa vector và vượt ngưỡng lọc nhiễu."
    return "Vượt ngưỡng lọc nhiễu theo điểm lexical/proximity."


def _paragraph_no(row: dict, fallback_rank: int) -> str:
    del fallback_rank
    return str(row.get("display_paragraph_no") or row.get("xml_paragraph_no") or row.get("paragraph_no") or "N/A")


def _candidate(row: dict, score: float, keyword: float, concept: float, corpus_types: list[str], pitaka_type: str | None, rank: int) -> dict:
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
        "matchReason": _match_reason(score, keyword, concept, float(row.get("semantic_score") or 0)),
        "sectionId": str(section_id) if section_id else None,
        "sectionTitle": row.get("section_title"),
        "canOpenSection": bool(section_id),
        "_documentId": row.get("document_id"),
        "_needsNearbyHeading": _source_has_noisy_item(row),
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
      s.title as section_title,
      s.source_path as section_source_path
    """


def _retrieve_candidates(query: str, corpus_types: list[str], pitaka_type: str | None, analysis: dict, limit: int) -> list[dict]:
    pitaka_sql, pitaka_params = _pitaka_sql(pitaka_type)
    candidates: list[dict] = []

    phrase_patterns = _phrase_patterns([*analysis["expandedQueries"], *analysis["mustHavePali"], *analysis["shouldHavePali"]])
    if phrase_patterns:
        rows = fetch_all(
            f"""
            select
              {_candidate_columns()},
              0::float as semantic_score,
              (
                select count(*)
                from unnest(%s::text[]) pattern
                where p.normalized_pali like pattern
              ) as phrase_hits
            from passages p
            join documents d on d.id = p.document_id
            left join sections s on s.id = p.section_id
            where d.corpus_type = any(%s)
              and p.normalized_pali like any(%s)
              {pitaka_sql}
            order by phrase_hits desc, length(p.normalized_pali) asc, p.sort_order asc
            limit %s
            """,
            _params_with_pitaka([phrase_patterns, corpus_types, phrase_patterns], pitaka_params, [limit]),
        )
        for row in rows:
            score, keyword, concept = _score(row, analysis, base=0.52)
            candidates.append({"row": row, "score": score, "keyword": keyword, "concept": concept})

    keyword_terms = sorted({*analysis["paliHints"], *analysis["mustHavePali"], *analysis["shouldHavePali"]})
    keyword_single_terms = _single_terms(keyword_terms)
    if keyword_single_terms:
        rows = fetch_all(
            f"""
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
            where d.corpus_type = any(%s)
              and p.normalized_pali ~* %s
              {pitaka_sql}
            order by term_hits desc, length(p.normalized_pali) asc, p.sort_order asc
            limit %s
            """,
            _params_with_pitaka([keyword_single_terms, corpus_types, _regex_for_terms(keyword_single_terms)], pitaka_params, [limit * 2]),
        )
        for row in rows:
            score, keyword, concept = _score(row, analysis, base=0.18)
            candidates.append({"row": row, "score": score, "keyword": keyword, "concept": concept})

    if settings()["search_enable_vector"]:
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
                  and d.corpus_type = any(%s)
                  {pitaka_sql}
                order by p.embedding <=> %s::vector
                limit %s
                """,
                _params_with_pitaka([vector, corpus_types], pitaka_params, [vector, limit]),
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


def _attach_translations(results: list[dict]) -> None:
    for item in results:
        try:
            if item.get("paliText") != item.get("_originalPaliText"):
                item["translation"] = translate_text(item["paliText"])
            else:
                item["translation"] = translate_passage(item["id"])
        except Exception:
            item["translation"] = {
                "vi": None,
                "fromCache": False,
                "error": public_translation_error(),
            }

def _page_results(candidate_results: list[dict], page: int, page_size: int) -> list[dict]:
    start = (page - 1) * page_size
    results = candidate_results[start : start + page_size]
    for idx, item in enumerate(results):
        item["rank"] = start + idx + 1
    return results


def _strip_internal_fields(results: list[dict]) -> None:
    for item in results:
        item.pop("_documentId", None)
        item.pop("_needsNearbyHeading", None)
        item.pop("_textHash", None)
        item.pop("_keyword", None)
        item.pop("_concept", None)
        item.pop("_contentWords", None)
        item.pop("_sortOrder", None)
        item.pop("_originalPaliText", None)


def search_passages(query: str, corpus_types: list[str], pitaka_type: str | None, page: int = 1, page_size: int = 5) -> dict:
    local_analysis = analyze_query(query, corpus_types)
    analysis = merge_expansion(local_analysis, expand_query_with_ai(query))
    limit = max(600, page * page_size * 80)
    candidates = _retrieve_candidates(query, corpus_types, pitaka_type, analysis, limit)

    min_score = float(settings()["search_min_score"])
    by_hash: dict[str, dict] = {}
    by_hash_all: dict[str, dict] = {}
    for item in candidates:
        text_hash = item["row"]["text_hash"]
        if text_hash not in by_hash_all or item["score"] > by_hash_all[text_hash]["score"]:
            by_hash_all[text_hash] = item
        if item["score"] >= min_score:
            if text_hash not in by_hash or item["score"] > by_hash[text_hash]["score"]:
                by_hash[text_hash] = item

    ranked = sorted((by_hash or by_hash_all).values(), key=lambda item: item["score"], reverse=True)
    rerank_limit = max(5, int(settings()["search_rerank_limit"]))
    rerank_window = ranked[:rerank_limit]
    rerank_candidates = [
        _candidate(item["row"], item["score"], item["keyword"], item["concept"], corpus_types, pitaka_type, idx + 1)
        for idx, item in enumerate(rerank_window)
    ]

    reranked = rerank_candidates_with_ai(query, analysis, rerank_candidates)
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
            adjusted["matchReason"] = item.get("reason") or "Gemini rerank đánh giá đoạn này sát ý tìm kiếm."
            reranked_items.append(adjusted)

        remaining_candidates = [item for item in rerank_candidates if item["id"] not in used]
        tail = [
            _candidate(item["row"], item["score"], item["keyword"], item["concept"], corpus_types, pitaka_type, rerank_limit + idx + 1)
            for idx, item in enumerate(ranked[rerank_limit:])
        ]
        candidate_results = sorted([*reranked_items, *remaining_candidates, *tail], key=lambda item: item["score"], reverse=True)
    else:
        candidate_results = [
            _candidate(item["row"], item["score"], item["keyword"], item["concept"], corpus_types, pitaka_type, idx + 1)
            for idx, item in enumerate(ranked)
        ]

    candidate_results = _diversify_results(candidate_results)
    results = _page_results(candidate_results, page, page_size)
    for item in results:
        _append_nearby_heading_to_source(item)
    _expand_short_snippets(results)
    _attach_translations(results)
    _strip_internal_fields(results)
    _insert_log(query, corpus_types, pitaka_type, analysis, [item["id"] for item in results])

    start = (page - 1) * page_size
    return {
        "query": query,
        "analysis": analysis,
        "results": results,
        "pagination": {
            "page": page,
            "pageSize": page_size,
            "hasMore": len(candidate_results) > start + page_size,
        },
        "warning": "Đây là bản dịch của AI, chưa có sự kiểm chứng.",
    }
