"""Recoverable owner capability, encrypted separately from its indexed hash."""

import hashlib
import os
import re
import secrets
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet


class TokenVault:
    def __init__(self, root, *, create=True):
        path = Path(root) / "keys/commercial-offers.key"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() and not create:
            self.cipher = None
            return
        if not path.exists():
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                stream.write(Fernet.generate_key())
                temporary = Path(stream.name)
            try:
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    pass
            finally:
                temporary.unlink()
        self.cipher = Fernet(path.read_bytes())

    def create(self):
        if self.cipher is None:
            raise ValueError("Восстановите ключ шифрования ссылок из согласованной резервной копии")
        token = secrets.token_urlsafe(32)
        return token, self.digest(token), self.cipher.encrypt(token.encode()).decode()

    def reveal(self, encrypted):
        if self.cipher is None:
            raise ValueError("Восстановите ключ шифрования ссылок из согласованной резервной копии")
        return self.cipher.decrypt(encrypted.encode()).decode()

    @staticmethod
    def digest(token):
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            return None
        return hashlib.sha256(token.encode()).hexdigest()


def public_origin(value):
    if not value:
        return None  # Local-only preview is explicit, never advertised as external.
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("PUBLIC_FRONTEND_ORIGIN должен содержать только доверенный HTTPS origin")
    import ipaddress

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
        raise ValueError("Публичный origin не может быть localhost")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError("Публичный origin не может быть локальным адресом")
    return value.strip().rstrip("/")
