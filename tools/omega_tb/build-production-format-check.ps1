param(
    [string]$OutputPath = "",
    [ValidateSet("Release", "Debug")]
    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$tools = Join-Path $root "tools\omega_tb"
$source = Join-Path $root "src"
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $root ".build-omega-tb\production_format_check.exe"
}
$OutputPath = [IO.Path]::GetFullPath($OutputPath)
$objectDirectory = Join-Path (Split-Path -Parent $OutputPath) "production-format-check-obj"
New-Item -ItemType Directory -Force -Path $objectDirectory | Out-Null

function Import-VcVars {
    if ((Get-Command cl.exe -ErrorAction SilentlyContinue) -and
        (Get-Command link.exe -ErrorAction SilentlyContinue)) { return }
    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path -LiteralPath $vswhere)) {
        throw "cl.exe and vswhere.exe were not found"
    }
    $installation = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if ([string]::IsNullOrWhiteSpace($installation)) {
        throw "Visual Studio C++ tools were not found"
    }
    $vcvars = Join-Path $installation "VC\Auxiliary\Build\vcvars64.bat"
    $environment = & cmd.exe /d /s /c "`"$vcvars`" >nul && set"
    if ($LASTEXITCODE -ne 0) { throw "vcvars64.bat failed" }
    foreach ($line in $environment) {
        $separator = $line.IndexOf('=')
        if ($separator -gt 0) {
            [Environment]::SetEnvironmentVariable($line.Substring(0, $separator), $line.Substring($separator + 1), "Process")
        }
    }
    if (-not (Get-Command cl.exe -ErrorAction SilentlyContinue)) {
        throw "vcvars64.bat did not expose cl.exe"
    }
}

Import-VcVars
$compile = @("/nologo", "/EHsc", "/std:c++14", "/W4", "/I$source")
if ($Configuration -eq "Debug") { $compile += @("/Od", "/Zi") } else { $compile += "/O2" }

$formatObject = Join-Path $objectDirectory "production_format.obj"
$checkObject = Join-Path $objectDirectory "production_format_check.obj"
& cl.exe @compile "/c" (Join-Path $source "production_format.cpp") "/Fo:$formatObject"
if ($LASTEXITCODE -ne 0) { throw "production-format reader compilation failed" }
& cl.exe @compile "/c" (Join-Path $tools "production_format_check.cpp") "/Fo:$checkObject"
if ($LASTEXITCODE -ne 0) { throw "production-format checker compilation failed" }
& link.exe "/NOLOGO" $formatObject $checkObject "/OUT:$OutputPath"
if ($LASTEXITCODE -ne 0) { throw "production-format checker link failed" }
Write-Host "Built $OutputPath"
