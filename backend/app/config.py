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
    amount_min_sats: int = 10_000
    amount_near_match_sats: int = 5_000
    amount_min_occurrences: int = 2
    peel_chain_max_hops: int = 20
    change_min_score: float = 0.40
    change_min_score_gap: float = 0.12
    change_high_output_threshold: int = 10


settings = Settings()
