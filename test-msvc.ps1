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

$root = $PSScriptRoot
$source = Join-Path $root "src"
$tests = Join-Path $root "tests"
$outputRoot = Join-Path $root ".build-msvc-tests-g4"
$output = Join-Path $outputRoot $Configuration
$objects = Join-Path $output "obj"
$executables = Join-Path $output "tests"
foreach ($candidate in @($outputRoot, $output, $executables)) {
    if ((Test-Path -LiteralPath $candidate) -and
        ((Get-Item -LiteralPath $candidate).Attributes -band
            [IO.FileAttributes]::ReparsePoint)) {
        throw "Refusing to build through a reparse point: $candidate"
    }
}
New-Item -ItemType Directory -Force -Path $objects, $executables | Out-Null

$compile = @("/nologo", "/EHsc", "/std:c++14", "/I$source")
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
    & cl.exe @compile "/DDEBUG" "/c" $file.FullName "/Fo:$object"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to compile $($file.Name)"
    }
    $nativeObjects += $object
}

$mainObject = Join-Path $objects "main.obj"
& cl.exe @compile "/DDEBUG" "/c" (Join-Path $source "main.cpp") "/Fo:$mainObject"
if ($LASTEXITCODE -ne 0) {
    throw "Failed to compile main.cpp"
}

$engine = Join-Path $output "senpai-test.exe"
& link.exe "/NOLOGO" @nativeObjects $mainObject "/OUT:$engine"
if ($LASTEXITCODE -ne 0) {
    throw "Failed to link the protocol-test engine"
}

$testFiles = Get-ChildItem $tests -Filter "*.cpp" | Sort-Object Name
$passed = 0
foreach ($test in $testFiles) {
    $testObject = Join-Path $objects ("test-" + $test.BaseName + ".obj")
    $testExecutable = Join-Path $executables ($test.BaseName + ".exe")

    & cl.exe @compile "/c" $test.FullName "/Fo:$testObject"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to compile test $($test.Name)"
    }

    $linkObjects = @($testObject)
    if (-not $test.BaseName.StartsWith("companion_")) {
        $linkObjects += $nativeObjects
    }

    & link.exe "/NOLOGO" @linkObjects "/OUT:$testExecutable"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to link test $($test.Name)"
    }

    Write-Host "Running $($test.BaseName)"
    & $testExecutable
    if ($LASTEXITCODE -ne 0) {
        throw "Test $($test.BaseName) failed with exit code $LASTEXITCODE"
    }
    $passed++
}

& (Join-Path $tests "uci_nodes.ps1") -EnginePath $engine
if ($LASTEXITCODE -ne 0) {
    throw "UCI node-limit protocol test failed with exit code $LASTEXITCODE"
}

& (Join-Path $tests "uci_book.ps1") -EnginePath $engine
if ($LASTEXITCODE -ne 0) {
    throw "UCI opening-book protocol test failed with exit code $LASTEXITCODE"
}

& (Join-Path $tests "uci_nnue.ps1") `
    -EnginePath $engine `
    -FixtureWriterPath (Join-Path $executables "omega_nnue.exe")
if ($LASTEXITCODE -ne 0) {
    throw "UCI Omega NNUE protocol test failed with exit code $LASTEXITCODE"
}

$python = Get-Command python.exe -ErrorAction SilentlyContinue
$numpyAvailable = $false
if ($null -ne $python) {
    & $python.Source `
        (Join-Path $root "tools\omega_nnue\label_hce.py") `
        "--self-test" `
        "--cpp-evaluator" (Join-Path $executables "omega_nnue.exe")
    if ($LASTEXITCODE -ne 0) {
        throw "Omega HCE JSONL labeling test failed with exit code $LASTEXITCODE"
    }

    & $python.Source `
        (Join-Path $root "tools\omega_nnue\build_residual_targets.py") `
        "--self-test"
    if ($LASTEXITCODE -ne 0) {
        throw "Omega residual-target alignment test failed with exit code $LASTEXITCODE"
    }

    & $python.Source -c "import numpy" 2>$null
    $numpyAvailable = $LASTEXITCODE -eq 0
} else {
    Write-Host "Skipping Omega HCE JSONL labeling test: Python is unavailable"
}

if ($numpyAvailable) {
    & $python.Source `
        (Join-Path $root "tools\omega_nnue\train.py") `
        "--self-test" `
        "--quiet" `
        "--cpp-evaluator" (Join-Path $executables "omega_nnue.exe")
    if ($LASTEXITCODE -ne 0) {
        throw "Python/C++ Omega NNUE parity test failed with exit code $LASTEXITCODE"
    }

    & $python.Source `
        (Join-Path $tests "omega_nnue_arch4_parity.py") `
        "--cpp-evaluator" (Join-Path $executables "omega_nnue.exe")
    if ($LASTEXITCODE -ne 0) {
        throw "Architecture-4 Python/C++ parity test failed with exit code $LASTEXITCODE"
    }
} else {
    Write-Host "Skipping Python/C++ NNUE parity test: Python with NumPy is unavailable"
}

if ($env:OMEGA_FULL_TABLEBASE_PATH) {
    & (Join-Path $tests "uci_kcck_dtm.ps1") `
        -EnginePath $engine `
        -TablebasePath $env:OMEGA_FULL_TABLEBASE_PATH
    if ($LASTEXITCODE -ne 0) {
        throw "UCI KCCK DTM protocol test failed with exit code $LASTEXITCODE"
    }
} else {
    Write-Host "Skipping full KCCK DTM protocol test: OMEGA_FULL_TABLEBASE_PATH is not set"
}

Write-Host "$passed C++ tests and the UCI protocol tests passed ($Configuration)"
