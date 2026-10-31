from __future__ import annotations

from collections.abc import Coroutine
from typing import Any

import asyncio
import atexit
import logging
import threading

_log = logging.getLogger("nzpy_extended._runner")


class _SyncRunner:
    _instance: _SyncRunner | None = None

    def __new__(cls) -> _SyncRunner:
        if cls._instance is not None:
            return cls._instance
        cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if hasattr(self, '_loop'):
            return
        self._lock = threading.Lock()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever,
            daemon=True,
            name="nzpy-sync-runner",
        )
        self._thread.start()
        atexit.register(self._atexit_cleanup)

    def run(self, coro: Coroutine[Any, Any, Any] | asyncio.Future[Any]) -> Any:
        with self._lock:
            if not self._loop.is_running():
                self._restart()
            loop = self._loop
        try:
            future = asyncio.run_coroutine_threadsafe(coro, loop)  # type: ignore[arg-type]
        except RuntimeError:
            # The loop stopped between the check and the submit. Submission failed
            # before the coroutine started, so it can still run on a fresh loop.
            with self._lock:
                self._restart()
                loop = self._loop
            future = asyncio.run_coroutine_threadsafe(coro, loop)  # type: ignore[arg-type]
        # Errors raised by the coroutine itself propagate unchanged. Retrying here
        # would re-submit a coroutine that already ran.
        return future.result()

    def _restart(self) -> None:
        try:
            self.close()
        except Exception as exc:
            _log.debug("Error closing runner during restart: %s", exc, exc_info=True)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever,
            daemon=True,
            name="nzpy-sync-runner",
        )
        self._thread.start()

    def close(self) -> None:
        if not self._loop.is_running():
            return
        self._loop.call_soon_threadsafe(self._loop.stop)
        try:
            self._thread.join(timeout=2.0)
        except Exception as exc:
            _log.debug("Error joining sync runner thread: %s", exc, exc_info=True)
        try:
            self._loop.close()
        except Exception as exc:
            _log.debug("Error closing sync runner loop: %s", exc, exc_info=True)

    @property
    def is_running(self) -> bool:
        return hasattr(self, '_loop') and self._loop.is_running()

    def _atexit_cleanup(self) -> None:
        try:
            self.close()
        except Exception as exc:
            _log.debug("Error during sync runner atexit cleanup: %s", exc, exc_info=True)


runner: _SyncRunner = _SyncRunner()
