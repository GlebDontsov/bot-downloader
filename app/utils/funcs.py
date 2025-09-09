import os
import shutil
import asyncio

from loguru import logger
from app.models import DownloadHistory, DownloadStatus


def format_file_size(total_size: int) -> str:
    """Форматирует размер файла в удобочитаемый вид."""
    if total_size > 1024 * 1024 * 1024:
        return f"{total_size / (1024 * 1024 * 1024):.1f} ГБ"
    elif total_size > 1024 * 1024:
        return f"{total_size / (1024 * 1024):.1f} МБ"
    elif total_size > 1024:
        return f"{total_size / 1024:.1f} КБ"
    else:
        return f"{total_size} Б"


def format_duration(seconds: int) -> str:
    """
    Конвертирует секунды в формат ЧЧ:ММ:СС
    """
    if not isinstance(seconds, int) or seconds < 0:
        return "0:00"

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60

    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    else:
        return f"{minutes}:{seconds:02d}"


async def cleanup_old_files(disk_path: str = "/", target_usage: float = 50) -> int:
    """
    Очищает старые скачанные файлы при превышении лимита диска.
    Удаляет файлы от самых старых до достижения целевого уровня использования диска.
    """
    # Проверяем текущее использование диска
    total, used, free = shutil.disk_usage(disk_path)
    current_usage_percent = (used / total) * 100

    # Если использование меньше 70%, очистка не требуется
    logger.warning(current_usage_percent)
    if current_usage_percent < 90:
        return 0

    logger.warning(f"Переполнение диска! Использование: {current_usage_percent:.1f}%")
    cleaned_count = 0
    target_bytes = total * (target_usage / 100)
    bytes_to_free = used - target_bytes

    # Получаем файлы для удаления, отсортированные по дате завершения (самые старые first)
    old_downloads = await DownloadHistory.filter(
        status=DownloadStatus.COMPLETED,
        file_path__not_isnull=True,
    ).order_by("completed_at")

    for download in old_downloads:
        if bytes_to_free <= 0:
            break

        try:
            if download.file_path and os.path.exists(download.file_path):
                # Получаем размер файла перед удалением
                file_size = os.path.getsize(download.file_path)

                # Удаляем файл
                os.remove(download.file_path)
                bytes_to_free -= file_size
                cleaned_count += 1

                # Удаляем пустую папку
                parent_dir = os.path.dirname(download.file_path)
                if os.path.exists(parent_dir) and not os.listdir(parent_dir):
                    os.rmdir(parent_dir)

                # Обновляем запись в базе
                download.file_path = None
                await download.save(update_fields=["file_path"])

                logger.debug(f"Удален файл {download.file_path} ({file_size} bytes)")

        except Exception as e:
            logger.error(f"Ошибка удаления файла {download.file_path}: {e}")
            continue

    # Получаем итоговую статистику
    total, used, free = shutil.disk_usage(disk_path)
    final_usage_percent = (used / total) * 100

    logger.info(
        f"Очистка завершена. Удалено {cleaned_count} файлов. "
        f"Использование диска: {final_usage_percent:.1f}%"
    )

    return cleaned_count
