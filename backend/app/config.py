from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mempool_base_url: str = "https://mempool.space/api"
    mempool_timeout_seconds: float = 30.0
    csv_max_bytes: int = 2_000_000
    max_transactions: int = 500
    # Tight activity window. Do not use 10–60 minutes (ordinary confirmation lag).
    timing_window_seconds: int = 120
    timing_min_occurrences: int = 2


settings = Settings()
