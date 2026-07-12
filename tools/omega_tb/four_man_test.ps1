param(
    [int]$SmallStates = 100000,
    [string]$KrknFullPath = ""
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

    Write-Host "Four-man KRKC/KRKN bounded and requested full-artifact tests passed"
} finally {
    if (Test-Path -LiteralPath $temporary) {
        Remove-Item -LiteralPath $temporary -Recurse -Force
    }
}
