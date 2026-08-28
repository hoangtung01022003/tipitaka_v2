import json
import re
import hashlib
from itertools import count
from threading import Lock

from google import genai
from pydantic import BaseModel

from .config import settings
from .db import execute, fetch_one
from .i18n import DEFAULT_LANGUAGE, TRANSLATION_TARGETS, normalize_language


PROMPT_VERSION = "python-pali-vi-contextual-v5"
CHUNKED_PROMPT_VERSION = f"{PROMPT_VERSION}-chunked"
SUMMARY_PROMPT_VERSION = "python-pali-summary-v3-linked-paragraphs"
# Để RIÊNG khỏi PROMPT_VERSION: nội dung là định nghĩa tiếng Anh của DPD, không phải Pāli,
# nên hai loại không được dùng chung ô đệm trong `text_translations`.
DICTIONARY_PROMPT_VERSION = "python-dpd-gloss-v2-labelled"
SUMMARY_CHUNK_CHARS = 8000
SUMMARY_MAX_POINTS = 15
SUMMARY_MAX_POINTS_PER_CHUNK = 6
TRANSLATION_FALLBACK_CHUNK_CHARS = 3200
TRANSLATION_RESCUE_CHUNK_CHARS = 900
# Model Google đã ngừng phục vụ - gọi vào là 404, chỉ tổ đốt một vòng lặp dự phòng rồi mới
# sang được model sống. Kiểm chứng ngày 2026-08-28 bằng cách gọi thật từng model:
#   gemini-2.5-flash       404 "no longer available to new users"
#   gemini-2.5-flash-lite  404 "no longer available"
#   gemini-3-flash         404 "not found for API version v1beta"
BAD_TEXT_MODELS = {"gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-3-flash"}
FALLBACK_TEXT_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.6-flash",
]
PUBLIC_TRANSLATION_ERROR = "Chưa dịch được đoạn này. Vui lòng kiểm tra GEMINI_API_KEY hoặc thử lại sau."
PUBLIC_SUMMARY_ERROR = "Không thể tạo tóm tắt lúc này. Vui lòng thử lại sau."
_MODEL_CURSOR = count()
_MODEL_LOCK = Lock()


class Translation(BaseModel):
    translatedText: str
    notes: str | None = None
    model: str | None = None


class SummaryPoint(BaseModel):
    summary_text: str
    passage_ids: list[str]


class SectionSummary(BaseModel):
    points: list[SummaryPoint]


def _client() -> genai.Client:
    api_key = str(settings()["gemini_api_key"])
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")
    return genai.Client(
        api_key=api_key,
        http_options={"timeout": int(settings()["gemini_request_timeout_ms"])},
    )


def _models() -> list[str]:
    configured = [str(item).strip() for item in settings()["gemini_text_models"] if str(item).strip()]
    merged: list[str] = []
    for model in [*configured, *FALLBACK_TEXT_MODELS]:
        if model and model not in merged and model not in BAD_TEXT_MODELS:
            merged.append(model)
    return merged


def _models_for_call() -> list[str]:
    models = _models()
    if len(models) <= 1:
        return models
    with _MODEL_LOCK:
        offset = next(_MODEL_CURSOR) % len(models)
    return [*models[offset:], *models[:offset]]


def public_translation_error() -> str:
    return PUBLIC_TRANSLATION_ERROR


def _translation_payload(text: str | None, notes: str | None, model: str | None, language: str, from_cache: bool) -> dict:
    # Khoá "vi" là tên cũ, giữ lại để template và JS hiện có không phải đổi;
    # "text" là tên trung lập dùng cho mọi ngôn ngữ.
    return {
        "vi": text,
        "text": text,
        "language": language,
        "notes": notes,
        "model": model,
        "fromCache": from_cache,
    }


def translate_passage(passage_id: str, language: str = DEFAULT_LANGUAGE) -> dict:
    language = normalize_language(language)
    active_models = _models()
    cached = fetch_one(
        """
        select translated_text, notes, model
        from translations
        where passage_id = %s
          and language = %s
          and model = any(%s)
          and prompt_version = %s
        order by created_at desc
        limit 1
        """,
        [passage_id, language, active_models, PROMPT_VERSION],
    )
    if cached:
        return _translation_payload(cached["translated_text"], cached["notes"], cached["model"], language, True)

    passage = fetch_one("select pali_text from passages where id = %s", [passage_id])
    if not passage:
        raise RuntimeError("Passage not found.")

    translated = _translate_text_resilient(passage["pali_text"], language)
    model = translated.model or active_models[0]
    execute(
        """
        insert into translations (passage_id, language, model, prompt_version, translated_text, notes)
        values (%s, %s, %s, %s, %s, %s)
        on conflict (passage_id, language, model, prompt_version)
        do update set translated_text = excluded.translated_text, notes = excluded.notes, created_at = now()
        """,
        [passage_id, language, model, PROMPT_VERSION, translated.translatedText, translated.notes],
    )
    return _translation_payload(translated.translatedText, translated.notes, model, language, False)


def translate_text(pali_text: str, language: str = DEFAULT_LANGUAGE) -> dict:
    language = normalize_language(language)
    translated = _translate_text_resilient(pali_text, language)
    model = translated.model or (_models()[0] if _models() else None)
    return _translation_payload(translated.translatedText, translated.notes, model, language, False)


def _text_hash(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def translate_text_cached(pali_text: str, language: str = DEFAULT_LANGUAGE) -> dict:
    language = normalize_language(language)
    active_models = _models()
    text_hash = _text_hash(pali_text)
    cached = fetch_one(
        """
        select translated_text, notes, model
        from text_translations
        where text_hash = %s
          and language = %s
          and model = any(%s)
          and prompt_version = %s
        order by created_at desc
        limit 1
        """,
        [text_hash, language, active_models, PROMPT_VERSION],
    )
    if cached:
        payload = _translation_payload(cached["translated_text"], cached["notes"], cached["model"], language, True)
        payload["textHash"] = text_hash
        return payload

    translated = _translate_text_resilient(pali_text, language)
    model = translated.model or active_models[0]
    execute(
        """
        insert into text_translations (text_hash, language, model, prompt_version, source_text, translated_text, notes)
        values (%s, %s, %s, %s, %s, %s, %s)
        on conflict (text_hash, language, model, prompt_version)
        do update set translated_text = excluded.translated_text, notes = excluded.notes, source_text = excluded.source_text, created_at = now()
        """,
        [text_hash, language, model, PROMPT_VERSION, pali_text, translated.translatedText, translated.notes],
    )
    payload = _translation_payload(translated.translatedText, translated.notes, model, language, False)
    payload["textHash"] = text_hash
    return payload


def _dictionary_gloss_prompt(numbered: str, language: str) -> str:
    target = TRANSLATION_TARGETS.get(normalize_language(language), TRANSLATION_TARGETS[DEFAULT_LANGUAGE])
    return "\n".join(
        [
            f"Bạn là trợ lý dịch mục từ điển Pāḷi - Anh sang {target}.",
            f"Nhiệm vụ: dịch phần nghĩa của từng mục từ sang {target}, ngắn gọn đúng văn phong từ điển.",
            "Giữ NGUYÊN VĂN các phần sau, không dịch, không bỏ:",
            "- cụm cấu tạo từ trong ngoặc vuông, ví dụ [√bhaj + a + vant + ā];",
            "- chữ Pāḷi và ký hiệu ngữ căn √.",
            f"LUÔN dịch nhãn từ loại đứng đầu sang {target}, kể cả khi phần nghĩa rất ngắn, "
            "và giữ dấu chấm sau nhãn: masc. = Giống đực. / fem. = Giống cái. / "
            "nt. = Giống trung. / adj. = Tính từ. / adv. = Trạng từ. / ind. = Bất biến từ. / "
            "pr. = Thì hiện tại. / aor. = Thì quá khứ. / pp. = Quá khứ phân từ. / "
            "abs. = Bất biến quá khứ phân từ. / inf. = Nguyên mẫu. / root. = Ngữ căn.",
            "Giữ dấu chấm phẩy ngăn giữa các nghĩa như bản gốc. Không thêm giải thích ngoài nội dung đã cho.",
            "Dùng thuật ngữ Phật học Theravāda quen thuộc.",
            "",
            "Ví dụ đúng:",
            "  vào: 1. masc. by the Buddha; with the Buddha [√bhaj + a + vant + ā]",
            "  ra:  1. Giống đực. Bởi Đức Phật; cùng với Đức Phật. [√bhaj + a + vant + ā]",
            "  vào: 2. masc. animal; beast",
            "  ra:  2. Giống đực. Con thú; loài vật.",
            "",
            f"BẮT BUỘC về định dạng: trả về ĐÚNG {numbered.count(chr(10)) + 1} dòng, "
            "mỗi dòng bắt đầu bằng số thứ tự và dấu chấm giống hệt đầu vào (1. 2. 3. ...).",
            "Mỗi mục chỉ một dòng, không xuống dòng giữa chừng, không thêm dòng trống, "
            "không markdown, không giải thích.",
            "",
            "Cần dịch:",
            numbered,
        ]
    )


def _generate_plain_text(prompt: str) -> str:
    """Gọi Gemini lấy văn bản thuần, xoay vòng qua danh sách mô hình như các hàm dịch khác."""
    client = _client()
    errors: list[str] = []
    for model in _models_for_call():
        try:
            response = client.models.generate_content(model=model, contents=prompt)
            text = _strip_code_fence(response.text or "")
            if text:
                return text
            errors.append(f"{model}: rỗng")
        except Exception as exc:
            errors.append(f"{model}: {type(exc).__name__}: {str(exc)[:180]}")
    raise RuntimeError("All Gemini text models failed. " + " | ".join(errors))


_NUMBERED_LINE = re.compile(r"^\s*(\d+)\s*[.)]\s*(.+)$")


def translate_dictionary_glosses(glosses: list[str], language: str = DEFAULT_LANGUAGE) -> list[str] | None:
    """Dịch phần nghĩa của các mục từ điển DPD, GỘP một lượt gọi cho cả lần tra.

    Tách khỏi `translate_text_cached` vì đây là việc khác hẳn: đầu vào là định nghĩa từ điển
    TIẾNG ANH, còn prompt dịch kinh điển nói thẳng "dịch văn bản Pali" và kết thúc bằng nhãn
    "Pali:" - đưa tiếng Anh vào đó thì mô hình hiểu sai việc phải làm. Phiên bản prompt cũng
    để riêng nên hai loại nội dung không bao giờ đụng nhau trong bảng đệm.

    Đánh số từng dòng và bắt trả về đúng số dòng đó. Cần thiết vì một nghĩa như
    "masc. by the Buddha; with the Buddha [√bhaj + a + vant + ā]" rất dễ bị mô hình tách
    thành hai dòng, mà lệch một dòng là lệch nghĩa của mọi mục phía sau.

    Trả `None` khi không khớp được số dòng - giao diện giữ nguyên tiếng Anh. Gán nhầm nghĩa
    còn tệ hơn không có bản dịch.
    """
    lines = [re.sub(r"\s+", " ", gloss).strip() for gloss in glosses]
    lines = [line for line in lines if line]
    if not lines:
        return None

    language = normalize_language(language)
    numbered = "\n".join(f"{index}. {line}" for index, line in enumerate(lines, start=1))
    text_hash = _text_hash(numbered)
    active_models = _models()

    cached = fetch_one(
        """
        select translated_text
        from text_translations
        where text_hash = %s
          and language = %s
          and model = any(%s)
          and prompt_version = %s
        order by created_at desc
        limit 1
        """,
        [text_hash, language, active_models, DICTIONARY_PROMPT_VERSION],
    )
    if cached:
        return _parse_numbered_lines(str(cached["translated_text"] or ""), len(lines))

    raw = _generate_plain_text(_dictionary_gloss_prompt(numbered, language))
    parsed = _parse_numbered_lines(raw, len(lines))
    if parsed is None:
        return None

    execute(
        """
        insert into text_translations (text_hash, language, model, prompt_version, source_text, translated_text, notes)
        values (%s, %s, %s, %s, %s, %s, %s)
        on conflict (text_hash, language, model, prompt_version)
        do update set translated_text = excluded.translated_text, source_text = excluded.source_text, created_at = now()
        """,
        [text_hash, language, active_models[0], DICTIONARY_PROMPT_VERSION, numbered, raw, None],
    )
    return parsed


def _parse_numbered_lines(raw: str, expected: int) -> list[str] | None:
    """Đọc lại các dòng đã đánh số. Thiếu hoặc thừa dòng thì trả None."""
    found: dict[int, str] = {}
    for line in raw.splitlines():
        match = _NUMBERED_LINE.match(line)
        if match:
            found[int(match.group(1))] = match.group(2).strip()
    if len(found) == expected and set(found) == set(range(1, expected + 1)):
        return [found[index] for index in range(1, expected + 1)]

    # Mô hình bỏ đánh số nhưng vẫn đủ dòng thì vẫn dùng được.
    plain = [line.strip() for line in raw.splitlines() if line.strip()]
    if len(plain) == expected:
        return [_NUMBERED_LINE.sub(r"\2", line) for line in plain]
    return None


def _strip_code_fence(text: str) -> str:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value)
    return value.strip()


def _parse_translation_response(raw_text: str, model: str) -> Translation:
    text = _strip_code_fence(raw_text or "")
    if not text:
        raise ValueError("Empty Gemini translation response.")

    attempts = [text]
    if text.startswith("{") and not text.endswith("}"):
        attempts.append(text + "}")
    if text.startswith("{") and text.count("{") > text.count("}"):
        attempts.append(text + ("}" * (text.count("{") - text.count("}"))))

    for candidate in attempts:
        try:
            parsed = Translation.model_validate_json(candidate)
            parsed.model = model
            if parsed.translatedText.strip():
                return parsed
        except Exception:
            pass

    try:
        data = json.loads(attempts[-1])
        translated = str(data.get("translatedText") or data.get("translation") or "").strip()
        notes = data.get("notes")
        if translated:
            return Translation(translatedText=translated, notes=str(notes) if notes else None, model=model)
    except Exception:
        pass

    match = re.search(r'"translatedText"\s*:\s*"((?:\\.|[^"\\])*)', text, flags=re.DOTALL)
    if match:
        encoded = '"' + match.group(1) + '"'
        try:
            translated = json.loads(encoded).strip()
        except Exception:
            translated = match.group(1).strip()
        if translated:
            return Translation(translatedText=translated, notes=None, model=model)

    if not text.startswith("{") and len(text) >= 20:
        return Translation(translatedText=text, notes=None, model=model)

    raise ValueError("Gemini translation response was not usable JSON.")


def _translation_prompt(pali_text: str, json_mode: bool, language: str = DEFAULT_LANGUAGE) -> str:
    target = TRANSLATION_TARGETS.get(normalize_language(language), TRANSLATION_TARGETS[DEFAULT_LANGUAGE])
    lines = [
        f"Bạn là trợ lý dịch thuật Pali sang {target} cho văn bản kinh điển Phật giáo Theravāda.",
        f"Nhiệm vụ: dịch văn bản Pali sang {target} tự nhiên, rõ nghĩa, trang nghiêm và chính xác.",
        f"BẮT BUỘC: toàn bộ bản dịch phải viết bằng {target}, không được dùng ngôn ngữ khác.",
        "Không dịch máy móc từng chữ. Hãy ưu tiên truyền đạt đúng ý nghĩa của câu Pali bằng tiếng Việt dễ hiểu.",
        "Giữ đầy đủ nội dung của văn bản gốc; không tóm tắt, không bỏ ý, không thêm ý giáo lý ngoài văn bản.",
        f"Nếu câu Pali rất dài, được phép tách thành vài câu {target} ngắn hơn để dễ đọc, miễn không đổi nghĩa.",
        "Nếu văn bản thuộc dạng vấn đáp, tranh luận, phân tích pháp số hoặc định nghĩa Abhidhamma, hãy dịch theo đúng văn thể đó.",
        f"Không dịch kiểu chú giải từng cụm trong ngoặc. Không chèn từ Pali sau mỗi cụm {target}.",
        "Chỉ giữ thuật ngữ Pali trong ngoặc khi thuật ngữ đó quan trọng, khó dịch hết nghĩa, hoặc cần đối chiếu học thuật.",
        f"Dùng thuật ngữ Phật học {target} nhất quán, quen thuộc với truyền thống Theravāda.",
        "Với các thuật ngữ có nhiều cách dịch, hãy chọn cách dịch phù hợp nhất theo văn cảnh.",
        "Không áp dụng máy móc một bảng thuật ngữ cố định; luôn xét nghĩa theo văn cảnh Pali cụ thể.",
        "Với các đoạn lặp công thức hoặc ký hiệu lược như ...pe..., hãy dịch gọn theo đúng ý lược, không tự thêm nội dung không có trong văn bản.",
        "Văn phong nên trong sáng, mạch lạc, tự nhiên với người đọc bản ngữ.",
        "Nếu đoạn dài, vẫn dịch đủ toàn bộ, không tóm tắt.",
    ]
    if json_mode:
        lines.append('Trả JSON thuần, đúng một object: {"translatedText":"...","notes":"..."}')
    else:
        lines.append(f"Chỉ trả bản dịch {target} thuần, không bọc JSON, không markdown, không giải thích thêm.")
    lines.extend(["", "Pali:", pali_text])
    return "\n".join(lines)


def _split_text_for_translation(text: str, max_chars: int = TRANSLATION_FALLBACK_CHUNK_CHARS) -> list[str]:
    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if current:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0

    def split_long_paragraph(paragraph: str) -> list[str]:
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

    for paragraph in paragraphs:
        for piece in ([paragraph] if len(paragraph) <= max_chars else split_long_paragraph(paragraph)):
            next_len = current_len + len(piece) + (2 if current else 0)
            if current and next_len > max_chars:
                flush()
            current.append(piece)
            current_len = len(piece) if current_len == 0 else current_len + len(piece) + 2

    flush()
    return chunks


def _translate_text_resilient(pali_text: str, language: str = DEFAULT_LANGUAGE) -> Translation:
    try:
        return _translate_text(pali_text, language)
    except Exception as first_error:
        chunks = _split_text_for_translation(pali_text, max_chars=TRANSLATION_RESCUE_CHUNK_CHARS)
        if len(chunks) <= 1:
            raise first_error

        translated_parts: list[str] = []
        models: list[str] = []
        failed_chunks: list[int] = []
        for index, chunk in enumerate(chunks, start=1):
            try:
                translated = _translate_text(chunk, language)
                translated_parts.append(translated.translatedText.strip())
                if translated.model and translated.model not in models:
                    models.append(translated.model)
            except Exception:
                failed_chunks.append(index)

        if not translated_parts:
            raise first_error

        notes = f"Dịch fallback theo {len(chunks)} phần rồi ghép lại vì dịch nguyên đoạn bị lỗi."
        if failed_chunks:
            notes += f" Một số phần chưa dịch được: {', '.join(map(str, failed_chunks))}."

        return Translation(
            translatedText="\n\n".join(part for part in translated_parts if part),
            notes=notes,
            model=", ".join(models) if models else None,
        )


def _translate_text(pali_text: str, language: str = DEFAULT_LANGUAGE) -> Translation:
    client = _client()
    errors: list[str] = []
    prompt = _translation_prompt(pali_text, json_mode=True, language=language)
    plain_prompt = _translation_prompt(pali_text, json_mode=False, language=language)

    for model in _models_for_call():
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    response_mime_type="application/json",
                ),
            )
            return _parse_translation_response(response.text or "", model)
        except Exception as exc:
            errors.append(f"{model}/json: {type(exc).__name__}: {str(exc)[:180]}")
            message = str(exc).lower()
            if any(part in message for part in ["404", "not found", "unsupported", "quota", "429", "rate"]):
                continue

            try:
                response = client.models.generate_content(model=model, contents=plain_prompt)
                return _parse_translation_response(response.text or "", model)
            except Exception as plain_exc:
                errors.append(f"{model}/plain: {type(plain_exc).__name__}: {str(plain_exc)[:180]}")
                continue

    raise RuntimeError("All Gemini text models failed. " + " | ".join(errors))


def embed_query_vector(text: str) -> str | None:
    api_key = str(settings()["gemini_api_key"])
    if not api_key:
        return None
    try:
        client = genai.Client(
            api_key=api_key,
            http_options={"timeout": int(settings()["gemini_request_timeout_ms"])},
        )
        response = client.models.embed_content(
            model="gemini-embedding-2",
            contents=text,
            config={"output_dimensionality": 768},
        )
        values = response.embeddings[0].values if response.embeddings else None
        if not values:
            return None
        return "[" + ",".join(f"{float(value):.8f}" for value in values) + "]"
    except Exception:
        return None


def summarize_plain_pali_text(pali_text: str, language: str = DEFAULT_LANGUAGE) -> dict:
    """Tóm tắt văn bản Pali thủ công và lưu bền vững theo hash nội dung."""
    language = normalize_language(language)
    pali_text = str(pali_text or "").strip()
    if not pali_text:
        return {"points": [], "fromCache": False}

    text_hash = _text_hash(pali_text)
    cached = fetch_one(
        "select summary from text_summaries "
        "where text_hash=%s and language=%s and prompt_version=%s",
        [text_hash, language, SUMMARY_PROMPT_VERSION],
    )
    if cached:
        summary = cached.get("summary")
        if isinstance(summary, str):
            summary = json.loads(summary)
        payload = dict(summary or {"points": []})
        payload["fromCache"] = True
        return payload

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", pali_text) if part.strip()]
    paragraph_ids = [f"manual-p-{index}" for index in range(1, len(paragraphs) + 1)]
    linked_text = "\n\n".join(
        f"[ID: {paragraph_id}]\n{paragraph}"
        for paragraph_id, paragraph in zip(paragraph_ids, paragraphs)
    )
    allowed_ids = set(paragraph_ids)
    target_language = TRANSLATION_TARGETS.get(language, TRANSLATION_TARGETS[DEFAULT_LANGUAGE])
    prompt = (
        "You are a Buddhist scholar. Read the following complete Pali text and summarize "
        f"its main teachings in {target_language}. Return no more than 10-15 concise points. "
        "Do not invent details outside the supplied text. Every paragraph has an ID. For "
        "each summary point, passage_ids MUST contain one or more exact IDs of the paragraphs "
        "that support that point.\n\nPali text:\n"
        + linked_text
    )
    client = _client()
    errors: list[str] = []
    for model_name in _models_for_call():
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=SectionSummary,
                    temperature=0.2,
                ),
            )
            data = json.loads(response.text or "{}")
            points = data.get("points") if isinstance(data, dict) else None
            if not isinstance(points, list):
                data = {"points": []}
            else:
                for point in points:
                    if not isinstance(point, dict):
                        continue
                    ids = point.get("passage_ids")
                    point["passage_ids"] = [
                        passage_id
                        for passage_id in (ids if isinstance(ids, list) else [])
                        if passage_id in allowed_ids
                    ]
            execute(
                "insert into text_summaries "
                "(text_hash, language, prompt_version, model, source_text, summary) "
                "values (%s, %s, %s, %s, %s, %s::jsonb) "
                "on conflict (text_hash, language, prompt_version) do update set "
                "model=excluded.model, source_text=excluded.source_text, "
                "summary=excluded.summary, created_at=now()",
                [
                    text_hash,
                    language,
                    SUMMARY_PROMPT_VERSION,
                    model_name,
                    pali_text,
                    json.dumps(data, ensure_ascii=False),
                ],
            )
            data["fromCache"] = False
            return data
        except Exception as exc:
            errors.append(f"{model_name}: {type(exc).__name__}")

    print(f"All models failed to summarize manual Pali text: {errors}")
    return {"points": [], "fromCache": False, "error": "summary_failed"}


EXCERPT_SUMMARY_PROMPT_VERSION = "python-pali-excerpt-summary-v1"


def summarize_excerpt_text(pali_text: str, language: str = DEFAULT_LANGUAGE) -> dict:
    """Tóm tắt NGẮN (một đoạn văn) cho trích đoạn Pali hiện ở kết quả tìm kiếm.

    Khác `summarize_plain_pali_text`: đoạn trích ngắn nên khách chỉ cần một câu tóm tắt
    duy nhất, không phải danh sách nhiều điểm gắn `passage_ids` như bản tóm tắt cả bài
    kinh trong popup "Xem toàn bộ bài kinh". Dùng chung bảng cache `text_summaries` nhưng
    `prompt_version` riêng nên không lẫn với cache của hàm kia.
    """
    language = normalize_language(language)
    pali_text = str(pali_text or "").strip()
    if not pali_text:
        return {"summary": "", "fromCache": False}

    text_hash = _text_hash(pali_text)
    cached = fetch_one(
        "select summary from text_summaries "
        "where text_hash=%s and language=%s and prompt_version=%s",
        [text_hash, language, EXCERPT_SUMMARY_PROMPT_VERSION],
    )
    if cached:
        summary = cached.get("summary")
        if isinstance(summary, str):
            summary = json.loads(summary)
        return {"summary": (summary or {}).get("summary", ""), "fromCache": True}

    target_language = TRANSLATION_TARGETS.get(language, TRANSLATION_TARGETS[DEFAULT_LANGUAGE])
    prompt = (
        "You are a Buddhist scholar. Read the following short Pali excerpt and summarize "
        f"its meaning in ONE short paragraph (2-3 sentences), in {target_language}. "
        "Do not invent details outside the supplied text.\n\nPali text:\n" + pali_text
    )
    client = _client()
    errors: list[str] = []
    for model_name in _models_for_call():
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=genai.types.GenerateContentConfig(temperature=0.2),
            )
            summary_text = (response.text or "").strip()
            if not summary_text:
                raise ValueError("empty response")
            execute(
                "insert into text_summaries "
                "(text_hash, language, prompt_version, model, source_text, summary) "
                "values (%s, %s, %s, %s, %s, %s::jsonb) "
                "on conflict (text_hash, language, prompt_version) do update set "
                "model=excluded.model, source_text=excluded.source_text, "
                "summary=excluded.summary, created_at=now()",
                [
                    text_hash,
                    language,
                    EXCERPT_SUMMARY_PROMPT_VERSION,
                    model_name,
                    pali_text,
                    json.dumps({"summary": summary_text}, ensure_ascii=False),
                ],
            )
            return {"summary": summary_text, "fromCache": False}
        except Exception as exc:
            errors.append(f"{model_name}: {type(exc).__name__}")

    print(f"All models failed to summarize excerpt: {errors}")
    return {"summary": "", "fromCache": False, "error": "summary_failed"}


_SUMMARY_CACHE = {}


def _batch_summary_chunks(text_chunks: list[str], max_chars: int) -> list[str]:
    """Gom các đoạn [IDs...] liền kề thành từng batch không vượt quá max_chars.

    Gom theo ranh giới đoạn (không cắt giữa một đoạn) nên mỗi batch vẫn giữ
    nguyên cặp [IDs]/text; một đoạn đơn lẻ dài hơn max_chars vẫn được giữ
    trọn vẹn trong batch riêng của nó thay vì bị cắt dở.
    """
    batches: list[str] = []
    current: list[str] = []
    current_len = 0
    for chunk in text_chunks:
        chunk_len = len(chunk)
        if current and current_len + chunk_len > max_chars:
            batches.append("\n\n".join(current))
            current = []
            current_len = 0
        current.append(chunk)
        current_len += chunk_len
    if current:
        batches.append("\n\n".join(current))
    return batches


def _summarize_text_batch(batch_text: str, target_language: str, max_points: int) -> dict:
    prompt = (
        f"You are a Buddhist scholar. Read the following Pali text and its passage IDs.\n"
        f"Provide a comprehensive summary of the main points. BẮT BUỘC viết tóm tắt bằng ngôn ngữ: {target_language}.\n"
        f"Group multiple IDs into one summary point if they discuss the same topic.\n"
        f"If they are distinct, separate them. Ensure every point has at least one associated ID from the text.\n"
        f"CRITICAL: Keep the summary extremely concise. Do not exceed {max_points} main points to avoid timeouts.\n\n"
        f"Text:\n{batch_text}"
    )
    try:
        api_key = str(settings()["gemini_api_key"])
        client = genai.Client(
            api_key=api_key,
            http_options={"timeout": 60000},
        )
    except Exception as ex:
        print(f"Gemini client init failed: {ex}")
        return {"points": [], "error": f"client init: {ex}"}

    errors = []
    for model_name in _models_for_call():
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=SectionSummary,
                    temperature=0.2,
                ),
            )
            if response.text:
                return {"points": json.loads(response.text).get("points", [])}
            return {"points": []}
        except Exception as ex:
            print(f"Summary generation failed for {model_name}: {ex}")
            errors.append(f"{model_name}: {ex}")

    return {"points": [], "error": errors[0] if errors else "Unknown error"}


def summarize_section_text(section_payload: dict, language: str = DEFAULT_LANGUAGE) -> dict:
    """Summarize an entire section into key points with mapped passage IDs.

    Section dài (nhiều đoạn / commentary dày đặc) được chia thành nhiều batch
    tối đa SUMMARY_CHUNK_CHARS ký tự Pali và tóm tắt riêng từng batch rồi gộp
    lại, thay vì nhồi toàn bộ text vào một prompt khổng lồ: prompt càng lớn
    Gemini càng chậm, mà mỗi lần gọi lại thử lần lượt nhiều model dự phòng
    nên tổng thời gian dễ vượt quá timeout 60s mặc định của nginx phía
    trước, khiến trình duyệt nhận lỗi dù backend vẫn đang âm thầm chạy tiếp
    (và cache lại kết quả cho lượt mở sau).
    """
    blocks = section_payload.get("paragraphs", [])
    if not blocks:
        return {"points": []}

    text_chunks = []
    for block in blocks:
        passage_ids = block.get("passageIds", [])
        pali_text = block.get("text", "").strip()
        if not pali_text or not passage_ids:
            continue
        text_chunks.append(f"[IDs: {', '.join(passage_ids)}]\n{pali_text}")

    full_text = "\n\n".join(text_chunks)
    if not full_text.strip():
        return {"points": []}

    cache_key = f"{hashlib.md5(full_text.encode()).hexdigest()}_{language}"
    if cache_key in _SUMMARY_CACHE:
        return _SUMMARY_CACHE[cache_key]

    from .i18n import TRANSLATION_TARGETS, normalize_language
    target_language = TRANSLATION_TARGETS.get(normalize_language(language), TRANSLATION_TARGETS[DEFAULT_LANGUAGE])

    batches = _batch_summary_chunks(text_chunks, SUMMARY_CHUNK_CHARS)
    max_points = SUMMARY_MAX_POINTS if len(batches) == 1 else SUMMARY_MAX_POINTS_PER_CHUNK

    points: list[dict] = []
    errors: list[str] = []
    for batch_text in batches:
        result = _summarize_text_batch(batch_text, target_language, max_points)
        if result.get("points"):
            points.extend(result["points"])
        elif result.get("error"):
            errors.append(result["error"])

    if not points:
        print(f"All batches failed to generate summary: {errors}")
        return {"points": [{"summary_text": PUBLIC_SUMMARY_ERROR, "passage_ids": []}]}

    data = {"points": points}
    _SUMMARY_CACHE[cache_key] = data
    return data

