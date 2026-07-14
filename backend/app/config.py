from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root is two directories up from this file:
#   backend/app/config.py  ->  parents[0]=app, parents[1]=backend, parents[2]=repo root
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Typed application settings.

    Values come from environment variables, falling back to the repo-root `.env`
    file for local development. Pydantic validates them: if `database_url` is
    missing, the app fails loudly at startup instead of blowing up mid-request.

    In production the SAME environment variables get populated from AWS Secrets
    Manager / Parameter Store, so no code changes are needed to deploy — the app
    only ever reads from the environment, never from a hardcoded value.
    """

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",  # ignore any env vars we haven't declared below
    )

    # --- Database ---
    database_url: str  # required — no default, so startup fails if it's absent

    # --- Auth (wired up when we build the auth layer) ---
    jwt_secret: str = "dev-insecure-change-me"
    jwt_access_ttl_minutes: int = 20

    # --- Anthropic (wired up when we build generation) ---
    anthropic_api_key: str = ""


# One shared, importable instance. Importing this triggers loading + validation.
settings = Settings()
