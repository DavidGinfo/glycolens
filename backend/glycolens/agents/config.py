from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google import genai

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_PATH = BACKEND_ROOT / ".env"

# Load backend/.env at import/startup without clobbering explicit process env vars.
load_dotenv(DEFAULT_ENV_PATH, override=False)

DEFAULT_FAST_MODEL = "gemini-3.8-flash"
DEFAULT_PRO_MODEL = "gemini-3.8-flash"

_PLACEHOLDER_KEYS = {
    "",
    "your_api_key_here",
    "your-gemini-api-key",
    "replace_me",
    "none",
    "null",
}

_lock = threading.RLock()
_runtime_api_key: str | None = None


def _clean_key(raw: str | None) -> str:
    if not raw:
        return ""
    cleaned = raw.strip().strip('"').strip("'").strip()
    if cleaned.lower() in _PLACEHOLDER_KEYS:
        return ""
    return cleaned


def get_active_api_key() -> str:
    """Return the active Gemini API key from runtime memory or environment."""
    with _lock:
        if _runtime_api_key is not None:
            return _runtime_api_key
        env_key = _clean_key(os.getenv("GEMINI_API_KEY")) or _clean_key(
            os.getenv("GOOGLE_API_KEY")
        )
        return env_key


def has_gemini_api_key() -> bool:
    """Return True if a non-empty Gemini API key is configured."""
    return bool(get_active_api_key())


def get_fast_model() -> str:
    model = (os.getenv("GEMINI_FAST_MODEL") or DEFAULT_FAST_MODEL).strip()
    return model or DEFAULT_FAST_MODEL


def get_pro_model() -> str:
    model = (os.getenv("GEMINI_PRO_MODEL") or DEFAULT_PRO_MODEL).strip()
    return model or DEFAULT_PRO_MODEL


def get_gemini_config() -> dict[str, Any]:
    """Return current Gemini runtime configuration metadata."""
    return {
        "gemini_configured": has_gemini_api_key(),
        "fast_model": get_fast_model(),
        "pro_model": get_pro_model(),
    }


def _persist_key_to_env_file(env_path: Path, api_key: str) -> None:
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    updated = False
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("GEMINI_API_KEY=") or stripped.startswith(
            "export GEMINI_API_KEY="
        ):
            if api_key:
                new_lines.append(f"GEMINI_API_KEY={api_key}")
            updated = True
        else:
            new_lines.append(line)

    if not updated and api_key:
        if new_lines and new_lines[-1].strip() != "":
            new_lines.append("")
        new_lines.append(f"GEMINI_API_KEY={api_key}")

    payload = "\n".join(new_lines).rstrip("\n")
    env_path.write_text(f"{payload}\n" if payload else "", encoding="utf-8")


def set_runtime_api_key(api_key: str, persist_env_path: str | None = None) -> None:
    """
    Update GEMINI_API_KEY in runtime memory/environment and optionally persist
    to backend/.env (when persist_env_path is provided).
    """
    cleaned = _clean_key(api_key)
    with _lock:
        global _runtime_api_key
        _runtime_api_key = cleaned
        if cleaned:
            os.environ["GEMINI_API_KEY"] = cleaned
        else:
            os.environ.pop("GEMINI_API_KEY", None)

        if persist_env_path is not None:
            target = Path(persist_env_path)
            _persist_key_to_env_file(target, cleaned)


def create_genai_client() -> genai.Client:
    """Instantiate an official google-genai Client using the active API key."""
    key = get_active_api_key()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")
    return genai.Client(api_key=key)
