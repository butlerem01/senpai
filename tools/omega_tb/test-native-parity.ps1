param(
    [ValidateRange(0, 273816)]
    [int]$Samples = 5000,
    [switch]$Exhaustive,
    [ValidateSet("Release", "Debug")]
    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"
$builder = Join-Path $PSScriptRoot "build-native-oracle.ps1"
$oracle = & $builder -Configuration $Configuration | Select-Object -Last 1
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $oracle)) {
    throw "Native three-man oracle build failed"
}

$arguments = @(
    (Join-Path $PSScriptRoot "native_parity.py"),
    "--native", $oracle,
    "--samples", $Samples
)
if ($Exhaustive) { $arguments += "--exhaustive" }
& python @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Native/Python tablebase parity failed"
}
