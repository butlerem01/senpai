param(
    [string]$KrkInput = "",
    [string]$KckInput = "",
    [string]$KrkcInput = "",
    [string]$KrknInput = "",
    [string]$KwknInput = "",
    [string]$KckwInput = "",
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
if ([string]::IsNullOrWhiteSpace($KckInput)) {
    $KckInput = Join-Path $root ".build-omega-tb\omega-kck-wdl-v1.omtb3"
}
if ([string]::IsNullOrWhiteSpace($KrkcInput)) {
    $KrkcInput = Join-Path $root "build\omega-tb\omega-krkc-wdl-v1.omtb4"
}
if ([string]::IsNullOrWhiteSpace($KrknInput)) {
    $KrknInput = Join-Path $root "build\omega-tb\omega-krkn-wdl-v1.omtb4"
}
if ([string]::IsNullOrWhiteSpace($KwknInput)) {
    $KwknInput = Join-Path $root "build\omega-tb\omega-kwkn-wdl-v1.omtb4"
}
if ([string]::IsNullOrWhiteSpace($KckwInput)) {
    $KckwInput = Join-Path $root ".build-omega-tb\omega-kckw-wdl-v1.omtb4"
}
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $root ".build-omega-tb\production"
}

$sources = @($KrkInput, $KckInput, $KrkcInput, $KrknInput, $KwknInput, $KckwInput)
foreach ($sourcePath in $sources) {
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
        throw "Missing source artifact: $sourcePath"
    }
}
$expectedKckwSourceHash = "d1d404abf0dbd2958230cfd0e67c015b31c264258550066d9cc3851839d46353"
$actualKckwSourceHash = (Get-FileHash -LiteralPath $KckwInput -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualKckwSourceHash -ne $expectedKckwSourceHash) {
    throw "KCKW source container checksum changed: $actualKckwSourceHash"
}

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$converter = Join-Path $tools "convert_to_production.py"
$jobs = @(
    @{ Material = "KRK";  Input = $KrkInput;  Output = "omega-krk-wdl-v1.omtb";  Counts = "38783,339,0,232962,0,1732"; Dependency = $false },
    @{ Material = "KCK";  Input = $KckInput;  Output = "omega-kck-wdl-v1.omtb";  Counts = "29037,0,0,244779,0,0"; Dependency = $false },
    @{ Material = "KRKC"; Input = $KrkcInput; Output = "omega-krkc-wdl-v1.omtb"; Counts = "4987490,2909,0,22576396,0,27901"; Dependency = $true },
    @{ Material = "KRKN"; Input = $KrknInput; Output = "omega-krkn-wdl-v1.omtb"; Counts = "4560350,2564,0,23005864,0,25918"; Dependency = $true },
    @{ Material = "KWKN"; Input = $KwknInput; Output = "omega-kwkn-wdl-v1.omtb"; Counts = "3516341,17131,0,23997362,0,63862"; Dependency = $false },
    @{ Material = "KCKW"; Input = $KckwInput; Output = "omega-kckw-wdl-v1.omtb"; Counts = "3943481,4647,0,23631170,0,15398"; Dependency = $false }
)

foreach ($job in $jobs) {
    $outputPath = Join-Path $OutputDirectory $job.Output
    $arguments = @($converter, "--input", $job.Input, "--output", $outputPath)
    if ($job.Dependency) { $arguments += @("--krk-dependency", $KrkInput) }
    & python @arguments
    if ($LASTEXITCODE -ne 0) { throw "$($job.Material) production conversion failed" }
}
$kckwOutput = Join-Path $OutputDirectory "omega-kckw-wdl-v1.omtb"
$expectedKckwProductionHash = "189c55def880086582fd2af97aeb436d9409a89c23c6da4598b06e950f420c77"
$actualKckwProductionHash = (Get-FileHash -LiteralPath $kckwOutput -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualKckwProductionHash -ne $expectedKckwProductionHash) {
    throw "KCKW production container checksum changed: $actualKckwProductionHash"
}

$checker = Join-Path $root ".build-omega-tb\production_format_check.exe"
& (Join-Path $tools "build-production-format-check.ps1") `
    -OutputPath $checker -Configuration $Configuration
if ($LASTEXITCODE -ne 0) { throw "native production checker build failed" }

foreach ($job in $jobs) {
    $outputPath = Join-Path $OutputDirectory $job.Output
    & $checker --input $outputPath --material $job.Material --counts $job.Counts
    if ($LASTEXITCODE -ne 0) { throw "native $($job.Material) production validation failed" }
}

Write-Host "Six-table production directory verified: $([IO.Path]::GetFullPath($OutputDirectory))"
foreach ($job in $jobs) {
    $outputPath = Join-Path $OutputDirectory $job.Output
    $hash = (Get-FileHash -LiteralPath $outputPath -Algorithm SHA256).Hash.ToLowerInvariant()
    Write-Host "  $($job.Material) $hash"
}
