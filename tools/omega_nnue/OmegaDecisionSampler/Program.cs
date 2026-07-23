using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using ChessLib;

const string IdDomain = "omega-decision-child-v1";

if (args.Any(value => value.Equals("--self-test", StringComparison.OrdinalIgnoreCase))) {
    await SelfTest();
    return;
}

var options = Options.Parse(args);
var input = Path.GetFullPath(options.Input);
var output = Path.GetFullPath(options.Output);
var manifestPath = Path.GetFullPath(options.Manifest ?? options.Output + ".manifest.json");
var sealPath = Path.GetFullPath(options.Seal ?? options.Output + ".complete.seal.json");
if (!File.Exists(input)) throw new FileNotFoundException("root JSONL is missing", input);
if (StringComparer.OrdinalIgnoreCase.Equals(input, output))
    throw new ArgumentException("--input and --output must differ");
RefuseExisting(output, "output");
RefuseExisting(manifestPath, "manifest");
RefuseExisting(sealPath, "completion seal");
Directory.CreateDirectory(Path.GetDirectoryName(output)!);
Directory.CreateDirectory(Path.GetDirectoryName(manifestPath)!);

var inputBefore = Identity(input);
var outputTemporary = AdjacentTemporary(output);
var manifestTemporary = AdjacentTemporary(manifestPath);
var sealTemporary = AdjacentTemporary(sealPath);
var rootIds = new HashSet<string>(StringComparer.Ordinal);
var rootOfens = new Dictionary<string, string>(StringComparer.Ordinal);
var childIds = new HashSet<string>(StringComparer.Ordinal);
var phaseCounts = new Dictionary<string, int>(StringComparer.Ordinal);
var sideCounts = new Dictionary<string, int>(StringComparer.Ordinal);
var rootCount = 0;
var childCount = 0;
var zeroChildRoots = 0;
var minimumChildren = int.MaxValue;
var maximumChildren = 0;
FileIdentity? publishedOutput = null;
FileIdentity? publishedManifest = null;

try {
    await using (var stream = new FileStream(
        outputTemporary, FileMode.CreateNew, FileAccess.Write, FileShare.None,
        1 << 20, FileOptions.SequentialScan))
    await using (var writer = new StreamWriter(
        stream, new UTF8Encoding(false), 1 << 20, leaveOpen: true)) {
        using var reader = new StreamReader(input, new UTF8Encoding(false, true));
        string? line;
        var lineNumber = 0;
        while ((line = await reader.ReadLineAsync()) != null) {
            lineNumber++;
            if (String.IsNullOrWhiteSpace(line)) continue;
            using var document = JsonDocument.Parse(line);
            if (document.RootElement.ValueKind != JsonValueKind.Object)
                throw new InvalidDataException($"{input}:{lineNumber}: expected an object");
            var root = document.RootElement;
            var rootId = RequiredString(root, options.RootIdField, input, lineNumber);
            var groupId = RequiredString(root, options.GroupIdField, input, lineNumber);
            var ofen = NormalizeOfen(
                RequiredString(root, options.OfenField, input, lineNumber));
            if (!rootIds.Add(rootId))
                throw new InvalidDataException($"{input}:{lineNumber}: duplicate rootId {rootId}");
            if (rootOfens.TryGetValue(ofen, out var priorRoot))
                throw new InvalidDataException(
                    $"{input}:{lineNumber}: duplicate normalized root OFEN for {priorRoot} and {rootId}");
            rootOfens.Add(ofen, rootId);
            var phase = OptionalString(root, options.PhaseField);
            var sourceGameId = OptionalString(root, options.SourceGameIdField);
            var rootPvMove = OptionalString(root, options.RootPvMoveField)?.ToLowerInvariant();
            var candidateRole = OptionalString(root, options.CandidateRoleField);
            var selectionRank = OptionalString(root, options.SelectionRankField);
            var children = await Expand(rootId, groupId, ofen);
            rootCount++;
            minimumChildren = Math.Min(minimumChildren, children.Count);
            maximumChildren = Math.Max(maximumChildren, children.Count);
            if (children.Count == 0) zeroChildRoots++;
            if (phase != null) Increment(phaseCounts, phase);
            Increment(sideCounts, ofen.Split(' ')[1].ToLowerInvariant());
            for (var ordinal = 0; ordinal < children.Count; ordinal++) {
                var child = children[ordinal];
                if (!childIds.Add(child.Id))
                    throw new InvalidDataException(
                        $"stable child ID collision for {rootId} {child.Move}");
                var record = new {
                    schemaVersion = 1,
                    kind = "omega-legal-child",
                    rootId,
                    groupId,
                    sourceGameId,
                    phase,
                    rootPvMove,
                    candidateRole,
                    selectionRank,
                    parentOfen = ofen,
                    parentSideToMove = ofen.Split(' ')[1].ToLowerInvariant(),
                    childId = child.Id,
                    move = child.Move,
                    moveOrdinal = ordinal,
                    childOfen = child.Ofen,
                    childSideToMove = child.Ofen.Split(' ')[1].ToLowerInvariant(),
                    isPromotion = child.Move.Length == 5,
                };
                await writer.WriteLineAsync(JsonSerializer.Serialize(record));
                childCount++;
            }
        }
        await writer.FlushAsync();
        stream.Flush(flushToDisk: true);
    }

    var inputAfter = Identity(input);
    if (!inputBefore.Equals(inputAfter))
        throw new IOException("input JSONL changed while it was being expanded");
    var temporaryOutputIdentity = Identity(outputTemporary);
    var manifest = new {
        schemaVersion = 1,
        kind = "omega-decision-sampler-manifest",
        createdUtc = DateTime.UtcNow,
        policy = new {
            variant = "omega",
            legalMoveAuthority = "ChessLib.Game.GetAvailableSquares",
            childApplication = "fresh Game initialized from parent OFEN; DoMove(checkEndGame=false)",
            promotionSuffixOrder = "qrbncw",
            moveOrder = "coordinate ordinal",
            stableIdDomain = IdDomain,
            fields = new {
                rootId = options.RootIdField,
                groupId = options.GroupIdField,
                ofen = options.OfenField,
                phase = options.PhaseField,
                sourceGameId = options.SourceGameIdField,
                rootPvMove = options.RootPvMoveField,
                candidateRole = options.CandidateRoleField,
                selectionRank = options.SelectionRankField,
            },
        },
        coverage = new {
            roots = rootCount,
            children = childCount,
            zeroChildRoots,
            minimumChildrenPerRoot = rootCount == 0 ? 0 : minimumChildren,
            maximumChildrenPerRoot = maximumChildren,
            phaseCounts,
            sideToMoveCounts = sideCounts,
            uniqueRootIds = rootIds.Count,
            uniqueChildIds = childIds.Count,
        },
        input = inputBefore,
        output = temporaryOutputIdentity.WithPath(output),
        runtime = new {
            framework = System.Runtime.InteropServices.RuntimeInformation.FrameworkDescription,
            samplerAssembly = Identity(Assembly.GetExecutingAssembly().Location),
            chessLibAssembly = Identity(typeof(Game).Assembly.Location),
        },
        finalStageSeal = false,
    };
    await WriteTemporaryJson(manifestTemporary, manifest);
    RefuseExisting(output, "output");
    RefuseExisting(manifestPath, "manifest");
    RefuseExisting(sealPath, "completion seal");
    File.Move(outputTemporary, output);
    publishedOutput = Identity(output);
    File.Move(manifestTemporary, manifestPath);
    publishedManifest = Identity(manifestPath);
    var seal = new {
        schemaVersion = 1,
        kind = "omega-decision-sampler-completion-seal",
        createdUtc = DateTime.UtcNow,
        input = inputBefore,
        output = Identity(output),
        manifest = Identity(manifestPath),
        producer = new {
            samplerAssembly = Identity(Assembly.GetExecutingAssembly().Location),
            chessLibAssembly = Identity(typeof(Game).Assembly.Location),
            framework = System.Runtime.InteropServices.RuntimeInformation.FrameworkDescription,
        },
        finalStageSeal = true,
    };
    await WriteTemporaryJson(sealTemporary, seal);
    RefuseExisting(sealPath, "completion seal");
    File.Move(sealTemporary, sealPath);
} catch {
    TryDelete(outputTemporary);
    TryDelete(manifestTemporary);
    TryDelete(sealTemporary);
    // The seal is the publication commit point.  Before that point, roll back
    // only bytes whose identity proves this invocation created them.  If an
    // external actor changed either path, leave the partial state for explicit
    // recovery rather than clobbering it.
    TryDeleteIfIdentity(manifestPath, publishedManifest);
    TryDeleteIfIdentity(output, publishedOutput);
    throw;
}

Console.WriteLine($"Expanded {rootCount} roots into {childCount} legal children.");
Console.WriteLine($"Output: {output}");
Console.WriteLine($"Manifest: {manifestPath}");
Console.WriteLine($"Completion seal: {sealPath}");

static async Task<List<Child>> Expand(string rootId, string groupId, string ofen)
{
    const string childIdDomain = "omega-decision-child-v1";
    using var game = CreateGame(ofen);
    var moves = LegalMoves(game);
    var children = new List<Child>(moves.Count);
    foreach (var move in moves) {
        using var childGame = CreateGame(ofen);
        await childGame.DoMove(move, checkEndGame: false);
        var childOfen = NormalizeOfen(childGame.GetFenString());
        var payload = $"{childIdDomain}\0{rootId}\0{groupId}\0{move}\0{childOfen}";
        var id = Convert.ToHexString(
            SHA256.HashData(Encoding.UTF8.GetBytes(payload))).ToLowerInvariant();
        children.Add(new Child(id, move, childOfen));
    }
    children.Sort((left, right) => StringComparer.Ordinal.Compare(left.Move, right.Move));
    return children;
}

static Game CreateGame(string ofen)
{
    var settings = new Game.GameSettings {
        Variant = ChessVariant.Omega,
        InitialFenPosition = ofen,
        DrawForRepetition = 3,
    };
    settings.Players.Add(new HumanPlayer(Game.Colors.White, "Decision white", null));
    settings.Players.Add(new HumanPlayer(Game.Colors.Black, "Decision black", null));
    var game = new Game();
    game.Init(settings);
    return game;
}

static List<string> LegalMoves(Game game)
{
    var result = new HashSet<string>(StringComparer.Ordinal);
    foreach (var square in game.Board.Squares) {
        var piece = square.Piece;
        if (piece == null || piece.Color != game.ToMove) continue;
        foreach (var target in game.GetAvailableSquares(square)) {
            if (target.Notation.Equals(square.Notation, StringComparison.OrdinalIgnoreCase))
                continue;
            var coordinate =
                square.Notation.ToLowerInvariant() + target.Notation.ToLowerInvariant();
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
    return result.OrderBy(value => value, StringComparer.Ordinal).ToList();
}

static string NormalizeOfen(string value)
{
    var fields = value.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries);
    if (fields.Length != 6) throw new InvalidDataException("OFEN must contain six fields");
    if (fields[1] is not ("w" or "b"))
        throw new InvalidDataException("OFEN side to move must be w or b");
    if (!fields[0].Contains('[') || !fields[0].Contains(']'))
        throw new InvalidDataException("Omega OFEN must contain four corner-square fields");
    return String.Join(' ', fields);
}

static string RequiredString(
    JsonElement root, string name, string input, int lineNumber)
{
    var value = OptionalString(root, name);
    return !String.IsNullOrWhiteSpace(value)
        ? value
        : throw new InvalidDataException($"{input}:{lineNumber}: missing string {name}");
}

static string? OptionalString(JsonElement root, string name)
{
    foreach (var property in root.EnumerateObject()) {
        if (property.Name.Equals(name, StringComparison.OrdinalIgnoreCase)) {
            return property.Value.ValueKind == JsonValueKind.String
                ? property.Value.GetString()
                : null;
        }
    }
    return null;
}

static void Increment(Dictionary<string, int> counts, string key) =>
    counts[key] = counts.GetValueOrDefault(key) + 1;

static FileIdentity Identity(string path)
{
    var full = Path.GetFullPath(path);
    using var stream = File.OpenRead(full);
    var hash = Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();
    return new FileIdentity(full, stream.Length, hash);
}

static string AdjacentTemporary(string path) =>
    Path.Combine(
        Path.GetDirectoryName(path)!,
        $".{Path.GetFileName(path)}.{Guid.NewGuid():N}.tmp");

static async Task WriteTemporaryJson(string temporary, object value)
{
    var bytes = Encoding.UTF8.GetBytes(
        JsonSerializer.Serialize(value, new JsonSerializerOptions { WriteIndented = true }) + "\n");
    await using var stream = new FileStream(
        temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None);
    await stream.WriteAsync(bytes);
    await stream.FlushAsync();
    stream.Flush(flushToDisk: true);
}

static void RefuseExisting(string path, string description)
{
    if (File.Exists(path) || Directory.Exists(path))
        throw new IOException($"refusing to overwrite existing {description}: {path}");
}

static void TryDelete(string path)
{
    try { if (File.Exists(path)) File.Delete(path); } catch (IOException) { }
}

static void TryDeleteIfIdentity(string path, FileIdentity? expected)
{
    if (expected is null) return;
    try {
        if (File.Exists(path) && Identity(path).Equals(expected.Value))
            File.Delete(path);
    } catch (IOException) {
        // A nonmatching or locked artifact is intentionally recoverable state.
    } catch (UnauthorizedAccessException) {
        // Do not broaden cleanup authority after a failed publication.
    }
}

static async Task SelfTest()
{
    const string start =
        "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/PPPPPPPPPP/" +
        "CRNBQKBNRC[W/W/w/w] w KQkq - 0 1";
    var first = await Expand("self-root", "self-group", start);
    var second = await Expand("self-root", "self-group", start);
    if (first.Count == 0 || !first.SequenceEqual(second))
        throw new Exception("initial-position expansion is empty or nondeterministic");
    if (first.Select(value => value.Move).Distinct(StringComparer.Ordinal).Count() != first.Count)
        throw new Exception("initial-position expansion contains duplicate moves");
    if (!first.Any(value => value.Move == "w1a2") ||
        !first.Any(value => value.Move == "w2j2"))
        throw new Exception("Omega corner-wizard moves are missing");

    const string promotion =
        "k9/4P5/10/10/10/10/10/10/10/9K[-/-/-/-] w - - 0 1";
    var promotions = await Expand("promotion-root", "promotion-group", promotion);
    foreach (var suffix in "qrbncw") {
        if (!promotions.Any(value => value.Move == "e8e9" + suffix))
            throw new Exception($"Omega promotion suffix {suffix} is missing");
    }
    var scratch = Path.Combine(Path.GetTempPath(), $"omega-decision-sampler-{Guid.NewGuid():N}");
    Directory.CreateDirectory(scratch);
    try {
        var staged = Path.Combine(scratch, "staged.jsonl");
        await File.WriteAllTextAsync(staged, "created\n", new UTF8Encoding(false));
        var created = Identity(staged);
        await File.WriteAllTextAsync(staged, "externally changed\n", new UTF8Encoding(false));
        TryDeleteIfIdentity(staged, created);
        if (!File.Exists(staged))
            throw new Exception("rollback clobbered a changed publication artifact");
        var changed = Identity(staged);
        TryDeleteIfIdentity(staged, changed);
        if (File.Exists(staged))
            throw new Exception("rollback did not remove its identity-matching artifact");
    } finally {
        try { Directory.Delete(scratch, recursive: true); } catch (IOException) { }
    }
    Console.WriteLine(
        $"OmegaDecisionSampler self-test passed ({first.Count} initial children, " +
        $"{promotions.Count} promotion-position children)." );
}

readonly record struct Child(string Id, string Move, string Ofen);
readonly record struct FileIdentity(string path, long bytes, string sha256)
{
    public FileIdentity WithPath(string value) =>
        this with { path = System.IO.Path.GetFullPath(value) };
}

sealed record Options(
    string Input,
    string Output,
    string? Manifest,
    string? Seal,
    string RootIdField,
    string GroupIdField,
    string OfenField,
    string PhaseField,
    string SourceGameIdField,
    string RootPvMoveField,
    string CandidateRoleField,
    string SelectionRankField)
{
    public static Options Parse(string[] args)
    {
        string? Value(string name)
        {
            var index = Array.FindIndex(
                args, value => value.Equals(name, StringComparison.OrdinalIgnoreCase));
            return index >= 0 && index + 1 < args.Length ? args[index + 1] : null;
        }
        return new Options(
            Value("--input") ?? throw new ArgumentException("--input FILE is required"),
            Value("--output") ?? throw new ArgumentException("--output FILE is required"),
            Value("--manifest"),
            Value("--seal"),
            Value("--root-id-field") ?? "rootId",
            Value("--group-id-field") ?? "groupId",
            Value("--ofen-field") ?? "ofen",
            Value("--phase-field") ?? "phase",
            Value("--source-game-id-field") ?? "sourceGameId",
            Value("--root-pv-move-field") ?? "rootPvMove",
            Value("--candidate-role-field") ?? "candidateRole",
            Value("--selection-rank-field") ?? "selectionRank");
    }
}
