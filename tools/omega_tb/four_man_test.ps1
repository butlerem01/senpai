param(
    [int]$SmallStates = 100000
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

    & $generator --self-test --verify-counts --krk $krk
    if ($LASTEXITCODE -ne 0) { throw "four-man self-test failed" }

    $small = Join-Path $temporary "omega-krkc-small.omtb4"
    & $generator --small $SmallStates --krk $krk --verify --output $small
    if ($LASTEXITCODE -ne 0) { throw "small four-man solve failed" }
    & $generator --inspect $small
    if ($LASTEXITCODE -ne 0) { throw "small four-man file verification failed" }

    if ($SmallStates -eq 100000) {
        $reader = [IO.File]::OpenText($small)
        try { $header = ($reader.ReadLine() | ConvertFrom-Json) } finally { $reader.Dispose() }
        if ($header.legal_count -ne 85795 -or $header.counts.invalid -ne 14205 -or
            $header.counts.loss -ne 2 -or $header.counts.draw -ne 85785 -or
            $header.counts.win -ne 8 -or
            $header.payload_sha256 -ne "cee3e49ad3ffc16c4c940cb85e80fa223d75438b7f5daaa37b656c3196ae15b2") {
            throw "small four-man deterministic regression counts changed"
        }
    }

    Write-Host "Four-man KRKC small solve and Bellman tests passed"
} finally {
    if (Test-Path -LiteralPath $temporary) {
        Remove-Item -LiteralPath $temporary -Recurse -Force
    }
}
