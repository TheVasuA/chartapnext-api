from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str
    REDIS_URL: str
    CELERY_BROKER_URL: str
    CELERY_RESULT_BACKEND: str

    BINANCE_WS_URL: str = "wss://stream.binance.com:9443/stream"
    KLINE_INTERVAL: str = "1m"
    SIGNAL_CACHE_TTL: int = 120
    OHLCV_MAX_CANDLES: int = 200
    # Plain str — avoids pydantic-settings v2 JSON-parsing List fields from env
    ALLOWED_ORIGINS: str = "http://localhost:3000"

    @property
    def allowed_origins_list(self) -> list[str]:
        """Split comma-separated origins into a list."""
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
