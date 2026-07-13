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

$engine = (Resolve-Path -LiteralPath $EnginePath).Path
$commands = [IO.Path]::GetTempFileName()

try {
    # Senpai's input thread polls stdin while searching. Keep each successful
    # protocol search below its 256-node input-poll interval so the following
    # queued command cannot terminate it before its own limit does. The C++
    # search_nodes test separately exercises an exact 257-node direct search.
    [IO.File]::WriteAllLines($commands, @(
        "uci",
        "setoption name Threads value 1",
        "setoption name OmegaTablebasePath value <empty>",
        "setoption name UCI_Variant value omega",
        "isready",
        "position startpos",
        "go nodes 127",
        "setoption name Clear Hash",
        "position startpos",
        "go depth 2",
        "setoption name Clear Hash",
        "position startpos",
        "go depth 1 nodes 9223372036854775807",
        "go nodes 0",
        "go nodes -1",
        "go nodes 9223372036854775808",
        "quit"
    ), [Text.Encoding]::ASCII)

    $command = '"{0}" < "{1}"' -f $engine, $commands
    $output = @(& cmd.exe /d /c $command 2>&1 | ForEach-Object { $_.ToString() })
    if ($LASTEXITCODE -ne 0) {
        throw "Protocol-test engine exited with code $LASTEXITCODE`n$($output -join [Environment]::NewLine)"
    }

    Require ($output -contains "uciok") "UCI handshake did not complete"
    Require ($output -contains "readyok") "UCI readiness handshake did not complete"
    Require ($output -contains "option name OwnBook type check default false") "OwnBook option was not advertised"
    Require ($output -contains "option name OmegaBookFile type string default <empty>") "Omega book file option was not advertised"
    Require ($output -contains "option name OmegaTablebasePath type string default <empty>") "Omega tablebase path option was not advertised"
    Require ($output -contains "info string Omega tablebases disabled") "Omega tablebase disable command was not acknowledged"

    $reportedNodes = @($output | ForEach-Object {
        if ($_ -match '\bnodes ([0-9]+)') { [Int64]$Matches[1] }
    })
    Require ($reportedNodes -contains 127) "go nodes 127 did not report exactly 127 nodes"

    Require ([bool]($output | Where-Object { $_ -match '^info depth 2 ' })) "go depth 2 no longer completed depth 2"
    Require (($output | Where-Object { $_ -eq "info string Invalid node limit" }).Count -eq 3) "invalid node limits were not all rejected"
    Require (($output | Where-Object { $_ -eq "bestmove 0000" }).Count -eq 3) "invalid node limits did not terminate cleanly"
    Require (($output | Where-Object { $_ -match '^bestmove (?!0000)' }).Count -ge 3) "valid node/depth searches did not return legal moves"

    Write-Host "UCI node-limit protocol tests passed"
} finally {
    Remove-Item -LiteralPath $commands -Force -ErrorAction SilentlyContinue
}
