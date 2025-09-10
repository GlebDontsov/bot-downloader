import os
import shutil
import asyncio
import aiofiles.os as aio_os

from loguru import logger
from app.models import DownloadHistory, DownloadStatus
from app.utils.constants import CLEANUP_THRESHOLD, TARGET_USAGE_DISK, DISK_CLEANUP_INTERVAL


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


async def cleanup_old_files(disk_path: str = "/") -> int:
    """
    Очищает старые скачанные файлы при превышении лимита диска.
    Удаляет файлы от самых старых до достижения целевого уровня использования диска.
    """
    # Проверяем текущее использование диска
    total, used, free = await async_disk_usage("/")
    current_usage_percent = (used / total) * 100

    if current_usage_percent < CLEANUP_THRESHOLD:
        logger.info(
            f"Очистка не требуется. Использование диска: {current_usage_percent:.1f}% "
            f"(порог: {CLEANUP_THRESHOLD}%, свободно: {free / (1024 ** 3):.1f} GB)"
        )
        return 0

    logger.warning(f"Переполнение диска! Использование: {current_usage_percent:.1f}%")
    cleaned_count = 0
    target_bytes = total * (TARGET_USAGE_DISK / 100)
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
            if download.file_path and await aio_os.path.exists(download.file_path):
                # Получаем размер файла перед удалением
                file_size = await aio_os.path.getsize(download.file_path)

                # Удаляем файл
                await aio_os.remove(download.file_path)
                bytes_to_free -= file_size
                cleaned_count += 1

                # Удаляем пустую папку
                parent_dir = os.path.dirname(download.file_path)
                if await aio_os.path.exists(parent_dir) and not await aio_os.listdir(parent_dir):
                    await aio_os.rmdir(parent_dir)

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


async def async_disk_usage(path: str = "/") -> tuple:
    """Асинхронная проверка использования диска"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, shutil.disk_usage, path)


async def cleanup_scheduler():
    """Планировщик очистки с защитой от частого запуска"""
    while True:
        try:
            await cleanup_old_files()
            await asyncio.sleep(DISK_CLEANUP_INTERVAL)

        except Exception as e:
            logger.error(f"Ошибка в планировщике очистки: {e}")
            await asyncio.sleep(DISK_CLEANUP_INTERVAL)