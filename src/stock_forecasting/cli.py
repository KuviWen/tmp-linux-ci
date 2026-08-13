from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from stock_forecasting.acceptance import run_ticket_01, run_ticket_02, run_ticket_03
from stock_forecasting.runtime import RuntimeSettings


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("information cutoff must include a timezone")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stock-forecasting")
    commands = parser.add_subparsers(dest="command", required=True)
    acceptance = commands.add_parser("acceptance")
    acceptance.add_argument("ticket", choices=["ticket-01", "ticket-02", "ticket-03"])
    acceptance.add_argument("--database-url", required=True)
    acceptance.add_argument("--object-root", type=Path, required=True)
    acceptance.add_argument("--information-cutoff", type=_instant, required=True)
    acceptance.add_argument("--observed-at", type=_instant, required=True)
    acceptance.add_argument("--base-url")
    acceptance.add_argument("--dagster-url")
    relay = commands.add_parser("relay")
    relay.add_argument("--once", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    if arguments.command == "acceptance" and arguments.ticket in {
        "ticket-01",
        "ticket-02",
        "ticket-03",
    }:
        if (arguments.base_url is None) != (arguments.dagster_url is None):
            parser.error("--base-url and --dagster-url must be provided together")
        runners = {
            "ticket-01": run_ticket_01,
            "ticket-02": run_ticket_02,
            "ticket-03": run_ticket_03,
        }
        runner = runners[arguments.ticket]
        report = runner(
            database_url=arguments.database_url,
            object_root=arguments.object_root,
            information_cutoff=arguments.information_cutoff,
            observed_at=arguments.observed_at,
            base_url=arguments.base_url,
            dagster_url=arguments.dagster_url,
        )
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0 if report["status"] == "passed" else 1
    if arguments.command == "relay":
        if not arguments.once:
            parser.error("relay currently requires --once")
        outcome = RuntimeSettings.from_environment().build_application().relay_outbox()
        print(json.dumps(asdict(outcome), ensure_ascii=False, sort_keys=True))
        return 1 if outcome.status in {"failed", "deferred", "isolated"} else 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
