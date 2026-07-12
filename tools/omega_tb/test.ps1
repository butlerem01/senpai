param()

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

Push-Location $root
try {
    python -m unittest discover -s tools/omega_tb/tests -v
    if ($LASTEXITCODE -ne 0) {
        throw "Omega tablebase foundation tests failed"
    }
} finally {
    Pop-Location
}
