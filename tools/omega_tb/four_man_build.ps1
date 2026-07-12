param(
    [string]$OutputPath = "",
    [ValidateSet("Release", "Debug")]
    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$tools = Join-Path $root "tools\omega_tb"
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $root ".build-omega-tb\four_man_wdl.exe"
}
$OutputPath = [IO.Path]::GetFullPath($OutputPath)
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutputPath) | Out-Null

function Import-VcVars {
    if (Get-Command cl.exe -ErrorAction SilentlyContinue) { return }
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
$arguments = @("/nologo", "/EHsc", "/std:c++17", "/W4", "/I$tools")
if ($Configuration -eq "Debug") {
    $arguments += @("/Od", "/Zi")
} else {
    $arguments += "/O2"
}
$arguments += @(
    (Join-Path $tools "four_man_core.cpp"),
    (Join-Path $tools "four_man_wdl.cpp"),
    "/Fe:$OutputPath"
)
& cl.exe @arguments
if ($LASTEXITCODE -ne 0) { throw "four-man generator compilation failed with exit code $LASTEXITCODE" }
Write-Host "Built $OutputPath"
