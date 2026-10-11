from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    PROJECT_NAME: str = "ToneLoom API"
    VERSION: str = "1.1.0"
    API_V1_STR: str = "/api/v1"

    # Supabase
    SUPABASE_URL: str = ""
    SUPABASE_KEY: str = ""

    # LLM — OpenRouter is the primary gateway for all chat models
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_SITE_URL: str = "https://www.toneloom.pro"
    OPENROUTER_APP_NAME: str = "ToneLoom"
    DEFAULT_LLM: str = "gpt-4o-mini"

    # Optional legacy keys (embeddings / unused for chat once OpenRouter is set)
    OPENAI_API_KEY: str = ""
    CLAUDE_API_KEY: str = ""
    GEMINI_API_KEY: str = ""

    # Creem payment gateway (https://creem.io)
    CREEM_API_KEY: str = ""
    CREEM_WEBHOOK_SECRET: str = ""
    CREEM_PRODUCT_PRO: str = ""              # Pro monthly ($12/mo)
    CREEM_PRODUCT_PRO_YEARLY: str = ""       # Pro yearly ($79/yr)
    CREEM_PRODUCT_BYOK: str = ""             # BYOK monthly ($4/mo)
    CREEM_PRODUCT_BYOK_YEARLY: str = ""      # BYOK yearly ($29/yr)
    CREEM_PRODUCT_TEAM: str = ""             # Optional legacy team product id
    CREEM_TEST_MODE: bool = True             # True → test-api.creem.io, False → api.creem.io
    CREEM_SUCCESS_URL: str = ""              # Optional override; plain https URL, no query string

    # Legacy Stripe keys (unused — billing uses Creem)
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_PRICE_PRO: str = ""
    STRIPE_PRICE_TEAM: str = ""

    # App
    FRONTEND_URL: str = ""
    CORS_ORIGINS: str = ""

    # Plan limits (-1 = unlimited)
    FREE_REWRITES_LIMIT: int = 30
    FREE_MEMORIES_LIMIT: int = 50
    FREE_TONE_PROFILES_LIMIT: int = 1
    PRO_REWRITES_LIMIT: int = -1
    PRO_MEMORIES_LIMIT: int = -1
    PRO_TONE_PROFILES_LIMIT: int = 3
    BYOK_REWRITES_LIMIT: int = -1
    BYOK_MEMORIES_LIMIT: int = -1
    BYOK_TONE_PROFILES_LIMIT: int = 3

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
