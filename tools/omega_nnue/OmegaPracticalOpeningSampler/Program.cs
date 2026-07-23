using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Globalization;
using ChessLib;

const string Kind = "omega-rules-only-balanced-opening-prefix-v2";
const string ManifestKind = "omega-rules-only-balanced-opening-prefix-manifest-v2";
const string SealKind = "omega-rules-only-balanced-opening-prefix-completion-seal-v2";
const string OmegaStart =
    "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/" +
    "PPPPPPPPPP/CRNBQKBNRC[W/W/w/w] w KQkq - 0 1";

CultureInfo.CurrentCulture = CultureInfo.InvariantCulture;
CultureInfo.CurrentUICulture = CultureInfo.InvariantCulture;

if (args.Length == 1 && args[0].Equals("--self-test", StringComparison.OrdinalIgnoreCase)) {
    await SelfTest();
    Console.WriteLine("OmegaPracticalOpeningSampler self-test passed.");
    return;
}

var options = Options.Parse(args);
var output = Path.GetFullPath(options.Output);
var manifestPath = output + ".manifest.json";
var sealPath = output + ".complete.seal.json";
RefuseExisting(output, "output");
RefuseExisting(manifestPath, "manifest");
RefuseExisting(sealPath, "completion seal");
Directory.CreateDirectory(Path.GetDirectoryName(output)!);
var temporary = AdjacentTemporary(output);
var manifestTemporary = AdjacentTemporary(manifestPath);
var sealTemporary = AdjacentTemporary(sealPath);
var outputPublished = false;
var manifestPublished = false;
var sealPublished = false;

try {
    var records = await GenerateAll(options);
    await WriteJsonLines(temporary, records);
    var stagedOutput = Identity(temporary, output);
    var depthCounts = options.Depths.ToDictionary(
        depth => depth.ToString(),
        depth => records.Count(record => record.targetPlies == depth),
        StringComparer.Ordinal);
    var manifest = new {
        schemaVersion = 2,
        kind = ManifestKind,
        createdUtc = DateTime.UtcNow,
        policy = new {
            officialInitialOfen = OmegaStart,
            deterministicPrng = "SplitMix64",
            seed = options.Seed.ToString(),
            trajectories = options.Trajectories,
            workers = options.Workers,
            targetPlies = options.Depths,
            assignment = "trajectory index modulo frozen target-ply order",
            movePolicy = "uniform random permutation; first legal vertically colour-reflected full-move pair",
            verticalColourReflection = new {
                ordinarySquares = "file unchanged; rank r maps to 9-r",
                corners = new Dictionary<string, string> {
                    ["w1"] = "w4", ["w4"] = "w1",
                    ["w2"] = "w3", ["w3"] = "w2"
                },
                promotionSuffixUnchanged = true,
            },
            candidateInputs = 0,
            engineEvaluationInputs = 0,
            terminalPrefixesEmitted = 0,
            minimumPieces = options.MinimumPieces,
        },
        coverage = new {
            records = records.Length,
            depthCounts,
            uniqueMovePrefixes = records.Select(record => string.Join(' ', record.moves)).Distinct(StringComparer.Ordinal).Count(),
            uniqueFinalOfens = records.Select(record => record.finalOfen).Distinct(StringComparer.Ordinal).Count(),
        },
        runtime = new {
            framework = System.Runtime.InteropServices.RuntimeInformation.FrameworkDescription,
            samplerAssembly = Identity(Assembly.GetExecutingAssembly().Location),
            chessLibAssembly = Identity(typeof(Game).Assembly.Location),
        },
        output = stagedOutput,
        finalStageSeal = false,
    };
    await WriteTemporaryJson(manifestTemporary, manifest);
    var seal = new {
        schemaVersion = 2,
        kind = SealKind,
        createdUtc = DateTime.UtcNow,
        output = stagedOutput,
        manifest = Identity(manifestTemporary, manifestPath),
        producer = new {
            samplerAssembly = Identity(Assembly.GetExecutingAssembly().Location),
            chessLibAssembly = Identity(typeof(Game).Assembly.Location),
            framework = System.Runtime.InteropServices.RuntimeInformation.FrameworkDescription,
        },
        finalStageSeal = true,
    };
    await WriteTemporaryJson(sealTemporary, seal);
    RefuseExisting(output, "output");
    RefuseExisting(manifestPath, "manifest");
    RefuseExisting(sealPath, "completion seal");
    File.Move(temporary, output);
    outputPublished = true;
    File.Move(manifestTemporary, manifestPath);
    manifestPublished = true;
    File.Move(sealTemporary, sealPath);
    sealPublished = true;
    Console.WriteLine($"Wrote {records.Length} candidate-blind balanced Omega opening prefixes.");
    Console.WriteLine($"Output: {output}");
    Console.WriteLine($"Manifest: {manifestPath}");
    Console.WriteLine($"Completion seal: {sealPath}");
} catch {
    if (sealPublished) TryDelete(sealPath);
    if (manifestPublished) TryDelete(manifestPath);
    if (outputPublished) TryDelete(output);
    TryDelete(temporary);
    TryDelete(manifestTemporary);
    TryDelete(sealTemporary);
    throw;
}

static async Task<PrefixRecord[]> GenerateAll(Options options)
{
    var records = new PrefixRecord[options.Trajectories];
    await Parallel.ForEachAsync(
        Enumerable.Range(0, options.Trajectories),
        new ParallelOptions { MaxDegreeOfParallelism = options.Workers },
        async (index, _) => {
            records[index] = await Generate(index, options);
        });
    if (records.Any(record => record is null))
        throw new InvalidOperationException("parallel generation left an empty trajectory slot");
    return records;
}

static async Task<PrefixRecord> Generate(int index, Options options)
{
    var targetPlies = options.Depths[index % options.Depths.Length];
    var trajectorySeed = Mix(
        options.Seed ^ ((ulong)(index + 1) * 0x9E3779B97F4A7C15UL));
    var rng = new SplitMix64(trajectorySeed);
    var moves = new List<string>(targetPlies);
    using var game = CreateGame();
    while (moves.Count < targetPlies) {
        if (game.ToMove != Game.Colors.White || moves.Count % 2 != 0)
            throw new InvalidOperationException("balanced prefix escaped a White full-move boundary");
        var candidates = LegalMoves(game);
        Shuffle(candidates, ref rng);
        var accepted = false;
        foreach (var whiteMove in candidates) {
            var blackMove = MirrorMove(whiteMove);
            using var trial = await Replay(moves);
            try {
                await trial.DoMove(whiteMove, checkEndGame: false);
            } catch {
                continue;
            }
            if (!LegalMoves(trial).Contains(blackMove, StringComparer.OrdinalIgnoreCase))
                continue;
            try {
                await trial.DoMove(blackMove, checkEndGame: false);
            } catch {
                continue;
            }
            if (trial.ToMove != Game.Colors.White || !BoardIsColourReflected(trial))
                continue;
            if (trial.IsDraw() || LegalMoves(trial).Count == 0)
                continue;
            moves.Add(whiteMove.ToLowerInvariant());
            moves.Add(blackMove.ToLowerInvariant());
            await game.DoMove(whiteMove, checkEndGame: false);
            await game.DoMove(blackMove, checkEndGame: false);
            accepted = true;
            break;
        }
        if (!accepted)
            throw new InvalidOperationException(
                $"no balanced legal move pair at trajectory {index + 1}, ply {moves.Count}");
    }

    var pieces = game.Board.Squares.Count(square => square.Piece != null);
    if (pieces < options.MinimumPieces)
        throw new InvalidOperationException(
            $"trajectory {index + 1} ended below the frozen opening-piece floor");
    var finalOfen = game.GetFenString();
    var rankPayload =
        $"omega-practical-opening-v2\0{options.Seed}\0{trajectorySeed}\0" +
        $"{index}\0{targetPlies}\0{string.Join(' ', moves)}\0{finalOfen}";
    var selectionRank = Convert.ToHexString(
        SHA256.HashData(Encoding.UTF8.GetBytes(rankPayload))).ToLowerInvariant();
    return new PrefixRecord(
        schemaVersion: 2,
        kind: Kind,
        generatorSeed: options.Seed.ToString(),
        trajectoryIndex: index + 1,
        trajectorySeed: trajectorySeed.ToString(),
        targetPlies: targetPlies,
        initialOfen: OmegaStart,
        moves: moves.ToArray(),
        finalOfen: finalOfen,
        pieceCount: pieces,
        sideToMove: "w",
        selectionRank: selectionRank);
}

static Game CreateGame()
{
    var settings = new Game.GameSettings {
        Variant = ChessVariant.Omega,
        InitialFenPosition = OmegaStart,
        DrawForRepetition = 3,
    };
    settings.Players.Add(new HumanPlayer(Game.Colors.White, "Rules-only White", null));
    settings.Players.Add(new HumanPlayer(Game.Colors.Black, "Rules-only Black", null));
    var game = new Game();
    game.Init(settings);
    return game;
}

static async Task<Game> Replay(IReadOnlyList<string> moves)
{
    var game = CreateGame();
    try {
        foreach (var move in moves)
            await game.DoMove(move, checkEndGame: false);
        return game;
    } catch {
        game.Dispose();
        throw;
    }
}

static List<string> LegalMoves(Game game)
{
    var result = new List<string>();
    foreach (var square in game.Board.Squares) {
        var piece = square.Piece;
        if (piece == null || piece.Color != game.ToMove) continue;
        foreach (var target in game.GetAvailableSquares(square)) {
            if (target.Notation.Equals(square.Notation, StringComparison.OrdinalIgnoreCase))
                continue;
            if (target.Piece?.Type == Piece.Pieces.King)
                continue;
            var coordinate = square.Notation.ToLowerInvariant() + target.Notation.ToLowerInvariant();
            var promotion = piece.Type == Piece.Pieces.Pawn &&
                ((piece.Color == Game.Colors.White && target.Rank == 9) ||
                 (piece.Color == Game.Colors.Black && target.Rank == 0));
            if (promotion) {
                foreach (var suffix in "qrbncw") result.Add(coordinate + suffix);
            } else {
                result.Add(coordinate);
            }
        }
    }
    result.Sort(StringComparer.Ordinal);
    return result;
}

static string MirrorMove(string move)
{
    if (move.Length is not (4 or 5))
        throw new InvalidOperationException($"cannot reflect malformed move {move}");
    return MirrorSquare(move[..2]) + MirrorSquare(move.Substring(2, 2)) +
        (move.Length == 5 ? move[4].ToString().ToLowerInvariant() : string.Empty);
}

static string MirrorSquare(string square)
{
    square = square.ToLowerInvariant();
    if (square[0] == 'w') {
        return square[1] switch {
            '1' => "w4", '4' => "w1", '2' => "w3", '3' => "w2",
            _ => throw new InvalidOperationException($"unknown Omega corner {square}"),
        };
    }
    if (square[0] < 'a' || square[0] > 'j' || square[1] < '0' || square[1] > '9')
        throw new InvalidOperationException($"unknown Omega square {square}");
    return $"{square[0]}{(char)('9' - (square[1] - '0'))}";
}

static bool BoardIsColourReflected(Game game)
{
    foreach (var square in game.Board.Squares) {
        var reflected = game.Board.GetSquare(MirrorSquare(square.Notation));
        var piece = square.Piece;
        var other = reflected.Piece;
        if (piece == null || other == null) {
            if (piece != null || other != null) return false;
            continue;
        }
        if (piece.Type != other.Type || piece.Color == other.Color) return false;
    }
    var rights = game.GetFenString().Split(' ', StringSplitOptions.RemoveEmptyEntries)[2];
    return rights.Contains('K') == rights.Contains('k') &&
           rights.Contains('Q') == rights.Contains('q');
}

static void Shuffle(List<string> values, ref SplitMix64 rng)
{
    for (var index = values.Count - 1; index > 0; index--) {
        var other = (int)rng.Next((ulong)(index + 1));
        (values[index], values[other]) = (values[other], values[index]);
    }
}

static async Task WriteJsonLines(string path, IEnumerable<PrefixRecord> records)
{
    await using var stream = new FileStream(
        path, FileMode.CreateNew, FileAccess.Write, FileShare.None,
        1 << 20, FileOptions.WriteThrough | FileOptions.SequentialScan);
    await using var writer = new StreamWriter(stream, new UTF8Encoding(false), 1 << 20, leaveOpen: true);
    foreach (var record in records)
        await writer.WriteLineAsync(JsonSerializer.Serialize(record));
    await writer.FlushAsync();
    stream.Flush(flushToDisk: true);
}

static async Task WriteTemporaryJson(string path, object value)
{
    var payload = JsonSerializer.Serialize(value, new JsonSerializerOptions { WriteIndented = true }) + "\n";
    await using var stream = new FileStream(
        path, FileMode.CreateNew, FileAccess.Write, FileShare.None,
        1 << 16, FileOptions.WriteThrough);
    await using var writer = new StreamWriter(stream, new UTF8Encoding(false), 1 << 16, leaveOpen: true);
    await writer.WriteAsync(payload);
    await writer.FlushAsync();
    stream.Flush(flushToDisk: true);
}

static object Identity(string path, string? reportedPath = null)
{
    var bytes = File.ReadAllBytes(path);
    return new {
        path = Path.GetFullPath(reportedPath ?? path),
        bytes = bytes.Length,
        sha256 = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant(),
    };
}

static ulong Mix(ulong value)
{
    value += 0x9E3779B97F4A7C15UL;
    value = (value ^ (value >> 30)) * 0xBF58476D1CE4E5B9UL;
    value = (value ^ (value >> 27)) * 0x94D049BB133111EBUL;
    return value ^ (value >> 31);
}

static string AdjacentTemporary(string path) => Path.Combine(
    Path.GetDirectoryName(path)!, $".{Path.GetFileName(path)}.{Guid.NewGuid():N}.tmp");

static void RefuseExisting(string path, string label)
{
    if (File.Exists(path)) throw new IOException($"Refusing to replace existing {label}: {path}");
}

static void TryDelete(string path)
{
    try { if (File.Exists(path)) File.Delete(path); } catch { }
}

static async Task SelfTest()
{
    var options = new Options(
        "unused", 0x123456789ABCDEF0UL, 16, new[] { 4, 8, 12, 16 }, 2, 37);
    var first = await GenerateAll(options);
    var second = await GenerateAll(options);
    var left = JsonSerializer.Serialize(first);
    var right = JsonSerializer.Serialize(second);
    if (!left.Equals(right, StringComparison.Ordinal))
        throw new InvalidOperationException("deterministic generation changed");
    if (first.Length != 16 || first.Any(record => record.moves.Length != record.targetPlies))
        throw new InvalidOperationException("self-test record shape changed");
    if (first[0].trajectorySeed != "16483130067377077212")
        throw new InvalidOperationException("cross-language trajectory-seed vector changed");
    if (first.Select(record => record.selectionRank).Distinct().Count() != first.Length)
        throw new InvalidOperationException("self-test selection ranks collided");
    foreach (var record in first) {
        using var replay = await Replay(record.moves);
        if (!replay.GetFenString().Equals(record.finalOfen, StringComparison.Ordinal))
            throw new InvalidOperationException("self-test replay differs from emitted OFEN");
        if (!BoardIsColourReflected(replay))
            throw new InvalidOperationException("self-test prefix lost board symmetry");
    }
    if (MirrorSquare("a0") != "a9" || MirrorSquare("w1") != "w4" ||
        MirrorMove("a0c2") != "a9c7" || MirrorMove("w2j2") != "w3j7")
        throw new InvalidOperationException("Omega reflection mapping changed");

    // The shortest stratum has the smallest state space, so prove before a
    // production run that the frozen generator supplies at least its full
    // 128-root quota even when random trajectories legitimately repeat.
    var coverage = await GenerateAll(
        new Options("unused", 0x0F1E2D3C4B5A6978UL, 512, new[] { 4 }, 4, 37));
    if (coverage.Select(record => string.Join(' ', record.moves))
            .Distinct(StringComparer.Ordinal).Count() < 128 ||
        coverage.Select(record => record.finalOfen)
            .Distinct(StringComparer.Ordinal).Count() < 128)
        throw new InvalidOperationException(
            "depth-4 practical sampler no longer supplies its root quota");
}

sealed record PrefixRecord(
    int schemaVersion,
    string kind,
    string generatorSeed,
    int trajectoryIndex,
    string trajectorySeed,
    int targetPlies,
    string initialOfen,
    string[] moves,
    string finalOfen,
    int pieceCount,
    string sideToMove,
    string selectionRank);

sealed record Options(
    string Output,
    ulong Seed,
    int Trajectories,
    int[] Depths,
    int Workers,
    int MinimumPieces)
{
    public static Options Parse(string[] args)
    {
        string? Value(string name)
        {
            var index = Array.FindIndex(args, item => item.Equals(name, StringComparison.OrdinalIgnoreCase));
            return index >= 0 && index + 1 < args.Length ? args[index + 1] : null;
        }
        var output = Value("--output") ?? throw new ArgumentException("--output FILE is required");
        var seed = ulong.Parse(Value("--seed") ?? throw new ArgumentException("--seed UINT64 is required"));
        var trajectories = int.Parse(Value("--trajectories") ?? "8192");
        var depths = (Value("--target-plies") ?? "4,8,12,16")
            .Split(',', StringSplitOptions.RemoveEmptyEntries)
            .Select(int.Parse).ToArray();
        var workers = int.Parse(Value("--workers") ?? "4");
        var minimumPieces = int.Parse(Value("--minimum-pieces") ?? "37");
        if (trajectories <= 0 || workers <= 0 || minimumPieces < 2)
            throw new ArgumentException("counts must be positive");
        if (depths.Length == 0 || depths.Any(depth => depth <= 0 || depth % 2 != 0) ||
            depths.Distinct().Count() != depths.Length)
            throw new ArgumentException("target plies must be distinct positive even integers");
        return new Options(output, seed, trajectories, depths, workers, minimumPieces);
    }
}

struct SplitMix64
{
    private ulong _state;
    public SplitMix64(ulong seed) => _state = seed;
    public ulong Next(ulong exclusiveMaximum)
    {
        if (exclusiveMaximum == 0) throw new ArgumentOutOfRangeException(nameof(exclusiveMaximum));
        // Reject the short high-end residue so every Fisher-Yates index has
        // exactly the same number of 64-bit preimages.
        var threshold = unchecked(0UL - exclusiveMaximum) % exclusiveMaximum;
        while (true) {
            var value = Next();
            if (value >= threshold) return value % exclusiveMaximum;
        }
    }
    private ulong Next()
    {
        _state += 0x9E3779B97F4A7C15UL;
        var value = _state;
        value = (value ^ (value >> 30)) * 0xBF58476D1CE4E5B9UL;
        value = (value ^ (value >> 27)) * 0x94D049BB133111EBUL;
        return value ^ (value >> 31);
    }
}
