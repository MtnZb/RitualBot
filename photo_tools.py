import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime
import os

def ultra_obscured_version(input_path):
    """
    Делает искажённую версию фото с таймстампом из времени модификации.
    Возвращает путь к новому файлу.
    """
    original = cv2.imread(input_path)
    if original is None:
        print(f"[photo_tools] ❌ Не удалось открыть: {input_path}")
        return None

    # 1. Снижение качества
    small = cv2.resize(original, (100, 75), interpolation=cv2.INTER_LINEAR)
    img = cv2.resize(small, (800, 600), interpolation=cv2.INTER_NEAREST)

    # 2. Сильное размытие
    img = cv2.GaussianBlur(img, (21, 21), 5)

    # 3. Шум
    noise = np.random.normal(0, 80, img.shape).astype(np.int16)
    img = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)

    # 4. Волновое искажение
    for y in range(img.shape[0]):
        shift = int(15.0 * np.sin(2 * np.pi * y / 80))
        img[y] = np.roll(img[y], shift, axis=0)

    # Перевод в PIL и добавление таймстампа
    img_pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(img_pil)

    try:
        timestamp = datetime.fromtimestamp(os.path.getmtime(input_path)).strftime("%Y-%m-%d %H:%M:%S")
    except:
        timestamp = "????-??-?? ??:??:??"

    try:
        font = ImageFont.truetype("PressStart2P-Regular.ttf", 28)
    except:
        font = ImageFont.load_default()

    draw.text((20, img_pil.height - 60), timestamp, fill=(255, 255, 255), font=font)

    output_path = os.path.splitext(input_path)[0] + "_distorted.jpg"
    img_pil.save(output_path, "JPEG")
    print(f"[photo_tools] ✅ Сохранено: {output_path}")
    return output_path