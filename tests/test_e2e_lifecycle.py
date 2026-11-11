"""Lifecycle contracts of the isolated harness, without product services."""
import asyncio
import os

import pytest

from scripts.e2e_harness.server import owned_event_loop, run_owned_server


class SyntheticServer:
    started = True

    def __init__(self, mode="normal"):
        self.mode = mode
        self.exit = asyncio.Event()
        self._should_exit = False

    @property
    def should_exit(self):
        return self._should_exit

    @should_exit.setter
    def should_exit(self, value):
        self._should_exit = value
        if value and self.mode == "normal":
            self.exit.set()

    async def serve(self, *, sockets):
        assert len(sockets) == 1
        if self.mode == "failure":
            raise RuntimeError("Synthetic controlled failure")
        await self.exit.wait()


def test_owned_loop_selects_windows_selector_and_closes():
    loop = owned_event_loop()
    try:
        if os.name == "nt":
            assert isinstance(loop, asyncio.SelectorEventLoop)
    finally:
        loop.close()


def test_normal_stop_emits_clean_only_after_server_finishes(capsys):
    async def check():
        stop = asyncio.Event()
        server = SyntheticServer()
        assert await run_owned_server(server, object(), stop, stop.set) == 0
        assert server.should_exit
    asyncio.run(check())
    assert capsys.readouterr().out == "E2E_SERVER_STOPPED clean=true\n"


def test_shutdown_deadline_is_effective_and_cannot_emit_clean(capsys):
    async def check():
        stop = asyncio.Event()
        server = SyntheticServer("stuck")
        with pytest.raises(RuntimeError, match="shutdown deadline exceeded; clean=false"):
            await run_owned_server(server, object(), stop, stop.set, shutdown_timeout=0.01)
    asyncio.run(check())
    assert "clean=true" not in capsys.readouterr().out


def test_controlled_server_failure_cannot_emit_clean(capsys):
    async def check():
        stop = asyncio.Event()
        with pytest.raises(RuntimeError, match="Synthetic controlled failure"):
            await run_owned_server(SyntheticServer("failure"), object(), stop, lambda: None)
    asyncio.run(check())
    assert "clean=true" not in capsys.readouterr().out
