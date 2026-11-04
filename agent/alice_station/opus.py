"""Минимальная обёртка libopus через ctypes (без сторонних Python-пакетов).

На колонке libopus ставится из Entware: `opkg install libopus`.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os

OPUS_APPLICATION_VOIP = 2048
OPUS_OK = 0


def _load() -> ctypes.CDLL:
    candidates = [
        os.environ.get("ALICE_STATION_LIBOPUS", ""),
        "/opt/lib/libopus.so.0",
        "/opt/lib/libopus.so",
        ctypes.util.find_library("opus") or "",
        "libopus.so.0",
    ]
    last: Exception | None = None
    for name in candidates:
        if not name:
            continue
        try:
            return ctypes.CDLL(name)
        except OSError as e:
            last = e
    raise OSError(f"libopus не найдена (opkg install libopus): {last}")


_lib: ctypes.CDLL | None = None


def lib() -> ctypes.CDLL:
    global _lib
    if _lib is None:
        l = _load()
        l.opus_encoder_create.restype = ctypes.c_void_p
        l.opus_encoder_create.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        l.opus_encode.restype = ctypes.c_int32
        l.opus_encode.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int32]
        l.opus_encoder_destroy.argtypes = [ctypes.c_void_p]
        l.opus_decoder_create.restype = ctypes.c_void_p
        l.opus_decoder_create.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        l.opus_decode.restype = ctypes.c_int
        l.opus_decode.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int32, ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
        l.opus_decoder_destroy.argtypes = [ctypes.c_void_p]
        _lib = l
    return _lib


class Encoder:
    def __init__(self, rate: int = 16000, channels: int = 1):
        err = ctypes.c_int()
        self._st = lib().opus_encoder_create(rate, channels, OPUS_APPLICATION_VOIP, ctypes.byref(err))
        if err.value != OPUS_OK or not self._st:
            raise RuntimeError(f"opus_encoder_create: {err.value}")
        self.channels = channels
        self._out = ctypes.create_string_buffer(4000)

    def encode(self, pcm: bytes) -> bytes:
        frame = len(pcm) // (2 * self.channels)
        n = lib().opus_encode(self._st, pcm, frame, self._out, len(self._out))
        if n < 0:
            raise RuntimeError(f"opus_encode: {n}")
        return self._out.raw[:n]

    def __del__(self):
        if getattr(self, "_st", None) and _lib is not None:
            _lib.opus_encoder_destroy(self._st)


class Decoder:
    def __init__(self, rate: int = 24000, channels: int = 1):
        err = ctypes.c_int()
        self._st = lib().opus_decoder_create(rate, channels, ctypes.byref(err))
        if err.value != OPUS_OK or not self._st:
            raise RuntimeError(f"opus_decoder_create: {err.value}")
        self.channels = channels
        # максимум 120 мс при 48 кГц
        self._max = 5760
        self._out = ctypes.create_string_buffer(self._max * 2 * channels)

    def decode(self, packet: bytes) -> bytes:
        n = lib().opus_decode(self._st, packet, len(packet), self._out, self._max, 0)
        if n < 0:
            raise RuntimeError(f"opus_decode: {n}")
        return self._out.raw[: n * 2 * self.channels]

    def __del__(self):
        if getattr(self, "_st", None) and _lib is not None:
            _lib.opus_decoder_destroy(self._st)
