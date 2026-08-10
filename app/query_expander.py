import hashlib
import json

from google import genai
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from .config import settings
from .db import execute, fetch_one
from .i18n import DEFAULT_LANGUAGE, normalize_language
from .normalize import normalize_pali

# Doi chuoi nay khi doi cach phan tich/xep hang de bo qua cache cu.
# v3: prompt tach rieng theo ngon ngu (vi/en/my) va khoa cache co them ngon ngu - ban ghi
# cu deu sinh ra tu prompt noi cung "cau hoi tieng Viet" nen phai bo di.
# v4: bat `reason` viet hoan toan bang ngon ngu dang chon, khong lan thuat ngu tieng Viet.
PIPELINE_VERSION = "v4-per-language-reason"


def _cache_key(*parts: str) -> str:
    return hashlib.sha256("||".join(parts).encode("utf-8")).hexdigest()


def _cache_get(key: str, kind: str) -> dict | None:
    """Doc ket qua AI da nho. Cache hong khong duoc lam hong ca truy van."""
    try:
        row = fetch_one(
            "select payload from query_ai_cache where cache_key = %s and kind = %s and pipeline_version = %s",
            [key, kind, PIPELINE_VERSION],
        )
    except Exception:  # noqa: BLE001 - chua chay migration thi coi nhu chua co cache
        return None
    return row["payload"] if row else None


def _cache_put(key: str, kind: str, payload: dict) -> None:
    try:
        execute(
            """
            insert into query_ai_cache (cache_key, kind, pipeline_version, payload)
            values (%s, %s, %s, %s)
            on conflict (cache_key, kind, pipeline_version) do nothing
            """,
            [key, kind, PIPELINE_VERSION, Jsonb(payload)],
        )
    except Exception:  # noqa: BLE001
        pass


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


_EXPANSION_CACHE: dict[tuple[str, str], dict] = {}


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


def _has_pali_search_terms(data: dict) -> bool:
    return any(
        data.get(key)
        for key in [
            "paliHints",
            "paliExactTerms",
            "paliRelatedTerms",
            "mustHavePali",
            "shouldHavePali",
            "expandedQueries",
        ]
    )


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
        if key in {
            "paliHints",
            "paliExactTerms",
            "paliRelatedTerms",
            "mustHavePali",
            "shouldHavePali",
            "avoidPali",
            "expandedQueries",
        }:
            merged[key] = _normalize_list(values)
        else:
            merged[key] = list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))

    merged["mainMeaning"] = ai.get("mainMeaning") or local.get("mainMeaning")
    merged["cleanQuery"] = ai.get("cleanQuery") or local.get("cleanQuery") or local.get("mainMeaning")
    merged["intent"] = ai.get("intent") or local.get("intent") or "search"
    merged["paliHints"] = _normalize_list(
        [
            *(merged.get("paliHints") or []),
            *(merged.get("paliExactTerms") or []),
            *(merged.get("paliRelatedTerms") or []),
        ]
    )
    merged["mustHavePali"] = _normalize_list([*(merged.get("mustHavePali") or []), *(merged.get("paliExactTerms") or [])])
    merged["shouldHavePali"] = _normalize_list(
        [*(merged.get("shouldHavePali") or []), *(merged.get("paliRelatedTerms") or [])]
    )
    return merged


def _is_retryable_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(part in message for part in ["429", "quota", "rate", "resource_exhausted", "404", "not found", "unsupported"])


# Phần prompt PHỤ THUỘC ngôn ngữ của người hỏi. Đầu ra thì luôn là Pali cho mọi ngôn ngữ -
# DB chỉ có văn bản Pali - nên chỉ có phần mô tả đầu vào và các ví dụ là đổi.
#
# Vì sao phải tách: trước đây prompt nói cứng "biến câu hỏi tiếng Việt thành...", nên hỏi
# bằng tiếng Anh hay tiếng Myanmar thì model vẫn nhận chỉ thị sai về ngôn ngữ đầu vào, và
# mọi ví dụ dẫn đường đều là tiếng Việt. Khách đã báo đúng triệu chứng này: "khi nhập
# tiếng Anh thì tìm cũng ra nhưng các bài kinh ra thường ít có liên quan".
_EXPANSION_PROMPT_BY_LANGUAGE: dict[str, list[str]] = {
    "vi": [
        "Nhiệm vụ: biến câu hỏi TIẾNG VIỆT thành nhiều tín hiệu tìm kiếm Pali để search DB Pali.",
        "Mục tiêu là tìm được đoạn kinh/chú giải/phụ chú giải đúng ý nghĩa, không phải chỉ trùng chữ tiếng Việt.",
        "cleanQuery: chủ đề chính đã bỏ từ đệm tiếng Việt, ví dụ 'khái niệm của tăng bảo là gì' -> 'tăng bảo'.",
        "Nếu truy vấn là khái niệm Phật học tiếng Việt, hãy suy luận thuật ngữ Pali tương ứng.",
        "Ví dụ không ăn phi thời -> vikalabhojana, vikalabhojana veramani, vikale bhojanam, sikkhapada.",
        "Ví dụ quy y không lệ thuộc -> sarana, saranagamana, aparappaccaya, esa me saranam, esa me parayanam.",
        "Ví dụ khái niệm Tăng bảo -> sangharatana, sangha, ariyasangha, savakasangha, ratanattaya.",
        "Ví dụ quả báo bố thí -> dana, dakkhina, caga, vipaka, phala, punna, anisamsa.",
        "Ví dụ trộm cắp -> adinnadana, adinnam, theyya, theyyasankhata, adinnadana veramani.",
        "Ví dụ lòng từ bi -> metta, karuna, mettasahagata, karunasahagata, brahmavihara, appamanna.",
        "Ví dụ Tứ Diệu Đế -> cattari ariyasaccani, dukkha, samudaya, nirodha, magga.",
    ],
    "en": [
        "Task: turn an ENGLISH question into Pali search signals for a Pali-only database.",
        "Aim for passages that match the MEANING in the canon, commentary or sub-commentary,",
        "not passages that merely repeat the English wording.",
        "cleanQuery: the core topic with filler removed, e.g. 'what is the concept of the Sangha' -> 'Sangha'.",
        "Map English Buddhist terminology to its Pali equivalent, including common renderings by",
        "Bhikkhu Bodhi and Bhikkhu Sujato, since the canon is indexed in Pali only.",
        "Example eating at the wrong time -> vikalabhojana, vikalabhojana veramani, vikale bhojanam, sikkhapada.",
        "Example going for refuge -> sarana, saranagamana, aparappaccaya, esa me saranam, esa me parayanam.",
        "Example the Sangha as a jewel -> sangharatana, sangha, ariyasangha, savakasangha, ratanattaya.",
        "Example fruit of giving -> dana, dakkhina, caga, vipaka, phala, punna, anisamsa.",
        "Example stealing / taking what is not given -> adinnadana, adinnam, theyya, theyyasankhata, adinnadana veramani.",
        "Example loving-kindness and compassion -> metta, karuna, mettasahagata, karunasahagata, brahmavihara, appamanna.",
        "Example the Four Noble Truths -> cattari ariyasaccani, dukkha, samudaya, nirodha, magga.",
    ],
    "my": [
        "Task: turn a BURMESE (Myanmar) question into Pali search signals for a Pali-only database.",
        "Burmese Buddhist vocabulary is largely borrowed from Pali, so prefer restoring the original",
        "Pali spelling of a borrowed term before guessing a synonym.",
        "Aim for passages that match the MEANING in the canon, commentary or sub-commentary.",
        "cleanQuery: the core topic with filler removed, written in Pali or English.",
        "Example ဝိကာလဘောဇန (eating at the wrong time) -> vikalabhojana, vikalabhojana veramani, sikkhapada.",
        "Example သရဏဂုံ (going for refuge) -> sarana, saranagamana, esa me saranam.",
        "Example သံဃရတနာ (the Sangha jewel) -> sangharatana, sangha, ariyasangha, ratanattaya.",
        "Example ဒါန (giving) -> dana, dakkhina, caga, vipaka, phala, punna, anisamsa.",
        "Example ခိုးယူခြင်း (stealing) -> adinnadana, adinnam, theyya, theyyasankhata.",
        "Example မေတ္တာ ကရုဏာ -> metta, karuna, mettasahagata, karunasahagata, brahmavihara.",
        "Example သစ္စာလေးပါး (Four Noble Truths) -> cattari ariyasaccani, dukkha, samudaya, nirodha, magga.",
    ],
}


def expand_query_with_ai(query: str, clean_query: str = "", language: str = DEFAULT_LANGUAGE) -> dict | None:
    if settings()["search_ai_mode"] not in {"query", "full"}:
        return None
    if not settings()["gemini_api_key"]:
        return None

    language = normalize_language(language)
    # Ngon ngu PHAI nam trong khoa cache: cung mot chuoi truy van nhung prompt khac nhau
    # cho ra bo thuat ngu khac nhau, dung chung khoa thi cau tra loi cua ngon ngu nay se
    # duoc phat lai cho ngon ngu kia.
    memory_key = (query.strip(), clean_query.strip(), language)
    cached = _EXPANSION_CACHE.get(memory_key)
    if cached:
        return dict(cached)

    # Nho xuong DB nua: chi nho trong bo nho thi khoi dong lai server la cung cau hoi
    # ra ket qua khac han, vi Gemini moi lan tra ve mot bo thuat ngu khac.
    key = _cache_key(query.strip(), clean_query.strip(), language)
    stored = _cache_get(key, "expansion")
    if stored:
        _EXPANSION_CACHE[memory_key] = dict(stored)
        return dict(stored)

    prompt = "\n".join(
        [
            "Bạn là bộ phân tích truy vấn cho search engine kinh điển Pali.",
            "DB chỉ có văn bản Pali, không có tiếng Việt.",
            *_EXPANSION_PROMPT_BY_LANGUAGE[language],
            "Không trả lời nội dung kinh. Không dịch đoạn kinh. Không rewrite câu hỏi để thay thế query gốc.",
            "Hãy tạo search plan Pali rộng nhưng có trọng tâm: thuật ngữ chính, thuật ngữ liên quan, cụm Pali, biến thể không dấu.",
            "Trả JSON thuần theo schema:",
            '{"mainMeaning":"","cleanQuery":"","intent":"","vietnameseKeywords":[],"relatedConcepts":[],"paliHints":[],"paliExactTerms":[],"paliRelatedTerms":[],"mustHavePali":[],"shouldHavePali":[],"avoidPali":[],"expandedQueries":[]}',
            "intent: một trong các ý như definition, benefit, result, rule, story, comparison, general_search.",
            "paliHints: thuật ngữ Pali liên quan rộng.",
            "paliExactTerms: thuật ngữ Pali trọng tâm nhất, nếu có.",
            "paliRelatedTerms: thuật ngữ Pali liên quan để mở rộng tìm kiếm.",
            "mustHavePali: chỉ 1-3 thuật ngữ/cụm gần như bắt buộc nếu chủ đề rất rõ. Đừng đưa quá nhiều từ vào mustHavePali.",
            "shouldHavePali: thuật ngữ nên có để tăng độ chính xác.",
            "avoidPali: thuật ngữ dễ gây nhiễu nếu có.",
            "expandedQueries: các cụm Pali hoặc cách diễn đạt Pali liên quan, tối đa 20 mục.",
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
            for key in [
                "paliHints",
                "paliExactTerms",
                "paliRelatedTerms",
                "mustHavePali",
                "shouldHavePali",
                "avoidPali",
                "expandedQueries",
            ]:
                data[key] = _normalize_list(data.get(key) or [])
            if not _has_pali_search_terms(data):
                continue
            _EXPANSION_CACHE[memory_key] = dict(data)
            _cache_put(key, "expansion", data)
            return data
        except Exception as exc:
            if not _is_retryable_error(exc):
                break

    return None


# Ngon ngu de model viet `reason` - chuoi nay nam trong cau tieng Viet cua prompt.
_REASON_LANGUAGE = {"vi": "tiếng Việt", "en": "tiếng Anh", "my": "tiếng Myanmar (Miến Điện)"}

# Ten goi cac tang van theo tung ngon ngu, dung NGAY TRONG `reason`.
#
# Vi sao can: phan chung cua prompt viet bang tieng Viet va goi la "Chánh tạng", nen model
# be nguyen cum tieng Viet do vao cau tra loi - do duoc mot `reason` tieng Myanmar lai
# ket thuc bang "... Chánh tạng ဖြစ်သည်။". Khong sai nghia nhung lan ngon ngu.
_CORPUS_NAMES_FOR_REASON = {
    "vi": "Chánh tạng / Chú giải / Phụ chú giải",
    "en": "the canon (Tipiṭaka Mūla) / commentary (Aṭṭhakathā) / sub-commentary (Ṭīkā)",
    "my": "ပါဠိတော် (Tipiṭaka Mūla) / အဋ္ဌကထာ / ဋီကာ",
}


def rerank_candidates_with_ai(
    query: str, analysis: dict, candidates: list[dict], language: str = DEFAULT_LANGUAGE
) -> list[dict] | None:
    if settings()["search_ai_mode"] != "full":
        return None
    if not settings()["gemini_api_key"] or not candidates:
        return None

    language = normalize_language(language)
    # Ngon ngu vao khoa cache: `reason` duoc hien thang cho nguoi doc nen moi ngon ngu la
    # mot ket qua khac, dung chung khoa thi nguoi doc tieng Anh nhan duoc ly do tieng Viet.
    rerank_key = _cache_key(query.strip(), "|".join(item["id"] for item in candidates), language)
    stored = _cache_get(rerank_key, "rerank")
    if stored is not None:
        return list(stored.get("results") or [])

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
            "Khi có cả bản văn Chánh tạng (nguồn bắt đầu bằng 'Tipiṭaka Mūla') lẫn Chú giải/Phụ chú giải",
            "cùng trả lời được câu hỏi, hãy xếp bản văn Chánh tạng lên trước: đó là bản gốc,",
            "còn Chú giải chỉ nên đứng trên khi người dùng hỏi đúng về phần luận giải.",
            "Trả JSON thuần theo schema:",
            '{"results":[{"id":"...","relevance":0.0,"reason":"..."}]}',
            "relevance từ 0 đến 1. Sắp xếp từ liên quan nhất đến kém hơn.",
            "Có thể bỏ qua ứng viên không liên quan.",
            # `reason` hien thang cho nguoi doc nen phai viet dung ngon ngu ho dang xem, va
            # phai viet HOAN TOAN bang ngon ngu do - khong duoc muon lai thuat ngu tieng
            # Viet trong phan huong dan nay.
            f"Reason viết ngắn, HOÀN TOÀN bằng {_REASON_LANGUAGE[language]}.",
            "Tuyệt đối không chèn từ hay thuật ngữ của ngôn ngữ khác vào reason.",
            f"Trong reason, gọi tên các tạng văn theo đúng ngôn ngữ đó: {_CORPUS_NAMES_FOR_REASON[language]}.",
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
            results = [item.model_dump() for item in parsed.results]
            _cache_put(rerank_key, "rerank", {"results": results})
            return results
        except Exception as exc:
            if not _is_retryable_error(exc):
                break

    return None
