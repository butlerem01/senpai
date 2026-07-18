param(
    [Parameter(Mandatory = $true)]
    [string]$EnginePath,

    [Parameter(Mandatory = $true)]
    [string]$FixtureWriterPath
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

function Search-Signatures([string[]]$Output) {
    $results = @()
    $lastInfo = $null

    foreach ($line in $Output) {
        if ($line -match '^info depth .* score (?:cp|mate) -?[0-9]+ .* pv ') {
            $lastInfo = $line
        } elseif ($line -match '^bestmove (\S+)') {
            Require ($Matches[1] -ne "0000") "Search returned bestmove 0000`n$($Output -join [Environment]::NewLine)"
            Require ($null -ne $lastInfo) "Search returned no completed PV before $line"
            $score = ""
            $pv = ""
            Require ([bool]($lastInfo -match ' score ((?:cp|mate) -?[0-9]+) ')) "Could not parse score from $lastInfo"
            $score = $Matches[1]
            Require ([bool]($lastInfo -match ' pv (.+)$')) "Could not parse PV from $lastInfo"
            $pv = $Matches[1]
            $results += "$score|$pv|$line"
            $lastInfo = $null
        }
    }

    return @($results)
}

function Require-One-Legal-Search([string[]]$Output, [string]$Context) {
    Require (Has-NodeCount $Output 127) "$Context did not report exactly 127 nodes"
    $signatures = @(Search-Signatures $Output)
    Require ($signatures.Count -eq 1) "$Context produced $($signatures.Count) searches instead of one"
    return $signatures[0]
}

$script:Engine = (Resolve-Path -LiteralPath $EnginePath).Path
$fixtureWriter = (Resolve-Path -LiteralPath $FixtureWriterPath).Path
$fixtureDirectory = Join-Path ([IO.Path]::GetTempPath()) ("senpai omega nnue " + [Guid]::NewGuid().ToString("N"))
$networkFile = Join-Path $fixtureDirectory "known valid network.omnnue"
$corruptFile = Join-Path $fixtureDirectory "bad checksum network.omnnue"
$missingFile = Join-Path $fixtureDirectory "missing network.omnnue"

try {
    New-Item -ItemType Directory -Path $fixtureDirectory | Out-Null

    & $fixtureWriter --write-fixture $networkFile
    Require ($LASTEXITCODE -eq 0) "NNUE fixture writer exited with $LASTEXITCODE"
    Require (Test-Path -LiteralPath $networkFile -PathType Leaf) "NNUE fixture writer did not create $networkFile"
    Require ((Get-Item -LiteralPath $networkFile).Length -eq 435692) "NNUE fixture has the wrong OMNNUE1 file size"

    $corrupt = [IO.File]::ReadAllBytes($networkFile)
    $corrupt[$corrupt.Length - 1] = [byte]($corrupt[$corrupt.Length - 1] -bxor 1)
    [IO.File]::WriteAllBytes($corruptFile, $corrupt)

    $handcrafted = Invoke-Uci @(
        "uci",
        "setoption name Threads value 1",
        "setoption name UCI_Variant value omega",
        "isready",
        "position startpos",
        "go nodes 127",
        "quit"
    )
    Require ($handcrafted -contains "uciok") "UCI handshake did not complete"
    Require ($handcrafted -contains "readyok") "UCI readiness handshake did not complete"
    Require ($handcrafted -contains "option name UseOmegaNNUE type check default false") "UseOmegaNNUE option was not advertised"
    Require ($handcrafted -contains "option name OmegaNNUEFile type string default <empty>") "OmegaNNUEFile option was not advertised"
    $handcraftedSignature = Require-One-Legal-Search $handcrafted "Handcrafted Omega baseline"

    # Use can be requested before a network or even before Omega geometry.
    # A successful later load must activate it without another option change.
    $useFirst = Invoke-Uci @(
        "uci",
        "setoption name Threads value 1",
        "setoption name UseOmegaNNUE value true",
        "setoption name OmegaNNUEFile value $networkFile",
        "setoption name UCI_Variant value omega",
        "isready",
        "position startpos",
        "go nodes 127",
        "quit"
    )
    Require ($useFirst -contains "info string Omega NNUE requested without a loaded network; handcrafted fallback active") "Use-before-file did not acknowledge fallback"
    Require ([bool]($useFirst | Where-Object { $_ -like "info string Omega NNUE loaded: PS104-128x2-32 from *known valid network.omnnue" })) "Use-before-file did not load the valid network"
    Require ($useFirst -contains "info string Omega NNUE loaded; activates for omega") "Use-before-file did not defer activation while standard chess was selected"
    $nnueSignature = Require-One-Legal-Search $useFirst "Use-before-file NNUE search"

    # Loading is geometry-independent and may precede both Use and variant.
    $fileFirst = Invoke-Uci @(
        "uci",
        "setoption name Threads value 1",
        "setoption name OmegaNNUEFile value $networkFile",
        "setoption name UCI_Variant value omega",
        "setoption name UseOmegaNNUE value true",
        "isready",
        "position startpos",
        "go nodes 127",
        "quit"
    )
    Require ([bool]($fileFirst | Where-Object { $_ -like "info string Omega NNUE loaded: PS104-128x2-32 from *known valid network.omnnue" })) "File-before-use did not load"
    Require ($fileFirst -contains "info string Omega NNUE evaluation active") "File-before-use did not activate"
    Require ((Require-One-Legal-Search $fileFirst "File-before-use NNUE search") -eq $nnueSignature) "Option order changed deterministic NNUE search"

    # A missing first network leaves Use requested and falls through exactly
    # to the handcrafted evaluator.
    $missing = Invoke-Uci @(
        "uci",
        "setoption name Threads value 1",
        "setoption name UseOmegaNNUE value true",
        "setoption name OmegaNNUEFile value $missingFile",
        "setoption name UCI_Variant value omega",
        "isready",
        "position startpos",
        "go nodes 127",
        "quit"
    )
    Require ([bool]($missing | Where-Object { $_ -like "info string Omega NNUE load failed: cannot open *missing network.omnnue" })) "Missing network failure was not reported"
    Require ((Require-One-Legal-Search $missing "Missing-network fallback") -eq $handcraftedSignature) "Missing-network fallback changed handcrafted search"

    # A malformed replacement is atomic: the previous immutable network
    # remains active and produces the same search.
    $retained = Invoke-Uci @(
        "uci",
        "setoption name Threads value 1",
        "setoption name OmegaNNUEFile value $networkFile",
        "setoption name UseOmegaNNUE value true",
        "setoption name OmegaNNUEFile value $corruptFile",
        "setoption name UCI_Variant value omega",
        "isready",
        "position startpos",
        "go nodes 127",
        "quit"
    )
    Require ([bool]($retained | Where-Object { $_ -match '^info string Omega NNUE load failed: payload checksum mismatch .*; previous network retained$' })) "Invalid replacement did not retain the previous network"
    Require ((Require-One-Legal-Search $retained "Retained-network search") -eq $nnueSignature) "Invalid replacement changed the serving network"

    # Explicit unload while Use remains requested must acknowledge both the
    # unload and the handcrafted fallback.
    $unloaded = Invoke-Uci @(
        "uci",
        "setoption name Threads value 1",
        "setoption name OmegaNNUEFile value $networkFile",
        "setoption name UseOmegaNNUE value true",
        "setoption name OmegaNNUEFile value <empty>",
        "setoption name UCI_Variant value omega",
        "isready",
        "position startpos",
        "go nodes 127",
        "quit"
    )
    Require ($unloaded -contains "info string Omega NNUE disabled") "Explicit NNUE unload was not acknowledged"
    Require ($unloaded -contains "info string Omega NNUE unavailable; handcrafted fallback active") "Unload did not announce handcrafted fallback"
    Require ((Require-One-Legal-Search $unloaded "Unloaded-network fallback") -eq $handcraftedSignature) "Unloaded network changed handcrafted search"

    # Backend and file changes must clear incompatible TT entries. Repeating
    # each mode in one engine process should reproduce its first result.
    $toggle = Invoke-Uci @(
        "uci",
        "setoption name Threads value 1",
        "setoption name UCI_Variant value omega",
        "isready",
        "position startpos",
        "go nodes 127",
        "setoption name OmegaNNUEFile value $networkFile",
        "setoption name UseOmegaNNUE value true",
        "position startpos",
        "go nodes 127",
        "setoption name UseOmegaNNUE value false",
        "position startpos",
        "go nodes 127",
        "setoption name UseOmegaNNUE value true",
        "position startpos",
        "go nodes 127",
        "quit"
    )
    $toggleSignatures = @(Search-Signatures $toggle)
    Require ($toggleSignatures.Count -eq 4) "Backend toggle produced $($toggleSignatures.Count) searches instead of four"
    Require ($toggleSignatures[0] -eq $toggleSignatures[2]) "OFF -> ON -> OFF did not restore deterministic handcrafted search"
    Require ($toggleSignatures[1] -eq $toggleSignatures[3]) "ON -> OFF -> ON did not restore deterministic NNUE search"
    Require (($toggle | Where-Object { $_ -match '\bnodes 127\b' }).Count -ge 4) "Backend toggle did not honor every node limit"

    # Loading and enabling an Omega network must leave standard Senpai's
    # original 8x8 evaluator and search byte-for-byte deterministic.
    $chessBaseline = Invoke-Uci @(
        "uci",
        "setoption name Threads value 1",
        "isready",
        "position startpos",
        "go nodes 127",
        "quit"
    )
    $chessBaselineSignature = Require-One-Legal-Search $chessBaseline "Standard-chess baseline"

    $chessLoaded = Invoke-Uci @(
        "uci",
        "setoption name Threads value 1",
        "setoption name OmegaNNUEFile value $networkFile",
        "setoption name UseOmegaNNUE value true",
        "setoption name UCI_Variant value chess",
        "isready",
        "position startpos",
        "go nodes 127",
        "quit"
    )
    Require ($chessLoaded -contains "info string Omega NNUE loaded; activates for omega") "Loaded Omega NNUE was not reported inert in standard chess"
    Require ((Require-One-Legal-Search $chessLoaded "Standard chess with Omega NNUE loaded") -eq $chessBaselineSignature) "Omega NNUE perturbed standard-chess search"

    Write-Host "UCI Omega NNUE option, fallback, atomic-load, toggle, and isolation tests passed"
} finally {
    $resolved = [IO.Path]::GetFullPath($fixtureDirectory)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if ($resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $resolved -Recurse -Force -ErrorAction SilentlyContinue
    }
}
