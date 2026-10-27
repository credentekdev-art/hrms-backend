from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Kritika AI HR Assistant API"
    api_prefix: str = "/api/v1"
    database_url: str = "sqlite:///./hrms_resource.db"
    secret_key: str = "dev-only-change-me"
    access_token_expire_minutes: int = 480
    cors_origins: str = "http://localhost:5173"
    default_max_concurrent_projects: int = 3
    enable_internal_scheduler: bool = False
    auto_deallocate_hour: int = 1
    bootstrap_demo_data: bool = True

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

    @property
    def cors_origin_list(self) -> list[str]:
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
