param(
    [int]$SmallStates = 100000,
    [string]$KrknFullPath = "",
    [string]$KwknFullPath = "",
    [string]$KckwFullPath = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$tools = Join-Path $root "tools\omega_tb"
$temporary = Join-Path ([IO.Path]::GetTempPath()) ("omega-tb4-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $temporary | Out-Null

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

    Write-Host "Four-man KRKC/KRKN/KWKN/KCKW bounded and requested full-artifact tests passed"
} finally {
    if (Test-Path -LiteralPath $temporary) {
        Remove-Item -LiteralPath $temporary -Recurse -Force
    }
}
