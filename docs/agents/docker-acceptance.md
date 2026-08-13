# Docker acceptance on Windows

Use this procedure when a ticket requires Docker, Compose, PostgreSQL in a container, or deployed acceptance. A static Compose parse or a host-only database test is supporting evidence, not a replacement for the deployed public seam.

## Resolve Docker Desktop

Prefer the Docker CLI on `PATH`. If it is missing or Windows reports `Access is denied`, resolve the Docker Desktop CLI explicitly:

```powershell
$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
$dockerExe = if ($dockerCommand) {
    $dockerCommand.Source
} else {
    Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin\docker.exe'
}

if (-not (Test-Path -LiteralPath $dockerExe)) {
    throw "Docker Desktop CLI was not found at $dockerExe"
}
```

In a Codex Windows task, an executable under the per-user Docker Desktop directory can be readable but blocked by the workspace sandbox. Run the Docker command through the shell's approved escalation with the resolved absolute path. This is the expected boundary; do not switch to WSL as a workaround.

Prove both the CLI and Compose model before starting services:

```powershell
& $dockerExe compose version
& $dockerExe compose -f compose.yaml config --quiet
```

## Ticket 04 clean-container seam

The `stock-forecasting-ticket-04` Compose project and its named volumes are disposable acceptance evidence. Recreate them so migrations, policy initialization, application roles, and ephemeral credentials are exercised from a clean state:

```powershell
& $dockerExe compose -f compose.yaml --profile acceptance down --volumes --remove-orphans
& $dockerExe compose -f compose.yaml --profile acceptance up -d --build --wait api-ingress denied-api-ingress dagster-webserver dagster-daemon
& $dockerExe compose -f compose.yaml --profile acceptance run --rm acceptance
```

The seam is complete only when the final command exits zero and prints one JSON result whose `status` is `passed`. Every check must be true, including REST, Dagster, CLI, platform-admin denial, and `application_database_role_least_privilege`. Record the executed commands and result in the ticket before checking its deployed acceptance criterion.

The separate `compose.test.yaml` project exposes PostgreSQL on `127.0.0.1:55432` for repository integration tests. Its availability does not prove ticket 04's clean-container acceptance profile.
