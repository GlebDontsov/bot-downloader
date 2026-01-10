from typing import Callable, Dict, Any, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery

from app.services.logger import get_logger
from app.utils.funcs import (
    get_subscription_config,
    check_user_subscription,
    create_subscription_keyboard,
    increment_subscription_counter,
    processed_users,
)

logger = get_logger(__name__)


class SubscriptionMiddleware(BaseMiddleware):
    async def __call__(
            self,
            handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
            event: TelegramObject,
            data: Dict[str, Any],
    ) -> Any:
        config = get_subscription_config()

        if not config.get("active", False) or not config.get("channel_id"):
            return await handler(event, data)

        db_user = data.get("user")
        if not db_user:
            return await handler(event, data)

        if hasattr(db_user, 'is_admin') and db_user.is_admin:
            return await handler(event, data)

        bot = data.get("bot")
        if not bot:
            logger.error("Bot instance not found in data")
            return await handler(event, data)

        user_id = db_user.telegram_id

        if user_id in processed_users:
            return await handler(event, data)

        # Проверяем подписку
        user_subscribed = await check_user_subscription(
            bot,
            user_id,
            config["channel_id"]
        )

        if user_subscribed and user_id in processed_users:
            return await handler(event, data)

        if isinstance(event, CallbackQuery) and event.data == "check_subscription":
            user_subscribed = await check_user_subscription(
                bot,
                user_id,
                config["channel_id"]
            )

            if user_subscribed:
                # Увеличиваем счетчик и проверяем лимит
                subscription_disabled = await increment_subscription_counter(user_id, bot)

                await event.answer("✅ Спасибо за подписку!", show_alert=True)
                try:
                    await event.message.delete()
                except:
                    pass

                return await handler(event, data)
            else:
                await event.answer(
                    "❌ Вы все еще не подписаны на канал. Пожалуйста, подпишитесь и попробуйте снова.",
                    show_alert=True
                )
                return

        if isinstance(event, Message) and event.text and event.text.startswith('/check_subscription'):
            return await handler(event, data)

        # Показываем сообщение о необходимости подписки
        message_text = (
            f"🔔 <b>Обязательная подписка!</b>\n\n"
            f"Для дальнейшего использования бота необходимо подписаться на наш канал: {config['channel_name']}\n"
            f"🔗 {config['channel_url']}\n\n"
            f"💡 <b>Что нужно сделать:</b>\n"
            f"1. Нажмите на кнопку «Подписаться на канал»\n"
            f"2. Подпишитесь на канал\n"
            f"3. Вернитесь и нажмите «Я подписался»\n\n"
            f"<i>✅ Это займет всего 30 секунд, после чего все функции бота снова будут доступны!</i>"
        )

        keyboard = create_subscription_keyboard(
            config["channel_name"],
            config["channel_url"]
        )

        if isinstance(event, Message):
            await event.answer(
                message_text,
                reply_markup=keyboard.as_markup(),
                parse_mode="HTML"
            )
        elif isinstance(event, CallbackQuery):
            try:
                await event.message.edit_text(
                    message_text,
                    reply_markup=keyboard.as_markup(),
                    parse_mode="HTML"
                )
            except:
                await event.message.answer(
                    message_text,
                    reply_markup=keyboard.as_markup(),
                    parse_mode="HTML"
                )
            await event.answer()

        return None