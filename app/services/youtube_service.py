"""
Сервис для работы с YouTube
"""

import os
import re
import asyncio
import tempfile
from pathlib import Path
from typing import Optional, Dict, List, Any
from datetime import datetime

import yt_dlp
import aiofiles.os as aio_os
from tortoise.exceptions import DoesNotExist
from tortoise.expressions import Q

from app.models import Video, User, DownloadHistory, DownloadStatus
from app.config.settings import settings
from app.services.logger import get_logger
from app.utils.funcs import format_file_size, get_moscow_time
from app.utils.constants import MOSCOW_TZ

logger = get_logger(__name__)


class YouTubeService:
    """Сервис для работы с YouTube"""

    def __init__(self):
        self.download_path = Path(settings.download_path)
        self.download_path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def extract_video_id(url: str) -> Optional[str]:
        """Извлекает ID видео из URL YouTube, TikTok, Rutube и VK"""
        patterns = [
            # YouTube patterns
            r"(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]+)",
            r"(?:https?://)?(?:www\.)?youtu\.be/([a-zA-Z0-9_-]+)",
            r"(?:https?://)?(?:www\.)?youtube\.com/embed/([a-zA-Z0-9_-]+)",
            r"(?:https?://)?(?:www\.)?youtube\.com/v/([a-zA-Z0-9_-]+)",
            r"(?:https?://)?(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]+)",
            # TikTok patterns
            r"(?:https?://)?(?:www\.|vm\.|vt\.)?tiktok\.com/@[^/]+/video/(\d+)",
            r"(?:https?://)?(?:vm\.|vt\.)?tiktok\.com/([A-Za-z0-9]+)",
            r"(?:https?://)?(?:www\.)?tiktok\.com/t/([a-zA-Z0-9]+)/",
            r"(?:https?://)?m\.tiktok\.com/v/(\d+)\.html",
            # Rutube patterns
            r"(?:https?://)?(?:www\.)?rutube\.ru/video/([a-f0-9]+)/?",
            r"(?:https?://)?(?:www\.)?rutube\.ru/shorts/([a-f0-9]+)/?",
            r"(?:https?://)?(?:www\.)?rutube\.ru/video/([a-f0-9]+)\?",
            r"(?:https?://)?(?:www\.)?rutube\.ru/shorts/([a-f0-9]+)\?",
            # VK patterns
            r"(?:https?://)?(?:www\.)?vk\.com/video(-?\d+_\d+)",
            r"(?:https?://)?(?:www\.)?vk\.com/vkvideo\?z=video(-?\d+_\d+)",
            r"(?:https?://)?(?:www\.)?vk\.com/clip(-?\d+_\d+)",
            r"(?:https?://)?(?:www\.)?vk\.com/shvideo\?.*?z=clip(-?\d+_\d+)",
            r"(?:https?://)?(?:www\.)?vk\.com/search/video\?.*?z=video(-?\d+_\d+)",
            r"(?:https?://)?(?:www\.)?vkvideo\.ru/video(-?\d+_\d+)",
            r"(?:https?://)?(?:www\.)?vkvideo\.ru/playlist/[^/]+/video(-?\d+_\d+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)

        return None

    @staticmethod
    def is_valid_url(url: str) -> bool:
        """Проверяет, является ли URL валидным YouTube URL"""
        return YouTubeService.extract_video_id(url) is not None

    async def get_video_info(self, url: str) -> Optional[Dict[str, Any]]:
        """Получает информацию о видео"""
        try:
            video_id = self.extract_video_id(url)
            if not video_id:
                return None

            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "extractaudio": False,
                "format": "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = await asyncio.get_event_loop().run_in_executor(
                    None, ydl.extract_info, url, False
                )
                return info

        except Exception as e:
            logger.error(f"Ошибка получения информации о видео {url}: {e}")
            return None

    async def get_or_create_video(self, url: str) -> Optional[Video]:
        """Получает или создает запись видео в базе данных"""
        try:
            video_id = self.extract_video_id(url)
            if not video_id:
                return None

            # Проверяем, есть ли видео в базе
            try:
                video = await Video.get(video_id=video_id)
                return video
            except DoesNotExist:
                pass

            # Получаем информацию о видео
            info = await self.get_video_info(url)
            if not info:
                return None

            # Проверяем ограничения
            duration = info.get("duration", 0)
            if duration and duration > settings.max_video_duration:
                logger.warning(f"Видео {video_id} слишком длинное: {duration}s")
                return None

            # Создаем запись в базе
            upload_date = None
            if info.get("upload_date"):
                try:
                    upload_date_naive = datetime.strptime(
                        info["upload_date"], "%Y%m%d"
                    ).date()

                    upload_date = MOSCOW_TZ.localize(
                        datetime.combine(upload_date_naive, datetime.min.time())
                    ).date()
                except ValueError:
                    pass

            video = await Video.create(
                video_id=video_id,
                url=url,
                title=info.get("title", "Без названия"),
                description=info.get("description"),
                duration=duration,
                view_count=info.get("view_count"),
                like_count=info.get("like_count"),
                channel_name=info.get("uploader") or info.get("channel"),
                channel_id=info.get("channel_id"),
                upload_date=upload_date,
                thumbnail_url=info.get("thumbnail"),
                available_formats=self._extract_formats(info),
            )

            logger.info(f"Создано новое видео: {video}")
            return video

        except Exception as e:
            logger.error(f"Ошибка создания видео {url}: {e}")
            return None

    @staticmethod
    def _extract_formats(info: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Извлекает доступные форматы видео"""
        formats = []
        if "formats" in info:
            for fmt in info["formats"]:
                if fmt.get("vcodec") != "none":  # Только видео форматы
                    file_size = fmt.get("filesize", 0)

                    # Если filesize отсутствует, пытаемся рассчитать
                    if not file_size and fmt.get("tbr") and info.get("duration"):
                        file_size = int((fmt["tbr"] * 1000 * info["duration"]) / 8)

                    formats.append(
                        {
                            "format_id": fmt.get("format_id"),
                            "ext": fmt.get("ext"),
                            "quality": fmt.get("quality"),
                            "height": fmt.get("height"),
                            "width": fmt.get("width"),
                            "filesize": file_size,
                            "fps": fmt.get("fps"),
                            "vcodec": fmt.get("vcodec"),
                            "acodec": fmt.get("acodec"),
                        }
                    )
        return formats

    @staticmethod
    async def get_existing_download(
        video: Video, quality: str, format_type: str
    ) -> Optional[DownloadHistory]:
        """Возвращает существующую запись о скачивании если она есть"""
        return (
            await DownloadHistory.filter(
                video=video,
                quality=quality,
                format_type=format_type,
                status=DownloadStatus.COMPLETED,
            )
            .filter(
                Q(file_path__not_isnull=True) | Q(telegram_file_id__not_isnull=True)
            )
            .first()
        )

    async def download_video(
        self,
        video: Video,
        user: User,
        existing_download: Optional[DownloadHistory],
        quality: str = "720p",
        format_type: str = "mp4",
        file_size: int = settings.max_file_size,
    ) -> Optional[DownloadHistory]:
        """Скачивает видео"""
        # Создаем запись о скачивании
        download = await DownloadHistory.create(
            user=user,
            video=video,
            quality=quality,
            format_type=format_type,
            status=DownloadStatus.PENDING,
        )

        if existing_download:
            download.telegram_file_id = existing_download.telegram_file_id
            download.status = existing_download.status
            download.file_size = existing_download.file_size
            await download.save(
                update_fields=["status", "file_size", "telegram_file_id"]
            )

            await self.update_download_statistics(video, user, file_size)

            logger.info(
                f"Видео уже было скачано ранее: {video.title} в качестве {quality} для пользователя {user.telegram_id}"
            )
            return existing_download

        return await self.start_new_download(
            download, video, user, quality, format_type, file_size
        )

    async def start_new_download(
        self,
        download: DownloadHistory,
        video: Video,
        user: User,
        quality: str,
        format_type: str,
        file_size: int,
    ) -> DownloadHistory:
        """Начинает новое скачивание видео"""
        try:
            await download.mark_as_started()

            # Создаем временную папку для скачивания
            temp_dir = tempfile.mkdtemp(dir=self.download_path)

            # Настройки yt-dlp
            output_template = os.path.join(temp_dir, "%(title)s.%(ext)s")

            # Определяем формат для скачивания
            if format_type == "mp3":
                # Для аудио формата
                format_selector = "bestaudio/best"
            else:
                format_selector = f"bestvideo[height<={quality}][vcodec^=avc1]+bestaudio/best[height<={quality}]/best"

            ydl_opts = {
                "format": format_selector,
                "outtmpl": output_template,
                "quiet": True,
                "no_warnings": True,
                "writeinfojson": False,
                "writesubtitles": False,
                "writeautomaticsub": False,
                "ignoreerrors": False,
                "extractaudio": format_type == "mp3",
                "audioformat": "mp3" if format_type == "mp3" else None,
                "audioquality": "192" if format_type == "mp3" else None,
            }

            # Добавляем ограничение размера файла
            if settings.max_file_size and format_type != "mp3":
                ydl_opts["max_filesize"] = settings.max_file_size

            if file_size > settings.max_file_size:
                raise ValueError(f"Файл слишком большой: {format_file_size(file_size)}")

            # Скачиваем видео
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                await asyncio.get_event_loop().run_in_executor(
                    None, ydl.download, [video.url]
                )

            # Находим скачанный файл
            downloaded_files = list(Path(temp_dir).glob("*"))
            if not downloaded_files:
                raise Exception("Файл не был скачан")

            downloaded_file = downloaded_files[0]
            actual_file_size = downloaded_file.stat().st_size

            # Завершаем скачивание
            await download.mark_as_completed(
                file_path=str(downloaded_file), file_size=actual_file_size
            )

            await self.update_download_statistics(video, user, actual_file_size)

            # Обновляем информацию о видео
            if not video.file_size:
                video.file_size = actual_file_size
                video.quality = quality
                video.format_id = format_type
                await video.save(update_fields=["file_size", "quality", "format_id"])

            logger.info(
                f"Видео успешно скачано: {video.title} для пользователя {user.telegram_id}"
            )
            return download

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Ошибка скачивания видео {video.video_id}: {error_msg}")
            await download.mark_as_failed(error_msg)
            return download

    @staticmethod
    async def update_download_statistics(video: Video, user: User, file_size: int):
        await video.increment_download_count()
        await user.increment_downloads(file_size)

    async def get_available_qualities(self, video: Video) -> List[Dict[str, Any]]:
        """Получает доступные качества для скачивания"""
        if not video.available_formats:
            return []

        # Группируем форматы по качеству
        qualities = {}
        for fmt in video.available_formats:
            height = fmt.get("height")
            if height:
                quality_name = f"{height}p"
                if quality_name not in qualities:
                    qualities[quality_name] = {
                        "name": quality_name,
                        "height": height,
                        "format_id": fmt.get("format_id"),
                        "filesize": fmt.get("filesize"),
                        "ext": fmt.get("ext", "mp4"),
                    }

        # Сортируем по качеству (по убыванию)
        return sorted(qualities.values(), key=lambda x: x["height"], reverse=True)

    @staticmethod
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

    @staticmethod
    async def get_download_stats() -> Dict[str, Any]:
        """Получает статистику скачиваний"""
        total_downloads = await DownloadHistory.all().count()
        completed_downloads = await DownloadHistory.filter(
            status=DownloadStatus.COMPLETED
        ).count()
        failed_downloads = await DownloadHistory.filter(
            status=DownloadStatus.FAILED
        ).count()

        # Статистика за сегодня
        today = get_moscow_time().date()
        today_downloads = await DownloadHistory.filter(created_at__gte=today).count()

        # Популярные видео
        popular_videos = await Video.all().order_by("-download_count").limit(10)

        return {
            "total_downloads": total_downloads,
            "completed_downloads": completed_downloads,
            "failed_downloads": failed_downloads,
            "success_rate": (completed_downloads / total_downloads * 100)
            if total_downloads > 0
            else 0,
            "today_downloads": today_downloads,
            "popular_videos": [
                {
                    "title": v.title,
                    "download_count": v.download_count,
                    "url": v.url,
                }
                for v in popular_videos
            ],
        }
