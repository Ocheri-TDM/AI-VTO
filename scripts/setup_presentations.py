"""Install the explicitly licensed local model and redistributable Noto fonts.

Only downloads data from fixed upstream URLs. Does not execute downloaded code.
"""

import hashlib
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from app.config import Settings
from app.domain.presentation import MODEL_MD5, MODEL_NAME


def download(url, path, expected_md5=None):
    response = httpx.get(url, follow_redirects=True, timeout=120)
    response.raise_for_status()
    data = response.content
    if expected_md5 and hashlib.md5(data).hexdigest() != expected_md5:
        raise ValueError("Model checksum mismatch")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    print(path.name, len(data), hashlib.sha256(data).hexdigest())


if __name__ == "__main__":
    fonts = ROOT / "apps/api/app/assets/fonts"
    for face in ("Regular", "Bold"):
        download(
            f"https://raw.githubusercontent.com/notofonts/noto-fonts/main/hinted/ttf/NotoSans/NotoSans-{face}.ttf",
            fonts / f"NotoSans-{face}.ttf",
        )
    download(
        "https://raw.githubusercontent.com/notofonts/noto-fonts/main/LICENSE",
        fonts / "OFL.txt",
    )
    model = Settings().presentation_storage / f"models/{MODEL_NAME}.onnx"
    if not model.exists() or hashlib.md5(model.read_bytes()).hexdigest() != MODEL_MD5:
        download(
            f"https://github.com/danielgatis/rembg/releases/download/v0.0.0/{MODEL_NAME}.onnx",
            model,
            MODEL_MD5,
        )
    download(
        "https://raw.githubusercontent.com/xuebinqin/U-2-Net/master/LICENSE",
        model.parent / "U2NET-LICENSE.txt",
    )
