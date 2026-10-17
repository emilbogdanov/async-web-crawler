"""Crawler settings, loaded from environment variables (prefix ``CRAWLER_``) or ``.env``."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CRAWLER_",
        env_file=".env",
        extra="ignore",
        frozen=True,
    )

    user_agent: str = Field(
        default="AsyncWebCrawler/0.1 (+https://github.com/ESBogdanov25/async-web-crawler)",
        min_length=1,
    )
    max_depth: int = Field(default=2, ge=0)
    max_pages: int = Field(default=100, ge=1)
    concurrency: int = Field(default=10, ge=1, le=100)
    per_domain_delay: float = Field(default=1.0, ge=0, description="Seconds between hits to a host")
    request_timeout: float = Field(default=10.0, gt=0, description="Seconds per request")
    max_retries: int = Field(default=3, ge=0)
    backoff_base: float = Field(default=0.5, gt=0, description="Initial retry delay in seconds")
    max_response_bytes: int = Field(default=5 * 1024 * 1024, ge=1)
    respect_robots: bool = True
    same_host_only: bool = True
