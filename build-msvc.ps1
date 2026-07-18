param(
    [ValidateSet("Release", "Debug")]
    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command cl.exe -ErrorAction SilentlyContinue)) {
    throw "cl.exe was not found. Run this script from a Visual Studio Developer PowerShell."
}

$root = $PSScriptRoot
$source = Join-Path $root "src"
$output = Join-Path $root "build-msvc"
New-Item -ItemType Directory -Force -Path $output | Out-Null

$common = @("/nologo", "/EHsc", "/std:c++14", "/I$source")
if ($Configuration -eq "Debug") {
    $common += @("/Od", "/DDEBUG")
} else {
    $common += "/O2"
}

$nativeSources = Get-ChildItem $source -Filter "*.cpp" |
    Where-Object Name -ne "omega.cpp" |
    ForEach-Object FullName

Push-Location $output
try {
    & cl.exe @common @nativeSources "/Fe:$(Join-Path $output 'senpai.exe')"
    if ($LASTEXITCODE -ne 0) { throw "Native Senpai build failed with exit code $LASTEXITCODE." }
    Copy-Item -Force `
        (Join-Path $output "senpai.exe") `
        (Join-Path $output "senpai-omega-integrated.exe")

    & cl.exe @common (Join-Path $source "omega.cpp") "/Fe:$(Join-Path $output 'senpai-omega-companion.exe')"
    if ($LASTEXITCODE -ne 0) { throw "Omega companion build failed with exit code $LASTEXITCODE." }
} finally {
    Pop-Location
}

Write-Host "Built: $(Join-Path $output 'senpai.exe')"
Write-Host "Built: $(Join-Path $output 'senpai-omega-integrated.exe')"
Write-Host "Built: $(Join-Path $output 'senpai-omega-companion.exe')"
