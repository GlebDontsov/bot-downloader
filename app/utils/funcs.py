import os
import shutil
import asyncio
import aiofiles.os as aio_os
from typing import Dict, Any
from datetime import datetime, timedelta

from loguru import logger
from aiogram.utils.keyboard import InlineKeyboardBuilder
from app.models import DownloadHistory, DownloadStatus, User
from app.utils.constants import (
    DISK_CLEANUP_INTERVAL,
    MOSCOW_TZ,
)


def format_file_size(total_size: int) -> str:
    """Форматирует размер файла в удобочитаемый вид."""
    if not total_size:
        return "unknown"

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


async def cleanup_all_files() -> int:
    """Очищает старые скачанные файлы"""
    cleaned_count = 0

    old_downloads = await DownloadHistory.filter(
        status=DownloadStatus.COMPLETED,
        file_path__not_isnull=True,
    )

    for download in old_downloads:
        try:
            if download.file_path and await aio_os.path.exists(download.file_path):
                await aio_os.remove(download.file_path)
                # Удаляем также пустую папку
                parent_dir = os.path.dirname(download.file_path)
                if await aio_os.path.exists(
                        parent_dir
                ) and not await aio_os.listdir(parent_dir):
                    await aio_os.rmdir(parent_dir)

                download.file_path = None
                await download.save(update_fields=["file_path"])
                cleaned_count += 1

        except Exception as e:
            logger.error(f"Ошибка удаления файла {download.file_path}: {e}")

    logger.info(f"Очищено {cleaned_count} старых файлов")
    return cleaned_count


async def async_disk_usage(path: str = "/") -> tuple:
    """Асинхронная проверка использования диска"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, shutil.disk_usage, path)


async def cleanup_scheduler():
    """Планировщик очистки с защитой от частого запуска"""
    while True:
        try:
            await cleanup_all_files()
            await asyncio.sleep(DISK_CLEANUP_INTERVAL)

        except Exception as e:
            logger.error(f"Ошибка в планировщике очистки: {e}")
            await asyncio.sleep(DISK_CLEANUP_INTERVAL)


async def generate_stats_file() -> tuple[str, dict, int]:
    """Генерирует файл со статистикой по пользователям за последние 30 дней в читаемом формате"""
    # Изменяем временной диапазон на последние 30 дней
    thirty_days_ago = get_moscow_time() - timedelta(days=30)
    thirty_days_ago_start = datetime.combine(
        thirty_days_ago.date(), datetime.min.time()
    )

    user_stats = await DownloadHistory.filter(
        created_at__gte=thirty_days_ago_start
    ).prefetch_related("user")

    # Группируем по пользователям
    user_downloads = {}
    for download in user_stats:
        user_id = download.user.id if download.user else "Аноним"
        username = (
            f"@{download.user.username}"
            if download.user and download.user.username
            else "Без username"
        )
        full_name = download.user.full_name if download.user else "Аноним"

        if user_id not in user_downloads:
            user_downloads[user_id] = {
                "username": username,
                "full_name": full_name,
                "total": 0,
                "completed": 0,
                "failed": 0,
            }

        user_downloads[user_id]["total"] += 1
        if download.status == DownloadStatus.COMPLETED:
            user_downloads[user_id]["completed"] += 1
        elif download.status == DownloadStatus.FAILED:
            user_downloads[user_id]["failed"] += 1

    # Создаем текстовое содержимое
    text_content = "📊 СТАТИСТИКА СКАЧИВАНИЙ ЗА ПОСЛЕДНИЕ 30 ДНЕЙ\n"
    text_content += f"Период: {thirty_days_ago.strftime('%d.%m.%Y')} - {datetime.now().strftime('%d.%m.%Y')}\n"
    text_content += "=" * 60 + "\n\n"

    # Сортируем пользователей по количеству скачиваний (по убыванию)
    sorted_users = sorted(
        user_downloads.items(), key=lambda x: x[1]["total"], reverse=True
    )

    # Статистика по пользователям
    text_content += "👥 ПОЛЬЗОВАТЕЛИ:\n"
    text_content += "-" * 30 + "\n"

    for i, (user_id, stats) in enumerate(sorted_users, 1):
        success_rate = (
            (stats["completed"] / stats["total"] * 100) if stats["total"] > 0 else 0
        )
        text_content += f"{i}. {stats['full_name']} ({stats['username']})\n"
        text_content += f"   📥 Всего: {stats['total']} | ✅ Успешно: {stats['completed']} | ❌ Ошибок: {stats['failed']}\n"
        text_content += f"   📊 Успешность: {success_rate:.1f}%\n\n"

    # Общая статистика
    total_downloads = sum(stats["total"] for stats in user_downloads.values())
    total_completed = sum(stats["completed"] for stats in user_downloads.values())
    total_failed = sum(stats["failed"] for stats in user_downloads.values())
    overall_success_rate = (
        (total_completed / total_downloads * 100) if total_downloads > 0 else 0
    )

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
    text_content += (
        "• Анонимные пользователи - те, кто начал скачивание\n  до регистрации в боте\n"
    )
    text_content += "• Успешность считается как отношение успешных\n  скачиваний к общему количеству попыток\n"
    text_content += (
        f"• Отчет сгенерирован: {get_moscow_time().strftime('%d.%m.%Y %H:%M')}"
    )

    return text_content, user_downloads, total_downloads


async def generate_users_id_file() -> str:
    """Генерирует текстовое содержимое со списком ID пользователей"""
    users = await User.all()

    text_content = ""
    for i, user in enumerate(users, 1):
        text_content += f"{user.telegram_id}\n"

    return text_content


def get_moscow_time() -> datetime:
    """
    Упрощенная версия получения московского времени
    """
    return datetime.now(MOSCOW_TZ)


_subscription_config = {
    "active": False,
    "channel_id": None,
    "channel_name": "",
    "channel_url": "",
    "required_subscribers": 0,
    "current_count": 0
}

processed_users = set()


def set_subscription_config(config: Dict[str, Any]) -> None:
    """Устанавливает конфигурацию подписки (вызывается админом)"""
    global _subscription_config
    _subscription_config = config


def get_subscription_config() -> Dict[str, Any]:
    """Возвращает текущую конфигурацию подписки"""
    global _subscription_config
    return _subscription_config.copy()


async def check_user_subscription(bot, user_id: int, channel_id: int) -> bool:
    """Проверяет, подписан ли пользователь на канал"""
    try:
        member = await bot.get_chat_member(channel_id, user_id)
        return member.status not in ["left", "kicked"]
    except Exception as e:
        logger.error(f"Ошибка проверки подписки для пользователя {user_id}: {e}")
        return False


async def increment_subscription_counter(user_id: int, bot) -> bool:
    """
    Увеличивает счетчик подписчиков для конкретного пользователя
    Возвращает True если подписка отключена из-за достижения лимита
    """
    global _subscription_config, processed_users

    config = get_subscription_config()

    if not config["active"]:
        return False

    if user_id in processed_users:
        return False

    processed_users.add(user_id)

    config["current_count"] += 1

    set_subscription_config(config)

    logger.info(
        f"📊 Счетчик подписчиков увеличен пользователем {user_id}: {config['current_count']}/{config['required_subscribers']}")

    # Проверяем, достигнут ли лимит
    if config["current_count"] >= config["required_subscribers"]:
        config["active"] = False
        set_subscription_config(config)
        processed_users.clear()  # Очищаем список обработанных пользователей
        logger.info(
            f"✅ Обязательная подписка отключена. Достигнут лимит: {config['current_count']}/{config['required_subscribers']}")
        return True

    return False


def is_user_processed(user_id: int) -> bool:
    """Проверяет, нажимал ли пользователь кнопку 'Я подписался'"""
    global processed_users
    return user_id in processed_users


def mark_user_processed(user_id: int):
    """Помечает пользователя как обработанного"""
    global processed_users
    processed_users.add(user_id)


def create_subscription_keyboard(channel_name: str, channel_url: str) -> InlineKeyboardBuilder:
    """Создает клавиатуру для подписки"""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=f"📢 Подписаться на {channel_name}",
        url=channel_url
    )
    builder.button(
        text="✅ Я подписался",
        callback_data="check_subscription"
    )
    builder.adjust(1)
    return builder
