import asyncio
import logging
from pathlib import Path
from typing import List
from PIL import Image, UnidentifiedImageError
from telegram import Bot, InputMediaPhoto
from telegram.error import RetryAfter, TimedOut, NetworkError, TelegramError
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import shutil

# -------------------- Настройки --------------------
BOT_TOKEN = "8307958255:AAHOXk2JP0_j-lBh8Xf48lRX180J7GKRMpQ"
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

# -------------------- Основная логика с двумя прогресс-барами --------------------
async def process_upload(channel_link: str, photos_root: Path, pb_compress, lbl_compress, pb_upload, lbl_upload, root):
    if not BOT_TOKEN:
        messagebox.showerror("Ошибка", "Укажите BOT_TOKEN в коде.")
        return

    bot = Bot(BOT_TOKEN)
    images = find_images(photos_root)

    if not images:
        messagebox.showinfo("Информация", "Изображения не найдены.")
        return

    # ---- Сжатие ----
    compressed_paths: List[Path] = []
    pb_compress["value"] = 0
    pb_compress["maximum"] = len(images)

    for idx, src in enumerate(images, start=1):
        dst = compress_image(src)
        if dst is not None and dst.exists():
            compressed_paths.append(dst)
        pb_compress["value"] = idx
        lbl_compress.config(text=f"Сжато: {idx}/{len(images)}")
        root.update_idletasks()

    if not compressed_paths:
        messagebox.showinfo("Информация", "После сжатия не осталось файлов.")
        return

    # ---- Отправка ----
    batches = chunk_list(compressed_paths, MEDIA_GROUP_SIZE)
    sent_count = 0
    total_files = len(compressed_paths)
    pb_upload["value"] = 0
    pb_upload["maximum"] = total_files

    for batch in batches:
        media_group = []
        for p in batch:
            try:
                media_group.append(InputMediaPhoto(media=open(str(p), "rb")))
            except Exception as e:
                logger.exception(f"Не удалось подготовить файл {p}: {e}")

        if not media_group:
            continue

        try:
            await safe_send_media_group(bot, channel_link, media_group)
            sent_count += len(media_group)

            # Удаляем сжатые файлы после отправки
            for p in batch:
                if p.exists():
                    try:
                        p.unlink()
                    except Exception as e:
                        logger.warning(f"Не удалось удалить временный файл {p}: {e}")

        except Exception as e:
            logger.exception(f"Ошибка при отправке: {e}")

        pb_upload["value"] = sent_count
        lbl_upload.config(text=f"Отправлено: {sent_count}/{total_files}")
        root.update_idletasks()

        await asyncio.sleep(ALBUM_DELAY_SECONDS)

    # ---- Удаляем временную папку после завершения ----
    if TEMP_DIR.exists():
        try:
            shutil.rmtree(TEMP_DIR)
        except Exception as e:
            logger.warning(f"Не удалось удалить временную папку {TEMP_DIR}: {e}")

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
            asyncio.run(process_upload(
                channel_link, Path(folder_path),
                pb_compress, lbl_compress,
                pb_upload, lbl_upload,
                root
            ))

    root = tk.Tk()
    root.title("Загрузчик фото в Telegram")
    root.geometry("500x250")

    tk.Label(root, text="Ссылка на канал или группу:").pack(pady=5)
    entry_channel = tk.Entry(root, width=50)
    entry_channel.pack()
    entry_channel.insert(0, "-1002727935304")

    tk.Button(root, text="Выбрать папку с файлами", command=choose_folder).pack(pady=10)

    tk.Label(root, text="Сжатие файлов:").pack()
    pb_compress = ttk.Progressbar(root, orient="horizontal", length=400, mode="determinate")
    pb_compress.pack(pady=2)
    lbl_compress = tk.Label(root, text="Ожидание...")
    lbl_compress.pack()

    tk.Label(root, text="Отправка в Telegram:").pack()
    pb_upload = ttk.Progressbar(root, orient="horizontal", length=400, mode="determinate")
    pb_upload.pack(pady=2)
    lbl_upload = tk.Label(root, text="Ожидание...")
    lbl_upload.pack()

    root.mainloop()

if __name__ == "__main__":
    start_gui()
