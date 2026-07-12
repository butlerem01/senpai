param(
    [ValidateSet("Release", "Debug")]
    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"
if (-not (Get-Command cl.exe -ErrorAction SilentlyContinue)) {
    throw "cl.exe was not found. Run this script from a Visual Studio Developer PowerShell."
}
if (-not (Get-Command link.exe -ErrorAction SilentlyContinue)) {
    throw "link.exe was not found. Run this script from a Visual Studio Developer PowerShell."
}

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$source = Join-Path $root "src"
$output = Join-Path $root ".build-omega-tb\$Configuration"
$objects = Join-Path $output "obj"
New-Item -ItemType Directory -Force -Path $objects | Out-Null

$compile = @(
    "/nologo", "/EHsc", "/std:c++14", "/I$source",
    "/Fd:$(Join-Path $objects 'vc140.pdb')"
)
if ($Configuration -eq "Debug") { $compile += "/Od" } else { $compile += "/O2" }

$nativeObjects = @()
$nativeSources = Get-ChildItem $source -Filter "*.cpp" |
    Where-Object { $_.Name -ne "main.cpp" -and $_.Name -ne "omega.cpp" } |
    Sort-Object Name
foreach ($file in $nativeSources) {
    $object = Join-Path $objects ($file.BaseName + ".obj")
    & cl.exe @compile "/DDEBUG" "/c" $file.FullName "/Fo:$object"
    if ($LASTEXITCODE -ne 0) { throw "Failed to compile $($file.Name)" }
    $nativeObjects += $object
}

$oracleSource = Join-Path $PSScriptRoot "native_three_man_oracle.cpp"
$oracleObject = Join-Path $objects "native_three_man_oracle.obj"
& cl.exe @compile "/c" $oracleSource "/Fo:$oracleObject"
if ($LASTEXITCODE -ne 0) { throw "Failed to compile native_three_man_oracle.cpp" }

$oracle = Join-Path $output "native-three-man-oracle.exe"
& link.exe "/NOLOGO" $oracleObject @nativeObjects "/OUT:$oracle"
if ($LASTEXITCODE -ne 0) { throw "Failed to link native three-man oracle" }

Write-Output $oracle
