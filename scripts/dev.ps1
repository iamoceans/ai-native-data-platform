# PowerShell dispatcher mirroring the Makefile targets.
# Usage:  .\scripts\dev.ps1 doctor
#         .\scripts\dev.ps1 up-core
#         .\scripts\dev.ps1 test-unit
# Keep in sync with the Makefile.

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Target
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$uv = "uv"
$dockerDesktop = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
$docker = if (Test-Path -LiteralPath $dockerDesktop) { $dockerDesktop } else { "docker" }
$compose = @("compose", "-f", "compose.yaml")
$composeDev = @("compose", "-f", "compose.yaml", "-f", "compose.dev.yaml")
$core = @("--profile", "core")

function Invoke-Step([scriptblock]$Block) {
    & $Block
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

function Ensure-ComposeNetworks {
    & $docker network inspect "datahub-net" *> $null
    if ($LASTEXITCODE -ne 0) {
        Invoke-Step { & $docker network create "datahub-net" }
    }
}

function Set-DataHubTestEnv {
    # M3: host-side tests reach DataHub through the loopback-published GMS port;
    # the DataHub integration tests skip (never fail) when the stack is absent.
    $env:AIND_DATAHUB_ENABLED = "1"
    $env:AIND_DATAHUB_GMS_URL = "http://127.0.0.1:18080"
    $env:AIND_DATAHUB_FRONTEND_URL = "http://127.0.0.1:9002"
    $env:AIND_CONTAINER_API_URL = "http://127.0.0.1:8000"
}

function Get-DotEnv {
    $envFile = Join-Path $root ".env"
    $map = @{}
    if (Test-Path $envFile) {
        Get-Content $envFile | ForEach-Object {
            if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
                $map[$Matches[1]] = $Matches[2].Trim()
            }
        }
    }
    return $map
}

switch ($Target) {
    "doctor" { Invoke-Step { & $uv run --project backend --frozen python scripts/doctor.py } }
    "setup-secrets" { Invoke-Step { & $uv run --project backend --frozen python scripts/setup_secrets.py } }
    "up-core" {
        Ensure-ComposeNetworks
        Invoke-Step { & $docker @($compose + $core + @("up", "-d", "--build")) }
    }
    "down" { Invoke-Step { & $docker @($compose + @("down", "--remove-orphans")) } }
    "build-core" { Invoke-Step { & $docker @($compose + $core + @("build")) } }
    "migrate" { Invoke-Step { & $docker @($compose + @("run", "--rm", "--no-deps", "backend", "alembic", "upgrade", "head")) } }
    "bootstrap" { Invoke-Step { & $docker @($compose + @("run", "--rm", "--no-deps", "backend", "python", "-m", "app.cli", "bootstrap")) } }
    "ps" { Invoke-Step { & $docker @($compose + $core + @("ps")) } }
    "logs" { Invoke-Step { & $docker @($compose + $core + @("logs", "-f", "--tail=100")) } }
    "db-up-dev" { Invoke-Step { & $docker @($composeDev + $core + @("up", "-d", "control-postgres", "source-postgres")) } }
    "test-unit" { Invoke-Step { & $uv run --project backend --frozen pytest backend/tests/unit -m "not integration" } }
    "test-security" { Invoke-Step { & $uv run --project backend --frozen pytest backend/tests/security -m "not integration" } }
    "test-integration" {
        & $docker @($composeDev + $core + @("up", "-d", "control-postgres", "source-postgres"))
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        $e = Get-DotEnv
        $controlUrl = "postgresql+psycopg://$($e['CONTROL_DB_USER']):$($e['CONTROL_DB_PASSWORD'])@127.0.0.1:$($e['CONTROL_DB_PORT'])/$($e['CONTROL_DB_NAME'])"
        $sourceUrl = "postgresql+psycopg://$($e['SOURCE_BOOTSTRAP_USER']):$($e['SOURCE_BOOTSTRAP_PASSWORD'])@127.0.0.1:$($e['SOURCE_DB_PORT'])/$($e['SOURCE_DB_NAME'])"
        $env:AIND_DATABASE_URL = $controlUrl
        $env:AIND_TEST_SOURCE_URL = $sourceUrl
        $env:AIND_SECRETS_DIR = Join-Path $root "infra\local-secrets"
        Set-DataHubTestEnv
        & $uv run --project backend --frozen pytest backend/tests/integration backend/tests/contract -m integration
        exit $LASTEXITCODE
    }
    "test-full" {
        & $docker @($composeDev + @("--profile", "full", "up", "-d", "source-mysql", "doris-fe", "doris-be", "doris-init"))
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & $uv run --project backend --frozen python scripts/seed_sources.py
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        $e = Get-DotEnv
        $env:AIND_DATABASE_URL = "postgresql+psycopg://$($e['CONTROL_DB_USER']):$($e['CONTROL_DB_PASSWORD'])@127.0.0.1:$($e['CONTROL_DB_PORT'])/$($e['CONTROL_DB_NAME'])"
        $env:AIND_TEST_SOURCE_URL = "postgresql+psycopg://$($e['SOURCE_BOOTSTRAP_USER']):$($e['SOURCE_BOOTSTRAP_PASSWORD'])@127.0.0.1:$($e['SOURCE_DB_PORT'])/$($e['SOURCE_DB_NAME'])"
        $env:AIND_TEST_MYSQL_URL = "mysql://$($e['MYSQL_ADMIN_USER']):$($e['MYSQL_ADMIN_PASSWORD'])@127.0.0.1:$($e['MYSQL_DB_PORT'])/$($e['MYSQL_SOURCE_DB'])"
        $env:AIND_TEST_DORIS_URL = "mysql://$($e['DORIS_ADMIN_USER']):$($e['DORIS_ADMIN_PASSWORD'])@127.0.0.1:$($e['DORIS_FE_SQL_PORT'])/demo"
        $env:AIND_SECRETS_DIR = Join-Path $root "infra\local-secrets"
        Set-DataHubTestEnv
        & $uv run --project backend --frozen pytest backend/tests/integration backend/tests/contract -m integration
        exit $LASTEXITCODE
    }
    "seed-sources" { Invoke-Step { & $uv run --project backend --frozen python scripts/seed_sources.py } }
    "demo-generate" { Invoke-Step { & $uv run --project backend --frozen python scripts/demo_generate.py } }
    "demo-load" { Invoke-Step { & $uv run --project backend --frozen python scripts/demo_load.py } }
    "demo-verify" { Invoke-Step { & $uv run --project backend --frozen python scripts/demo_verify.py } }
    "demo-lineage" {
        $runs = Get-ChildItem (Join-Path $root "runtime\demo") -Directory -ErrorAction SilentlyContinue |
            Where-Object { Test-Path (Join-Path $_.FullName "pipeline_lineage.json") } |
            Sort-Object LastWriteTime -Descending
        if (-not $runs) { Write-Host "no demo runs under runtime\demo; run demo-generate first"; exit 2 }
        $run = $runs[0].Name
        Invoke-Step {
            & $docker @("compose", "run", "--rm", "--no-deps", "-v", "$root\runtime:/data/runtime:ro",
                "--entrypoint", "python", "ingestion", "/opt/ainative/publish_lineage.py",
                "--manifest", "/data/runtime/$run/pipeline_lineage.json")
        }
    }
    "reset-demo" {
        if ($env:CONFIRM -ne "demo") { Write-Host "set `$env:CONFIRM='demo' to confirm the destructive reset"; exit 2 }
        Invoke-Step { & $uv run --project backend --frozen python scripts/demo_load.py --reset-demo }
    }
    "test-core" {
        & $PSCommandPath test-unit; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & $PSCommandPath test-security; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & $PSCommandPath test-integration; exit $LASTEXITCODE
    }
    "smoke-core" { Invoke-Step { & $uv run --project backend --frozen python scripts/smoke_core.py } }
    "api-spec" {
        Push-Location backend
        try { Invoke-Step { & $uv run --frozen python -m app.openapi_export --output ../frontend/openapi.json } }
        finally { Pop-Location }
    }
    "types" { & $PSCommandPath api-spec; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; Invoke-Step { Push-Location frontend; & cmd /c "npm run gen:api"; Pop-Location } }
    default {
        Write-Host "Unknown target '$Target'. Mirrors the Makefile: doctor, setup-secrets, up-core, down, migrate, bootstrap, test-core, test-unit, test-security, test-integration, smoke-core, api-spec, types, ps, logs, build-core"
        exit 2
    }
}
