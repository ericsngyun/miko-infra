from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Postgres
    pg_host: str = "master-postgres"
    pg_port: int = 5432
    pg_user: str = "awaas_master"
    pg_password: str
    pg_database: str = "awaas_master"

    # Telegram
    telegram_bot_token: str

    # Polling
    health_poll_interval_s: int = 300

    # Spend caps (USD/day)
    spend_cap_pleadly: float = 30.0
    spend_cap_awaas: float = 50.0
    spend_cap_trading: float = 10.0

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
