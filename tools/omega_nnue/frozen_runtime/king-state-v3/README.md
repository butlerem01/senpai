# Omega NNUE generation-3 frozen runtime

This directory preserves the exact local application binaries used by the
generation-3 teacher, parity checks, opening replay, and paired-match
confirmation.  The experiment code verifies these identities before use.
Build directories elsewhere in the repository are disposable and are not the
authoritative runtime source for generation 3.

| Role | Relative path | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| Senpai teacher and match engine | `engine/senpai.exe` | 619520 | `37b4846f6f12cd795730d6472279007a0f53d8653e2b9a0071c1d13d2eed09e0` |
| C++ NNUE parity evaluator | `evaluator/omega_nnue.exe` | 544768 | `b6bdd2310901319d85464a1cb570557dfceaee0ebfefba0563c9564728c27c04` |
| OmegaMatch assembly | `omegamatch/OmegaMatch.dll` | 176640 | `2f66adb65aaa9c1075ee27374bf8f02e95d09a1108f674cc332a6d9f7cc10e70` |
| Opening-replay assembly | `opening-replay/OmegaOpeningPrefixReplay.dll` | 54272 | `a7407261529df4908458905c3d32f46a461d76625b77e6d23ecd6f9f73e099e3` |
| Rules-only root sampler | `root-sampler/OmegaRootSampler.dll` | 44544 | `f443506d478ff1696558e6a23064e44d20a29b7a4a855221af6a18c57b395b53` |
| History snapshot helper | `history-snapshot/OmegaHistorySnapshot.dll` | 36352 | `4c66833c12debdcc4dacb9e92fffab5b979eff7a3ed5ed09cdaebabfeff2a5fb` |

Every regular file in each app-local directory is identity-pinned; symlink
and reparse entries are rejected. The complete app-local OmegaMatch bundle
contains 25 identity-pinned files and
has ordered bundle SHA-256
`36dcaea0a528f30316e54cb020971cd5d3328390b41d90325840aae55fe3d3dd`.
The complete app-local opening-replay bundle contains 26 pinned files and
has ordered bundle SHA-256
`9eb3d7f23ab6fd944a0254fd07cf8021fb4d03511e26ed32bfbe702a41bd6218`.
The root-sampler and history-snapshot bundles each contain 26 pinned files,
with ordered bundle SHA-256 values
`6f1c8ea0c2dd41430ef61d7b1ee4a99dd4449d52816a9a224503a6072c2e6ff4`
and
`432c476dc56dfe71e31af10fe6426f5e677b061cc9dc6a5819f3adf7a1575c9f`,
respectively.

All managed applications are launched by the separately identity-pinned local
host at `../.dotnet/dotnet.exe`. The complete host/fxr selection namespace is
1 file / 379728 bytes / SHA-256
`afb0fcbb37547b54c2cd3cbebdcd21d209e2aac55e277724e22fb5ea29a451dd`;
the Microsoft.NETCore.App selection namespace is 189 files / 79709188 bytes /
SHA-256
`f4aa7e85c288786345f40ce7568dcdfec46bbdf940746ed7fc8b8ad5bf85a09c`.
Both selection namespaces must contain exactly one immediate version
directory, `10.0.9`; even an empty higher-version sibling fails verification.
Every managed launch clears ambient `DOTNET_`, `CORE_`, `COREHOST_`,
`CORECLR_`, `COR_`, and `COMPLUS_` variables, then installs the frozen local
host root, patch-roll-forward, multilevel-lookup, prerelease, and diagnostics
policy. It also removes `ProgramFiles(x86)` so the x64 host cannot discover an
ambient global `coreservicing` tree. Both the app bundle and .NET selection namespaces are rehashed
immediately before and after execution. The host's default shared-store path
`../.dotnet/store` is required to remain absent before and after every launch.

Senpai's engine source files are unchanged between the build-time tree and repository commit
`fb323686395fa62519ba2f8d9561c761f9efcd00`; the executable identity above,
not a rebuild, is authoritative for the experiment.
