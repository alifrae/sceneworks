[CmdletBinding()]
param(
    [switch]$Dev,
    [switch]$Rebuild,
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
$ApiUrl = "http://127.0.0.1:8010/api/health"
$WebUrl = "http://127.0.0.1:3000"
$TunnelReadyUrl = "http://127.0.0.1:8080/readyz"

function Require-Command([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found on PATH."
    }
}

function Test-Http([string]$Url, [int]$TimeoutSec = 1) {
    try {
        $null = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec $TimeoutSec
        return $true
    }
    catch {
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
    $sourceRoots = @(
        (Join-Path $WebDir "app"),
        (Join-Path $WebDir "components"),
        (Join-Path $WebDir "lib")
    )
    $latestSource = Get-ChildItem -Path $sourceRoots -Recurse -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1

    foreach ($manifest in @("package.json", "package-lock.json", "next.config.js", "next.config.ts")) {
        $path = Join-Path $WebDir $manifest
        if ((Test-Path $path) -and (Get-Item $path).LastWriteTimeUtc -gt $buildTime) {
            return $false
        }
    }

    return $null -ne $latestSource -and $latestSource.LastWriteTimeUtc -le $buildTime
}

function Resolve-RepoPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) {
        return $null
    }
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $Root $Path))
}

function Resolve-TunnelClientPath {
    if (-not [string]::IsNullOrWhiteSpace($TunnelClientPath)) {
        return (Resolve-RepoPath $TunnelClientPath)
    }
    if (-not [string]::IsNullOrWhiteSpace($env:SCENEWORKS_TUNNEL_CLIENT_PATH)) {
        return (Resolve-RepoPath $env:SCENEWORKS_TUNNEL_CLIENT_PATH)
    }
    return (Join-Path $ToolsDir "tunnel-client-runtime-cloudflared.exe")
}

function Start-SecureMcpTunnel {
    if ($NoTunnel) {
        Write-Host "Secure MCP tunnel disabled (-NoTunnel)."
        return
    }

    if (Test-Http $TunnelReadyUrl 1) {
        Write-Host "Secure MCP tunnel already ready at $TunnelReadyUrl."
        return
    }

    $clientPath = Resolve-TunnelClientPath
    $missing = @()
    if ([string]::IsNullOrWhiteSpace($env:CONTROL_PLANE_TUNNEL_ID)) {
        $missing += "CONTROL_PLANE_TUNNEL_ID"
    }
    if ([string]::IsNullOrWhiteSpace($env:CONTROL_PLANE_API_KEY)) {
        $missing += "CONTROL_PLANE_API_KEY"
    }

    if ($missing.Count -gt 0) {
        Write-Warning "Secure MCP tunnel not started. Missing environment variable(s): $($missing -join ', ')."
        return
    }

    if (-not (Test-Path -LiteralPath $clientPath -PathType Leaf)) {
        Write-Warning "Secure MCP tunnel not started. Tunnel client not found at '$clientPath'. Put tunnel-client-runtime-cloudflared.exe under tools\ or pass -TunnelClientPath / set SCENEWORKS_TUNNEL_CLIENT_PATH."
        return
    }

    if (-not (Test-Http $McpServerUrl 3)) {
        Write-Warning "Secure MCP tunnel not started because SceneWorks MCP is not reachable at $McpServerUrl."
        return
    }

    $env:MCP_SERVER_URL = $McpServerUrl
    $quotedClient = Quote-PowerShellPath $clientPath

    Write-Host "Starting Secure MCP tunnel..."
    Start-Terminal "SceneWorks MCP Tunnel" $Root "& $quotedClient run"

    try {
        Wait-Http $TunnelReadyUrl 20 "Secure MCP tunnel"
    }
    catch {
        Write-Warning $_.Exception.Message
        Write-Warning "SceneWorks is running, but the MCP tunnel did not become ready. Check the 'SceneWorks MCP Tunnel' terminal."
    }
}

if ([string]::IsNullOrWhiteSpace($McpServerUrl)) {
    if (-not [string]::IsNullOrWhiteSpace($env:MCP_SERVER_URL)) {
        $McpServerUrl = $env:MCP_SERVER_URL
    }
    else {
        $McpServerUrl = "http://127.0.0.1:8010/mcp"
    }
}

Require-Command "uv"
Require-Command "npm"

Write-Host "SceneWorks launcher" -ForegroundColor Cyan
Write-Host "  root: $Root"
Write-Host "  frontend: $(if ($Dev) { 'development' } else { 'production' })"
Write-Host "  MCP: $McpServerUrl"

if (-not (Test-Path (Join-Path $WebDir "node_modules"))) {
    Write-Host "Installing frontend dependencies..."
    Push-Location $WebDir
    try { npm ci }
    finally { Pop-Location }
}

if (-not $Dev) {
    $needsBuild = $Rebuild -or -not (Test-FrontendBuildFresh)
    if ($needsBuild) {
        Write-Host "Building the production frontend..." -ForegroundColor Yellow
        Push-Location $WebDir
        try { npm run build }
        finally { Pop-Location }
    }
    else {
        Write-Host "Production frontend build is current."
    }
}

if (Test-Http $ApiUrl 1) {
    Write-Host "Backend already running at $ApiUrl."
}
else {
    Write-Host "Starting backend..."
    Start-Terminal "SceneWorks API" $BackendDir "uv run python -m app.main"
    Wait-Http $ApiUrl 45 "Backend"
}

if (Test-Http $WebUrl 1) {
    Write-Host "Frontend already running at $WebUrl."
}
else {
    Write-Host "Starting frontend..."
    if ($Dev) {
        Start-Terminal "SceneWorks Web (dev)" $WebDir "npm run dev"
    }
    else {
        Start-Terminal "SceneWorks Web" $WebDir "npm run start"
    }
    Wait-Http $WebUrl 60 "Frontend"
}

Start-SecureMcpTunnel

if (-not $NoBrowser) {
    Start-Process $WebUrl | Out-Null
}

Write-Host "SceneWorks is running: $WebUrl" -ForegroundColor Green
Write-Host "Use -Dev only while changing the UI. Normal use should stay in production mode for fast route switching."
