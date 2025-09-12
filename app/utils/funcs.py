import os
import shutil
import asyncio
import aiofiles.os as aio_os
from datetime import datetime, timedelta, timezone

from loguru import logger
from app.models import DownloadHistory, DownloadStatus
from app.utils.constants import CLEANUP_THRESHOLD, TARGET_USAGE_DISK, DISK_CLEANUP_INTERVAL, MOSCOW_TZ


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


async def generate_stats_file(yesterday: datetime.date) -> tuple[str, dict, int]:
    """Генерирует файл со статистикой по пользователям за указанный день в читаемом формате"""
    yesterday_start = datetime.combine(yesterday, datetime.min.time())
    yesterday_end = datetime.combine(yesterday + timedelta(days=1), datetime.min.time())

    user_stats = await DownloadHistory.filter(
        created_at__gte=yesterday_start,
        created_at__lt=yesterday_end
    ).prefetch_related("user")

    # Группируем по пользователям
    user_downloads = {}
    for download in user_stats:
        user_id = download.user.id if download.user else "Аноним"
        username = f"@{download.user.username}" if download.user and download.user.username else "Без username"
        full_name = download.user.full_name if download.user else "Аноним"

        if user_id not in user_downloads:
            user_downloads[user_id] = {
                "username": username,
                "full_name": full_name,
                "total": 0,
                "completed": 0,
                "failed": 0
            }

        user_downloads[user_id]["total"] += 1
        if download.status == DownloadStatus.COMPLETED:
            user_downloads[user_id]["completed"] += 1
        elif download.status == DownloadStatus.FAILED:
            user_downloads[user_id]["failed"] += 1

    # Создаем текстовое содержимое
    text_content = f"📊 СТАТИСТИКА СКАЧИВАНИЙ ЗА {yesterday.strftime('%d.%m.%Y')}\n"
    text_content += "=" * 50 + "\n\n"

    # Сортируем пользователей по количеству скачиваний (по убыванию)
    sorted_users = sorted(user_downloads.items(), key=lambda x: x[1]["total"], reverse=True)

    # Статистика по пользователям
    text_content += "👥 ПОЛЬЗОВАТЕЛИ:\n"
    text_content += "-" * 30 + "\n"

    for i, (user_id, stats) in enumerate(sorted_users, 1):
        success_rate = (stats["completed"] / stats["total"] * 100) if stats["total"] > 0 else 0
        text_content += f"{i}. {stats['full_name']} ({stats['username']})\n"
        text_content += f"   📥 Всего: {stats['total']} | ✅ Успешно: {stats['completed']} | ❌ Ошибок: {stats['failed']}\n"
        text_content += f"   📊 Успешность: {success_rate:.1f}%\n\n"

    # Общая статистика
    total_downloads = sum(stats["total"] for stats in user_downloads.values())
    total_completed = sum(stats["completed"] for stats in user_downloads.values())
    total_failed = sum(stats["failed"] for stats in user_downloads.values())
    overall_success_rate = (total_completed / total_downloads * 100) if total_downloads > 0 else 0

    text_content += "📈 ОБЩАЯ СТАТИСТИКА:\n"
    text_content += "-" * 30 + "\n"
    text_content += f"• Всего пользователей: {len(user_downloads)}\n"
    text_content += f"• Всего скачиваний: {total_downloads}\n"
    text_content += f"• Успешных скачиваний: {total_completed}\n"
    text_content += f"• Неудачных скачиваний: {total_failed}\n"
    text_content += f"• Общая успешность: {overall_success_rate:.1f}%\n\n"

    # Дополнительная информация
    text_content += "💡 ПРИМЕЧАНИЕ:\n"
    text_content += "-" * 30 + "\n"
    text_content += "• Анонимные пользователи - те, кто начал скачивание\n  до регистрации в боте\n"
    text_content += "• Успешность считается как отношение успешных\n  скачиваний к общему количеству попыток\n"
    text_content += f"• Отчет сгенерирован: {yesterday.strftime('%d.%m.%Y')}"

    return text_content, user_downloads, total_downloads


def get_moscow_time() -> datetime:
    """
    Упрощенная версия получения московского времени
    """
    return datetime.now(MOSCOW_TZ)
