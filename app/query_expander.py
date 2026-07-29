from functools import lru_cache
import json

from google import genai
from pydantic import BaseModel, Field

from .config import settings
from .normalize import normalize_pali


class QueryExpansion(BaseModel):
    mainMeaning: str = ""
    cleanQuery: str = ""
    intent: str = ""
    vietnameseKeywords: list[str] = Field(default_factory=list)
    relatedConcepts: list[str] = Field(default_factory=list)
    paliHints: list[str] = Field(default_factory=list)
    paliExactTerms: list[str] = Field(default_factory=list)
    paliRelatedTerms: list[str] = Field(default_factory=list)
    mustHavePali: list[str] = Field(default_factory=list)
    shouldHavePali: list[str] = Field(default_factory=list)
    avoidPali: list[str] = Field(default_factory=list)
    expandedQueries: list[str] = Field(default_factory=list)


class RerankItem(BaseModel):
    id: str
    relevance: float = Field(ge=0, le=1)
    reason: str = ""


class RerankOutput(BaseModel):
    results: list[RerankItem] = Field(default_factory=list)


def _client() -> genai.Client:
    api_key = str(settings()["gemini_api_key"])
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")
    return genai.Client(
        api_key=api_key,
        http_options={"timeout": int(settings()["gemini_request_timeout_ms"])},
    )


def _models() -> list[str]:
    return list(settings()["gemini_text_models"])


def _normalize_list(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        normalized = normalize_pali(str(value))
        if normalized and normalized not in seen:
            seen.add(normalized)
            output.append(normalized)
    return output


def merge_expansion(local: dict, ai: dict | None) -> dict:
    if not ai:
        return local

    merged = dict(local)
    for key in [
        "vietnameseKeywords",
        "relatedConcepts",
        "paliHints",
        "paliExactTerms",
        "paliRelatedTerms",
        "mustHavePali",
        "shouldHavePali",
        "avoidPali",
        "expandedQueries",
    ]:
        values = [*(local.get(key) or []), *(ai.get(key) or [])]
        if key in {"paliHints", "paliExactTerms", "paliRelatedTerms", "mustHavePali", "shouldHavePali", "avoidPali", "expandedQueries"}:
            merged[key] = _normalize_list(values)
        else:
            merged[key] = list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))

    merged["mainMeaning"] = ai.get("mainMeaning") or local.get("mainMeaning")
    merged["cleanQuery"] = ai.get("cleanQuery") or local.get("cleanQuery") or local.get("mainMeaning")
    merged["intent"] = ai.get("intent") or local.get("intent") or "search"
    merged["paliHints"] = _normalize_list([*(merged.get("paliHints") or []), *(merged.get("paliExactTerms") or []), *(merged.get("paliRelatedTerms") or [])])
    merged["mustHavePali"] = _normalize_list([*(merged.get("mustHavePali") or []), *(merged.get("paliExactTerms") or [])])
    merged["shouldHavePali"] = _normalize_list([*(merged.get("shouldHavePali") or []), *(merged.get("paliRelatedTerms") or [])])
    return merged


def _is_retryable_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(part in message for part in ["429", "quota", "rate", "resource_exhausted", "404", "not found", "unsupported"])


@lru_cache(maxsize=512)
def expand_query_with_ai(query: str, clean_query: str = "") -> dict | None:
    if settings()["search_ai_mode"] not in {"query", "full"}:
        return None
    if not settings()["gemini_api_key"]:
        return None

    prompt = "\n".join(
        [
            "Bạn là bộ phân tích truy vấn cho search engine kinh điển Pali.",
            "Không trả lời nội dung kinh. Không dịch đoạn kinh. Chỉ chuyển câu hỏi tiếng Việt thành tín hiệu tìm kiếm Pali.",
            "Mục tiêu là tìm được đoạn kinh/chú giải/phụ chú giải đúng ý nghĩa, không phải chỉ trùng chữ tiếng Việt.",
            "Trả JSON thuần theo schema:",
            '{"mainMeaning":"","cleanQuery":"","intent":"","vietnameseKeywords":[],"relatedConcepts":[],"paliHints":[],"paliExactTerms":[],"paliRelatedTerms":[],"mustHavePali":[],"shouldHavePali":[],"avoidPali":[],"expandedQueries":[]}',
            "intent: một trong các ý như definition, benefit, result, rule, story, comparison, general_search.",
            "cleanQuery: chủ đề chính đã bỏ từ đệm tiếng Việt, ví dụ 'khái niệm của tăng bảo là gì' -> 'tăng bảo'.",
            "paliHints: thuật ngữ Pali liên quan rộng.",
            "paliExactTerms: thuật ngữ Pali trọng tâm nhất, nếu có.",
            "paliRelatedTerms: thuật ngữ Pali liên quan để mở rộng tìm kiếm.",
            "mustHavePali: thuật ngữ/cụm gần như bắt buộc nếu chủ đề rõ.",
            "shouldHavePali: thuật ngữ nên có để tăng độ chính xác.",
            "avoidPali: thuật ngữ dễ gây nhiễu nếu có.",
            "expandedQueries: các cụm Pali hoặc cách diễn đạt Pali liên quan, tối đa 12 mục.",
            "Nếu truy vấn là một khái niệm Phật học tiếng Việt, hãy suy luận các thuật ngữ Pali tương ứng.",
            "Ví dụ không ăn phi thời -> vikālabhojana, vikālabhojanā veramaṇī, vikāle bhojanaṃ, sikkhāpada.",
            "Ví dụ quy y không lệ thuộc -> saraṇa, saraṇagamana, aparappaccaya, esa me saraṇaṃ, esa me parāyaṇaṃ.",
            "Ví dụ khái niệm Tăng bảo -> saṅgharatana, saṅgha, ariyasaṅgha, sāvakasaṅgha, ratanattaya.",
            "Ví dụ khái niệm Phật bảo/Pháp bảo -> buddharatana/buddha hoặc dhammaratana/dhamma, ratanattaya.",
            "Ví dụ quả báo bố thí -> dāna, dakkhiṇā, cāga, vipāka, phala, puñña, ānisaṃsa.",
            "Ví dụ trộm cắp -> adinnādāna, adinnaṃ, theyya, theyyasaṅkhāta, adinnādānā veramaṇī.",
            f"Truy vấn gốc: {query}",
            f"Clean query sơ bộ: {clean_query or query}",
        ]
    )

    client = _client()
    for model in _models():
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
            expansion = QueryExpansion.model_validate_json(response.text or "{}")
            data = json.loads(expansion.model_dump_json())
            for key in ["paliHints", "paliExactTerms", "paliRelatedTerms", "mustHavePali", "shouldHavePali", "avoidPali", "expandedQueries"]:
                data[key] = _normalize_list(data.get(key) or [])
            return data
        except Exception as exc:
            if not _is_retryable_error(exc):
                break

    return None


def rerank_candidates_with_ai(query: str, analysis: dict, candidates: list[dict]) -> list[dict] | None:
    if settings()["search_ai_mode"] != "full":
        return None
    if not settings()["gemini_api_key"] or not candidates:
        return None

    compact_candidates = [
        {
            "id": item["id"],
            "rank": index + 1,
            "score": item["score"],
            "source": item["sourcePath"],
            "paliText": item["paliText"][:900],
        }
        for index, item in enumerate(candidates)
    ]
    prompt = "\n".join(
        [
            "Bạn là bộ rerank kết quả tìm kiếm kinh điển Pali.",
            "Nhiệm vụ: xếp hạng lại các đoạn thật sự trả lời đúng ý truy vấn người dùng.",
            "Không ưu tiên đoạn chỉ trùng từ nhưng sai nghĩa.",
            "Ưu tiên đoạn có nội dung Pali khớp ý chính, đúng corpus/Tạng đã chọn và nguồn rõ.",
            "Trả JSON thuần theo schema:",
            '{"results":[{"id":"...","relevance":0.0,"reason":"..."}]}',
            "relevance từ 0 đến 1. Sắp xếp từ liên quan nhất đến kém hơn.",
            "Có thể bỏ qua ứng viên không liên quan. Reason viết ngắn bằng tiếng Việt.",
            "",
            f"Truy vấn người dùng: {query}",
            f"Phân tích query: {json.dumps(analysis, ensure_ascii=False)}",
            f"Ứng viên: {json.dumps(compact_candidates, ensure_ascii=False)}",
        ]
    )

    client = _client()
    for model in _models():
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
            parsed = RerankOutput.model_validate_json(response.text or '{"results":[]}')
            return [item.model_dump() for item in parsed.results]
        except Exception as exc:
            if not _is_retryable_error(exc):
                break

    return None
