# Exact KCKW theoretical-WDL note

## Result and scope

The completed KCKW table is an exact, full-boundary theoretical-WDL solve over
the retained `D4-first-piece-v1` index. It uses the native Omega Champion and
Wizard jumps, all four detached corners, historical legality, and the current
automatic draws for KCK and KWK after a capture. A second full pass verified
every Bellman equation.

Frozen artifact facts:

- Dense slots: 27,594,696
- Legal slots: 23,651,215
- Invalid: 3,943,481
- Loss: 4,647
- Draw: 23,631,170
- Win: 15,398
- Full solve plus Bellman verification: 60.81 seconds on the development
  machine
- Source payload SHA-256:
  `7d6f40e6ebb0bb4817bc9c177c0a84fe3cfe136de3941cd75076aca1e19b2d59`
- Complete container SHA-256:
  `d1d404abf0dbd2958230cfd0e67c015b31c264258550066d9cc3851839d46353`
- Rules SHA-256:
  `adb94009588fe013d37c5984717e63638fb0cb35ad816924b1c6b3cf5973b66b`
- Capture-policy SHA-256:
  `e1feaf717c6f4ba6abed046a2577ecd00714ea2c4c3e0b3aa6f7fa745d0c364f`
- Production rules SHA-256:
  `f46be111c8f52a8b22777a83bd463fe2ad7368a260fed23b0fd8956d751415d7`
- Remapped production payload SHA-256:
  `fedd4701f498e0871cff7b8c6aa263dc0c0daf7110bb4c73b8a02b62e73981d4`
- Complete production container SHA-256:
  `189c55def880086582fd2af97aeb436d9409a89c23c6da4598b06e950f420c77`

Reproduction commands from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File tools/omega_tb/four_man_build.ps1
.\.build-omega-tb\four_man_wdl.exe --material kckw --self-test --verify-counts
.\.build-omega-tb\four_man_wdl.exe --material kckw --full --verify `
  --output .build-omega-tb/omega-kckw-wdl-v1.omtb4
.\.build-omega-tb\four_man_wdl.exe --summary `
  .build-omega-tb/omega-kckw-wdl-v1.omtb4
python tools/omega_tb/convert_to_production.py `
  --input .build-omega-tb/omega-kckw-wdl-v1.omtb4 `
  --output .build-omega-tb/production/omega-kckw-wdl-v1.omtb
```

WDL is always relative to the side to move. The exact turn split is:

| Side to move | Legal | Loss | Draw | Win | Decisive |
|---|---:|---:|---:|---:|---:|
| Champion side | 11,770,075 | 3,045 | 11,760,837 | 6,193 | 9,238 |
| Wizard side | 11,881,140 | 1,602 | 11,870,333 | 9,205 | 10,807 |
| Total | 23,651,215 | 4,647 | 23,631,170 | 15,398 | 20,045 |

Thus 99.9152% of legal canonical records are draws and 0.0848% are decisive.
Re-expressed by the material side that eventually wins, the Champion side wins
7,795 canonical records and the Wizard side wins 12,250. These are counts of
D4-canonical records, not a probability distribution over reachable game
positions and not a centipawn-value ratio. They show that neither piece can
generally force conversion in this material class; among its rare decisive
geometry, Wizard-side wins are more numerous in this index.

## Turn-paired placements

The summary also pairs the two turn records for every canonical spatial
placement. Both turn records are historically legal in 10,835,275 placements:

| Paired outcome | Placements |
|---|---:|
| Draw for either turn | 10,819,885 |
| Champion side wins for either turn | 1,407 |
| Wizard side wins for either turn | 2,634 |
| Different material winner by turn | 0 |
| One turn decisive and the other drawn | 11,349 |

Another 2,962,073 canonical placements have at least one historically illegal
turn record. "For either turn" does not imply a distance to mate; it only means
that the same material side wins both WDL records for that spatial placement.

## Verified decisive witnesses

The offline `--summary` pass regenerates legal moves for its witnesses and
checks their stored children rather than classifying from the byte value alone:

- Terminal mate: index 1,287,937,
  `CK=a1,C=b1,WK=w1,W=a3,turn=wizard` is a checkmate loss.
- Immediate mating tactic: index 1,267,332,
  `CK=a1,C=b0,WK=w1,W=a3,turn=champion` is a win with a move to terminal-loss
  child 1,287,937.
- Deeper nonterminal win: index 1,081,900,
  `CK=a1,C=a0,WK=w1,W=a6,turn=champion` has a nonterminal loss child.
- Nonterminal forced loss: index 1,081,907,
  `CK=a1,C=a0,WK=w1,W=a9,turn=wizard` has legal moves but is lost.
- Turn-independent Champion win: indices 1,081,906/1,081,907,
  `CK=a1,C=a0,WK=w1,W=a9` is won by the Champion side for either turn.
- Turn-independent Wizard win: indices 26,585,662/26,585,663,
  `CK=w1,C=f5,WK=a1,W=b1` is won by the Wizard side for either turn.

Capturing the opposing leaper is never a decisive boundary in this solve:
both KCK and KWK are policy draws. Decisive records therefore arise from mate
nets and forced non-capture geometry, not from assigning either lone leaper a
bare-king mating win.

The table contains neither DTM nor 100-ply-aware DTZ. The verified source can
be converted into the checked production container and loaded by the runtime
probe. Search consumes exact KCKW draws only; decisive records remain visible
to diagnostics but fall through to normal search. No engine evaluation term
consumes KCKW records.
