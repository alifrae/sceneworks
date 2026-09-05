[CmdletBinding()]
param(
    [switch]$Dev,
    [switch]$Rebuild,
    [switch]$Restart,
    [switch]$NoBrowser,
    [switch]$NoTunnel,
    [string]$TunnelClientPath,
    [string]$McpServerUrl
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir
$BackendDir = Join-Path $Root "backend"
$WebDir = Join-Path $Root "web"
$ToolsDir = Join-Path $Root "tools"
$WebUrl = "http://127.0.0.1:3000"
$SupervisorUrl = "http://127.0.0.1:8020"
$SupervisorStatusUrl = "$SupervisorUrl/v1/status"
$SupervisorDataDir = if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    Join-Path $HOME ".local\share\sceneworks\supervisor"
} else {
    Join-Path $env:LOCALAPPDATA "SceneWorks\supervisor"
}
$SupervisorTokenPath = Join-Path $SupervisorDataDir "token"

function Require-Command([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found on PATH."
    }
}

function Test-Http([string]$Url, [int]$TimeoutSec = 1) {
    try {
        $null = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec $TimeoutSec
        return $true
    } catch {
        return $false
    }
}

function Wait-Http([string]$Url, [int]$TimeoutSec, [string]$Label) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-Http $Url 1) {
            Write-Host "$Label is ready." -ForegroundColor Green
            return
        }
        Start-Sleep -Milliseconds 350
    }
    throw "$Label did not become reachable at $Url within $TimeoutSec seconds."
}

function Quote-PowerShellPath([string]$Path) {
    return "'" + $Path.Replace("'", "''") + "'"
}

function Start-Terminal([string]$Title, [string]$WorkingDirectory, [string]$Command) {
    $dir = Quote-PowerShellPath $WorkingDirectory
    $escapedTitle = $Title.Replace("'", "''")
    $full = "`$Host.UI.RawUI.WindowTitle = '$escapedTitle'; Set-Location $dir; $Command"
    Start-Process powershell.exe -ArgumentList @("-NoExit", "-NoProfile", "-Command", $full) | Out-Null
}

function Test-FrontendBuildFresh {
    $buildId = Join-Path $WebDir ".next\BUILD_ID"
    if (-not (Test-Path $buildId)) { return $false }
    $buildTime = (Get-Item $buildId).LastWriteTimeUtc
    $sourceRoots = @((Join-Path $WebDir "app"), (Join-Path $WebDir "components"), (Join-Path $WebDir "lib"))
    $latestSource = Get-ChildItem -Path $sourceRoots -Recurse -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
    foreach ($manifest in @("package.json", "package-lock.json", "next.config.js", "next.config.ts")) {
        $path = Join-Path $WebDir $manifest
        if ((Test-Path $path) -and (Get-Item $path).LastWriteTimeUtc -gt $buildTime) { return $false }
    }
    return $null -ne $latestSource -and $latestSource.LastWriteTimeUtc -le $buildTime
}

function Resolve-RepoPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return $null }
    if ([System.IO.Path]::IsPathRooted($Path)) { return [System.IO.Path]::GetFullPath($Path) }
    return [System.IO.Path]::GetFullPath((Join-Path $Root $Path))
}

function Resolve-TunnelClientPath {
    if (-not [string]::IsNullOrWhiteSpace($TunnelClientPath)) { return (Resolve-RepoPath $TunnelClientPath) }
    if (-not [string]::IsNullOrWhiteSpace($env:SCENEWORKS_TUNNEL_CLIENT_PATH)) { return (Resolve-RepoPath $env:SCENEWORKS_TUNNEL_CLIENT_PATH) }
    return (Join-Path $ToolsDir "tunnel-client-runtime-cloudflared.exe")
}

function Get-SupervisorToken {
    if (-not (Test-Path -LiteralPath $SupervisorTokenPath -PathType Leaf)) {
        throw "SceneWorks supervisor token was not created at $SupervisorTokenPath."
    }
    $token = (Get-Content -Raw -LiteralPath $SupervisorTokenPath).Trim()
    if ([string]::IsNullOrWhiteSpace($token)) { throw "SceneWorks supervisor token is empty." }
    return $token
}

function Wait-SupervisorOperation([string]$OperationId, [int]$TimeoutSec = 120) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        $row = Invoke-RestMethod -Method Get -Uri "$SupervisorUrl/v1/operations/$OperationId" -TimeoutSec 3
        if ($row.state -in @("SUCCEEDED", "FAILED", "PARTIAL", "REJECTED")) {
            if ($row.state -ne "SUCCEEDED") {
                throw "Supervisor operation $OperationId ended in $($row.state): $($row.detail)"
            }
            return $row
        }
        Start-Sleep -Milliseconds 300
    }
    throw "Supervisor operation $OperationId did not finish within $TimeoutSec seconds."
}

function Invoke-SupervisorAction([string]$Action, [string]$Component = "") {
    $SupervisorToken = Get-SupervisorToken
    $headers = @{
        Authorization = "Bearer $SupervisorToken"
        "X-SceneWorks-Actor" = "launcher"
    }
    $body = @{}
    if (-not [string]::IsNullOrWhiteSpace($Component)) { $body.component = $Component }
    $response = Invoke-RestMethod -Method Post -Uri "$SupervisorUrl/v1/actions/$Action" -Headers $headers -ContentType "application/json" -Body ($body | ConvertTo-Json -Compress) -TimeoutSec 5
    return Wait-SupervisorOperation ([string]$response.operation_id)
}

if ([string]::IsNullOrWhiteSpace($McpServerUrl)) {
    $McpServerUrl = if (-not [string]::IsNullOrWhiteSpace($env:MCP_SERVER_URL)) { $env:MCP_SERVER_URL } else { "http://127.0.0.1:8010/mcp" }
}

Require-Command "uv"
Require-Command "npm"

Write-Host "SceneWorks launcher" -ForegroundColor Cyan
Write-Host "  root: $Root"
Write-Host "  frontend: $(if ($Dev) { 'development' } else { 'production' })"
Write-Host "  MCP: $McpServerUrl"
Write-Host "  restart: $Restart"

if (-not (Test-Path (Join-Path $WebDir "node_modules"))) {
    Write-Host "Installing frontend dependencies..."
    Push-Location $WebDir
    try { npm ci } finally { Pop-Location }
}

if (-not $Dev) {
    $needsBuild = $Rebuild -or -not (Test-FrontendBuildFresh)
    if ($needsBuild) {
        Write-Host "Building the production frontend..." -ForegroundColor Yellow
        Push-Location $WebDir
        try { npm run build } finally { Pop-Location }
    } else {
        Write-Host "Production frontend build is current."
    }
}

Write-Host "Syncing backend dependencies, including the pinned OpenHands SDK..."
Push-Location $BackendDir
try { uv sync --frozen --extra openhands } finally { Pop-Location }

$tunnelEnabled = -not $NoTunnel
$clientPath = Resolve-TunnelClientPath
if ($tunnelEnabled -and ([string]::IsNullOrWhiteSpace($env:CONTROL_PLANE_TUNNEL_ID) -or [string]::IsNullOrWhiteSpace($env:CONTROL_PLANE_API_KEY))) {
    Write-Warning "Secure MCP tunnel disabled for this launch because tunnel credentials are missing."
    $tunnelEnabled = $false
}
if ($tunnelEnabled -and -not (Test-Path -LiteralPath $clientPath -PathType Leaf)) {
    Write-Warning "Secure MCP tunnel disabled because the tunnel client was not found at '$clientPath'."
    $tunnelEnabled = $false
}

if (-not (Test-Http $SupervisorStatusUrl 1)) {
    $quotedBackend = Quote-PowerShellPath $BackendDir
    $quotedRoot = Quote-PowerShellPath $Root
    $command = "uv run --project $quotedBackend python -m supervisor --repo-root $quotedRoot --mcp-server-url '$($McpServerUrl.Replace("'", "''"))'"
    if ($Dev) { $command += " --dev" }
    if (-not $tunnelEnabled) { $command += " --no-tunnel" }
    elseif (-not [string]::IsNullOrWhiteSpace($clientPath)) { $command += " --tunnel-client-path $(Quote-PowerShellPath $clientPath)" }
    Write-Host "Starting SceneWorks Supervisor..."
    Start-Terminal "SceneWorks Supervisor" $Root $command
    Wait-Http $SupervisorStatusUrl 20 "SceneWorks Supervisor"
} else {
    Write-Host "SceneWorks Supervisor already running at $SupervisorUrl."
}

if ($Restart) {
    $null = Invoke-SupervisorAction "restart-all"
} else {
    $null = Invoke-SupervisorAction "reconcile"
    $null = Invoke-SupervisorAction "start" "api"
    $null = Invoke-SupervisorAction "start" "web"
    if ($tunnelEnabled) { $null = Invoke-SupervisorAction "start" "mcp_tunnel" }
}

if (-not $NoBrowser) { Start-Process $WebUrl | Out-Null }

Write-Host "SceneWorks is running: $WebUrl" -ForegroundColor Green
Write-Host "Lifecycle ownership is handled by the local SceneWorks Supervisor at $SupervisorUrl."
Write-Host "Use -Restart for a supervised API/web/tunnel restart; -NoTunnel disables tunnel supervision for this supervisor instance."
