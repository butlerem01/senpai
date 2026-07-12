param(
    [string]$KrkInput = "",
    [string]$KrkcInput = "",
    [string]$OutputDirectory = "",
    [ValidateSet("Release", "Debug")]
    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$tools = Join-Path $root "tools\omega_tb"
if ([string]::IsNullOrWhiteSpace($KrkInput)) {
    $KrkInput = Join-Path $root ".build-omega-tb\omega-krk-wdl-v1.omtb3"
}
if ([string]::IsNullOrWhiteSpace($KrkcInput)) {
    $KrkcInput = Join-Path $root "build\omega-tb\omega-krkc-wdl-v1.omtb4"
}
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $root ".build-omega-tb\production"
}
foreach ($path in @($KrkInput, $KrkcInput)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing source artifact: $path" }
}
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$krkOutput = Join-Path $OutputDirectory "omega-krk-wdl-v1.omtb"
$krkcOutput = Join-Path $OutputDirectory "omega-krkc-wdl-v1.omtb"
$converter = Join-Path $tools "convert_to_production.py"

& python $converter --input $KrkInput --output $krkOutput
if ($LASTEXITCODE -ne 0) { throw "KRK production conversion failed" }
& python $converter --input $KrkcInput --output $krkcOutput --krk-dependency $KrkInput
if ($LASTEXITCODE -ne 0) { throw "KRKC production conversion failed" }

$firstKrkHash = (Get-FileHash -LiteralPath $krkOutput -Algorithm SHA256).Hash
$firstKrkcHash = (Get-FileHash -LiteralPath $krkcOutput -Algorithm SHA256).Hash
& python $converter --input $KrkInput --output $krkOutput
if ($LASTEXITCODE -ne 0) { throw "repeat KRK production conversion failed" }
& python $converter --input $KrkcInput --output $krkcOutput --krk-dependency $KrkInput
if ($LASTEXITCODE -ne 0) { throw "repeat KRKC production conversion failed" }
if ((Get-FileHash -LiteralPath $krkOutput -Algorithm SHA256).Hash -ne $firstKrkHash -or
    (Get-FileHash -LiteralPath $krkcOutput -Algorithm SHA256).Hash -ne $firstKrkcHash) {
    throw "production conversion is not deterministic"
}

$checker = Join-Path $root ".build-omega-tb\production_format_check.exe"
& (Join-Path $tools "build-production-format-check.ps1") -OutputPath $checker -Configuration $Configuration
& $checker --input $krkOutput --material KRK --counts "38783,339,0,232962,0,1732"
if ($LASTEXITCODE -ne 0) { throw "native KRK production validation failed" }
& $checker --input $krkcOutput --material KRKC --counts "4987490,2909,0,22576396,0,27901"
if ($LASTEXITCODE -ne 0) { throw "native KRKC production validation failed" }

Write-Host "Python/native full-file conversion passed; deterministic SHA256:"
Write-Host "  KRK  $firstKrkHash"
Write-Host "  KRKC $firstKrkcHash"
