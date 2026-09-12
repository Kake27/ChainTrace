from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mempool_base_url: str = "https://mempool.space/api"
    mempool_timeout_seconds: float = 30.0
    csv_max_bytes: int = 2_000_000
    max_transactions: int = 500


settings = Settings()
