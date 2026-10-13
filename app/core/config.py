from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ez_base_url: str = Field(alias="EZ_BASE_URL")
    ez_api_token: str | None = Field(default=None, alias="EZ_API_TOKEN")
    bridge_api_token: str | None = Field(default=None, alias="BRIDGE_API_TOKEN")
    bridge_users_file: str | None = Field(default=None, alias="BRIDGE_USERS_FILE")

    llm_base_url: str = Field(alias="LLM_BASE_URL")
    llm_api_key: str = Field(alias="LLM_API_KEY")
    llm_model: str = Field(alias="LLM_MODEL")

    default_timezone: str = Field(default="Asia/Shanghai", alias="DEFAULT_TIMEZONE")
    default_cash_account_name: str = Field(default="现金账户", alias="DEFAULT_CASH_ACCOUNT_NAME")

    prompts_dir: str = Field(default="./prompts", alias="PROMPTS_DIR")
    max_image_bytes: int = Field(default=5 * 1024 * 1024, alias="MAX_IMAGE_BYTES")

    ez_request_timeout_seconds: float = Field(default=30.0, alias="EZ_REQUEST_TIMEOUT_SECONDS")
    llm_request_timeout_seconds: float = Field(default=120.0, alias="LLM_REQUEST_TIMEOUT_SECONDS")

    # 现金对账分类（默认按名称含「修改余额」匹配）；投资对账默认含「涨跌」
    balance_adjust_expense_category_path: str | None = Field(
        default=None, alias="BALANCE_ADJUST_EXPENSE_CATEGORY_PATH"
    )
    balance_adjust_income_category_path: str | None = Field(
        default=None, alias="BALANCE_ADJUST_INCOME_CATEGORY_PATH"
    )
    balance_investment_expense_category_path: str | None = Field(
        default=None, alias="BALANCE_INVESTMENT_EXPENSE_CATEGORY_PATH"
    )
    balance_investment_income_category_path: str | None = Field(
        default=None, alias="BALANCE_INVESTMENT_INCOME_CATEGORY_PATH"
    )

    rate_limit_per_minute: int = Field(default=10, alias="RATE_LIMIT_PER_MINUTE")

    # 通知渠道：BARK_PUSH_URL 为空则不启用 Bark。新增渠道在此加配置，并在 app/notify/factory.py 注册。
    bark_push_url: str | None = Field(default=None, alias="BARK_PUSH_URL")
    duplicate_watch_daily_time: str = Field(default="09:00", alias="DUPLICATE_WATCH_DAILY_TIME")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
