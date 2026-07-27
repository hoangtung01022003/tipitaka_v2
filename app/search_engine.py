import math
import re
from typing import Any

from psycopg.types.json import Jsonb

from .config import settings
from .db import execute, fetch_all
from .glossary import analyze_query
from .normalize import normalize_pali
from .query_expander import expand_query_with_ai, merge_expansion, rerank_candidates_with_ai
from .translator import translate_passage


PITAKA_PREFIXES = {
    "vinaya": ["vin%"],
    "sutta": ["s%"],
    "abhidhamma": ["abh%"],
}


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
    return {
        word
        for word in normalize_pali(text).split()
        if len(word) >= 5 and word not in stopwords
    }


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _source_family(source_path: str) -> str:
    parts = [part.strip().lower() for part in source_path.split("->") if part.strip()]
    return " -> ".join(parts[:4])


def _is_near_duplicate(candidate: dict, selected: dict) -> bool:
    candidate_words = candidate.get("_contentWords") or set()
    selected_words = selected.get("_contentWords") or set()
    lexical_similarity = _jaccard(candidate_words, selected_words)
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


def _source_path(hierarchy: dict[str, Any]) -> list[str]:
    source_path = hierarchy.get("sourcePath")
    if isinstance(source_path, list):
        return [str(item) for item in source_path]
    return []


def _source_label(corpus_type: str) -> str:
    return {
        "mul": "Tipitaka (Mula)",
        "att": "Atthakatha",
        "tik": "Tika",
        "nrf": "Anna",
    }.get(corpus_type, corpus_type)


def _pitaka_label(pitaka_type: str | None, corpus_type: str) -> str | None:
    if not pitaka_type:
        return None
    base = {
        "vinaya": "Vinayapitaka",
        "sutta": "Suttapitaka",
        "abhidhammapitaka": "Abhidhammapitaka",
        "abhidhamma": "Abhidhammapitaka",
    }.get(pitaka_type, pitaka_type)
    if corpus_type == "mul":
        return base
    return f"{base} ({_source_label(corpus_type)})"


def _display_source(row: dict, corpus_types: list[str], pitaka_type: str | None) -> str:
    source = _source_path(row.get("hierarchy") or {})
    noisy = {
        "Namo tassa bhagavato arahato sammāsambuddhassa",
        "Nidānavaṇṇanā niṭṭhitā.",
        "Paṭhamavaggavaṇṇanā niṭṭhitā.",
        "Dutiyavaggavaṇṇanā niṭṭhitā.",
    }
    clean = [item for item in source if item and item not in noisy and "niṭṭhitā" not in item.lower()]
    corpus = corpus_types[0] if corpus_types else "mul"
    prefix = [_source_label(corpus)]
    pitaka = _pitaka_label(pitaka_type, corpus)
    if pitaka:
        prefix.append(pitaka)
    important = [item for item in clean if re.search(r"nikāy|pitak|aṭṭhakath|pāḷi", item, re.I)][:3]
    nearest = [clean[-1]] if clean else []
    compact = list(dict.fromkeys([*important, *nearest]))
    return " -> ".join([*prefix, *compact])


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


def _candidate(row: dict, score: float, keyword: float, concept: float, corpus_types: list[str], pitaka_type: str | None, rank: int) -> dict:
    return {
        "id": str(row["id"]),
        "rank": rank,
        "score": round(score, 4),
        "sourcePath": _display_source(row, corpus_types, pitaka_type),
        "paragraphNo": row.get("paragraph_no") or str(rank),
        "paliText": row["pali_text"],
        "translation": {"vi": None, "fromCache": False},
        "matchReason": _match_reason(score, keyword, concept, float(row.get("semantic_score") or 0)),
        "_textHash": row["text_hash"],
        "_keyword": keyword,
        "_concept": concept,
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


def _attach_translations(results: list[dict]) -> None:
    for item in results:
        try:
            item["translation"] = translate_passage(item["id"])
        except Exception as exc:
            item["translation"] = {
                "vi": None,
                "fromCache": False,
                "error": f"Chưa dịch được đoạn này: {exc}",
            }


def _retrieve_candidates(query: str, corpus_types: list[str], pitaka_type: str | None, analysis: dict, limit: int) -> list[dict]:
    pitaka_sql, pitaka_params = _pitaka_sql(pitaka_type)
    candidates: list[dict] = []

    phrase_patterns = _phrase_patterns([*analysis["expandedQueries"], *analysis["mustHavePali"], *analysis["shouldHavePali"]])
    if phrase_patterns:
        rows = fetch_all(
            f"""
            select
              p.id, p.paragraph_no, p.pali_text, p.normalized_pali, p.hierarchy, p.text_hash,
              0::float as semantic_score,
              (
                select count(*)
                from unnest(%s::text[]) pattern
                where p.normalized_pali like pattern
              ) as phrase_hits
            from passages p
            join documents d on d.id = p.document_id
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
              p.id, p.paragraph_no, p.pali_text, p.normalized_pali, p.hierarchy, p.text_hash,
              0::float as semantic_score,
              (
                select count(*)
                from unnest(%s::text[]) term
                where p.normalized_pali ~* ('(^|[[:space:]])' || term || '[[:alpha:]]*($|[[:space:]])')
              ) as term_hits
            from passages p
            join documents d on d.id = p.document_id
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
                  p.id, p.paragraph_no, p.pali_text, p.normalized_pali, p.hierarchy, p.text_hash,
                  1 - (p.embedding <=> %s::vector) as semantic_score,
                  0 as term_hits
                from passages p
                join documents d on d.id = p.document_id
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


def _page_results(candidate_results: list[dict], page: int, page_size: int) -> list[dict]:
    start = (page - 1) * page_size
    results = candidate_results[start : start + page_size]
    for idx, item in enumerate(results):
        item["rank"] = start + idx + 1
    for item in results:
        item.pop("_textHash", None)
        item.pop("_keyword", None)
        item.pop("_concept", None)
        item.pop("_contentWords", None)
    return results


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

    _attach_translations(results)
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
        "warning": "Đây là bản dịch của AI chỉ để tham khảo, chưa có sự kiểm chứng.",
    }
