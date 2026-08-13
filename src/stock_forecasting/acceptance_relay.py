from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from threading import Event

from stock_forecasting.outbox import RelayFault
from stock_forecasting.runtime import RuntimeSettings


class PausingRelayFault(RelayFault):
    def __init__(self, *, stage: str, ready_file: Path) -> None:
        self._stage = stage
        self._ready_file = ready_file

    def _pause(self, event_id: str) -> None:
        self._ready_file.parent.mkdir(parents=True, exist_ok=True)
        self._ready_file.write_text(event_id, encoding="utf-8")
        Event().wait()

    def before_consumers(self, event_id: str) -> None:
        if self._stage == "before_consumers":
            self._pause(event_id)

    def before_consumer_commit(self, consumer_name: str, event_id: str) -> None:
        pass

    def before_ack(self, event_id: str) -> None:
        if self._stage == "before_ack":
            self._pause(event_id)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stock-forecasting-acceptance-relay")
    parser.add_argument("--event-id", required=True)
    pause = parser.add_mutually_exclusive_group(required=True)
    pause.add_argument("--pause-before-consumers", type=Path)
    pause.add_argument("--pause-before-ack", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.pause_before_consumers is not None:
        stage = "before_consumers"
        ready_file = arguments.pause_before_consumers
    else:
        stage = "before_ack"
        ready_file = arguments.pause_before_ack
    settings = RuntimeSettings.from_environment()
    application = settings.build_application(
        relay_fault=PausingRelayFault(stage=stage, ready_file=ready_file)
    )
    outcome = application.relay_outbox(event_id=arguments.event_id)
    print(json.dumps(outcome.__dict__, sort_keys=True))
    return 0 if outcome.status in {"delivered", "already_delivered"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
