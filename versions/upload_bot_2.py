import asyncio
import logging
from pathlib import Path
from typing import List
from PIL import Image, UnidentifiedImageError
from telegram import Bot, InputMediaPhoto
from telegram.error import RetryAfter, TimedOut, NetworkError, TelegramError
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# -------------------- Настройки --------------------
BOT_TOKEN = "8307958255:AAHOXk2JP0_j-lBh8Xf48lRX180J7GKRMpQ"   # Вставьте сюда токен бота
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

def is_image_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in VALID_IMAGE_EXTS

def find_images(root: Path) -> List[Path]:
    if not root.exists():
        raise FileNotFoundError(f"Папка не найдена: {root}")
    images = [p for p in root.rglob("*") if is_image_file(p)]
    images.sort()
    return images

def compress_image(src: Path) -> Path:
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
        logger.warning(f"Файл не распознан как изображение и будет пропущен: {src}")
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
            logger.exception(f"TelegramError при отправке альбома: {e}")
            raise
        except Exception as e:
            logger.exception(f"Неожиданная ошибка при отправке альбома: {e}")
            raise

# -------------------- Основная логика с прогресс-баром --------------------
async def process_upload(channel_link: str, photos_root: Path, progress_bar, status_label, root):
    if not BOT_TOKEN:
        messagebox.showerror("Ошибка", "Укажите BOT_TOKEN в коде.")
        return

    logger.info(f"Выбрана папка: {photos_root}")
    bot = Bot(BOT_TOKEN)
    images = find_images(photos_root)

    if not images:
        messagebox.showinfo("Информация", "Изображения не найдены.")
        return

    logger.info(f"Найдено изображений: {len(images)}. Начинаем сжатие...")
    compressed_paths: List[Path] = []
    progress_bar["value"] = 0
    progress_bar["maximum"] = len(images)

    for idx, src in enumerate(images, start=1):
        dst = compress_image(src)
        if dst is not None and dst.exists():
            compressed_paths.append(dst)
        progress_bar["value"] = idx
        status_label.config(text=f"Сжато файлов: {idx}/{len(images)}")
        root.update_idletasks()

    if not compressed_paths:
        messagebox.showinfo("Информация", "После сжатия не осталось файлов.")
        return

    batches = chunk_list(compressed_paths, MEDIA_GROUP_SIZE)
    sent_count = 0
    total_batches = len(batches)
    total_files = len(compressed_paths)
    progress_bar["value"] = 0
    progress_bar["maximum"] = total_files

    for idx, batch in enumerate(batches, start=1):
        media_group = []
        for p in batch:
            try:
                media_group.append(InputMediaPhoto(media=open(str(p), "rb")))
            except Exception as e:
                logger.exception(f"Не удалось подготовить файл {p}: {e}")
        if not media_group:
            continue

        logger.info(f"Отправляем альбом {idx}/{total_batches}...")
        try:
            await safe_send_media_group(bot, channel_link, media_group)
            sent_count += len(media_group)
        except Exception as e:
            logger.exception(f"Ошибка при отправке альбома {idx}: {e}")

        progress_bar["value"] = sent_count
        status_label.config(text=f"Отправлено файлов: {sent_count}/{total_files}")
        root.update_idletasks()

        await asyncio.sleep(ALBUM_DELAY_SECONDS)

    messagebox.showinfo("Готово", f"Отправлено изображений: {sent_count}")

# -------------------- GUI --------------------
def start_gui():
    def choose_folder():
        channel_link = entry_channel.get().strip()
        if not channel_link:
            messagebox.showwarning("Предупреждение", "Введите ссылку или ID канала!")
            return
        folder_path = filedialog.askdirectory(title="Выберите папку с файлами")
        if folder_path:
            asyncio.run(process_upload(channel_link, Path(folder_path), progress_bar, status_label, root))

    root = tk.Tk()
    root.title("Загрузчик фото в Telegram")
    root.geometry("500x200")

    tk.Label(root, text="Ссылка на канал или группу:").pack(pady=5)
    entry_channel = tk.Entry(root, width=50)
    entry_channel.pack()
    entry_channel.insert(0, "-1002727935304")  # значение по умолчанию

    tk.Button(root, text="Выбрать папку с файлами", command=choose_folder).pack(pady=10)

    progress_bar = ttk.Progressbar(root, orient="horizontal", length=400, mode="determinate")
    progress_bar.pack(pady=5)

    status_label = tk.Label(root, text="Ожидание...")
    status_label.pack()

    root.mainloop()

if __name__ == "__main__":
    start_gui()
