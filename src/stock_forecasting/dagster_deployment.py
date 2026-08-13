from __future__ import annotations

import argparse
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from httpx import Client, HTTPError

DAGSTER_DEPLOYMENT_QUERY = """
query Ticket02DeploymentHealth {
  workspaceOrError {
    __typename
    ... on Workspace {
      locationEntries {
        name
        loadStatus
        locationOrLoadError {
          __typename
          ... on RepositoryLocation {
            name
            repositories {
              name
              assetNodes {
                assetKey { path }
              }
            }
          }
        }
      }
    }
  }
  instance {
    daemonHealth {
      allDaemonStatuses {
        daemonType
        required
        healthy
        lastHeartbeatTime
      }
    }
  }
}
"""

DAGSTER_LAUNCH_ASSET_MUTATION = """
mutation MaterializeAcceptanceAsset(
  $locationName: String!,
  $assetName: String!
) {
  launchPipelineExecution(
    executionParams: {
      selector: {
        repositoryLocationName: $locationName,
        repositoryName: "__repository__",
        pipelineName: "__ASSET_JOB__",
        assetSelection: [{path: [$assetName]}]
      },
      runConfigData: {}
    }
  ) {
    __typename
    ... on LaunchRunSuccess { run { runId } }
  }
}
"""

DAGSTER_RUN_STATUS_QUERY = """
query AcceptanceRunStatus($runId: ID!) {
  pipelineRunOrError(runId: $runId) {
    __typename
    ... on Run { status }
  }
}
"""


@dataclass(frozen=True)
class DagsterDeploymentStatus:
    workspace_ready: bool
    required_daemons_ready: bool

    @property
    def ready(self) -> bool:
        return self.workspace_ready and self.required_daemons_ready


def _workspace_ready(data: dict[str, Any]) -> bool:
    workspace = data.get("workspaceOrError", {})
    if workspace.get("__typename") != "Workspace":
        return False
    for entry in workspace.get("locationEntries", []):
        location = entry.get("locationOrLoadError") or {}
        if (
            entry.get("name") != "stock_forecasting"
            or entry.get("loadStatus") != "LOADED"
            or location.get("__typename") != "RepositoryLocation"
        ):
            continue
        asset_paths = {
            tuple(asset["assetKey"]["path"])
            for repository in location.get("repositories", [])
            for asset in repository.get("assetNodes", [])
        }
        required_assets = {("xtai_fixture_eod",), ("xnas_fixture_eod",)}
        return required_assets.issubset(asset_paths)
    return False


def _required_daemons_ready(data: dict[str, Any]) -> bool:
    statuses = data.get("instance", {}).get("daemonHealth", {}).get("allDaemonStatuses", [])
    required = [status for status in statuses if status.get("required") is True]
    return bool(required) and all(
        status.get("healthy") is True and status.get("lastHeartbeatTime") is not None
        for status in required
    )


def inspect_dagster_deployment(url: str) -> DagsterDeploymentStatus:
    try:
        with Client(timeout=10.0) as client:
            response = client.post(url, json={"query": DAGSTER_DEPLOYMENT_QUERY})
            response.raise_for_status()
            payload = response.json()
        if (
            not isinstance(payload, dict)
            or payload.get("errors")
            or not isinstance(payload.get("data"), dict)
        ):
            return DagsterDeploymentStatus(False, False)
        data: dict[str, Any] = payload["data"]
        return DagsterDeploymentStatus(
            workspace_ready=_workspace_ready(data),
            required_daemons_ready=_required_daemons_ready(data),
        )
    except (AttributeError, HTTPError, KeyError, TypeError, ValueError):
        return DagsterDeploymentStatus(False, False)


def materialize_deployed_asset(
    url: str,
    *,
    location_name: str,
    asset_name: str,
    timeout_seconds: float = 30.0,
    poll_interval_seconds: float = 0.25,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    try:
        with Client(timeout=10.0) as client:
            response = client.post(
                url,
                json={
                    "query": DAGSTER_LAUNCH_ASSET_MUTATION,
                    "variables": {
                        "locationName": location_name,
                        "assetName": asset_name,
                    },
                },
            )
            response.raise_for_status()
            launch_payload = response.json()
            launch = launch_payload.get("data", {}).get("launchPipelineExecution", {})
            if launch.get("__typename") != "LaunchRunSuccess":
                return False
            run_id = launch.get("run", {}).get("runId")
            if not isinstance(run_id, str) or not run_id:
                return False
            while time.monotonic() <= deadline:
                response = client.post(
                    url,
                    json={
                        "query": DAGSTER_RUN_STATUS_QUERY,
                        "variables": {"runId": run_id},
                    },
                )
                response.raise_for_status()
                status_payload = response.json()
                run = status_payload.get("data", {}).get("pipelineRunOrError", {})
                if run.get("__typename") != "Run":
                    return False
                status = run.get("status")
                if status == "SUCCESS":
                    return True
                if status in {"FAILURE", "CANCELED"}:
                    return False
                time.sleep(poll_interval_seconds)
    except (AttributeError, HTTPError, KeyError, TypeError, ValueError):
        return False
    return False


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stock-forecasting-dagster-health")
    parser.add_argument("--url", required=True)
    parser.add_argument("--require", choices=("workspace", "daemons", "all"), default="all")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    status = inspect_dagster_deployment(arguments.url)
    requirement: Literal["workspace", "daemons", "all"] = arguments.require
    ready = {
        "workspace": status.workspace_ready,
        "daemons": status.required_daemons_ready,
        "all": status.ready,
    }[requirement]
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
