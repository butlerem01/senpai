param(
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
$source = Join-Path $root "src"
$output = Join-Path $root ".build-omega-tb\$Configuration-kcck-native"
$objects = Join-Path $output "obj"
New-Item -ItemType Directory -Force -Path $objects | Out-Null

$compile = @(
    "/nologo", "/EHsc", "/std:c++14", "/DDEBUG", "/I$source",
    "/Fd:$(Join-Path $objects 'native-kcck.pdb')"
)
if ($Configuration -eq "Debug") {
    $compile += "/Od"
} else {
    $compile += "/O2"
}

$nativeObjects = @()
$nativeSources = Get-ChildItem $source -Filter "*.cpp" |
    Where-Object { $_.Name -ne "main.cpp" -and $_.Name -ne "omega.cpp" } |
    Sort-Object Name
foreach ($file in $nativeSources) {
    $object = Join-Path $objects ($file.BaseName + ".obj")
    & cl.exe @compile "/c" $file.FullName "/Fo:$object"
    if ($LASTEXITCODE -ne 0) { throw "Failed to compile $($file.Name)" }
    $nativeObjects += $object
}

$oracleSource = Join-Path $PSScriptRoot "native_kcck_oracle.cpp"
$oracleObject = Join-Path $objects "native_kcck_oracle.obj"
& cl.exe @compile "/c" $oracleSource "/Fo:$oracleObject"
if ($LASTEXITCODE -ne 0) { throw "Failed to compile native_kcck_oracle.cpp" }

$oracle = Join-Path $output "native-kcck-oracle.exe"
& link.exe "/NOLOGO" $oracleObject @nativeObjects "/OUT:$oracle"
if ($LASTEXITCODE -ne 0) { throw "Failed to link native KCCK oracle" }

Write-Output $oracle
