import os
from dataclasses import dataclass

from dotenv import find_dotenv, load_dotenv


class ConfigError(RuntimeError):
    """A required setting is missing."""


@dataclass(frozen=True, slots=True)
class Settings:
    keitaro_base_url: str
    keitaro_api_key: str

    def __repr__(self) -> str:
        return f"Settings(keitaro_base_url={self.keitaro_base_url!r}, api_key=<hidden>)"


def load_settings() -> Settings:
    # usecwd: the .env of the folder the app runs in, never the one beside the source.
    load_dotenv(find_dotenv(usecwd=True))
    base_url = os.environ.get("KEITARO_BASE_URL", "").strip().rstrip("/")
    api_key = os.environ.get("KEITARO_API_KEY", "").strip()
    if not base_url:
        raise ConfigError("KEITARO_BASE_URL is not set")
    if not api_key:
        raise ConfigError("KEITARO_API_KEY is not set")
    return Settings(keitaro_base_url=base_url, keitaro_api_key=api_key)
