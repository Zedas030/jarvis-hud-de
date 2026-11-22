"""Talks to Jarvis' /api/ask: one question with a key, one plain answer.

The Pi runs Tailscale with accept-dns off, so it cannot resolve the PC's *.ts.net name.
An optional fixed IP (the PC's Tailscale address) is used for that one host name only;
TLS still checks the certificate against the name, because the URL keeps it.
"""
from __future__ import annotations

import asyncio
import socket
import time

import aiohttp
from aiohttp.abc import AbstractResolver
from aiohttp.resolver import ThreadedResolver
from yarl import URL

from .const import CONNECT_TIMEOUT_S, DOWN_MEMORY_S, HEAR_TIMEOUT_S


class JarvisError(Exception):
    """Base error."""


class JarvisUnreachable(JarvisError):
    """PC off, Jarvis not running, network down."""


class JarvisAuthError(JarvisError):
    """The key was refused (deleted under 📱 Geräte, or mistyped)."""


class _PinnedResolver(AbstractResolver):
    def __init__(self, host: str, ip: str) -> None:
        self._host = host.lower()
        self._ip = ip
        self._default = ThreadedResolver()

    async def resolve(self, host, port=0, family=socket.AF_INET):
        if host.lower() == self._host:
            return [{"hostname": host, "host": self._ip, "port": port, "family": socket.AF_INET,
                     "proto": 0, "flags": socket.AI_NUMERICHOST}]
        return await self._default.resolve(host, port, family)

    async def close(self) -> None:
        await self._default.close()


class JarvisClient:
    def __init__(self, url: str, key: str, ip: str | None = None, timeout: float = 55) -> None:
        self.url = url.rstrip("/")
        self._key = key.strip()
        self._ip = (ip or "").strip() or None
        self._timeout = timeout
        self._session: aiohttp.ClientSession | None = None
        self._down_at = float("-inf")

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = None
            if self._ip:
                host = URL(self.url).host or ""
                connector = aiohttp.TCPConnector(resolver=_PinnedResolver(host, self._ip))
            self._session = aiohttp.ClientSession(connector=connector)
        return self._session

    @property
    def recently_down(self) -> bool:
        """Unreachable a moment ago (e.g. while transcribing): the answer goes to the fallback at once
        instead of waiting for a PC that is off."""
        return time.monotonic() - self._down_at < DOWN_MEMORY_S

    async def _post(self, path: str, timeout: float, **kw) -> dict:
        try:
            async with self._get_session().post(
                self.url + path,
                headers={"Authorization": "Bearer " + self._key, **kw.pop("headers", {})},
                # a PC that is off never answers the connect: give up on that quickly
                timeout=aiohttp.ClientTimeout(total=timeout, sock_connect=CONNECT_TIMEOUT_S),
                **kw,
            ) as resp:
                if resp.status in (401, 403):
                    raise JarvisAuthError(f"HTTP {resp.status}")
                if resp.status != 200:
                    raise JarvisUnreachable(f"HTTP {resp.status}")
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError) as err:
            self._down_at = time.monotonic()
            raise JarvisUnreachable(str(err) or type(err).__name__) from err
        if not isinstance(data, dict) or "text" not in data:
            raise JarvisUnreachable("unexpected answer")
        self._down_at = float("-inf")
        return data

    async def ask(self, text: str, room: str = "", timeout: float | None = None, speak: bool = False) -> dict:
        """-> {"text": ..., "status": ..., "spoken_on": speaker label if Jarvis already said it in the room}.
        Raises JarvisUnreachable or JarvisAuthError."""
        return await self._post("/api/ask", timeout or self._timeout,
                                json={"text": text, "source": "ha", "room": room, "speak": speak})

    async def hear(self, pcm: bytes, timeout: float = HEAR_TIMEOUT_S) -> dict:
        """16 kHz 16-bit mono PCM -> {"text", "status": ok|empty|error|warm, "ms"}. An empty `pcm` only
        warms Jarvis' Whisper up. Raises JarvisUnreachable or JarvisAuthError."""
        return await self._post("/api/hear", timeout, data=pcm,
                                headers={"Content-Type": "audio/L16; rate=16000; channels=1"})

    async def ping(self, timeout: float = 10) -> None:
        """An empty question costs nothing: Jarvis answers it without running anything."""
        await self.ask("", timeout=timeout)

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
