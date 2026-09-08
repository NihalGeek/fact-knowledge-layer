"""Central configuration, loaded from environment / .env.

All tunables live here so behaviour is never hidden inside modules. Nothing in
this file hard-codes anything about the starter dataset.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional at runtime
    pass


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    # --- LLM (optional) ---
    llm_enabled: bool = _bool("FACTLAYER_LLM_ENABLED", False)
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "").strip()
    llm_model: str = os.getenv("FACTLAYER_LLM_MODEL", "claude-3-5-sonnet-latest")

    # --- Storage ---
    db_path: str = os.getenv("FACTLAYER_DB_PATH", "data/factlayer.db")
    upload_dir: str = os.getenv("FACTLAYER_UPLOAD_DIR", "data/uploads")
    max_upload_mb: int = int(os.getenv("FACTLAYER_MAX_UPLOAD_MB", "50"))

    # --- Reasoning ---
    rel_tolerance: float = _float("FACTLAYER_REL_TOLERANCE", 0.005)

    @property
    def llm_active(self) -> bool:
        """LLM is used only if explicitly enabled AND a key is present."""
        return self.llm_enabled and bool(self.anthropic_api_key)

    def ensure_dirs(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.upload_dir).mkdir(parents=True, exist_ok=True)


settings = Settings()
