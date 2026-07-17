param(
    [int]$SmallStates = 100000,
    [string]$KrknFullPath = "",
    [string]$KwknFullPath = "",
    [string]$KckwFullPath = "",
    [string]$KcckFullPath = "",
    [string]$KcckDtmFullPath = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$tools = Join-Path $root "tools\omega_tb"
$temporary = Join-Path ([IO.Path]::GetTempPath()) ("omega-tb4-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $temporary | Out-Null

function New-MutatedHeaderCopy {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][string]$Old,
        [Parameter(Mandatory = $true)][string]$New
    )
    $bytes = [IO.File]::ReadAllBytes($Source)
    $newline = [Array]::IndexOf($bytes, [byte]10)
    if ($newline -lt 0) { throw "source artifact has no JSON header newline" }
    $header = [Text.Encoding]::UTF8.GetString($bytes, 0, $newline)
    if (-not $header.Contains($Old)) { throw "header mutation source text was not found: $Old" }
    $mutated = $header.Replace($Old, $New)
    $mutatedBytes = [Text.Encoding]::UTF8.GetBytes($mutated + "`n")
    $output = New-Object byte[] ($mutatedBytes.Length + $bytes.Length - $newline - 1)
    [Array]::Copy($mutatedBytes, 0, $output, 0, $mutatedBytes.Length)
    [Array]::Copy($bytes, $newline + 1, $output, $mutatedBytes.Length,
        $bytes.Length - $newline - 1)
    [IO.File]::WriteAllBytes($Destination, $output)
}

function Test-ArtifactRejected {
    param(
        [Parameter(Mandatory = $true)][string]$Generator,
        [Parameter(Mandatory = $true)][string]$Path
    )
    try {
        & $Generator --inspect $Path 2>$null | Out-Null
        return $LASTEXITCODE -ne 0
    } catch {
        return $true
    }
}

try {
    $generator = Join-Path $temporary "four_man_wdl.exe"
    & (Join-Path $tools "four_man_build.ps1") -OutputPath $generator
    if ($LASTEXITCODE -ne 0) { throw "four-man generator build failed" }

    python (Join-Path $tools "three_man_wdl.py") --material krk --output $temporary --samples 100
    if ($LASTEXITCODE -ne 0) { throw "KRK dependency generation failed" }
    $krk = Join-Path $temporary "omega-krk-wdl-v1.omtb3"

    & $generator --material krkc --self-test --verify-counts --krk $krk
    if ($LASTEXITCODE -ne 0) { throw "KRKC self-test failed" }
    & $generator --material krkn --self-test --verify-counts --krk $krk
    if ($LASTEXITCODE -ne 0) { throw "KRKN self-test failed" }
    & $generator --material kwkn --self-test --verify-counts
    if ($LASTEXITCODE -ne 0) { throw "KWKN self-test failed" }
    & $generator --material kckw --self-test --verify-counts
    if ($LASTEXITCODE -ne 0) { throw "KCKW self-test failed" }
    & $generator --material kcck --self-test --verify-counts
    if ($LASTEXITCODE -ne 0) { throw "KCCK self-test failed" }

    $smallKrkc = Join-Path $temporary "omega-krkc-small.omtb4"
    & $generator --material krkc --small $SmallStates --krk $krk --verify --output $smallKrkc
    if ($LASTEXITCODE -ne 0) { throw "small KRKC solve failed" }
    & $generator --inspect $smallKrkc
    if ($LASTEXITCODE -ne 0) { throw "small KRKC file verification failed" }
    & $generator --probe $smallKrkc --material krkc --index 0
    if ($LASTEXITCODE -ne 0) { throw "small KRKC file probe failed" }

    $smallKrkn = Join-Path $temporary "omega-krkn-small.omtb4"
    & $generator --material krkn --small $SmallStates --krk $krk --verify --output $smallKrkn
    if ($LASTEXITCODE -ne 0) { throw "small KRKN solve failed" }
    & $generator --inspect $smallKrkn
    if ($LASTEXITCODE -ne 0) { throw "small KRKN file verification failed" }
    & $generator --probe $smallKrkn --material krkn --index 0
    if ($LASTEXITCODE -ne 0) { throw "small KRKN file probe failed" }

    $smallKwkn = Join-Path $temporary "omega-kwkn-small.omtb4"
    & $generator --material kwkn --small $SmallStates --verify --output $smallKwkn
    if ($LASTEXITCODE -ne 0) { throw "small KWKN solve failed" }
    & $generator --inspect $smallKwkn
    if ($LASTEXITCODE -ne 0) { throw "small KWKN file verification failed" }
    & $generator --probe $smallKwkn --material kwkn --index 0
    if ($LASTEXITCODE -ne 0) { throw "small KWKN file probe failed" }

    $smallKckw = Join-Path $temporary "omega-kckw-small.omtb4"
    & $generator --material kckw --small $SmallStates --verify --output $smallKckw
    if ($LASTEXITCODE -ne 0) { throw "small KCKW solve failed" }
    & $generator --inspect $smallKckw
    if ($LASTEXITCODE -ne 0) { throw "small KCKW file verification failed" }
    & $generator --probe $smallKckw --material kckw --index 0
    if ($LASTEXITCODE -ne 0) { throw "small KCKW file probe failed" }

    $smallKcck = Join-Path $temporary "omega-kcck-small.omtb4"
    & $generator --material kcck --small $SmallStates --verify --output $smallKcck
    if ($LASTEXITCODE -ne 0) { throw "small KCCK solve failed" }
    & $generator --inspect $smallKcck
    if ($LASTEXITCODE -ne 0) { throw "small KCCK file verification failed" }
    & $generator --probe $smallKcck --material kcck --index 0
    if ($LASTEXITCODE -ne 0) { throw "small KCCK file probe failed" }

    $badSquareCount = Join-Path $temporary "omega-kcck-bad-square-count.omtb4"
    New-MutatedHeaderCopy $smallKcck $badSquareCount '"square_count":104' '"square_count":105'
    if (-not (Test-ArtifactRejected $generator $badSquareCount)) {
        throw "KCCK reader accepted a mismatched square count"
    }
    $badComplete = Join-Path $temporary "omega-kcck-bad-complete.omtb4"
    New-MutatedHeaderCopy $smallKcck $badComplete '"complete":false' '"complete":true'
    if (-not (Test-ArtifactRejected $generator $badComplete)) {
        throw "KCCK reader accepted mismatched completeness"
    }
    $badCodes = Join-Path $temporary "omega-kcck-bad-codes.omtb4"
    New-MutatedHeaderCopy $smallKcck $badCodes `
        '"codes":{"invalid":0,"loss":2,"draw":3,"win":4}' `
        '"codes":{"invalid":0,"loss":2,"draw":3,"win":5}'
    if (-not (Test-ArtifactRejected $generator $badCodes)) {
        throw "KCCK reader accepted a mismatched WDL code map"
    }

    if ($SmallStates -eq 100000) {
        $reader = [IO.File]::OpenText($smallKrkc)
        try { $krkcHeader = ($reader.ReadLine() | ConvertFrom-Json) } finally { $reader.Dispose() }
        if ($krkcHeader.legal_count -ne 85795 -or $krkcHeader.counts.invalid -ne 14205 -or
            $krkcHeader.counts.loss -ne 2 -or $krkcHeader.counts.draw -ne 85785 -or
            $krkcHeader.counts.win -ne 8 -or
            $krkcHeader.payload_sha256 -ne "cee3e49ad3ffc16c4c940cb85e80fa223d75438b7f5daaa37b656c3196ae15b2") {
            throw "small KRKC deterministic regression counts changed"
        }

        $reader = [IO.File]::OpenText($smallKrkn)
        try { $krknHeader = ($reader.ReadLine() | ConvertFrom-Json) } finally { $reader.Dispose() }
        if ($krknHeader.legal_count -ne 87191 -or $krknHeader.counts.invalid -ne 12809 -or
            $krknHeader.counts.loss -ne 0 -or $krknHeader.counts.draw -ne 87191 -or
            $krknHeader.counts.win -ne 0 -or
            $krknHeader.payload_sha256 -ne "29e4149b1594929a2047b7650da5ae34cfd0eccbecc59a5988264e7db183da58") {
            throw "small KRKN deterministic regression counts changed"
        }

        $reader = [IO.File]::OpenText($smallKwkn)
        try { $kwknHeader = ($reader.ReadLine() | ConvertFrom-Json) } finally { $reader.Dispose() }
        if ($kwknHeader.legal_count -ne 90753 -or $kwknHeader.counts.invalid -ne 9247 -or
            $kwknHeader.counts.loss -ne 0 -or $kwknHeader.counts.draw -ne 90753 -or
            $kwknHeader.counts.win -ne 0 -or
            $kwknHeader.payload_sha256 -ne "233f142577620eb3225e7815d4e96a0c956a13246e8ef9d2ce04fdd3647333c8") {
            throw "small KWKN deterministic regression counts changed"
        }

        $reader = [IO.File]::OpenText($smallKckw)
        try { $kckwHeader = ($reader.ReadLine() | ConvertFrom-Json) } finally { $reader.Dispose() }
        if ($kckwHeader.legal_count -ne 89623 -or $kckwHeader.counts.invalid -ne 10377 -or
            $kckwHeader.counts.loss -ne 0 -or $kckwHeader.counts.draw -ne 89623 -or
            $kckwHeader.counts.win -ne 0 -or
            $kckwHeader.payload_sha256 -ne "149e6e18a47857c6a2a44c98a8cb00fa63630883f47ba4bc49665c089b6c95a1") {
            throw "small KCKW deterministic regression counts changed"
        }

        $reader = [IO.File]::OpenText($smallKcck)
        try { $kcckHeader = ($reader.ReadLine() | ConvertFrom-Json) } finally { $reader.Dispose() }
        if ($kcckHeader.legal_count -ne 87535 -or $kcckHeader.counts.invalid -ne 12465 -or
            $kcckHeader.counts.loss -ne 71 -or $kcckHeader.counts.draw -ne 87207 -or
            $kcckHeader.counts.win -ne 257 -or
            $kcckHeader.payload_sha256 -ne "5a4d3882ce9f4b117d19919ac77aecd1d667271356e24611fd2dc58ae703537c") {
            throw "small KCCK deterministic regression counts changed"
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($KrknFullPath)) {
        $KrknFullPath = [IO.Path]::GetFullPath($KrknFullPath)
        if (-not (Test-Path -LiteralPath $KrknFullPath -PathType Leaf)) {
            throw "Missing full KRKN artifact: $KrknFullPath"
        }
        & $generator --inspect $KrknFullPath
        if ($LASTEXITCODE -ne 0) { throw "full KRKN file verification failed" }
        $reader = [IO.File]::OpenText($KrknFullPath)
        try { $fullHeader = ($reader.ReadLine() | ConvertFrom-Json) } finally { $reader.Dispose() }
        if (-not $fullHeader.complete -or $fullHeader.state_count -ne 27594696 -or
            $fullHeader.legal_count -ne 23034346 -or $fullHeader.counts.invalid -ne 4560350 -or
            $fullHeader.counts.loss -ne 2564 -or $fullHeader.counts.draw -ne 23005864 -or
            $fullHeader.counts.win -ne 25918 -or
            $fullHeader.payload_sha256 -ne "fa0c5095e7ea21970c935a5f7af6687760696ca38c525aed95a4917705d7f0e6") {
            throw "full KRKN deterministic regression counts changed"
        }
        $probes = @(& $generator --probe $KrknFullPath --material krkn `
            --state 34,77,102,45,0 --state 12,55,68,26,1)
        if ($LASTEXITCODE -ne 0 -or $probes -notcontains "index=24934484 wdl=draw code=3" -or
            $probes -notcontains "index=11739039 wdl=draw code=3") {
            throw "full KRKN suite probes changed"
        }
    }


    if (-not [string]::IsNullOrWhiteSpace($KwknFullPath)) {
        $KwknFullPath = [IO.Path]::GetFullPath($KwknFullPath)
        if (-not (Test-Path -LiteralPath $KwknFullPath -PathType Leaf)) {
            throw "Missing full KWKN artifact: $KwknFullPath"
        }
        & $generator --inspect $KwknFullPath
        if ($LASTEXITCODE -ne 0) { throw "full KWKN file verification failed" }
        $reader = [IO.File]::OpenText($KwknFullPath)
        try { $fullHeader = ($reader.ReadLine() | ConvertFrom-Json) } finally { $reader.Dispose() }
        if (-not $fullHeader.complete -or $fullHeader.state_count -ne 27594696 -or
            $fullHeader.legal_count -ne 24078355 -or $fullHeader.counts.invalid -ne 3516341 -or
            $fullHeader.counts.loss -ne 17131 -or $fullHeader.counts.draw -ne 23997362 -or
            $fullHeader.counts.win -ne 63862 -or
            $fullHeader.payload_sha256 -ne "8aedc0255122da32dc6c5684e673b5454eee55878caefc1908b429993cd6e2b8") {
            throw "full KWKN deterministic regression counts changed"
        }
        $probes = @(& $generator --probe $KwknFullPath --material kwkn `
            --state 88,97,102,0,1)
        if ($LASTEXITCODE -ne 0 -or -not ($probes -match "wdl=loss code=2")) {
            throw "full KWKN checkmate fixture changed"
        }
        $summary = @(& $generator --summary $KwknFullPath)
        if ($LASTEXITCODE -ne 0 -or
            $summary -notcontains "wizard-to-move invalid=1916208 loss=12239 draw=11853479 win=15422" -or
            $summary -notcontains "knight-to-move invalid=1600133 loss=4892 draw=12143883 win=48440" -or
            -not ($summary -match "terminal-checkmate") -or
            -not ($summary -match "mate-in-one-win") -or
            -not ($summary -match "nonterminal-forced-win") -or
            -not ($summary -match "nonterminal-forced-loss") -or
            -not ($summary -match "turn-independent-wizard-win") -or
            -not ($summary -match "turn-independent-knight-win")) {
            throw "full KWKN turn populations or decisive witnesses changed"
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($KckwFullPath)) {
        $KckwFullPath = [IO.Path]::GetFullPath($KckwFullPath)
        if (-not (Test-Path -LiteralPath $KckwFullPath -PathType Leaf)) {
            throw "Missing full KCKW artifact: $KckwFullPath"
        }
        & $generator --inspect $KckwFullPath
        if ($LASTEXITCODE -ne 0) { throw "full KCKW file verification failed" }
        $reader = [IO.File]::OpenText($KckwFullPath)
        try { $fullHeader = ($reader.ReadLine() | ConvertFrom-Json) } finally { $reader.Dispose() }
        if (-not $fullHeader.complete -or $fullHeader.state_count -ne 27594696 -or
            $fullHeader.legal_count -ne 23651215 -or $fullHeader.counts.invalid -ne 3943481 -or
            $fullHeader.counts.loss -ne 4647 -or $fullHeader.counts.draw -ne 23631170 -or
            $fullHeader.counts.win -ne 15398 -or
            $fullHeader.payload_sha256 -ne "7d6f40e6ebb0bb4817bc9c177c0a84fe3cfe136de3941cd75076aca1e19b2d59" -or
            $fullHeader.rules_sha256 -ne "adb94009588fe013d37c5984717e63638fb0cb35ad816924b1c6b3cf5973b66b" -or
            $fullHeader.capture_policy_sha256 -ne "e1feaf717c6f4ba6abed046a2577ecd00714ea2c4c3e0b3aa6f7fa745d0c364f") {
            throw "full KCKW deterministic regression metadata changed"
        }
        if ((Get-FileHash -LiteralPath $KckwFullPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne
            "d1d404abf0dbd2958230cfd0e67c015b31c264258550066d9cc3851839d46353") {
            throw "full KCKW container checksum changed"
        }
        $probes = @(& $generator --probe $KckwFullPath --material kckw `
            --state 1,11,100,3,1)
        if ($LASTEXITCODE -ne 0 -or $probes -notcontains "index=1287937 wdl=loss code=2") {
            throw "full KCKW checkmate fixture changed"
        }
        $summary = @(& $generator --summary $KckwFullPath)
        if ($LASTEXITCODE -ne 0 -or
            $summary -notcontains "champion-to-move invalid=2027273 loss=3045 draw=11760837 win=6193" -or
            $summary -notcontains "wizard-to-move invalid=1916208 loss=1602 draw=11870333 win=9205" -or
            $summary -notcontains "material-side-wins champion=7795 wizard=12250 draws=23631170 decisive=20045" -or
            $summary -notcontains "turn-paired-placements both-legal=10835275 champion-wins-both=1407 wizard-wins-both=2634 split-decisive=0 decisive-draw-mixed=11349 draws-both=10819885 at-least-one-invalid=2962073" -or
            -not ($summary -match "terminal-checkmate") -or
            -not ($summary -match "mate-in-one-win") -or
            -not ($summary -match "nonterminal-forced-win") -or
            -not ($summary -match "nonterminal-forced-loss") -or
            -not ($summary -match "turn-independent-champion-win") -or
            -not ($summary -match "turn-independent-wizard-win")) {
            throw "full KCKW populations or decisive witnesses changed"
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($KcckFullPath)) {
        $KcckFullPath = [IO.Path]::GetFullPath($KcckFullPath)
        if (-not (Test-Path -LiteralPath $KcckFullPath -PathType Leaf)) {
            throw "Missing full KCCK artifact: $KcckFullPath"
        }
        & $generator --inspect $KcckFullPath
        if ($LASTEXITCODE -ne 0) { throw "full KCCK file verification failed" }
        $reader = [IO.File]::OpenText($KcckFullPath)
        try { $fullHeader = ($reader.ReadLine() | ConvertFrom-Json) } finally { $reader.Dispose() }
        if (-not $fullHeader.complete -or $fullHeader.state_count -ne 27594696 -or
            $fullHeader.legal_count -ne 23638870 -or $fullHeader.counts.invalid -ne 3955826 -or
            $fullHeader.counts.loss -ne 11146894 -or $fullHeader.counts.draw -ne 1852083 -or
            $fullHeader.counts.win -ne 10639893 -or
            $fullHeader.payload_sha256 -ne "35f9d8bcb3b283dec2f918fcc4c5d62355edc2dee3ee297e16dd42a91940678e" -or
            $fullHeader.rules_sha256 -ne "ac44f4152d0c619468addc65c580c064a1e8c5bc6c31509f50243739e8f4431f" -or
            $fullHeader.capture_policy_sha256 -ne "286a4e80294c4118093c8223b3414859c3a8f634ae401ee49cecb4b636c7172b") {
            throw "full KCCK deterministic regression metadata changed"
        }
        if ((Get-FileHash -LiteralPath $KcckFullPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne
            "bff047fab9141666ae13f80db17ae2d5d5a808bdd115b131180e529bf493a244") {
            throw "full KCCK container checksum changed"
        }
        $probes = @(& $generator --probe $KcckFullPath --material kcck `
            --state 1,0,100,11,1 --state 1,0,100,2,1)
        if ($LASTEXITCODE -ne 0 -or
            $probes -notcontains "index=1081911 wdl=loss code=2" -or
            $probes -notcontains "index=1081893 wdl=draw code=3") {
            throw "full KCCK mate/stalemate fixtures changed"
        }
        $summary = @(& $generator --summary $KcckFullPath)
        if ($LASTEXITCODE -ne 0 -or
            $summary -notcontains "attacker-to-move invalid=3064208 loss=0 draw=93247 win=10639893" -or
            $summary -notcontains "defender-to-move invalid=891618 loss=11146894 draw=1758836 win=0" -or
            $summary -notcontains "material-side-wins attacker=21786787 defender=0 draws=1852083 decisive=21786787" -or
            $summary -notcontains "turn-paired-placements both-legal=10733140 attacker-wins-both=9872538 defender-wins-both=0 split-decisive=0 decisive-draw-mixed=767355 draws-both=93247 at-least-one-invalid=3064208" -or
            $summary -notcontains "KCCK label-swap-orbits invalid=1978509 loss=5574617 draw=926142 win=5321116 fixed=6072" -or
            $summary -notcontains "KCCK exhaustive D4 and Champion-label swap invariance: PASS" -or
            -not ($summary -match "terminal-checkmate") -or
            -not ($summary -match "mate-in-one-win") -or
            -not ($summary -match "nonterminal-forced-win") -or
            -not ($summary -match "nonterminal-forced-loss") -or
            -not ($summary -match "turn-independent-attacker-win")) {
            throw "full KCCK populations, symmetry, or decisive witnesses changed"
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($KcckDtmFullPath)) {
        if ([string]::IsNullOrWhiteSpace($KcckFullPath)) {
            throw "-KcckDtmFullPath requires -KcckFullPath for source binding"
        }
        $KcckDtmFullPath = [IO.Path]::GetFullPath($KcckDtmFullPath)
        if (-not (Test-Path -LiteralPath $KcckDtmFullPath -PathType Leaf)) {
            throw "Missing full KCCK DTM artifact: $KcckDtmFullPath"
        }
        $inspect = @(& $generator --inspect-dtm $KcckDtmFullPath --dtm-wdl $KcckFullPath)
        if ($LASTEXITCODE -ne 0 -or
            -not ($inspect -match "states=27594696 decisive=21786787 max=40") -or
            -not ($inspect -match "sha256=b702ce64eb13610a9d4a952d8bd9e72340e809775617c6c19b229fcc41de1748") -or
            $inspect -notcontains "KCCK DTM source-WDL checksum, sentinel map, and parity: PASS") {
            throw "full KCCK DTM inspection or WDL binding changed"
        }
        if ((Get-FileHash -LiteralPath $KcckDtmFullPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne
            "e6f12c4eda6064df9fe81f984d5d86222eb428552507401fae48ad0eed22275c") {
            throw "full KCCK DTM container checksum changed"
        }
        $reader = [IO.File]::OpenText($KcckDtmFullPath)
        try { $dtmHeader = ($reader.ReadLine() | ConvertFrom-Json) } finally { $reader.Dispose() }
        if ($dtmHeader.magic -ne "OMTB4DTM" -or $dtmHeader.version -ne 1 -or
            -not $dtmHeader.complete -or $dtmHeader.state_count -ne 27594696 -or
            $dtmHeader.decisive_count -ne 21786787 -or $dtmHeader.max_dtm -ne 40 -or
            $dtmHeader.within_20 -ne 17251404 -or
            $dtmHeader.within_40 -ne 21786787 -or
            $dtmHeader.beyond_100 -ne 0 -or
            $dtmHeader.source_wdl_payload_sha256 -ne
                "35f9d8bcb3b283dec2f918fcc4c5d62355edc2dee3ee297e16dd42a91940678e" -or
            $dtmHeader.payload_sha256 -ne
                "b702ce64eb13610a9d4a952d8bd9e72340e809775617c6c19b229fcc41de1748") {
            throw "full KCCK DTM deterministic metadata changed"
        }
        $probes = @(& $generator --probe-dtm $KcckDtmFullPath --dtm-wdl $KcckFullPath `
            --index 94094 --index 93985 --index 1328 --index 1081911 --index 1081893)
        if ($LASTEXITCODE -ne 0 -or
            $probes -notcontains "index=94094 AK=a0,Ca=w1,DK=g6,Cb=w3,turn=attacker dtm=39" -or
            $probes -notcontains "index=93985 AK=a0,Ca=w1,DK=f5,Cb=w3,turn=defender dtm=40" -or
            $probes -notcontains "index=1328 AK=a0,Ca=b1,DK=a2,Cb=a5,turn=attacker dtm=1" -or
            $probes -notcontains "index=1081911 AK=a1,Ca=a0,DK=w1,Cb=b1,turn=defender dtm=0" -or
            $probes -notcontains "index=1081893 AK=a1,Ca=a0,DK=w1,Cb=a2,turn=defender dtm=none") {
            throw "full KCCK DTM deterministic probes changed"
        }
        $boundary60 = @(& $generator --probe-dtm $KcckDtmFullPath `
            --dtm-wdl $KcckFullPath --halfmove 60 --index 93985)
        $boundary61 = @(& $generator --probe-dtm $KcckDtmFullPath `
            --dtm-wdl $KcckFullPath --halfmove 61 --index 93985 --index 94094)
        $boundary62 = @(& $generator --probe-dtm $KcckDtmFullPath `
            --dtm-wdl $KcckFullPath --halfmove 62 --index 94094)
        if ($LASTEXITCODE -ne 0 -or
            $boundary60 -notcontains
                "index=93985 AK=a0,Ca=w1,DK=f5,Cb=w3,turn=defender dtm=40 halfmove=60 budget=40 rule=theoretical-result-safe" -or
            $boundary61 -notcontains
                "index=93985 AK=a0,Ca=w1,DK=f5,Cb=w3,turn=defender dtm=40 halfmove=61 budget=39 rule=blessed-loss" -or
            $boundary61 -notcontains
                "index=94094 AK=a0,Ca=w1,DK=g6,Cb=w3,turn=attacker dtm=39 halfmove=61 budget=39 rule=theoretical-result-safe" -or
            $boundary62 -notcontains
                "index=94094 AK=a0,Ca=w1,DK=g6,Cb=w3,turn=attacker dtm=39 halfmove=62 budget=38 rule=cursed-win") {
            throw "full KCCK DTM 100-ply equality boundary changed"
        }
        $line = @(& $generator --line-dtm $KcckDtmFullPath --dtm-wdl $KcckFullPath `
            --index 94094)
        if ($LASTEXITCODE -ne 0 -or
            $line -notcontains
                "KCCK DTM line root-kind=canonical-index index=94094 AK=a0,Ca=w1,DK=g6,Cb=w3,turn=attacker wdl=win dtm=39" -or
            $line -notcontains
                "KCCK DTM line ply=1 move=Ca:w1-b1 preserving=1 optimal=1 index=457 AK=a0,Ca=b1,DK=g6,Cb=w3,turn=defender wdl=loss dtm=38" -or
            $line -notcontains "KCCK DTM line checkmate plies=39 PASS" -or
            @($line -match "^KCCK DTM line ply=").Count -ne 39) {
            throw "full KCCK DTM canonical optimal line changed"
        }
        $rotated = @(& $generator --line-dtm $KcckDtmFullPath --dtm-wdl $KcckFullPath `
            --state 99,102,33,100,0)
        $rotatedExit = $LASTEXITCODE
        $swapped = @(& $generator --line-dtm $KcckDtmFullPath --dtm-wdl $KcckFullPath `
            --state 0,102,66,100,0)
        $swappedExit = $LASTEXITCODE
        if ($rotatedExit -ne 0 -or $swappedExit -ne 0 -or
            $rotated -notcontains
                "KCCK DTM line root-kind=raw-state index=94094 AK=j9,Ca=w3,DK=d3,Cb=w1,turn=attacker wdl=win dtm=39" -or
            $rotated -notcontains "KCCK DTM line checkmate plies=39 PASS" -or
            $swapped -notcontains
                "KCCK DTM line root-kind=raw-state index=104486 AK=a0,Ca=w3,DK=g6,Cb=w1,turn=attacker wdl=win dtm=39" -or
            $swapped -notcontains "KCCK DTM line checkmate plies=39 PASS") {
            throw "full KCCK DTM raw-orientation or Champion-label line parity changed"
        }
        $atlas = @(& $generator --atlas-dtm $KcckDtmFullPath --dtm-wdl $KcckFullPath)
        if ($LASTEXITCODE -ne 0 -or
            $atlas -notcontains
                "KCCK atlas attacker-win detached-champions=0 count=9818608 mean=16.307 p50=17 p90=21 p99=23 max=37" -or
            $atlas -notcontains
                "KCCK atlas attacker-win detached-champions=2 count=10945 mean=25.492 p50=25 p90=29 p99=29 max=39" -or
            $atlas -notcontains
                "KCCK atlas attacker-turn detached-champions=0 wins=9818608 draws=34301 draw-share-percent=0.348" -or
            $atlas -notcontains
                "KCCK atlas attacker-turn detached-champions=2 wins=10945 draws=3226 draw-share-percent=22.765" -or
            $atlas -notcontains
                "KCCK atlas attacker-win defender-region=interior count=6242222 mean=17.583 p50=17 p90=21 p99=25 max=39" -or
            $atlas -notcontains
                "KCCK atlas attacker-win defender-region=detached count=503959 mean=13.588 p50=13 p90=19 p99=21 max=27" -or
            $atlas -notcontains
                "KCCK atlas attacker-turn defender-region=interior wins=6242222 draws=46476 draw-share-percent=0.739" -or
            $atlas -notcontains
                "KCCK atlas attacker-turn defender-region=detached wins=503959 draws=11611 draw-share-percent=2.252" -or
            $atlas -notcontains
                "KCCK atlas mate-shells total=3352 no-attacker-king-zone-control=2120 mutual-champion-support=2616" -or
            $atlas -notcontains
                "KCCK atlas mate-orbits d4-and-label-swap=1676 fixed-label-swap=0" -or
            $atlas -notcontains
                "KCCK atlas mate-shell region=regular-edge checking-champions=2 count=58" -or
            $atlas -notcontains "KCCK atlas max-roots attacker=22 defender=22") {
            throw "full KCCK mating-atlas census changed"
        }
    }

    Write-Host "Four-man KRKC/KRKN/KWKN/KCKW/KCCK bounded and requested full-artifact tests passed"
} finally {
    if (Test-Path -LiteralPath $temporary) {
        Remove-Item -LiteralPath $temporary -Recurse -Force
    }
}
