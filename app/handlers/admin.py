"""
Хендлеры для администраторов
"""

import os
import asyncio
from pathlib import Path

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext

from app.models import User
from app.services.user_service import UserService
from app.services.youtube_service import YouTubeService
from app.services.logger import get_logger
from app.middlewares import AdminMiddleware
from app.states.broadcast_states import BroadcastStates
from app.utils.funcs import (
    generate_stats_file,
    generate_users_id_file,
    get_moscow_time,
    cleanup_all_files,
    set_subscription_config,
    get_subscription_config,
)

logger = get_logger(__name__)
router = Router()

# Инициализируем сервисы
user_service = UserService()
youtube_service = YouTubeService()

router.message.middleware(AdminMiddleware())
router.callback_query.middleware(AdminMiddleware())


@router.message(Command("admin"))
async def admin_panel(message: Message, user: User):
    """Главная панель администратора"""

    # Получаем статистику
    stats = await youtube_service.get_download_stats()
    users_count = await user_service.get_users_count()
    active_users = await user_service.get_active_users_count()

    admin_text = f"""
👑 <b>Панель администратора</b>

📊 <b>Статистика:</b>
• 👥 Всего пользователей: {users_count}
• 🟢 Активных за неделю: {active_users}
• 📥 Всего скачиваний: {stats["total_downloads"]}
• ✅ Успешных: {stats["completed_downloads"]}
• ❌ Ошибок: {stats["failed_downloads"]}
• 📈 Успешность: {stats["success_rate"]:.1f}%
• 📅 Сегодня: {stats["today_downloads"]}

Выберите действие:
    """

    builder = InlineKeyboardBuilder()
    builder.button(text="👥 Пользователи", callback_data="admin_users")
    builder.button(text="📊 Подробная статистика", callback_data="admin_stats")
    builder.button(text="📹 Популярные видео", callback_data="admin_videos")
    builder.button(text="🧹 Очистка файлов", callback_data="admin_cleanup")
    builder.button(text="📢 Рассылка", callback_data="admin_broadcast")
    builder.button(text="⚙️ Настройки", callback_data="admin_settings")
    builder.adjust(2)

    await message.answer(
        admin_text, reply_markup=builder.as_markup(), parse_mode="HTML"
    )


@router.callback_query(F.data == "admin_users")
async def admin_users_callback(callback: CallbackQuery, user: User):
    """Управление пользователями"""

    users = await user_service.get_all_users(limit=20)
    users_count = await user_service.get_users_count()

    users_text = f"👥 <b>Пользователи</b> (показано 20 из {users_count})\n\n"

    for u in users:
        status = "👑" if u.is_admin else "🚫" if u.is_blocked else "👤"
        users_text += f"{status} <b>{u.full_name}</b> (@{u.username or 'нет'})\n"
        users_text += (
            f"    ID: <code>{u.telegram_id}</code> | Скачиваний: {u.total_downloads}\n"
        )
        users_text += f"    Регистрация: {u.created_at.strftime('%d.%m.%Y')}\n\n"

    builder = InlineKeyboardBuilder()
    builder.button(text="🔍 Найти пользователя", callback_data="admin_user_search")
    builder.button(text="📊 Топ пользователей", callback_data="admin_user_top")
    builder.button(text="🚫 Заблокированные", callback_data="admin_user_blocked")
    builder.button(text="◀️ Назад", callback_data="admin_back")
    builder.adjust(1)

    await callback.message.edit_text(
        users_text, reply_markup=builder.as_markup(), parse_mode="HTML"
    )


@router.callback_query(F.data == "admin_stats")
async def admin_stats_callback(callback: CallbackQuery, user: User):
    """Подробная статистика"""

    stats = await youtube_service.get_download_stats()

    stats_text = f"""
📊 <b>Подробная статистика</b>

📥 <b>Скачивания:</b>
• Всего: {stats["total_downloads"]}
• Успешных: {stats["completed_downloads"]}
• Ошибок: {stats["failed_downloads"]}
• Успешность: {stats["success_rate"]:.1f}%
• Сегодня: {stats["today_downloads"]}

🎬 <b>Популярные видео:</b>
    """

    for i, video in enumerate(stats["popular_videos"][:5], 1):
        stats_text += f"{i}. {video['title'][:50]}...\n"
        stats_text += f"   Скачиваний: {video['download_count']}\n\n"

    builder = InlineKeyboardBuilder()
    builder.button(text="📈 Экспорт статистики", callback_data="admin_export_stats")
    builder.button(text="🆔 Экспорт списка пользоватлей", callback_data="admin_export_user_ids_file")
    builder.button(text="◀️ Назад", callback_data="admin_back")
    builder.adjust(1)

    await callback.message.edit_text(
        stats_text, reply_markup=builder.as_markup(), parse_mode="HTML"
    )


@router.callback_query(F.data == "admin_export_stats")
async def admin_export_stats(callback: CallbackQuery, user: User):
    """Экспорт статистики по пользователям за последние 30 дней"""

    # Генерируем имя файла с текущей датой
    moscow_now = get_moscow_time()
    filename = Path(f"stats_30days_{moscow_now.strftime('%Y%m%d_%H%M')}.txt")

    try:
        text_content, user_downloads, total_downloads = await generate_stats_file()
        filename.write_text(text_content, encoding="utf-8")

        # Отправляем файл
        await callback.message.answer_document(
            document=BufferedInputFile(filename.read_bytes(), filename=filename.name),
            caption=f"📊 Статистика скачиваний за последние 30 дней\n"
            f"👥 Пользователей: {len(user_downloads)}\n"
            f"📥 Скачиваний: {total_downloads}",
        )

        await admin_back_callback(callback, user)

    except Exception as e:
        await callback.answer("❌ Ошибка при генерации статистики")
        logger.error(f"Ошибка при генерации статистики: {e}")

    finally:
        if os.path.exists(filename):
            os.remove(filename)


@router.callback_query(F.data == "admin_export_user_ids_file")
async def admin_export_user_ids_file(callback: CallbackQuery, user: User):
    """Экспорт списка пользователей"""

    moscow_now = get_moscow_time()
    filename = Path(f"user_ids_{moscow_now.strftime('%Y%m%d_%H%M')}.txt")

    try:
        text_content = await generate_users_id_file()
        filename.write_text(text_content, encoding="utf-8")

        await callback.message.answer_document(
            document=BufferedInputFile(filename.read_bytes(), filename=filename.name),
            caption=f"👥 Список пользователей бота",
        )

        await admin_back_callback(callback, user)

    except Exception as e:
        await callback.answer("❌ Ошибка при генерации списка пользователей")
        logger.error(f"Ошибка при генерации списка пользователей: {e}")

    finally:
        if os.path.exists(filename):
            os.remove(filename)


@router.callback_query(F.data == "admin_cleanup")
async def admin_cleanup_callback(callback: CallbackQuery, user: User):
    """Очистка файлов"""

    await callback.answer("🧹 Очищаем старые файлы...")

    try:
        cleaned_count = await cleanup_all_files()

        cleanup_text = f"""
🧹 <b>Очистка завершена</b>

✅ Удалено файлов: {cleaned_count}
📁 Все файлы успешно удалены
💾 Освобождено место на диске
"""

    except Exception as e:
        logger.error(f"Ошибка очистки файлов: {e}")
        cleanup_text = "❌ Ошибка при очистке файлов"

    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Назад", callback_data="admin_back")

    await callback.message.edit_text(
        cleanup_text, reply_markup=builder.as_markup(), parse_mode="HTML"
    )


@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_callback(callback: CallbackQuery, user: User):
    """Рассылка сообщений"""

    broadcast_text = """
📢 <b>Рассылка сообщений</b>

Для отправки рассылки используйте команду:
<code>/broadcast</code>

Сообщение будет отправлено всем пользователям.

📢 <b>Обязательная подписка:</b>
Установить обязательную подписку на канал
<code>/set_subscription channel_id "Название" ссылка количество</code>
Пример:<code>/set_subscription -1001234567890 "Мой канал" https://t.me/mychannel 100</code>

Показать текущий статус обязательной подписки:
<code>/subscription_status</code>

Принудительно отключить обязательную подписку:
<code>/disable_subscription</code>

⚠️ <b>Внимание:</b> Используйте команды осторожно!
    """

    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Назад", callback_data="admin_back")

    await callback.message.edit_text(
        broadcast_text, reply_markup=builder.as_markup(), parse_mode="HTML"
    )


@router.message(Command("broadcast"))
async def broadcast_command(message: Message, user: User, state: FSMContext):
    """Команда для начала рассылки"""

    if not user.is_admin:
        await message.answer("❌ Эта команда только для администраторов")
        return

    await message.answer(
        "📢 <b>Режим рассылки</b>\n\n"
        "Отправьте сообщение, которое нужно разослать всем пользователям.\n"
        "Можно отправить:\n"
        "• Текст\n"
        "• Фото с подписью\n"
        "• Видео\n"
        "• Документ\n"
        "• И любой другой тип сообщения\n\n"
        "❌ <i>Для отмены отправьте /cancel</i>",
        parse_mode="HTML"
    )

    await state.set_state(BroadcastStates.waiting_for_post)


@router.message(BroadcastStates.waiting_for_post)
async def process_broadcast_post(message: Message, state: FSMContext, user: User):
    """Обработка полученного поста для рассылки"""

    if message.text and message.text.lower() in ["/cancel", "отмена", "cancel"]:
        await message.answer("❌ Рассылка отменена")
        await state.clear()
        return

    users = await user_service.get_all_users(limit=500_000)

    if not users:
        await message.answer("❌ Нет пользователей для рассылки")
        await state.clear()
        return

    status_msg = await message.answer(f"📢 Начинаем рассылку для {len(users)} пользователей...")

    sent_count = 0
    failed_count = 0
    blocked_count = 0

    for target_user in users:
        if target_user.is_blocked:
            blocked_count += 1
            continue

        try:
            await copy_message_to_user(
                bot=message.bot,
                chat_id=target_user.telegram_id,
                source_message=message
            )
            sent_count += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            logger.error(f"Ошибка отправки пользователю {target_user.telegram_id}: {e}")
            failed_count += 1

    result_text = (
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"• 👥 Всего пользователей: {len(users)}\n"
        f"• 📤 Успешно отправлено: {sent_count}\n"
        f"• 🚫 Пропущено (заблокированы): {blocked_count}\n"
        f"• ❌ Ошибок отправки: {failed_count}\n\n"
        f"<i>Сообщение было отправлено {sent_count} пользователям.</i>"
    )

    await status_msg.edit_text(result_text, parse_mode="HTML")
    await state.clear()


async def copy_message_to_user(bot, chat_id: int, source_message):
    """Копирует любое сообщение пользователю"""

    # Получаем контент из исходного сообщения
    text = source_message.text or source_message.caption or ""
    entities = source_message.entities or source_message.caption_entities
    reply_markup = source_message.reply_markup
    parse_mode = None

    # Фото
    if source_message.photo:
        await bot.send_photo(
            chat_id=chat_id,
            photo=source_message.photo[-1].file_id,
            caption=text,
            caption_entities=entities,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )

    # Видео
    elif source_message.video:
        await bot.send_video(
            chat_id=chat_id,
            video=source_message.video.file_id,
            caption=text,
            caption_entities=entities,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )

    # Документ
    elif source_message.document:
        await bot.send_document(
            chat_id=chat_id,
            document=source_message.document.file_id,
            caption=text,
            caption_entities=entities,
            parse_mode=parse_mode,
            reply_markup = reply_markup,
        )

    # Аудио
    elif source_message.audio:
        await bot.send_audio(
            chat_id=chat_id,
            audio=source_message.audio.file_id,
            caption=text,
            caption_entities=entities,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )

    # Голосовое сообщение
    elif source_message.voice:
        await bot.send_voice(
            chat_id=chat_id,
            voice=source_message.voice.file_id,
            caption=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )

    # Анимация (GIF)
    elif source_message.animation:
        await bot.send_animation(
            chat_id=chat_id,
            animation=source_message.animation.file_id,
            caption=text,
            caption_entities=entities,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )

    elif source_message.sticker:
        # Для стикера отправляем сначала заголовок, потом стикер
        if text:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode
            )
        await bot.send_sticker(
            chat_id=chat_id,
            sticker=source_message.sticker.file_id
        )

    elif source_message.video_note:
        # Для видео-сообщений тоже сначала заголовок
        if text:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode
            )
        await bot.send_video_note(
            chat_id=chat_id,
            video_note=source_message.video_note.file_id
        )

    # Местоположение
    elif source_message.location:
        await bot.send_location(
            chat_id=chat_id,
            latitude=source_message.location.latitude,
            longitude=source_message.location.longitude
        )
        if text:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode
            )

    elif source_message.contact:
        await bot.send_contact(
            chat_id=chat_id,
            phone_number=source_message.contact.phone_number,
            first_name=source_message.contact.first_name,
            last_name=source_message.contact.last_name or ""
        )
        if text:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode
            )

    elif source_message.poll:
        await bot.send_poll(
            chat_id=chat_id,
            question=source_message.poll.question,
            options=[option.text for option in source_message.poll.options],
            is_anonymous=source_message.poll.is_anonymous,
            type=source_message.poll.type
        )
        if text:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode
            )

    else:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            entities=entities,
            parse_mode=parse_mode
        )


@router.callback_query(F.data == "admin_back")
async def admin_back_callback(callback: CallbackQuery, user: User):
    """Возврат к главной панели администратора"""

    # Получаем обновленную статистику
    stats = await youtube_service.get_download_stats()
    users_count = await user_service.get_users_count()
    active_users = await user_service.get_active_users_count()

    admin_text = f"""
👑 <b>Панель администратора</b>

📊 <b>Статистика:</b>
• 👥 Всего пользователей: {users_count}
• 🟢 Активных за неделю: {active_users}
• 📥 Всего скачиваний: {stats["total_downloads"]}
• ✅ Успешных: {stats["completed_downloads"]}
• ❌ Ошибок: {stats["failed_downloads"]}
• 📈 Успешность: {stats["success_rate"]:.1f}%
• 📅 Сегодня: {stats["today_downloads"]}

Выберите действие:
    """

    builder = InlineKeyboardBuilder()
    builder.button(text="👥 Пользователи", callback_data="admin_users")
    builder.button(text="📊 Подробная статистика", callback_data="admin_stats")
    builder.button(text="📹 Популярные видео", callback_data="admin_videos")
    builder.button(text="🧹 Очистка файлов", callback_data="admin_cleanup")
    builder.button(text="📢 Рассылка", callback_data="admin_broadcast")
    builder.button(text="⚙️ Настройки", callback_data="admin_settings")
    builder.adjust(2)

    await callback.message.edit_text(
        admin_text, reply_markup=builder.as_markup(), parse_mode="HTML"
    )


# Команды для управления пользователями
@router.message(Command("ban"))
async def ban_user_command(message: Message, user: User):
    """Команда блокировки пользователя"""

    # Получаем ID пользователя
    text_parts = message.text.split(" ", 1)
    if len(text_parts) < 2:
        await message.answer(
            "❌ Укажите ID пользователя:\n<code>/ban user_id</code>", parse_mode="HTML"
        )
        return

    try:
        target_user_id = int(text_parts[1])
        success = await user_service.block_user(target_user_id)

        if success:
            await message.answer(f"✅ Пользователь {target_user_id} заблокирован")
        else:
            await message.answer(f"❌ Пользователь {target_user_id} не найден")

    except ValueError:
        await message.answer("❌ Неверный формат ID пользователя")


@router.message(Command("unban"))
async def unban_user_command(message: Message, user: User):
    """Команда разблокировки пользователя"""

    # Получаем ID пользователя
    text_parts = message.text.split(" ", 1)
    if len(text_parts) < 2:
        await message.answer(
            "❌ Укажите ID пользователя:\n<code>/unban user_id</code>",
            parse_mode="HTML",
        )
        return

    try:
        target_user_id = int(text_parts[1])
        success = await user_service.unblock_user(target_user_id)

        if success:
            await message.answer(f"✅ Пользователь {target_user_id} разблокирован")
        else:
            await message.answer(f"❌ Пользователь {target_user_id} не найден")

    except ValueError:
        await message.answer("❌ Неверный формат ID пользователя")


@router.message(Command("set_subscription"))
async def set_subscription_command(message: Message, user: User):
    """
    Установка обязательной подписки
    Формат: /set_subscription channel_id "Название канала" https://t.me/link количество
    Пример: /set_subscription -1001234567890 "Мой канал" https://t.me/mychannel 100
    """

    args = message.text.split(maxsplit=4)

    if len(args) < 5:
        await message.answer(
            "❌ Неверный формат команды.\n\n"
            "Используйте:\n"
            "<code>/set_subscription channel_id \"Название канала\" https://t.me/link количество_подписчиков</code>\n\n"
            "Пример:\n"
            "<code>/set_subscription -1001234567890 \"Мой канал\" https://t.me/mychannel 100</code>",
            parse_mode="HTML"
        )
        return

    try:
        channel_id = int(args[1])
        channel_name = args[2].strip('"')
        channel_url = args[3]
        required_subscribers = int(args[4])

        # Создаем новую конфигурацию
        config = {
            "active": True,
            "channel_id": channel_id,
            "channel_name": channel_name,
            "channel_url": channel_url,
            "required_subscribers": required_subscribers,
            "current_count": 0
        }

        # Устанавливаем конфигурацию через функцию из middleware
        set_subscription_config(config)

        await message.answer(
            f"✅ Обязательная подписка установлена!\n\n"
            f"📢 Канал: {channel_name}\n"
            f"🔗 Ссылка: {channel_url}\n"
            f"🎯 Требуется подписчиков: {required_subscribers}\n\n"
            f"Теперь все новые пользователи должны будут подписаться на канал.\n"
            f"Используйте /update_subscription_count для подсчета текущих подписчиков."
        )

    except ValueError as e:
        await message.answer(f"❌ Ошибка в данных: {e}")
    except Exception as e:
        logger.error(f"Ошибка установки подписки: {e}")
        await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("subscription_status"))
async def subscription_status_command(message: Message, user: User):
    """Показывает текущий статус обязательной подписки"""

    config = get_subscription_config()

    if not config["active"]:
        await message.answer("📭 Обязательная подписка отключена.")
        return

    status_text = (
        f"📢 <b>Статус обязательной подписки</b>\n\n"
        f"✅ Активна: Да\n"
        f"📢 Канал: {config['channel_name']}\n"
        f"🆔 ID: <code>{config['channel_id']}</code>\n"
        f"🔗 Ссылка: {config['channel_url']}\n"
        f"🎯 Требуется подписчиков: {config['required_subscribers']}\n"
        f"📊 Текущее количество: {config['current_count']}\n\n"
        f"📈 Прогресс: {config['current_count']}/{config['required_subscribers']} "
        f"({(config['current_count'] / config['required_subscribers'] * 100):.1f}%)"
    )

    await message.answer(status_text, parse_mode="HTML")


@router.message(Command("disable_subscription"))
async def disable_subscription_command(message: Message, user: User):
    """Принудительно отключает обязательную подписку"""

    config = get_subscription_config()

    if not config["active"]:
        await message.answer("❌ Обязательная подписка итак отключена.")
        return

    config["active"] = False
    set_subscription_config(config)

    await message.answer("✅ Обязательная подписка принудительно отключена.")
