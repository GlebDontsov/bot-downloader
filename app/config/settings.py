"""
Настройки приложения
"""

from typing import List, Union, Any
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Настройки приложения"""

    # Telegram Bot
    bot_token: str = Field(..., description="Токен Telegram бота")
    admin_ids: Any = Field(default_factory=list, description="ID администраторов")

    # Database
    database_url: str = Field(
        default="postgres://user:password@localhost:5432/youtube_bot",
        description="URL подключения к базе данных",
    )

    # Redis
    redis_url: str = Field(
        default="redis://localhost:6379/0", description="URL подключения к Redis"
    )

    # YouTube Download
    download_path: str = Field(
        default="./downloads", description="Путь для сохранения скачанных видео"
    )
    max_video_duration: int = Field(
        default=3600,  # 1 час
        description="Максимальная длительность видео в секундах",
    )
    max_file_size: int = Field(
        default=50 * 1024 * 1024,  # 50MB
        description="Максимальный размер файла в байтах",
    )

    # Logging
    log_level: str = Field(default="INFO", description="Уровень логирования")
    log_file: str = Field(default="bot.log", description="Файл логов")

    # Rate limiting
    rate_limit_requests: int = Field(
        default=5, description="Количество запросов в минуту на пользователя"
    )

    @field_validator("admin_ids", mode="after")
    @classmethod
    def parse_admin_ids(cls, value: Union[str, List[int]]) -> List[int]:
        """Парсинг ID администраторов из строки в список"""
        if isinstance(value, str):
            if not value.strip():
                return []
            try:
                return [int(x.strip()) for x in value.split(",") if x.strip()]
            except ValueError:
                return []
        return value

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"  # Игнорировать неизвестные переменные из .env


# Глобальный экземпляр настроек
settings = Settings()
print(settings)
