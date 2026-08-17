from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Supabase Postgres. Use the *pooler* URI (aws-1-<region>.pooler.supabase.com),
    # not db.<ref>.supabase.co — the direct host is IPv6-only and Render's
    # outbound network is IPv4.
    database_url: str

    # LLM. Phase 1 does not call these yet; they are wired so Phase 2 is a
    # no-config change.
    anthropic_api_key: str = ""
    model_fast: str = "claude-haiku-4-5"
    model_smart: str = "claude-sonnet-5"

    # Embeddings. 1536 dims to match the vector(1536) column in the schema.
    embedding_api_key: str = ""
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_model: str = "text-embedding-3-small"
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
