param(
    [Parameter(Mandatory = $true)]
    [string]$EnginePath,

    [Parameter(Mandatory = $true)]
    [string]$TablebasePath
)

$ErrorActionPreference = "Stop"

function Require([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

function Invoke-Uci([string[]]$Commands) {
    $inputPath = Join-Path ([IO.Path]::GetTempPath()) (
        "senpai-kcck-dtm-" + [Guid]::NewGuid().ToString("N") + ".txt"
    )
    try {
        [IO.File]::WriteAllLines($inputPath, $Commands, [Text.Encoding]::ASCII)
        $output = @(Get-Content -LiteralPath $inputPath | & $EnginePath 2>&1)
        Require ($LASTEXITCODE -eq 0) (
            "Senpai exited with $LASTEXITCODE`n$($output -join [Environment]::NewLine)"
        )
        return $output
    } finally {
        Remove-Item -LiteralPath $inputPath -Force -ErrorAction SilentlyContinue
    }
}

function Exact-Run([string]$Ofen, [int]$Dtm, [int]$Mate) {
    Write-Host "Testing exact DTM $Dtm"
    $output = Invoke-Uci @(
        "uci",
        "setoption name UCI_Variant value omega",
        "setoption name OmegaTablebasePath value $TablebasePath",
        "isready",
        "position fen $Ofen",
        "go depth 1",
        "quit"
    )

    Require ($output -contains "info string omega KCCK DTM hit distance $Dtm") (
        "Missing exact DTM $Dtm hit`n$($output -join [Environment]::NewLine)"
    )
    $mateLine = @($output | Where-Object {
        $_ -match "^info .*score mate $Mate .*pv "
    })
    Require ($mateLine.Count -eq 1) (
        "Expected one exact mate $Mate info line`n$($output -join [Environment]::NewLine)"
    )
    $moves = @(($mateLine[0] -replace "^.* pv ", "") -split " ")
    Require ($moves.Count -eq $Dtm) (
        "Exact PV has $($moves.Count) plies instead of $Dtm`n$($mateLine[0])"
    )

    $best = if ($Dtm -gt 1) {
        "bestmove $($moves[0]) ponder $($moves[1])"
    } else {
        "bestmove $($moves[0])"
    }
    Require ($output -contains $best) (
        "Bestmove/ponder does not match exact PV`n$($output -join [Environment]::NewLine)"
    )
    return $output
}

Require (Test-Path -LiteralPath $EnginePath -PathType Leaf) "Engine not found: $EnginePath"
Require (Test-Path -LiteralPath $TablebasePath -PathType Container) (
    "Tablebase directory not found: $TablebasePath"
)

$dtm1 = "10/10/10/10/C9/10/10/k9/1C8/K9[-/-/-/-] w - - 0 1"
$dtm9 = "10/10/10/4C5/10/1k3C4/10/10/10/K9[-/-/-/-] w - - 0 1"
$dtm39 = "10/10/10/6k3/10/10/10/10/10/K9[C/-/C/-] w - - 0 1"
$dtm40h60 = "10/10/10/10/5k4/10/10/10/10/K9[C/-/C/-] b - - 60 1"

$null = Exact-Run $dtm1 1 1
$null = Exact-Run $dtm9 9 5
$null = Exact-Run $dtm39 39 20
$null = Exact-Run $dtm40h60 40 -20

# Analysis emits the complete exact line immediately but still waits for stop.
Write-Host "Testing exact analysis buffering"
$analysis = Invoke-Uci @(
    "uci",
    "setoption name UCI_Variant value omega",
    "setoption name OmegaTablebasePath value $TablebasePath",
    "isready",
    "position fen $dtm9",
    "go infinite",
    "isready",
    "stop",
    "quit"
)
Require ($analysis -contains "info string omega KCCK DTM hit distance 9") (
    "Analysis did not expose the exact DTM line"
)
Require ($analysis -contains "readyok") "Analysis buffering did not answer isready"
Require ([bool]($analysis | Where-Object { $_ -match "^bestmove f4d4 ponder " })) (
    "Analysis did not release the exact bestmove after stop"
)

# Ponder exposes the same line and waits for ponderhit before releasing it.
Write-Host "Testing exact ponder buffering"
$ponder = Invoke-Uci @(
    "uci",
    "setoption name UCI_Variant value omega",
    "setoption name OmegaTablebasePath value $TablebasePath",
    "isready",
    "position fen $dtm9",
    "go ponder depth 1",
    "isready",
    "ponderhit",
    "quit"
)
Require ($ponder -contains "info string omega KCCK DTM hit distance 9") (
    "Ponder did not expose the exact DTM line"
)
Require ($ponder -contains "readyok") "Ponder buffering did not answer isready"
Require ([bool]($ponder | Where-Object { $_ -match "^bestmove f4d4 ponder " })) (
    "Ponder did not release the exact bestmove after ponderhit"
)

# DTM 40 no longer fits after clock 60 and must fall back cleanly.
Write-Host "Testing unsafe-clock fallback"
$unsafe = Invoke-Uci @(
    "uci",
    "setoption name UCI_Variant value omega",
    "setoption name OmegaTablebasePath value $TablebasePath",
    "isready",
    "position fen 10/10/10/10/5k4/10/10/10/10/K9[C/-/C/-] b - - 61 1",
    "go nodes 1",
    "quit"
)
Require (-not ($unsafe -match "omega KCCK DTM hit")) (
    "Clock-unsafe DTM 40 position took the exact shortcut"
)
Require ([bool]($unsafe | Where-Object { $_ -match "^bestmove \S+" })) (
    "Clock-unsafe position did not fall back to a legal search"
)

# A six-core-plus-WDL directory remains compatible and simply omits DTM use.
$withoutDtm = Join-Path ([IO.Path]::GetTempPath()) (
    "senpai-kcck-no-dtm-" + [Guid]::NewGuid().ToString("N")
)
New-Item -ItemType Directory -Path $withoutDtm | Out-Null
try {
    Write-Host "Testing WDL-only fallback"
    Get-ChildItem -LiteralPath $TablebasePath -File |
        Where-Object Name -ne "omega-kcck-dtm-v1.omtb" |
        Copy-Item -Destination $withoutDtm
    $fallback = Invoke-Uci @(
        "uci",
        "setoption name UCI_Variant value omega",
        "setoption name OmegaTablebasePath value $withoutDtm",
        "isready",
        "position fen $dtm9",
        "go nodes 1",
        "quit"
    )
    Require (-not ($fallback -match "omega KCCK DTM hit")) (
        "WDL-only directory unexpectedly used DTM"
    )
    Require ([bool]($fallback | Where-Object { $_ -match "^bestmove \S+" })) (
        "WDL-only directory did not fall back to normal search"
    )
} finally {
    $resolved = [IO.Path]::GetFullPath($withoutDtm)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if ($resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $resolved -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "Testing standard-chess isolation"
$chess = Invoke-Uci @(
    "uci",
    "setoption name OmegaTablebasePath value $TablebasePath",
    "setoption name UCI_Variant value chess",
    "isready",
    "position startpos",
    "go nodes 1",
    "quit"
)
Require (-not ($chess -match "omega KCCK DTM hit")) (
    "Standard chess entered Omega DTM search"
)
Require ([bool]($chess | Where-Object { $_ -match "^bestmove [a-h][1-8][a-h][1-8]" })) (
    "Standard-chess fallback did not produce a legal-shaped bestmove"
)

Write-Host "UCI KCCK DTM mate-score, PV, analysis, ponder, and fallback tests passed"
