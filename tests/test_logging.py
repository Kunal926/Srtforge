from __future__ import annotations

import time

from srtforge.logging import log_heartbeat


def test_log_heartbeat_emits_while_context_is_open():
    messages: list[str] = []

    with log_heartbeat("slow step", messages.append, interval_seconds=0.01):
        time.sleep(0.04)

    assert any("slow step still running after" in message for message in messages)
