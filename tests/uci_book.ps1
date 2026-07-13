param(
    [Parameter(Mandatory = $true)]
    [string]$EnginePath
)

$ErrorActionPreference = "Stop"

function Require([bool]$Condition, [string]$Message) {
    if (-not $Condition) {
        throw $Message
    }
}

function Invoke-Uci([string[]]$Commands) {
    $commandFile = [IO.Path]::GetTempFileName()
    try {
        [IO.File]::WriteAllLines($commandFile, $Commands, [Text.Encoding]::ASCII)
        $command = '"{0}" < "{1}"' -f $script:Engine, $commandFile
        $output = @(& cmd.exe /d /c $command 2>&1 | ForEach-Object { $_.ToString() })
        if ($LASTEXITCODE -ne 0) {
            throw "Protocol-test engine exited with code $LASTEXITCODE`n$($output -join [Environment]::NewLine)"
        }
        return $output
    } finally {
        Remove-Item -LiteralPath $commandFile -Force -ErrorAction SilentlyContinue
    }
}

function Has-NodeCount([string[]]$Output, [Int64]$Expected) {
    return [bool]($Output | Where-Object {
        $_ -match '\bnodes ([0-9]+)' -and [Int64]$Matches[1] -eq $Expected
    })
}

$script:Engine = (Resolve-Path -LiteralPath $EnginePath).Path
$fixtureDirectory = Join-Path ([IO.Path]::GetTempPath()) ("senpai omega book " + [Guid]::NewGuid().ToString("N"))
$bookFile = Join-Path $fixtureDirectory "opening test.obk"
$missingFile = Join-Path $fixtureDirectory "missing replacement.obk"
# Deliberately differs from startpos only in fullmove metadata.  CoreChess and
# Senpai advance that field differently, so a valid book key must ignore it.
$start = "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/PPPPPPPPPP/CRNBQKBNRC[W/W/w/w] w KQkq - 0 37"

try {
    New-Item -ItemType Directory -Path $fixtureDirectory | Out-Null
    [IO.File]::WriteAllLines($bookFile, @(
        "senpai-omega-book-v1",
        ($start + "`tb0c0`t200"),
        ($start + "`ta0a9`t100"),
        ($start + "`tf1f2`t20"),
        ($start + "`ta0c2`t20")
    ), [Text.Encoding]::ASCII)

    # The book file deliberately arrives before UCI_Variant.  The load must
    # remain geometry-independent, OwnBook must activate it, and a path with
    # spaces must survive the ordinary UCI setoption parser.
    $hit = Invoke-Uci @(
        "uci",
        "setoption name OmegaBookFile value $bookFile",
        "setoption name OwnBook value true",
        "setoption name UCI_Variant value omega",
        "isready",
        "position startpos",
        "go nodes 127",
        "quit"
    )

    Require ($hit -contains "option name OwnBook type check default false") "OwnBook was not advertised"
    Require ($hit -contains "option name OmegaBookFile type string default <empty>") "OmegaBookFile was not advertised"
    Require ([bool]($hit | Where-Object { $_ -match '^info string Omega opening book loaded: 1 positions, 4 moves from ' })) "Book load was not acknowledged`n$($hit -join [Environment]::NewLine)"
    Require ($hit -contains "info string omega book hit a0c2") "Deterministic legal book move was not reported"
    Require ($hit -contains "bestmove a0c2") "Book hit did not become bestmove"

    # A failed replacement is atomic: the previous book keeps serving.
    $retained = Invoke-Uci @(
        "uci",
        "setoption name OmegaBookFile value $bookFile",
        "setoption name OmegaBookFile value $missingFile",
        "setoption name OwnBook value true",
        "setoption name UCI_Variant value omega",
        "isready",
        "position startpos",
        "go nodes 127",
        "quit"
    )
    Require ([bool]($retained | Where-Object { $_ -match 'previous book retained$' })) "Failed replacement did not retain the previous book"
    Require ($retained -contains "info string omega book hit a0c2") "Retained book no longer served its move"

    # Loaded does not mean active: OwnBook defaults false.
    $off = Invoke-Uci @(
        "uci",
        "setoption name OmegaBookFile value $bookFile",
        "setoption name UCI_Variant value omega",
        "isready",
        "position startpos",
        "go nodes 127",
        "quit"
    )
    Require (-not ($off -match '^info string omega book hit ')) "OwnBook=false still used the book"
    Require (Has-NodeCount $off 127) "OwnBook=false did not fall through to exact-node search"

    # Explicit disable also falls through even while OwnBook remains true.
    $disabled = Invoke-Uci @(
        "uci",
        "setoption name OmegaBookFile value $bookFile",
        "setoption name OwnBook value true",
        "setoption name OmegaBookFile value <empty>",
        "setoption name UCI_Variant value omega",
        "isready",
        "position startpos",
        "go nodes 127",
        "quit"
    )
    Require ($disabled -contains "info string Omega opening book disabled") "Book disable was not acknowledged"
    Require (-not ($disabled -match '^info string omega book hit ')) "Disabled book still produced a hit"
    Require (Has-NodeCount $disabled 127) "Disabled book did not fall through to exact-node search"

    # The same loaded file is inert for standard chess.
    $chess = Invoke-Uci @(
        "uci",
        "setoption name OmegaBookFile value $bookFile",
        "setoption name OwnBook value true",
        "isready",
        "position startpos",
        "go nodes 127",
        "quit"
    )
    Require (-not ($chess -match '^info string omega book hit ')) "Omega book activated in standard chess"
    Require (Has-NodeCount $chess 127) "Standard chess did not perform its requested search"

    # Analysis and pondering intentionally bypass the immediate book hook.
    $analysis = Invoke-Uci @(
        "uci",
        "setoption name OmegaBookFile value $bookFile",
        "setoption name OwnBook value true",
        "setoption name UCI_Variant value omega",
        "isready",
        "position startpos",
        "go infinite",
        "stop",
        "quit"
    )
    Require (-not ($analysis -match '^info string omega book hit ')) "Infinite analysis took the book shortcut"

    $ponder = Invoke-Uci @(
        "uci",
        "setoption name OmegaBookFile value $bookFile",
        "setoption name OwnBook value true",
        "setoption name UCI_Variant value omega",
        "isready",
        "position startpos",
        "go ponder depth 1",
        "ponderhit",
        "quit"
    )
    Require (-not ($ponder -match '^info string omega book hit ')) "Ponder search took the book shortcut"
    Require ([bool]($ponder | Where-Object { $_ -match '^info depth 1 ' })) "Ponder bypass did not complete a real depth-one search"

    Write-Host "UCI Omega opening-book protocol tests passed"
} finally {
    Remove-Item -LiteralPath $fixtureDirectory -Recurse -Force -ErrorAction SilentlyContinue
}
