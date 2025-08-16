import asyncio
import logging
import shutil
from pathlib import Path
from typing import List

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PIL import Image, UnidentifiedImageError
from telegram import Bot, InputMediaPhoto
from telegram.error import RetryAfter, TimedOut, NetworkError, TelegramError

# -------------------- Настройки --------------------
BOT_TOKEN = "YOUR_TOKEN"
MEDIA_GROUP_SIZE = 10
LOG_FILE = "uploader.log"

# Минимальная и максимальная задержка между альбомами (сек)
MIN_ALBUM_DELAY = 20
MAX_ALBUM_DELAY = 120
DELAY_STEP = 5  # на сколько увеличивать или уменьшать задержку

VALID_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
TEMP_DIR = Path(".compressed_tmp")
TEMP_DIR.mkdir(exist_ok=True)

# -------------------- Логирование --------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("uploader")

# -------------------- Утилиты --------------------
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
        logger.warning(f"Пропущен файл: {src}")
    except Exception as e:
        logger.exception(f"Ошибка сжатия {src}: {e}")
    return None

def chunk_list(items: List[Path], size: int) -> List[List[Path]]:
    return [items[i:i + size] for i in range(0, len(items), size)]

# -------------------- Telegram отправка с динамической задержкой --------------------
async def safe_send_media_group(bot: Bot, chat_id: str, media_group: List[InputMediaPhoto], current_delay: float) -> tuple:
    """Отправляет альбом и регулирует задержку между альбомами.
       Возвращает (delay, sent_successfully)."""
    try:
        await bot.send_media_group(chat_id=chat_id, media=media_group)
        # Если прошло успешно, уменьшаем задержку постепенно
        current_delay = max(MIN_ALBUM_DELAY, current_delay - DELAY_STEP)
        return current_delay, True

    except RetryAfter as e:
        wait_for = int(getattr(e, "retry_after", 5)) or 5
        logger.warning(f"Лимит запросов. Ждём {wait_for} сек...")
        await asyncio.sleep(wait_for)
        current_delay = min(MAX_ALBUM_DELAY, current_delay + DELAY_STEP)
        return current_delay, False  # этот альбом пропускаем

    except (TimedOut, NetworkError) as e:
        logger.warning(f"Сетевая ошибка ({e}). Пропускаем альбом.")
        current_delay = min(MAX_ALBUM_DELAY, current_delay + 1)
        return current_delay, False  # этот альбом пропускаем

    except TelegramError as e:
        logger.exception(f"TelegramError: {e}")
        return current_delay, False

    except Exception as e:
        logger.exception(f"Неожиданная ошибка: {e}")
        return current_delay, False


# -------------------- Основная логика --------------------
async def process_upload(channel_link: str, photos_root: Path,
                         pb_compress, lbl_compress, pb_upload, lbl_upload, root,
                         initial_delay: float, album_size: int):
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

    # ---- Разделяем на альбомы ----
    batches = chunk_list(compressed_paths, album_size)
    sent_count = 0
    total_files = len(compressed_paths)
    pb_upload["value"] = 0
    pb_upload["maximum"] = total_files
    current_delay = initial_delay

    for i, batch in enumerate(batches, start=1):
        media_group = []
        file_objects = []

        for p in batch:
            try:
                f = open(str(p), "rb")
                file_objects.append(f)
                media_group.append(InputMediaPhoto(media=f))
            except Exception as e:
                logger.exception(f"Ошибка подготовки файла {p}: {e}")

        if not media_group:
            continue

        try:
            # Отправка с динамической задержкой
            current_delay, success = await safe_send_media_group(bot, channel_link, media_group, current_delay)

            if success:
                # Обновление прогресса только если альбом реально отправлен
                for f, p in zip(file_objects, batch):
                    sent_count += 1
                    pb_upload["value"] = sent_count
                    lbl_upload.config(text=f"Отправлено: {sent_count}/{total_files}")
                    root.update_idletasks()
                    f.close()
                    if p.exists():
                        try:
                            p.unlink()
                        except Exception as e:
                            logger.warning(f"Не удалось удалить {p}: {e}")
            else:
                logger.info(f"Альбом #{i} пропущен из-за ошибки сети.")
                for f in file_objects:
                    f.close()

            # Делаем паузу между альбомами с текущей задержкой
            if i < len(batches):
                await asyncio.sleep(current_delay)

        except Exception as e:
            logger.exception(f"Ошибка при отправке: {e}")

        # Делаем паузу между альбомами с текущей задержкой
        if i < len(batches):
            await asyncio.sleep(current_delay)

    # ---- Очистка временной папки ----
    if TEMP_DIR.exists():
        try:
            shutil.rmtree(TEMP_DIR)
        except Exception as e:
            logger.warning(f"Не удалось удалить {TEMP_DIR}: {e}")

    messagebox.showinfo("Готово", f"Отправлено изображений: {sent_count}")

# -------------------- GUI --------------------
def start_gui():
    selected_folder = {"path": None}

    def choose_folder():
        folder_path = filedialog.askdirectory(title="Выберите папку с файлами")
        if folder_path:
            selected_folder["path"] = Path(folder_path)
            lbl_folder.config(text=f"Папка: {folder_path}")

    def send_files():
        if not selected_folder["path"]:
            messagebox.showwarning("Ошибка", "Сначала выберите папку с файлами!")
            return
        channel_link = entry_channel.get().strip()
        if not channel_link:
            messagebox.showwarning("Ошибка", "Введите ссылку или ID канала!")
            return
        try:
            album_size = int(scale_album.get())
            initial_delay = float(entry_delay.get())
        except ValueError:
            messagebox.showwarning("Ошибка", "Проверьте значения задержки и размера альбома!")
            return

        asyncio.run(process_upload(channel_link, selected_folder["path"],
                                   pb_compress, lbl_compress, pb_upload, lbl_upload,
                                   root, initial_delay, album_size))

    root = tk.Tk()
    root.title("Загрузчик фото в Telegram")
    root.geometry("500x550+600+250")

    tk.Label(root, text="Ссылка на канал или группу:").pack(pady=5)
    entry_channel = tk.Entry(root, width=50)
    entry_channel.pack()
    entry_channel.insert(0, "example: -1003432432 or @group")

    tk.Label(root, text="Минимальная задержка между альбомами (сек):").pack(pady=5)
    entry_delay = tk.Entry(root, width=10)
    entry_delay.pack()
    entry_delay.insert(0, str(MIN_ALBUM_DELAY))

    tk.Label(root, text="Максимальная задержка между альбомами (сек):").pack(pady=5)
    entry_delay = tk.Entry(root, width=10)
    entry_delay.pack()
    entry_delay.insert(0, str(MAX_ALBUM_DELAY))

    tk.Label(root, text="Шаг задержки (увеличение или уменьшение)").pack(pady=5)
    scale_album = tk.Scale(root, from_=1, to=10, orient="horizontal")
    scale_album.set(DELAY_STEP)
    scale_album.pack()

    tk.Button(root, text="Выбрать папку с файлами", command=choose_folder).pack(pady=5)
    lbl_folder = tk.Label(root, text="Папка: не выбрана", fg="blue")
    lbl_folder.pack()

    tk.Label(root, text="Размер альбома (кол-во фото в одном сообщении):").pack(pady=5)
    scale_album = tk.Scale(root, from_=1, to=10, orient="horizontal")
    scale_album.set(MEDIA_GROUP_SIZE)
    scale_album.pack()

    tk.Button(root, text="Отправить файлы", command=send_files, bg="green", fg="white").pack(pady=10)

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
    # Запуск GUI
    start_gui()
