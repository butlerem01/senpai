using System.Security.Cryptography;
using System.Reflection;
using System.Text;
using System.Text.Json;
using ChessLib;

const string OmegaStart =
    "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/" +
    "PPPPPPPPPP/CRNBQKBNRC[W/W/w/w] w KQkq - 0 1";

var options = Options.Parse(args);
Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(options.Output))!);
var temporary = options.Output + ".tmp";
if (File.Exists(temporary)) File.Delete(temporary);

var phaseCounts = new Dictionary<string, int>(StringComparer.Ordinal) {
    ["opening"] = 0, ["middlegame"] = 0, ["late"] = 0, ["endgame"] = 0
};
var sideCounts = new Dictionary<string, int>(StringComparer.Ordinal) {
    ["w"] = 0, ["b"] = 0
};
var terminalTrajectories = 0;
var maxPlyReached = 0;
var promotionSelections = "qrbncw".ToDictionary(
    value => value.ToString(), _ => 0, StringComparer.Ordinal);
await using (var stream = new FileStream(
    temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None,
    1 << 20, FileOptions.SequentialScan))
await using (var writer = new StreamWriter(
    stream, new UTF8Encoding(false), 1 << 20, leaveOpen: true)) {
    for (var pair = 0; pair < options.TrajectoryPairs; pair++) {
        foreach (var flavor in new[] { "ab", "ba" }) {
            var trajectorySeed = Mix(
                options.Seed
                ^ ((ulong)(pair + 1) * 0x9E3779B97F4A7C15UL)
                ^ (flavor == "ab"
                    ? 0xA0761D6478BD642FUL
                    : 0xE7037ED1A0B428DBUL));
            var rng = new SplitMix64(trajectorySeed);
            using var game = CreateGame();
            var retained = new Dictionary<(string Phase, string Side), List<Sample>>();
            var ended = false;
            for (var ply = 0; ply <= options.MaxPlies; ply++) {
                var moves = LegalMoves(game);
                if (moves.Count == 0 || game.IsDraw()) {
                    ended = true;
                    break;
                }
                Consider(
                    retained, game, pair, flavor, trajectorySeed, ply,
                    options.PositionsPerPhaseSide);
                if (ply == options.MaxPlies) break;
                var chosen = ChooseMove(moves, ref rng, options.CapturePercent);
                if (chosen.Coordinate.Length == 5)
                    promotionSelections[chosen.Coordinate[4].ToString()]++;
                await game.DoMove(chosen.Coordinate, checkEndGame: false);
                maxPlyReached = Math.Max(maxPlyReached, ply + 1);
            }
            if (ended) terminalTrajectories++;
            foreach (var sample in retained.Values.SelectMany(value => value)
                         .OrderBy(value => value.Phase, StringComparer.Ordinal)
                         .ThenBy(value => value.Side, StringComparer.Ordinal)
                         .ThenBy(value => value.Rank, StringComparer.Ordinal)) {
                var record = new {
                    schemaVersion = 1,
                    kind = "omega-rules-only-random-root",
                    generatorSeed = options.Seed.ToString(),
                    trajectorySeed = trajectorySeed.ToString(),
                    trajectoryPairId = $"random-pair-{pair + 1:D6}",
                    trajectoryId = $"random-pair-{pair + 1:D6}-{flavor}",
                    flavor,
                    ply = sample.Ply,
                    phase = sample.Phase,
                    sideToMove = sample.Side,
                    ofen = sample.Ofen,
                    pieceCount = sample.PieceCount,
                    whitePieces = sample.WhitePieces,
                    blackPieces = sample.BlackPieces,
                    champions = sample.Champions,
                    wizards = sample.Wizards,
                    halfmoveClock = sample.HalfmoveClock,
                    selectionRank = sample.Rank
                };
                await writer.WriteLineAsync(JsonSerializer.Serialize(record));
                phaseCounts[sample.Phase]++;
                sideCounts[sample.Side]++;
            }
        }
    }
    await writer.FlushAsync();
    stream.Flush(flushToDisk: true);
}
File.Move(temporary, options.Output, overwrite: true);

var outputBytes = await File.ReadAllBytesAsync(options.Output);
var manifest = new {
    schemaVersion = 1,
    kind = "omega-rules-only-random-root-manifest",
    createdUtc = DateTime.UtcNow,
    policy = new {
        deterministicPrng = "SplitMix64",
        seed = options.Seed.ToString(),
        trajectoryPairs = options.TrajectoryPairs,
        independentTrajectoriesPerPair = 2,
        maxPlies = options.MaxPlies,
        positionsPerPhaseAndSide = options.PositionsPerPhaseSide,
        captureSelectionPercent = options.CapturePercent,
        terminalRootsEmitted = 0,
        maximumHalfmoveClock = 89,
        minimumPieces = 7,
        minimumPiecesPerSide = 2,
        phasePlyWindows = Phase.Windows
    },
    coverage = new {
        records = phaseCounts.Values.Sum(),
        phaseCounts,
        sideToMoveCounts = sideCounts,
        terminalTrajectories,
        maxPlyReached,
        promotionSelections,
        enPassantClassification = (
            "An en-passant move lands on an empty target and remains in the " +
            "ordinary move pool; legality still comes from ChessLib.")
    },
    runtime = new {
        framework = System.Runtime.InteropServices.RuntimeInformation.FrameworkDescription,
        samplerAssembly = Identity(Assembly.GetExecutingAssembly().Location),
        chessLibAssembly = Identity(typeof(Game).Assembly.Location)
    },
    output = new {
        path = Path.GetFullPath(options.Output),
        bytes = outputBytes.Length,
        sha256 = Convert.ToHexString(SHA256.HashData(outputBytes)).ToLowerInvariant()
    }
};
var manifestPath = options.Output + ".manifest.json";
await File.WriteAllTextAsync(
    manifestPath,
    JsonSerializer.Serialize(
        manifest, new JsonSerializerOptions { WriteIndented = true }) + "\n",
    new UTF8Encoding(false));
Console.WriteLine(
    $"Wrote {phaseCounts.Values.Sum()} roots from " +
    $"{options.TrajectoryPairs * 2} legal trajectories.");
Console.WriteLine(
    $"Phases: {string.Join(", ", phaseCounts.Select(item => $"{item.Key}={item.Value}"))}");
Console.WriteLine($"Output: {Path.GetFullPath(options.Output)}");
Console.WriteLine($"Manifest: {Path.GetFullPath(manifestPath)}");

static Game CreateGame()
{
    var settings = new Game.GameSettings {
        Variant = ChessVariant.Omega,
        InitialFenPosition = OmegaStart,
        DrawForRepetition = 3
    };
    settings.Players.Add(new HumanPlayer(Game.Colors.White, "Random A", null));
    settings.Players.Add(new HumanPlayer(Game.Colors.Black, "Random B", null));
    var game = new Game();
    game.Init(settings);
    return game;
}

static List<LegalMove> LegalMoves(Game game)
{
    var result = new List<LegalMove>();
    foreach (var square in game.Board.Squares) {
        var piece = square.Piece;
        if (piece == null || piece.Color != game.ToMove) continue;
        foreach (var target in game.GetAvailableSquares(square)) {
            if (target.Notation.Equals(square.Notation, StringComparison.OrdinalIgnoreCase))
                continue;
            var capture = target.Piece != null && target.Piece.Color != piece.Color;
            var coordinate =
                square.Notation.ToLowerInvariant() + target.Notation.ToLowerInvariant();
            var promotion = piece.Type == Piece.Pieces.Pawn &&
                ((piece.Color == Game.Colors.White && target.Rank == 9) ||
                 (piece.Color == Game.Colors.Black && target.Rank == 0));
            if (promotion) {
                // All six legal Omega promotion choices participate in the
                // deterministic move draw; no queen-only shortcut.
                foreach (var suffix in "qrbncw")
                    result.Add(new LegalMove(coordinate + suffix, capture));
            } else {
                result.Add(new LegalMove(coordinate, capture));
            }
        }
    }
    return result;
}

static LegalMove ChooseMove(
    List<LegalMove> moves, ref SplitMix64 rng, int capturePercent)
{
    var captures = moves.Where(move => move.Capture).ToList();
    var pool = captures.Count > 0 && rng.Next(100) < (ulong)capturePercent
        ? captures
        : moves;
    return pool[(int)rng.Next((ulong)pool.Count)];
}

static void Consider(
    Dictionary<(string Phase, string Side), List<Sample>> retained,
    Game game,
    int pair,
    string flavor,
    ulong trajectorySeed,
    int ply,
    int limit)
{
    var pieces = game.Board.Squares
        .Where(square => square.Piece != null)
        .Select(square => square.Piece!)
        .ToList();
    var phase = Phase.Of(pieces.Count);
    if (phase == null || !Phase.InWindow(phase, ply)) return;
    var white = pieces.Count(piece => piece.Color == Game.Colors.White);
    var black = pieces.Count - white;
    if (pieces.Count < 7 || white < 2 || black < 2 || game.HalfMoveClock >= 90)
        return;
    var side = game.ToMove == Game.Colors.White ? "w" : "b";
    var ofen = game.GetFenString();
    var rankPayload =
        $"omega-root-sampler-v1\0{trajectorySeed}\0{pair}\0{flavor}\0{ply}\0{ofen}";
    var rank = Convert.ToHexString(
        SHA256.HashData(Encoding.UTF8.GetBytes(rankPayload))).ToLowerInvariant();
    var sample = new Sample(
        rank, ply, phase, side, ofen, pieces.Count, white, black,
        pieces.Count(piece => piece.Type == Piece.Pieces.Champion),
        pieces.Count(piece => piece.Type == Piece.Pieces.Wizard),
        game.HalfMoveClock);
    var key = (phase, side);
    if (!retained.TryGetValue(key, out var bucket)) {
        bucket = new List<Sample>();
        retained[key] = bucket;
    }
    bucket.Add(sample);
    bucket.Sort((left, right) => string.CompareOrdinal(left.Rank, right.Rank));
    if (bucket.Count > limit) bucket.RemoveAt(bucket.Count - 1);
}

static ulong Mix(ulong value)
{
    value += 0x9E3779B97F4A7C15UL;
    value = (value ^ (value >> 30)) * 0xBF58476D1CE4E5B9UL;
    value = (value ^ (value >> 27)) * 0x94D049BB133111EBUL;
    return value ^ (value >> 31);
}

static object Identity(string path)
{
    var bytes = File.ReadAllBytes(path);
    return new {
        path = Path.GetFullPath(path),
        bytes = bytes.Length,
        sha256 = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant()
    };
}

readonly record struct LegalMove(string Coordinate, bool Capture);
readonly record struct Sample(
    string Rank,
    int Ply,
    string Phase,
    string Side,
    string Ofen,
    int PieceCount,
    int WhitePieces,
    int BlackPieces,
    int Champions,
    int Wizards,
    int HalfmoveClock);

struct SplitMix64
{
    private ulong _state;
    public SplitMix64(ulong seed) => _state = seed;
    public ulong Next(ulong exclusiveMaximum)
    {
        if (exclusiveMaximum == 0) throw new ArgumentOutOfRangeException(nameof(exclusiveMaximum));
        return Next() % exclusiveMaximum;
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

static class Phase
{
    public static readonly Dictionary<string, int[]> Windows =
        new(StringComparer.Ordinal) {
            ["opening"] = new[] { 6, 48 },
            ["middlegame"] = new[] { 20, 140 },
            ["late"] = new[] { 40, 260 },
            ["endgame"] = new[] { 60, 400 }
        };

    public static string? Of(int pieces) =>
        pieces >= 37 ? "opening" :
        pieces >= 25 ? "middlegame" :
        pieces >= 13 ? "late" :
        pieces >= 7 ? "endgame" : null;

    public static bool InWindow(string phase, int ply)
    {
        var window = Windows[phase];
        return ply >= window[0] && ply <= window[1];
    }
}

sealed record Options(
    string Output,
    ulong Seed,
    int TrajectoryPairs,
    int MaxPlies,
    int PositionsPerPhaseSide,
    int CapturePercent)
{
    public static Options Parse(string[] args)
    {
        string? Value(string name)
        {
            var index = Array.FindIndex(
                args, value => value.Equals(name, StringComparison.OrdinalIgnoreCase));
            return index >= 0 && index + 1 < args.Length ? args[index + 1] : null;
        }
        var output = Value("--output")
            ?? throw new ArgumentException("--output FILE is required");
        var seed = ulong.Parse(Value("--seed") ?? "2026071802");
        var pairs = int.Parse(Value("--trajectory-pairs") ?? "2048");
        var maxPlies = int.Parse(Value("--max-plies") ?? "220");
        var retained = int.Parse(Value("--positions-per-phase-side") ?? "2");
        var capture = int.Parse(Value("--capture-percent") ?? "72");
        if (pairs <= 0 || maxPlies <= 0 || retained <= 0)
            throw new ArgumentException("numeric counts must be positive");
        if (capture < 0 || capture > 100)
            throw new ArgumentException("--capture-percent must be 0..100");
        return new Options(output, seed, pairs, maxPlies, retained, capture);
    }
}
