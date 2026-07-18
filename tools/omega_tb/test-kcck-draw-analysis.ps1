param(
    [string]$KcckWdlPath = "",
    [string]$AnalyzerPath = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if ([string]::IsNullOrWhiteSpace($KcckWdlPath)) {
    $KcckWdlPath = Join-Path $root ".build-omega-tb\omega-kcck-wdl-v1.omtb4"
}
$KcckWdlPath = [IO.Path]::GetFullPath($KcckWdlPath)
if (-not (Test-Path -LiteralPath $KcckWdlPath)) {
    throw "Frozen KCCK WDL artifact not found: $KcckWdlPath"
}

$temporary = Join-Path ([IO.Path]::GetTempPath()) (
    "omega-kcck-draw-analysis-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $temporary | Out-Null

function Assert-Equal {
    param(
        [Parameter(Mandatory = $true)]$Actual,
        [Parameter(Mandatory = $true)]$Expected,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if ($Actual -ne $Expected) {
        throw "$Label changed: actual=$Actual expected=$Expected"
    }
}

try {
    if ([string]::IsNullOrWhiteSpace($AnalyzerPath)) {
        $AnalyzerPath = Join-Path $temporary "kcck_draw_analysis.exe"
        & (Join-Path $PSScriptRoot "build-kcck-draw-analysis.ps1") `
            -OutputPath $AnalyzerPath
        if ($LASTEXITCODE -ne 0) { throw "KCCK draw analyzer build failed" }
    }
    $AnalyzerPath = [IO.Path]::GetFullPath($AnalyzerPath)
    if (-not (Test-Path -LiteralPath $AnalyzerPath)) {
        throw "KCCK draw analyzer not found: $AnalyzerPath"
    }

    $resultPath = Join-Path $temporary "omega-kcck-draw-analysis-v1.json"
    $transcript = & $AnalyzerPath --wdl $KcckWdlPath --output $resultPath
    if ($LASTEXITCODE -ne 0) { throw "KCCK draw analysis failed" }
    if (-not ($transcript -match "^KCCK draw analysis PASS time=")) {
        throw "KCCK draw analyzer did not emit its PASS sentinel"
    }
    $result = Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json

    Assert-Equal $result.magic "OMEGA-KCCK-DRAW-ANALYSIS" "analysis magic"
    Assert-Equal $result.version 1 "analysis version"
    Assert-Equal $result.source_wdl_payload_sha256 `
        "35f9d8bcb3b283dec2f918fcc4c5d62355edc2dee3ee297e16dd42a91940678e" `
        "source WDL payload SHA-256"
    Assert-Equal $result.source_wdl_container_sha256 `
        "bff047fab9141666ae13f80db17ae2d5d5a808bdd115b131180e529bf493a244" `
        "source WDL container SHA-256"
    Assert-Equal $result.source_rules_sha256 `
        "ac44f4152d0c619468addc65c580c064a1e8c5bc6c31509f50243739e8f4431f" `
        "source rules SHA-256"
    Assert-Equal $result.source_capture_policy_sha256 `
        "286a4e80294c4118093c8223b3414859c3a8f634ae401ee49cecb4b636c7172b" `
        "source capture-policy SHA-256"

    Assert-Equal $result.draw_records 1852083 "draw records"
    Assert-Equal $result.attacker_draws 93247 "attacker-to-move draws"
    Assert-Equal $result.defender_draws 1758836 "defender-to-move draws"
    Assert-Equal $result.local.stalemate 75388 "terminal stalemates"
    Assert-Equal $result.local.capture_a 735290 "immediate Champion A captures"
    Assert-Equal $result.local.capture_b 735290 "immediate Champion B captures"
    Assert-Equal $result.local.capture_both 20263 "immediate dual captures"
    Assert-Equal $result.local.detached_corner 84773 "detached-corner records"
    Assert-Equal $result.local.both_turn_records 186494 "both-turn records"
    Assert-Equal $result.local.both_turn_placements 93247 "both-turn placements"
    Assert-Equal $result.local.draw_edges 2173842 "draw-preserving edges"
    Assert-Equal $result.local.draw_edge_digest `
        "a27f5d0e1b3bc3f28420996ad8fceefdf567eb99a3150aa9ff7bd18efe50e724" `
        "draw edge digest"
    Assert-Equal $result.local.inverse_edge_parity $true "inverse-edge parity"
    Assert-Equal $result.local.label_swap_parity $true "Champion-label parity"

    Assert-Equal $result.scc.components 1640577 "SCC count"
    Assert-Equal $result.scc.cyclic_components 2082 "cyclic SCC count"
    Assert-Equal $result.scc.cyclic_members 213588 "cyclic SCC records"
    Assert-Equal $result.scc.largest_component 101164 "largest SCC"
    Assert-Equal $result.scc.detached_corner_cycles 0 "detached-corner SCCs"
    Assert-Equal $result.scc.corner_associated_cycles 0 "corner-associated SCCs"
    Assert-Equal $result.scc.normalized_digest `
        "2dd537502d53b53f4598d95fdc134a67a632953951a530f1f2bb71849ab55a4e" `
        "normalized SCC digest"
    Assert-Equal $result.scc.reverse_iteration_parity $true `
        "reversed SCC traversal parity"
    Assert-Equal $result.reachability.unexplained 0 "unexplained draws"

    Assert-Equal $result.defender_attractor.force_stalemate 77032 `
        "forced-stalemate attractor"
    Assert-Equal $result.defender_attractor.force_capture_a 866526 `
        "forced Champion A capture attractor"
    Assert-Equal $result.defender_attractor.force_capture_b 866526 `
        "forced Champion B capture attractor"
    Assert-Equal $result.defender_attractor.force_any_capture 1712789 `
        "forced either-capture attractor"
    Assert-Equal $result.defender_attractor.force_terminal_draw 1852083 `
        "forced terminal-draw attractor"
    Assert-Equal $result.defender_attractor.terminal_avoidable 0 `
        "terminal-avoidable records"

    Assert-Equal $result.robust_both_turn.placements 93247 `
        "robust both-turn placements"
    Assert-Equal $result.robust_both_turn.defender_immediate_capture 42972 `
        "robust placements with immediate capture"
    Assert-Equal $result.robust_both_turn.either_turn_stalemate 2247 `
        "robust placements with stalemate"
    Assert-Equal $result.robust_both_turn.both_records_force_terminal 93247 `
        "robust placements in the terminal attractor"

    # The complete deterministic JSON freezes every overlap and pair cross-tab.
    Assert-Equal (Get-FileHash -Algorithm SHA256 -LiteralPath $resultPath).Hash.ToLowerInvariant() `
        "0bacbc4be3af566d891bfb45541d6558fb356446075c8d557c01d535f54d9a05" `
        "complete result SHA-256"

    Write-Output ((
        "KCCK draw-analysis contract PASS draws={0} edges={1} " +
        "scc={2} cyclic={3} terminal_attractor={4}") -f
        $result.draw_records, $result.local.draw_edges, $result.scc.components,
        $result.scc.cyclic_components,
        $result.defender_attractor.force_terminal_draw)
} finally {
    Remove-Item -LiteralPath $temporary -Recurse -Force -ErrorAction SilentlyContinue
}
