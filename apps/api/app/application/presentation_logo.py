"""Freeze one real application/client logo; never silently replace a broken choice."""

import hashlib
import os
import tempfile
from pathlib import Path

from PIL import Image

from app.application.presentation_assets import safe_path

DEFAULT_LOGO = Path(__file__).resolve().parents[1] / "assets/presentations/251.png"
DEFAULT_SHA256 = "ede89826aed478c55b79cef412ae06a566d13c75d6a9bdfdbe5ea43b0b6ad290"


def freeze_logo(root, settings):
    custom = bool(settings.get("use_logo") and settings.get("logo"))
    path = safe_path(root, settings["logo"]) if custom else DEFAULT_LOGO
    try:
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if not custom and digest != DEFAULT_SHA256:
            raise ValueError("Проектный 251.png не соответствует зарегистрированной версии")
        if len(data) > 12 * 1024 * 1024:
            raise ValueError("Логотип превышает допустимый размер")
        with Image.open(path) as image:
            if image.format != "PNG" or image.width * image.height > 20_000_000:
                raise ValueError("Некорректный подготовленный логотип")
            image.load()
            if not image.convert("RGBA").getchannel("A").getbbox():
                raise ValueError("Логотип не содержит видимых пикселей")
            width, height = image.size
        key = f"assets/logos/{digest}.png"
        target = safe_path(root, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".tmp", delete=False) as stream:
                stream.write(data)
                temporary = stream.name
            try:
                # Publish a complete immutable file exactly once. Replacing an
                # existing file can conflict with a concurrent Windows reader.
                try:
                    os.link(temporary, target)
                except FileExistsError:
                    pass
            finally:
                Path(temporary).unlink()
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError("Сохранённый логотип повреждён")
        return {
            "source": "custom" if custom else "default",
            "asset_id": "logo:" + digest,
            "key": key,
            "hash": digest,
            "preparation_version": 1,
            "width": width,
            "height": height,
        }
    except (OSError, ValueError) as exc:
        label = (
            "Активный клиентский логотип повреждён или недоступен. Загрузите исправленный файл."
            if custom
            else "Проектный логотип 251.png недоступен или повреждён."
        )
        raise ValueError(label) from exc


def frozen_logo_path(root, value):
    path = safe_path(root, value["key"])
    if hashlib.sha256(path.read_bytes()).hexdigest() != value["hash"]:
        raise ValueError("Сохранённый логотип повреждён. Восстановите файл этой ревизии.")
    return path
