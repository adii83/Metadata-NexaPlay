param(
    [int]$ItemsPerRun = 1,
    [int]$Limit = 0,
    [int]$MaxFileSizeMB = 25,
    [double]$SleepSeconds = 1,
    [double]$LoopDelaySeconds = 3,
    [int]$PushEveryProcessedCount = 1000,
    [switch]$ForceRefresh,
    [switch]$RunOnce
)

function Import-DotEnv {
    param(
        [string]$Path = ".env"
    )

    if (-not (Test-Path $Path)) {
        return
    }

    Get-Content $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) {
            return
        }

        $parts = $line -split "=", 2
        if ($parts.Count -ne 2) {
            return
        }

        $key = $parts[0].Trim()
        $value = $parts[1].Trim()

        if (
            ($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }

        [Environment]::SetEnvironmentVariable($key, $value, "Process")
    }
}

function Set-GitIdentityFromEnv {
    if ($env:COMMIT_USER_NAME) {
        git config user.name "$($env:COMMIT_USER_NAME)" | Out-Null
    }

    if ($env:COMMIT_USER_EMAIL) {
        git config user.email "$($env:COMMIT_USER_EMAIL)" | Out-Null
    }
}

Import-DotEnv

if (-not $env:STEAMGRIDDB_API_KEY) {
    Write-Error "STEAMGRIDDB_API_KEY belum di-set. Isi lewat file .env atau environment variable."
    exit 1
}

$pushStatePath = Join-Path "dist" "steam_metadata_NP_push_state.json"
$manifestPath = Join-Path "dist" "steam_metadata_NP_manifest.json"

function Get-PushState {
    if (Test-Path $pushStatePath) {
        return Get-Content -Raw $pushStatePath | ConvertFrom-Json
    }

    return [pscustomobject]@{
        pending_processed_since_push = 0
        last_push_at = $null
    }
}

function Save-PushState([int]$PendingProcessedCount, [string]$LastPushAt) {
    $pushStateDirectory = Split-Path -Parent $pushStatePath
    if (-not (Test-Path $pushStateDirectory)) {
        New-Item -ItemType Directory -Path $pushStateDirectory | Out-Null
    }

    $state = [ordered]@{
        pending_processed_since_push = $PendingProcessedCount
        last_push_at = $LastPushAt
    }

    $state | ConvertTo-Json | Set-Content -Encoding UTF8 $pushStatePath
}

function Get-LastRunProcessedCount {
    if (-not (Test-Path $manifestPath)) {
        return 0
    }

    $manifest = Get-Content -Raw $manifestPath | ConvertFrom-Json
    if ($null -eq $manifest.total_appids_processed_this_run) {
        return 0
    }

    return [int]$manifest.total_appids_processed_this_run
}

function Invoke-AutoPush([int]$PendingProcessedCount) {
    if (-not (Test-Path ".git")) {
        Write-Host "[push] Folder ini belum repo git. Lewati auto-push." -ForegroundColor Yellow
        return $PendingProcessedCount
    }

    Set-GitIdentityFromEnv

    $gitStatus = git status --porcelain -- dist
    if (-not $gitStatus) {
        Write-Host "[push] Tidak ada perubahan untuk dipush." -ForegroundColor DarkYellow
        return $PendingProcessedCount
    }

    git add dist
    git commit -m "Update Steam metadata archive after $PendingProcessedCount appids"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[push] Commit gagal. Counter tidak direset." -ForegroundColor Red
        return $PendingProcessedCount
    }

    git push
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[push] Push gagal. Counter tidak direset." -ForegroundColor Red
        return $PendingProcessedCount
    }

    $timestamp = (Get-Date).ToUniversalTime().ToString("o")
    Save-PushState -PendingProcessedCount 0 -LastPushAt $timestamp
    Write-Host "[push] Berhasil setelah $PendingProcessedCount App ID." -ForegroundColor Green
    return 0
}

function Invoke-ArchiveRunner {
    $args = @(
        "final\steam_metadata_NP.py",
        "--output-dir", "dist",
        "--file-prefix", "steam_metadata_NP",
        "--batch-size", $ItemsPerRun,
        "--limit", $Limit,
        "--max-file-size-mb", $MaxFileSizeMB,
        "--sleep-seconds", $SleepSeconds
    )

    if ($ForceRefresh) {
        $args += "--force-refresh"
    }

    & python @args | Out-Host
    return [int]$LASTEXITCODE
}

if ($RunOnce) {
    $exitCode = Invoke-ArchiveRunner
    exit $exitCode
}

Write-Host "Mode kontinu aktif. Tekan Ctrl+C untuk berhenti."
Write-Host "Per putaran   : $ItemsPerRun App ID"
Write-Host "Jeda loop     : $LoopDelaySeconds detik"
Write-Host "Auto-push     : tiap $PushEveryProcessedCount App ID"

while ($true) {
    $startedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host ""
    Write-Host "[$startedAt] Mulai putaran..." -ForegroundColor Cyan

    $exitCode = Invoke-ArchiveRunner

    if ($exitCode -ne 0) {
        Write-Host "[runner] Gagal. Exit code: $exitCode. Coba lagi dalam $LoopDelaySeconds detik." -ForegroundColor Red
    }
    else {
        $processedThisRun = Get-LastRunProcessedCount
        $pushState = Get-PushState
        $pendingProcessedCount = [int]$pushState.pending_processed_since_push + $processedThisRun

        if ($processedThisRun -gt 0) {
            Save-PushState -PendingProcessedCount $pendingProcessedCount -LastPushAt $pushState.last_push_at
            Write-Host "[runner] Sukses | putaran ini: $processedThisRun | belum dipush: $pendingProcessedCount" -ForegroundColor Green
        }
        else {
            Write-Host "[runner] Tidak ada App ID yang diproses di putaran ini." -ForegroundColor DarkYellow
        }

        if ($PushEveryProcessedCount -gt 0 -and $pendingProcessedCount -ge $PushEveryProcessedCount) {
            $updatedPendingCount = Invoke-AutoPush -PendingProcessedCount $pendingProcessedCount
            if ($updatedPendingCount -ne 0) {
                Save-PushState -PendingProcessedCount $updatedPendingCount -LastPushAt $pushState.last_push_at
            }
        }
    }

    Write-Host "[runner] Tunggu $LoopDelaySeconds detik..." -ForegroundColor DarkGray
    Start-Sleep -Seconds $LoopDelaySeconds
}
