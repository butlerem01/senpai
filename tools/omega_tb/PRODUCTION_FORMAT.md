# OMTBPROD v1 binary format

`production_format.py` and `src/production_format.*` implement the same
fixed-layout, little-endian container. The Python implementation is the
offline atomic writer/reference reader. Senpai's native runtime loads only
files which pass the same strict reader.

## Semantic contract

- Geometry: 104 Omega squares.
- Index: `D4-first-piece-v1`, including two role-relative side-to-move states.
- Materials:
  - KRK: strong king, rook, bare king.
  - KCK: Champion-side king, Champion, bare king.
  - KRKC: rook-side king, rook, Champion-side king, Champion.
  - KRKN: rook-side king, rook, Knight-side king, Knight.
  - KWKN: Wizard-side king, Wizard, Knight-side king, Knight.
- Historically illegal dense slots use the `invalid` WDL code.
- Current legal/index populations are fixed in the header validator:

| Material | Dense states | Legal states |
|---|---:|---:|
| KRK | 273,816 | 235,033 |
| KCK | 273,816 | 244,779 |
| KRKC | 27,594,696 | 22,607,206 |
| KRKN | 27,594,696 | 23,034,346 |
| KWKN | 27,594,696 | 24,078,355 |

The canonical SHA-256 rules fingerprint covers geometry, index version,
historical legality, King/Rook/Champion/Knight moves, the automatic 100-ply draw,
current lone-N/B/C/W insufficient-material policy, and the WDL5/DTZ16
encoding. The legacy KRK/KCK/KRKC/KRKN v1 hexadecimal value is:

```text
cbae8f8bf3e2ab2e621a2eab8ddbec36895b3378535f9ce0ba134b72382ebf39
```

A rules change requires a new fingerprint and regenerated files. The native
reader also requires the caller's expected fingerprint, preventing a valid
file for different semantics from being silently accepted.

KWKN uses a material-specific fingerprint because Wizard geometry and the
KWK/KNK theoretical capture boundary are part of its identity:

```text
67de2569c248dfb80bb308fee09fa4deebf4225fdb3880a1507b21656b204ff7
```

## WDL and DTZ payloads

WDL is one byte per dense state:

| Code | Meaning |
|---:|---|
| 0 | invalid |
| 1 | loss |
| 2 | blessed loss |
| 3 | draw |
| 4 | cursed win |
| 5 | win |

The order is signed five-valued WDL shifted by three. Blessed/cursed codes
require a DTZ payload. DTZ, when present, is one unsigned little-endian
`uint16` per dense state. The container does not guess DTZ or reinterpret a
theoretical WDL table.

The verified three-man solver predates this mapping and uses work/result codes
`invalid=0, unknown=1, loss=2, draw=3, win=4`. Pass its completed payload
through `encode_theoretical_wdl()` before `write_table()`. The conversion
rejects `unknown` rather than silently storing it as a production loss.

KCK is additionally validated against current rule policy: every legal state
must be a draw. KRKC, KRKN, and KWKN use the same container and
material-specific semantic metadata. Only an
`OMTB4WDL` artifact marked `complete=true` with `boundary=full` is eligible
for production conversion.

## Verified source conversion

`convert_to_production.py` validates the complete JSON-line source container,
its frozen rules/index metadata, outcome populations and payload checksum
before explicitly remapping the four-valued source codes to WDL5. Both
rook-based four-man families also require the exact KRK source dependency used
during generation. KWKN instead validates its embedded capture-policy checksum:

```powershell
python tools/omega_tb/convert_to_production.py `
  --input .build-omega-tb/omega-krk-wdl-v1.omtb3 `
  --output .build-omega-tb/production/omega-krk-wdl-v1.omtb

python tools/omega_tb/convert_to_production.py `
  --input build/omega-tb/omega-krkc-wdl-v1.omtb4 `
  --krk-dependency .build-omega-tb/omega-krk-wdl-v1.omtb3 `
  --output .build-omega-tb/production/omega-krkc-wdl-v1.omtb

python tools/omega_tb/convert_to_production.py `
  --input build/omega-tb/omega-krkn-wdl-v1.omtb4 `
  --krk-dependency .build-omega-tb/omega-krk-wdl-v1.omtb3 `
  --output .build-omega-tb/production/omega-krkn-wdl-v1.omtb

python tools/omega_tb/convert_to_production.py `
  --input build/omega-tb/omega-kwkn-wdl-v1.omtb4 `
  --output .build-omega-tb/production/omega-kwkn-wdl-v1.omtb
```

`OmegaTablebasePath` names a directory containing the fixed production
filenames `omega-krk-wdl-v1.omtb`, `omega-kck-wdl-v1.omtb`,
`omega-krkc-wdl-v1.omtb`, `omega-krkn-wdl-v1.omtb`, and
`omega-kwkn-wdl-v1.omtb`. Loading is
all-or-nothing.

Run `test-production-conversion.ps1` to repeat the conversions, check
byte-for-byte determinism, and load the full outputs through the native C++
reader.

## Fixed 256-byte header

| Offset | Size | Field |
|---:|---:|---|
| 0 | 8 | ASCII magic `OMTBPROD` |
| 8 | 2 | format version (`1`) |
| 10 | 2 | header size (`256`) |
| 12 | 4 | endianness marker (`0x01020304`) |
| 16 | 1 | material code |
| 17 | 1 | labelled piece count |
| 18 | 1 | byte-WDL encoding code |
| 19 | 1 | DTZ encoding code |
| 20 | 4 | flags (`bit 0 = has DTZ`) |
| 24 | 2 | square count (`104`) |
| 26 | 1 | turn-state count (`2`) |
| 27 | 1 | rules semantic ID |
| 28 | 4 | index semantic ID |
| 32 | 8 | dense state count |
| 40 | 8 | legal state count |
| 48 | 8 | WDL offset |
| 56 | 8 | WDL byte size |
| 64 | 8 | DTZ offset or zero |
| 72 | 8 | DTZ byte size or zero |
| 80 | 4 | labelled piece-kind order |
| 84 | 4 | labelled material-role order |
| 88 | 2 | meaning of turn bits zero and one |
| 90 | 6 | explicit WDL code map |
| 96 | 48 | six `uint64` outcome populations |
| 144 | 32 | rules fingerprint |
| 176 | 32 | SHA-256 of concatenated WDL and DTZ payloads |
| 208 | 32 | SHA-256 of the header with this field zeroed |
| 240 | 16 | reserved, must be zero |

Payloads are contiguous: WDL begins at byte 256; optional DTZ immediately
follows it. Extra bytes, truncation, inconsistent offsets, unsupported codes,
wrong outcome counts, and nonzero reserved bytes are rejected.

## Write and load safety

The Python writer writes to a process-specific temporary file, flushes and
`fsync`s it, validates it with the reference reader, and atomically replaces
the destination. The native offline writer likewise writes a temporary,
re-opens it through the native loader, and renames it. Production generation
should prefer the Python atomic writer.

Both readers verify the header checksum before trusting lengths, verify the
payload checksum before returning data, scan WDL codes/counts, and leave the
destination table unchanged on native load failure.

## Tests

Python tests cover KRK/KCK round trips, optional DTZ, KRKC/KRKN/KWKN frozen metadata,
invalid WDL codes, KCK policy violations, material/rules mismatch, header and
payload corruption, and truncation:

```powershell
python -m unittest tools/omega_tb/tests/test_production_format.py -v
```

`tests/production_format.cpp` covers the equivalent native loader/writer,
checks the cross-language rules digest, and is picked up automatically by
`test-msvc.ps1`.
