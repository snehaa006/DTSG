from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Supabase Postgres. Use the *pooler* URI (aws-1-<region>.pooler.supabase.com),
    # not db.<ref>.supabase.co — the direct host is IPv6-only and Render's
    # outbound network is IPv4.
    database_url: str

    # Gemini. One key covers both generation and embeddings.
    gemini_api_key: str = ""

    # Model IDs are configuration, not constants — Google ships new ones often.
    # Confirm what your key can reach with `scripts/list_models.py` and override
    # here if these have moved on.
    #
    # FAST runs on every message (extraction + conflict classification), so it
    # dominates cost. SMART is reserved for answer generation and is not called
    # by any endpoint yet.
    model_fast: str = "gemini-2.5-flash"
    model_smart: str = "gemini-2.5-pro"

    # Embeddings. 1536 must match the vector(1536) column; changing it means a
    # migration on memories.embedding plus a re-embed of every row.
    embedding_model: str = "gemini-embedding-001"
    embedding_dim: int = 1536

    # Comma-separated list of allowed browser origins.
    cors_origins: str = "http://localhost:5173"

    # Connection pool sizing. Supabase's free-tier pooler is not generous;
    # keep this small.
    db_pool_min: int = 1
    db_pool_max: int = 5

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
