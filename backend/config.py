"""Runtime settings read from environment variables or a local .env file."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Shared process configuration; environment variables use RESCUEAI_ prefix."""

    model_config = SettingsConfigDict(env_prefix="RESCUEAI_", env_file=".env", extra="ignore")

    environment: str = Field(default="development", validation_alias="RESCUEAI_ENV")
    simulation_seed: int = Field(default=2026, ge=0)
