from functools import lru_cache
from pathlib import Path
import os
import secrets

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[2]
PYTHON_DIR = Path(__file__).resolve().parents[1]

load_dotenv(ROOT_DIR / ".env")
load_dotenv(ROOT_DIR.parent / ".env")
load_dotenv(PYTHON_DIR / ".env", override=True)


def bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, ""))
    except ValueError:
        return default


def int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, ""))
    except ValueError:
        return default


@lru_cache(maxsize=1)
def settings() -> dict[str, object]:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured.")

    return {
        "database_url": database_url,
        "gemini_api_key": os.getenv("GEMINI_API_KEY", ""),
        "gemini_text_models": [
            item.strip()
            for item in os.getenv(
                "GEMINI_TEXT_MODELS",
                "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-3.5-flash,gemini-3.6-flash,gemini-3-flash,gemini-2.5-flash-lite",
            ).split(",")
            if item.strip()
        ],
        "search_min_score": float_env("PY_SEARCH_MIN_SCORE", 0.0),
        "search_enable_vector": bool_env("PY_SEARCH_ENABLE_VECTOR", True),
        "search_ai_mode": os.getenv("PY_SEARCH_AI_MODE", "full").strip().lower(),
        "search_rerank_limit": int(os.getenv("PY_SEARCH_RERANK_LIMIT", "100")),
        "gemini_request_timeout_ms": int_env("GEMINI_REQUEST_TIMEOUT_MS", 12000),
        "ga_measurement_id": os.getenv("GA_MEASUREMENT_ID", "").strip(),
        "admin_username": os.getenv("ADMIN_USERNAME", "admin").strip(),
        "admin_password": os.getenv("ADMIN_PASSWORD", "123456").strip(),
        "secret_key": os.getenv("SECRET_KEY", secrets.token_hex(32)),
    }
