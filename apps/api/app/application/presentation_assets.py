"""Local image preparation. No generative edits, recoloring or white-pixel erasure."""

import hashlib
import io
import ipaddress
import json
import os
import socket
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from app.domain.presentation import MODEL_MD5, MODEL_NAME, PIPELINE

MAX_BYTES = 12 * 1024 * 1024
MAX_PIXELS = 16_000_000
ALLOWED_HOSTS = {"vto.kz", "s.a-5.ru"}


def safe_path(root, key):
    root = Path(root).resolve()
    path = (root / key).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Invalid storage path")
    return path


def decode_image(data):
    if len(data) > MAX_BYTES:
        raise ValueError("Изображение превышает 12 МБ")
    try:
        src = Image.open(io.BytesIO(data))
        with src:
            if src.format not in ("PNG", "JPEG", "WEBP") or getattr(src, "n_frames", 1) != 1:
                raise ValueError("Разрешены статические PNG, JPEG и WebP")
            if src.width * src.height > MAX_PIXELS or min(src.size) < 24:
                raise ValueError("Разрешение изображения должно быть от 24 px до 16 Мп")
            src.load()
            return ImageOps.exif_transpose(src).convert("RGBA")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Файл не является допустимым изображением") from exc


def transparent(image):
    alpha = image.getchannel("A")
    histogram = alpha.histogram()
    total = image.width * image.height
    return sum(histogram[:16]) / total > 0.01 and sum(histogram[128:]) / total > 0.005


def write_png(image, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(".tmp")
    image.save(part, format="PNG")
    part.replace(path)


def store_upload(root, doc_id, data, prepared=False):
    image = decode_image(data)
    if image.getchannel("A").getbbox() is None:
        raise ValueError("Изображение не содержит видимых пикселей")
    if prepared and not transparent(image):
        raise ValueError("Подготовленное фото должно иметь фактически прозрачный фон")
    image.thumbnail((3000, 3000), Image.Resampling.LANCZOS)
    digest = hashlib.sha256(data).hexdigest()
    key = f"documents/{doc_id}/uploads/{digest}.png"
    write_png(image, safe_path(root, key))
    # Retain the submitted bytes separately; normalized upload never replaces them.
    original = safe_path(root, f"documents/{doc_id}/uploads/{digest}.original")
    original.write_bytes(data)
    return key


def valid_source(url, resolve=True):
    value = urlparse(url or "")
    if (
        value.scheme != "https"
        or value.hostname not in ALLOWED_HOSTS
        or value.username
        or value.password
        or value.port not in (None, 443)
    ):
        raise ValueError("Недопустимый источник изображения")
    if resolve:
        addresses = socket.getaddrinfo(value.hostname, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ValueError("Недопустимый адрес источника")
    return url


def fetch_image(url):
    # Redirect destinations get the same host/address checks as the original.
    with httpx.Client(timeout=httpx.Timeout(20, connect=8), follow_redirects=False) as client:
        for _ in range(4):
            valid_source(url)
            with client.stream("GET", url) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers["location"])
                    continue
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError("Изображение превышает 12 МБ")
                    chunks.append(chunk)
                return b"".join(chunks)
    raise ValueError("Слишком много перенаправлений")


class AssetPreparer:
    def __init__(self, root):
        self.root = Path(root)
        self.session = None

    def prepare(self, item, original_fallback=False):
        started = time.perf_counter()
        if item.get("prepared_key"):
            data = safe_path(self.root, item["prepared_key"]).read_bytes()
        elif item.get("original_key"):
            data = safe_path(self.root, item["original_key"]).read_bytes()
        else:
            data = fetch_image(item.get("image_url"))
        source_hash = hashlib.sha256(data).hexdigest()
        original = f"assets/original/{source_hash}.img"
        path = safe_path(self.root, original)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(data)
        image = decode_image(data)
        identity = hashlib.sha256((source_hash + PIPELINE).encode()).hexdigest()
        key = f"assets/prepared/{identity}.png"
        meta_key = f"assets/prepared/{identity}.json"
        cached = safe_path(self.root, meta_key)
        if cached.exists() and safe_path(self.root, key).exists() and not original_fallback:
            return json.loads(cached.read_text("utf-8")) | {
                "cached": True,
                "seconds": round(time.perf_counter() - started, 3),
            }
        if original_fallback:
            key = f"assets/fallback/{source_hash}.png"
            write_png(image, safe_path(self.root, key))
            return {
                "key": key,
                "original": original,
                "hash": source_hash,
                "pipeline": "explicit-original-fallback",
                "review_required": False,
                "warnings": ["Исходное фото: фон не удалён, fallback выбран явно"],
                "seconds": round(time.perf_counter() - started, 3),
            }
        already = transparent(image)
        if not already:
            if self.session is None:
                model = safe_path(self.root, f"models/{MODEL_NAME}.onnx")
                if not model.exists():
                    raise ValueError("Локальная модель не установлена: выполните setup_presentations.py")
                with model.open("rb") as weights:
                    if hashlib.file_digest(weights, "md5").hexdigest() != MODEL_MD5:
                        raise ValueError("Checksum локальной модели не совпадает")
                os.environ["U2NET_HOME"] = str(model.parent)
                from rembg import new_session

                self.session = new_session(MODEL_NAME, providers=["CPUExecutionProvider"])
            from rembg import remove

            image.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
            image = remove(image, session=self.session, alpha_matting=False, post_process_mask=False).convert(
                "RGBA"
            )
        if not transparent(image):
            raise ValueError(
                "Не получен проверяемый прозрачный фон: загрузите подготовленное фото или разрешите оригинал"
            )
        bbox = image.getchannel("A").point(lambda v: 255 if v > 16 else 0).getbbox()
        if bbox is None:
            raise ValueError("Маска удалила весь предмет")
        warnings = []
        if bbox[0] == 0 or bbox[1] == 0 or bbox[2] == image.width or bbox[3] == image.height:
            warnings.append("Предмет касается края: проверьте ремни, ручки и тонкие детали")
        image = ImageOps.expand(image.crop(bbox), border=24, fill=(0, 0, 0, 0))
        write_png(image, safe_path(self.root, key))
        result = {
            "key": key,
            "original": original,
            "hash": identity,
            "source_hash": source_hash,
            "pipeline": PIPELINE,
            "model": None if already else MODEL_NAME,
            "source": "uploaded-prepared"
            if item.get("prepared_key")
            else "catalog-alpha"
            if already
            else "local-inference",
            "model_md5": MODEL_MD5,
            "parameters": {"alpha_matting": False, "padding": 24},
            "review_required": not already,
            "warnings": warnings,
            "width": image.width,
            "height": image.height,
            "seconds": round(time.perf_counter() - started, 3),
        }
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(result, ensure_ascii=False), "utf-8")
        return result
