param(
    [string]$OutputPath = "",
    [ValidateSet("Release", "Debug")]
    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"

function Import-VcVars {
    if (Get-Command cl.exe -ErrorAction SilentlyContinue) { return }
    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path -LiteralPath $vswhere)) {
        throw "cl.exe and vswhere.exe were not found"
    }
    $installation = & $vswhere -latest -products * `
        -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
        -property installationPath
    if ([string]::IsNullOrWhiteSpace($installation)) {
        throw "Visual Studio C++ tools were not found"
    }
    $vcvars = Join-Path $installation "VC\Auxiliary\Build\vcvars64.bat"
    $environment = & cmd.exe /d /s /c "`"$vcvars`" >nul && set"
    if ($LASTEXITCODE -ne 0) { throw "vcvars64.bat failed" }
    foreach ($line in $environment) {
        $separator = $line.IndexOf("=")
        if ($separator -gt 0) {
            [Environment]::SetEnvironmentVariable(
                $line.Substring(0, $separator),
                $line.Substring($separator + 1),
                "Process"
            )
        }
    }
    if (-not (Get-Command cl.exe -ErrorAction SilentlyContinue)) {
        throw "vcvars64.bat did not expose cl.exe"
    }
}

Import-VcVars

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $root ".build-omega-tb\kcck_draw_analysis.exe"
}
$OutputPath = [IO.Path]::GetFullPath($OutputPath)
$outputDirectory = Split-Path -Parent $OutputPath
New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null

$objects = Join-Path $outputDirectory "kcck-draw-analysis-obj"
New-Item -ItemType Directory -Force -Path $objects | Out-Null
$compile = @(
    "/nologo", "/EHsc", "/std:c++17", "/I$PSScriptRoot",
    "/Fd:$(Join-Path $objects 'kcck-draw-analysis.pdb')"
)
if ($Configuration -eq "Debug") {
    $compile += "/Od"
} else {
    $compile += "/O2"
}

$coreObject = Join-Path $objects "four_man_core.obj"
$analysisObject = Join-Path $objects "kcck_draw_analysis.obj"
& cl.exe @compile "/c" (Join-Path $PSScriptRoot "four_man_core.cpp") "/Fo:$coreObject"
if ($LASTEXITCODE -ne 0) { throw "Failed to compile four_man_core.cpp" }
& cl.exe @compile "/c" (Join-Path $PSScriptRoot "kcck_draw_analysis.cpp") "/Fo:$analysisObject"
if ($LASTEXITCODE -ne 0) { throw "Failed to compile kcck_draw_analysis.cpp" }
& link.exe "/NOLOGO" $coreObject $analysisObject "/OUT:$OutputPath"
if ($LASTEXITCODE -ne 0) { throw "Failed to link KCCK draw analyzer" }

Write-Output $OutputPath
