param(
    [ValidateSet("Release", "Debug")]
    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"
$builder = Join-Path $PSScriptRoot "build-native-kcck-oracle.ps1"
$oracle = & $builder -Configuration $Configuration | Select-Object -Last 1
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $oracle)) {
    throw "Native KCCK oracle build failed"
}

& python (Join-Path $PSScriptRoot "native_kcck_parity.py") --native $oracle
if ($LASTEXITCODE -ne 0) {
    throw "Native/independent KCCK parity failed"
}
