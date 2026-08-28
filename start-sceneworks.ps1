[CmdletBinding()]
param(
    [switch]$Dev,
    [switch]$Rebuild,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir = Join-Path $Root "backend"
$WebDir = Join-Path $Root "web"
$ApiUrl = "http://127.0.0.1:8010/api/health"
$WebUrl = "http://127.0.0.1:3000"

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

Require-Command "uv"
Require-Command "npm"

Write-Host "SceneWorks launcher" -ForegroundColor Cyan
Write-Host "  root: $Root"
Write-Host "  frontend: $(if ($Dev) { 'development' } else { 'production' })"

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

if (-not $NoBrowser) {
    Start-Process $WebUrl | Out-Null
}

Write-Host "SceneWorks is running: $WebUrl" -ForegroundColor Green
Write-Host "Use -Dev only while changing the UI. Normal use should stay in production mode for fast route switching."
