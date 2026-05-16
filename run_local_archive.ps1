param(
    [int]$BatchSize = 2000,
    [int]$Limit = 0,
    [int]$MaxFileSizeMB = 25,
    [double]$SleepSeconds = 1,
    [switch]$ForceRefresh
)

if (-not $env:STEAMGRIDDB_API_KEY) {
    Write-Error "STEAMGRIDDB_API_KEY belum di-set di environment variable."
    exit 1
}

$args = @(
    "final\steam_metadata_NP.py",
    "--output-dir", "dist",
    "--file-prefix", "steam_metadata_NP",
    "--batch-size", $BatchSize,
    "--limit", $Limit,
    "--max-file-size-mb", $MaxFileSizeMB,
    "--sleep-seconds", $SleepSeconds
)

if ($ForceRefresh) {
    $args += "--force-refresh"
}

python @args
