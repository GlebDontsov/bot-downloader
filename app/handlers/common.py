"""
Общие хендлеры бота
"""

from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command, CommandStart
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.models import User
from app.services.user_service import UserService
from app.services.logger import get_logger
from app.utils.funcs import format_file_size

logger = get_logger(__name__)
router = Router()


@router.message(CommandStart())
async def start_handler(message: Message, user: User):
    """Обработчик команды /start"""

    welcome_text = f"""
🎬 Привет, {user.full_name}! 👋

✨ Этот бот поможет вам скачивать видео с YouTube быстро и удобно.

<b>🌟 Что я умею:</b>
• 📥 Скачивать видео с YouTube
• 🎵 Конвертировать в MP3 (аудиодорожку)
• 📊 Показывать детальную информацию о видео
• 📈 Вести вашу персональную статистику

<b>🌐 Откуда можно скачивать?</b>
⚡ YouTube
⚡ TikTok
⚡ RuTube
⚡ VK

<b>🚀 Как пользоваться:</b>
🎯 Шаг 1: Отправьте мне ссылку на видео
🎯 Шаг 2: Выберите желаемое качество и формат
🎯 Шаг 3: Получите готовый файл мгновенно!

<b>💫 Наши преимущества:</b>
🧡 Доступ к видео без блокировок и VPN
🧡 Скачивай сейчас - смотри позже, даже оффлайн
🧡 Бесплатные скачивания за считанные секунды
🧡 Можно просматривать видео в фоновом режиме

<b>📋 Основные команды:</b>
/help - 📖 Помощь и инструкции
/stats - 📊 Ваша статистика скачиваний
/history - 🕐 История загрузок

🎉 Просто отправьте мне ссылку на видео, чтобы начать! 🚀
    """

    # Создаем клавиатуру с полезными ссылками
    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Помощь", callback_data="help")
    builder.button(text="📊 Статистика", callback_data="stats")
    builder.adjust(2)

    await message.answer(
        welcome_text, reply_markup=builder.as_markup(), parse_mode="HTML"
    )

    logger.info(f"Пользователь {user.telegram_id} запустил бота")


@router.message(Command("help"))
async def help_handler(message: Message, user: User):
    """Обработчик команды /help"""

    help_text = """
📖 <b>Руководство по использованию</b> 📚

<b>🎬 Скачивание видео:</b>
🎯 Отправьте ссылку на видео
🎯 Поддерживаются соц. сети: YouTube, TikTok

<b>📱 Поддерживаемые форматы:</b>
🎥 MP4 (видео) - рекомендуется
🎵 MP3 (только аудио)

<b>🔧 Основные команды:</b>
/start - 🏠 Главное меню
/help - ❓ Эта справка
/stats - 📊 Ваша статистика
/history - 📜 История скачиваний

<b>🆘 Возникли проблемы?</b>
🔍 Убедитесь, что ссылка корректная
🔍 Проверьте, что видео доступно
🔍 Попробуйте другое качество

<i>💡 Если проблема не решается, обратитесь к администратору.</i>
    """

    await message.answer(help_text, parse_mode="HTML")


@router.message(Command("stats"))
async def stats_handler(message: Message, user: User):
    """Обработчик команды /stats"""

    stats = await UserService.get_user_stats(user.telegram_id)
    if not stats:
        await message.answer("❌ Не удалось получить статистику")
        return

    # Форматируем размер файлов
    size_text = format_file_size(stats["total_download_size"])

    stats_text = f"""
📊 <b>Ваша статистика</b>

👤 <b>Пользователь:</b> {stats["full_name"]}
🆔 <b>ID:</b> <code>{stats["user_id"]}</code>

📥 <b>Всего скачиваний:</b> {stats["total_downloads"]}
💾 <b>Общий размер:</b> {size_text}
📅 <b>Сегодня:</b> {stats["today_downloads"]}
📈 <b>За неделю:</b> {stats["week_downloads"]}

🕐 <b>Регистрация:</b> {stats["created_at"].strftime("%d.%m.%Y %H:%M")}
    """

    if stats["last_download"]:
        stats_text += f"\n⏰ <b>Последнее скачивание:</b> {stats['last_download'].strftime('%d.%m.%Y %H:%M')}"

    if stats["is_admin"]:
        stats_text += "\n\n👑 <b>Администратор</b>"

    await message.answer(stats_text, parse_mode="HTML")


@router.message(Command("history"))
async def history_handler(message: Message, user: User):
    """Обработчик команды /history"""

    # Получаем последние 10 скачиваний
    downloads = (
        await user.download_history.filter()
        .order_by("-created_at")
        .limit(10)
        .prefetch_related("video")
    )

    if not downloads:
        await message.answer("📭 У вас пока нет истории скачиваний")
        return

    history_text = "📋 <b>История скачиваний</b>\n\n"

    for download in downloads:
        status_emoji = {
            "completed": "✅",
            "failed": "❌",
            "downloading": "⏳",
            "pending": "🕐",
            "cancelled": "🚫",
        }.get(download.status, "❓")

        date_str = download.created_at.strftime("%d.%m %H:%M")
        title = (
            download.video.title[:50] + "..."
            if len(download.video.title) > 50
            else download.video.title
        )

        history_text += f"{status_emoji} <b>{title}</b>\n"
        history_text += f"    📅 {date_str} | {download.quality or 'авто'} | {download.format_type}\n\n"

    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Обновить", callback_data="history_refresh")

    await message.answer(
        history_text, reply_markup=builder.as_markup(), parse_mode="HTML"
    )


@router.callback_query(F.data == "help")
async def help_callback(callback, user: User):
    """Обработчик callback для помощи"""
    await callback.message.edit_text(
        "📖 Для получения подробной помощи используйте команду /help", parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "stats")
async def stats_callback(callback, user: User):
    """Обработчик callback для статистики"""
    await callback.message.edit_text(
        "📊 Для получения подробной статистики используйте команду /stats",
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "history_refresh")
async def history_refresh_callback(callback, user: User):
    """Обработчик обновления истории"""
    await callback.answer("🔄 Обновляем историю...")
    # Имитируем обновление истории
    await callback.message.edit_text(
        "📋 История обновлена! Используйте команду /history для просмотра.",
        parse_mode="HTML",
    )
