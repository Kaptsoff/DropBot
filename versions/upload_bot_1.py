import asyncio
import logging
import sys
from pathlib import Path
from typing import List
from PIL import Image, UnidentifiedImageError
from telegram import Bot, InputMediaPhoto
from telegram.error import RetryAfter, TimedOut, NetworkError, TelegramError
from tkinter import Tk, filedialog

# -------------------- Настройки --------------------
BOT_TOKEN = "8307958255:AAHOXk2JP0_j-lBh8Xf48lRX180J7GKRMpQ"  # Токен бота
CHANNEL_ID = "-1002727935304"  # ID канала или @username
ALBUM_DELAY_SECONDS = 3
MEDIA_GROUP_SIZE = 10
LOG_FILE = "uploader.log"

# -------------------- Логирование --------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ],
)
logger = logging.getLogger("uploader")

# -------------------- Утилиты --------------------
VALID_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
TEMP_DIR = Path(".compressed_tmp")
TEMP_DIR.mkdir(exist_ok=True)

def resource_path(relative_path: str) -> Path:
    """Возвращает путь к ресурсу, работая и в .py, и в .exe."""
    try:
        base_path = sys._MEIPASS  # type: ignore
    except AttributeError:
        base_path = Path(".").resolve()
    return Path(base_path) / relative_path

def choose_photos_folder() -> Path | None:
    """Диалог выбора папки с фото. Возвращает Path или None."""
    root = Tk()
    root.withdraw()
    folder_path = filedialog.askdirectory(title="Выберите папку с фото")
    root.destroy()
    if not folder_path:
        return None
    return Path(folder_path).resolve()

def is_image_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in VALID_IMAGE_EXTS

def find_images(root: Path) -> List[Path]:
    if not root.exists():
        raise FileNotFoundError(f"Папка не найдена: {root}")
    images = [p for p in root.rglob("*") if is_image_file(p)]
    images.sort()
    return images

def compress_image(src: Path) -> Path | None:
    dst = TEMP_DIR / (src.stem + ".jpg")
    try:
        with Image.open(src) as img:
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            max_side = 4096
            w, h = img.size
            scale = min(1.0, max_side / max(w, h))
            if scale < 1.0:
                img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            img.save(dst, format="JPEG", optimize=True, quality=75, progressive=True)
        return dst
    except UnidentifiedImageError:
        logger.warning(f"Пропущен (неизвестный формат): {src}")
        return None
    except Exception as e:
        logger.exception(f"Ошибка при сжатии {src}: {e}")
        return None

def chunk_list(items: List[Path], size: int) -> List[List[Path]]:
    return [items[i:i + size] for i in range(0, len(items), size)]

async def safe_send_media_group(bot: Bot, chat_id: str, media_group: List[InputMediaPhoto]) -> None:
    attempt = 0
    while True:
        try:
            await bot.send_media_group(chat_id=chat_id, media=media_group)
            return
        except RetryAfter as e:
            wait_for = int(getattr(e, "retry_after", 5)) or 5
            logger.warning(f"Лимит запросов. Ждём {wait_for} сек...")
            await asyncio.sleep(wait_for)
        except (TimedOut, NetworkError) as e:
            attempt += 1
            wait_for = min(30, 2 ** attempt)
            logger.warning(f"Сетевая ошибка ({e}). Попытка #{attempt}. Ждём {wait_for} сек...")
            await asyncio.sleep(wait_for)
        except TelegramError as e:
            logger.exception(f"TelegramError: {e}")
            raise
        except Exception as e:
            logger.exception(f"Неожиданная ошибка: {e}")
            raise

# -------------------- Основная логика --------------------
async def run_bot():
    if not BOT_TOKEN or not CHANNEL_ID:
        logger.error("BOT_TOKEN и CHANNEL_ID не указаны.")
        return

    photos_root = choose_photos_folder()
    if photos_root is None:
        logger.info("Папка не выбрана. Выход.")
        return

    logger.info(f"Выбрана папка: {photos_root}")
    bot = Bot(BOT_TOKEN)
    images = find_images(photos_root)

    if not images:
        logger.warning("Изображения не найдены.")
        return

    logger.info(f"Найдено {len(images)} изображений. Сжимаем...")
    compressed_paths: List[Path] = []
    for src in images:
        dst = compress_image(src)
        if dst and dst.exists():
            compressed_paths.append(dst)

    if not compressed_paths:
        logger.warning("После сжатия файлов нет.")
        return

    batches = chunk_list(compressed_paths, MEDIA_GROUP_SIZE)
    sent_count = 0

    for idx, batch in enumerate(batches, start=1):
        media_group = []
        for p in batch:
            try:
                media_group.append(InputMediaPhoto(media=open(str(p), "rb")))
            except Exception as e:
                logger.exception(f"Не удалось открыть {p}: {e}")
        if not media_group:
            logger.warning(f"Альбом {idx} пуст. Пропуск.")
            continue

        logger.info(f"Отправка альбома {idx}/{len(batches)}...")
        try:
            await safe_send_media_group(bot, CHANNEL_ID, media_group)
            sent_count += len(media_group)
            logger.info(f"✅ Отправлено: {sent_count} файлов")
        except Exception as e:
            logger.exception(f"Ошибка при отправке альбома {idx}: {e}")

        await asyncio.sleep(ALBUM_DELAY_SECONDS)

    logger.info(f"Готово! Всего отправлено {sent_count} изображений.")

if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except Exception as e:
        logger.exception(f"Критическая ошибка: {e}")
        input("Нажмите Enter для выхода...")
