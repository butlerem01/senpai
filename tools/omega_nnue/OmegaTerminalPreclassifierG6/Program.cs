using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using ChessLib;
using ChessLib.Exceptions;

namespace OmegaTerminalPreclassifierG6;

internal static class Program
{
    private const string SourceKind = "omega-g6-history-root";
    private const string TranscriptKind = "omega-g6-terminal-preclassification";
    private const string EligibleRootKind = "omega-g6-terminal-safe-root";
    private const string EligibleChildKind = "omega-g6-terminal-safe-child";
    private const string ManifestKind = "omega-g6-terminal-preclassification-manifest";
    private const string ChildIdDomain = "omega-g6-terminal-safe-child-v1";
    private const string TranscriptHashDomain =
        "omega-g6-terminal-preclassification-transcript-v1";
    private const string ExpectedChessLibSha256 =
        "16a01414c9f486561aac0b48cebb7c485621d804a73c00803ec0aff55f572f4c";
    private const string OfficialInitialOfen =
        "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/PPPPPPPPPP/" +
        "CRNBQKBNRC[W/W/w/w] w KQkq - 0 1";

    private static readonly UTF8Encoding Utf8 = new(false, true);
    private static readonly JsonSerializerOptions Json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        WriteIndented = false
    };

    public static async Task<int> Main(string[] args)
    {
        AssertPinnedChessLib();
        if (args.Length == 1 && args[0] == "--self-test")
        {
            await SelfTest();
            return 0;
        }

        var options = Options.Parse(args);
        await Run(options);
        return 0;
    }

    private static async Task Run(Options options)
    {
        var input = Path.GetFullPath(options.Input);
        var transcript = Path.GetFullPath(options.Transcript);
        var eligibleRoots = Path.GetFullPath(options.EligibleRoots);
        var eligibleChildren = Path.GetFullPath(options.EligibleChildren);
        var manifest = Path.GetFullPath(options.Manifest);
        if (!File.Exists(input))
            throw new FileNotFoundException("history-root JSONL is missing", input);
        RequireDistinctPaths(input, transcript, eligibleRoots, eligibleChildren, manifest);
        RefuseExisting(transcript, "transcript");
        RefuseExisting(eligibleRoots, "eligible-root output");
        RefuseExisting(eligibleChildren, "eligible-child output");
        RefuseExisting(manifest, "manifest");
        foreach (var path in new[] { transcript, eligibleRoots, eligibleChildren, manifest })
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);

        var inputBefore = IdentityOf(input);
        var transcriptTemp = AdjacentTemporary(transcript);
        var rootsTemp = AdjacentTemporary(eligibleRoots);
        var childrenTemp = AdjacentTemporary(eligibleChildren);
        var manifestTemp = AdjacentTemporary(manifest);
        ArtifactIdentity? publishedTranscript = null;
        ArtifactIdentity? publishedRoots = null;
        ArtifactIdentity? publishedChildren = null;
        var seenRootIds = new HashSet<string>(StringComparer.Ordinal);
        var sourceRecords = 0;
        var acceptedRoots = 0;
        var rejectedRoots = 0;
        var eligibleChildCount = 0;
        var classificationCounts = Enum.GetValues<PositionClass>()
            .ToDictionary(value => ClassName(value), _ => 0, StringComparer.Ordinal);

        try
        {
            {
                await using var transcriptStream = NewOutput(transcriptTemp);
                await using var rootsStream = NewOutput(rootsTemp);
                await using var childrenStream = NewOutput(childrenTemp);
                await using var transcriptWriter = NewWriter(transcriptStream);
                await using var rootsWriter = NewWriter(rootsStream);
                await using var childrenWriter = NewWriter(childrenStream);
                using var reader = new StreamReader(input, Utf8, true, 1 << 20);
                string? line;
                var lineNumber = 0;
                while ((line = await reader.ReadLineAsync()) != null)
                {
                    lineNumber++;
                    if (string.IsNullOrWhiteSpace(line))
                        continue;
                    var source = ParseSource(line, input, lineNumber);
                    if (!seenRootIds.Add(source.RootId))
                        throw new InvalidDataException(
                            $"{input}:{lineNumber}: duplicate rootId {source.RootId}");
                    var analysis = await Analyze(source);
                    await transcriptWriter.WriteLineAsync(
                        JsonSerializer.Serialize(analysis.Transcript, Json));
                    sourceRecords++;
                    Count(
                        classificationCounts,
                        analysis.Transcript.Root?.Classification ??
                        analysis.Transcript.Rejection);
                    foreach (var child in analysis.Transcript.Children)
                        Count(classificationCounts, child.Classification);
                    if (analysis.Transcript.TeacherEligible)
                    {
                        acceptedRoots++;
                        await rootsWriter.WriteLineAsync(
                            JsonSerializer.Serialize(analysis.EligibleRoot, Json));
                        foreach (var child in analysis.EligibleChildren)
                        {
                            await childrenWriter.WriteLineAsync(
                                JsonSerializer.Serialize(child, Json));
                            eligibleChildCount++;
                        }
                    }
                    else
                    {
                        rejectedRoots++;
                    }
                }
                if (sourceRecords == 0)
                    throw new InvalidDataException("input contains no source records");
                await FlushToDisk(transcriptWriter, transcriptStream);
                await FlushToDisk(rootsWriter, rootsStream);
                await FlushToDisk(childrenWriter, childrenStream);
            }

            var inputAfter = IdentityOf(input);
            if (inputBefore != inputAfter)
                throw new IOException("input changed while it was being classified");
            var tempTranscript = IdentityOf(transcriptTemp);
            var tempRoots = IdentityOf(rootsTemp);
            var tempChildren = IdentityOf(childrenTemp);
            var manifestValue = new Manifest(
                1,
                ManifestKind,
                new Policy(
                    "omega",
                    "ChessLib.Game.DoMove(move, checkEndGame: false)",
                    new[]
                    {
                        "checkmate", "stalemate", "draw-repetition",
                        "draw-halfmove", "draw-insufficient", "nonterminal"
                    },
                    3,
                    new SenpaiOfenCompatibilityPolicy(
                        1,
                        new[] { "W1-W4", "all board squares on ranks 0 and 9" },
                        0,
                        1,
                        new[]
                        {
                            "initial-position",
                            "after-each-history-ply",
                            "after-each-legal-child"
                        }),
                    "reject the root unless root and every legal child are nonterminal"),
                new Coverage(
                    sourceRecords, acceptedRoots, rejectedRoots,
                    eligibleChildCount, classificationCounts),
                inputBefore,
                tempTranscript,
                tempRoots,
                tempChildren,
                new RuntimeEvidence(
                    ExpectedChessLibSha256,
                    HashFile(typeof(Game).Assembly.Location),
                    HashFile(Assembly.GetExecutingAssembly().Location)));
            await WriteJson(manifestTemp, manifestValue);

            RefuseExisting(transcript, "transcript");
            RefuseExisting(eligibleRoots, "eligible-root output");
            RefuseExisting(eligibleChildren, "eligible-child output");
            RefuseExisting(manifest, "manifest");
            File.Move(transcriptTemp, transcript);
            publishedTranscript = IdentityOf(transcript);
            File.Move(rootsTemp, eligibleRoots);
            publishedRoots = IdentityOf(eligibleRoots);
            File.Move(childrenTemp, eligibleChildren);
            publishedChildren = IdentityOf(eligibleChildren);
            File.Move(manifestTemp, manifest);
        }
        catch
        {
            TryDelete(transcriptTemp);
            TryDelete(rootsTemp);
            TryDelete(childrenTemp);
            TryDelete(manifestTemp);
            TryDeleteIfIdentity(transcript, publishedTranscript);
            TryDeleteIfIdentity(eligibleRoots, publishedRoots);
            TryDeleteIfIdentity(eligibleChildren, publishedChildren);
            throw;
        }

        Console.WriteLine(
            $"Classified {sourceRecords} roots: {acceptedRoots} eligible, " +
            $"{rejectedRoots} rejected, {eligibleChildCount} eligible children.");
        Console.WriteLine($"Transcript: {transcript}");
        Console.WriteLine($"Eligible roots: {eligibleRoots}");
        Console.WriteLine($"Eligible children: {eligibleChildren}");
        Console.WriteLine($"Manifest: {manifest}");
    }

    private static async Task<Analysis> Analyze(SourceRecord source)
    {
        var replay = await Replay(source, rejectTerminalContinuation: true);
        if (replay.Game is null)
        {
            var failedPayload = new TranscriptPayload(
                1, TranscriptKind, source.RootId, source.GroupId, false,
                ClassName(replay.FailureClass!.Value), replay.History, null,
                Array.Empty<ChildTranscript>());
            var failedTranscript = Seal(failedPayload);
            return new Analysis(failedTranscript, null, Array.Empty<EligibleChild>());
        }

        using var rootGame = replay.Game;
        var rootClass = await Classify(rootGame, root: true);
        var legalMoves = LegalMoves(rootGame);
        var rootPosition = PositionOf(rootGame, rootClass, legalMoves.Count);
        if (rootClass.Classification != PositionClass.RootNonterminal)
        {
            var terminalPayload = new TranscriptPayload(
                1, TranscriptKind, source.RootId, source.GroupId, false,
                ClassName(rootClass.Classification), replay.History,
                rootPosition, Array.Empty<ChildTranscript>());
            var terminalTranscript = Seal(terminalPayload);
            return new Analysis(terminalTranscript, null, Array.Empty<EligibleChild>());
        }

        var childTranscripts = new List<ChildTranscript>(legalMoves.Count);
        var pendingChildren = new List<PendingChild>(legalMoves.Count);
        for (var ordinal = 0; ordinal < legalMoves.Count; ordinal++)
        {
            var move = legalMoves[ordinal];
            var fresh = await Replay(source, rejectTerminalContinuation: true);
            if (fresh.Game is null)
            {
                childTranscripts.Add(new ChildTranscript(
                    ordinal, move, null, null,
                    ClassName(PositionClass.ReplayFailure),
                    fresh.History.FailureCode, null, null, null, 0));
                continue;
            }
            using var childGame = fresh.Game;
            try
            {
                await childGame.DoMove(move, checkEndGame: false);
                var compatibilityFailure =
                    SenpaiPositionCompatibilityFailure(childGame);
                if (compatibilityFailure is not null)
                {
                    var incompatibleOfen = NormalizeOfen(childGame.GetFenString());
                    childTranscripts.Add(new ChildTranscript(
                        ordinal, move, incompatibleOfen, HashText(incompatibleOfen),
                        ClassName(PositionClass.ReplayFailure),
                        compatibilityFailure, SideName(childGame.ToMove),
                        childGame.HalfMoveClock, null, 0));
                    continue;
                }
                var childClass = await Classify(childGame, root: false);
                var childOfen = NormalizeOfen(childGame.GetFenString());
                var childSha = HashText(childOfen);
                childTranscripts.Add(new ChildTranscript(
                    ordinal, move, childOfen, childSha,
                    ClassName(childClass.Classification), null,
                    SideName(childGame.ToMove), childGame.HalfMoveClock,
                    childClass.RepetitionCount, LegalMoves(childGame).Count));
                pendingChildren.Add(new PendingChild(
                    ordinal, move, childOfen, childSha,
                    childClass.Classification));
            }
            catch (InvalidMoveException)
            {
                childTranscripts.Add(new ChildTranscript(
                    ordinal, move, null, null,
                    ClassName(PositionClass.Illegal),
                    "chesslib-rejected-enumerated-legal-child",
                    null, null, null, 0));
            }
        }

        var firstRejected = childTranscripts.FirstOrDefault(child =>
            child.Classification != ClassName(PositionClass.ChildNonterminal));
        var teacherEligible = firstRejected is null &&
            childTranscripts.Count == legalMoves.Count && legalMoves.Count > 0;
        var rejection = teacherEligible ? null :
            firstRejected?.Classification ?? ClassName(PositionClass.ReplayFailure);
        var payload = new TranscriptPayload(
            1, TranscriptKind, source.RootId, source.GroupId, teacherEligible,
            rejection, replay.History, rootPosition, childTranscripts);
        var transcript = Seal(payload);
        if (!teacherEligible)
            return new Analysis(transcript, null, Array.Empty<EligibleChild>());

        var parentOfen = NormalizeOfen(rootGame.GetFenString());
        var parentSha = HashText(parentOfen);
        var eligibleRoot = new EligibleRoot(
            1, EligibleRootKind, source.RootId, source.GroupId,
            source.InitialOfen, source.Moves, source.PlyOfenSha256,
            parentOfen, parentSha, transcript.TranscriptSha256,
            pendingChildren.Count);
        var eligibleChildren = pendingChildren.Select(child =>
        {
            var id = HashText(
                $"{ChildIdDomain}\0{source.RootId}\0{source.GroupId}\0" +
                $"{child.Move}\0{child.Ofen}");
            return new EligibleChild(
                1, EligibleChildKind, source.RootId, source.GroupId,
                source.InitialOfen, source.Moves, source.PlyOfenSha256,
                parentOfen, parentSha, id, child.Move, child.Ordinal,
                child.Ofen, child.OfenSha256,
                ClassName(PositionClass.ChildNonterminal),
                transcript.TranscriptSha256);
        }).ToArray();
        return new Analysis(transcript, eligibleRoot, eligibleChildren);
    }

    private static async Task<ReplayOutcome> Replay(
        SourceRecord source,
        bool rejectTerminalContinuation)
    {
        var observed = new List<string>(source.Moves.Count);
        string initial;
        try
        {
            initial = NormalizeOfen(source.InitialOfen);
        }
        catch (InvalidDataException)
        {
            return ReplayFailure(
                source, observed, 0, null, "initial-ofen-invalid");
        }
        if (!string.Equals(initial, source.InitialOfen, StringComparison.Ordinal))
            return ReplayFailure(
                source, observed, 0, null, "initial-ofen-not-canonical");

        Game game;
        try
        {
            game = CreateGame(initial);
        }
        catch (Exception exception) when (
            exception is ArgumentException or FormatException or OverflowException)
        {
            return ReplayFailure(
                source, observed, 0, null, "initial-ofen-rejected-by-chesslib");
        }

        try
        {
            var compatibilityFailure = SenpaiPositionCompatibilityFailure(game);
            if (compatibilityFailure is not null)
            {
                game.Dispose();
                return ReplayFailure(
                    source, observed, 0, null, compatibilityFailure);
            }
            if (!string.Equals(
                    NormalizeOfen(game.GetFenString()), initial,
                    StringComparison.Ordinal))
            {
                game.Dispose();
                return ReplayFailure(
                    source, observed, 0, null, "initial-ofen-roundtrip-mismatch");
            }

            for (var index = 0; index < source.Moves.Count; index++)
            {
                if (rejectTerminalContinuation)
                {
                    var before = await Classify(game, root: true);
                    if (before.Classification != PositionClass.RootNonterminal)
                    {
                        game.Dispose();
                        return ReplayFailure(
                            source, observed, index + 1, source.Moves[index],
                            "history-continues-after-terminal");
                    }
                }
                try
                {
                    await game.DoMove(source.Moves[index], checkEndGame: false);
                }
                catch (InvalidMoveException)
                {
                    game.Dispose();
                    return IllegalFailure(
                        source, observed, index + 1, source.Moves[index]);
                }
                var canonicalMove = game.Moves[^1].Coordinate.ToLowerInvariant();
                if (!string.Equals(
                        canonicalMove, source.Moves[index],
                        StringComparison.Ordinal))
                {
                    game.Dispose();
                    return IllegalFailure(
                        source, observed, index + 1, source.Moves[index],
                        "chesslib-canonical-move-mismatch");
                }
                var transitionCompatibilityFailure =
                    SenpaiPositionCompatibilityFailure(game);
                if (transitionCompatibilityFailure is not null)
                {
                    game.Dispose();
                    return ReplayFailure(
                        source, observed, index + 1, source.Moves[index],
                        transitionCompatibilityFailure);
                }
                var actualOfen = NormalizeOfen(game.GetFenString());
                var actualSha = HashText(actualOfen);
                observed.Add(actualSha);
                if (!string.Equals(
                        actualSha, source.PlyOfenSha256[index],
                        StringComparison.Ordinal))
                {
                    game.Dispose();
                    return ReplayFailure(
                        source, observed, index + 1, source.Moves[index],
                        "ply-ofen-hash-mismatch", verifiedPlies: index);
                }
            }
            return new ReplayOutcome(
                game,
                new HistoryTranscript(
                    source.InitialOfen, HashText(source.InitialOfen),
                    source.Moves, source.PlyOfenSha256, observed,
                    observed.Count, null, null, null),
                null);
        }
        catch
        {
            game.Dispose();
            throw;
        }
    }

    private static ReplayOutcome ReplayFailure(
        SourceRecord source,
        IReadOnlyList<string> observed,
        int failurePly,
        string? failureMove,
        string code,
        int? verifiedPlies = null) =>
        new(
            null,
            new HistoryTranscript(
                source.InitialOfen, HashText(source.InitialOfen),
                source.Moves, source.PlyOfenSha256, observed,
                verifiedPlies ?? observed.Count,
                failurePly, failureMove, code),
            PositionClass.ReplayFailure);

    private static ReplayOutcome IllegalFailure(
        SourceRecord source,
        IReadOnlyList<string> observed,
        int failurePly,
        string failureMove,
        string code = "illegal-coordinate-history-move") =>
        new(
            null,
            new HistoryTranscript(
                source.InitialOfen, HashText(source.InitialOfen),
                source.Moves, source.PlyOfenSha256, observed,
                observed.Count, failurePly, failureMove,
                code),
            PositionClass.Illegal);

    private static async Task<Classified> Classify(Game game, bool root)
    {
        if (await game.IsCheckmate(game.ToMove))
            return new Classified(PositionClass.Checkmate, CurrentRepetitionCount(game));
        if (await game.IsStalemate(game.ToMove))
            return new Classified(PositionClass.Stalemate, CurrentRepetitionCount(game));

        var repetitionCount = CurrentRepetitionCount(game);
        if (game.Settings.DrawForRepetition > 0 &&
            repetitionCount >= game.Settings.DrawForRepetition)
        {
            if (!game.IsDraw(game.ToMove))
                throw new InvalidDataException(
                    "ChessLib repetition state disagrees with IsDraw");
            return new Classified(PositionClass.DrawRepetition, repetitionCount);
        }
        if (game.HalfMoveClock >= 100)
        {
            if (!game.IsDraw(game.ToMove))
                throw new InvalidDataException(
                    "ChessLib halfmove state disagrees with IsDraw");
            return new Classified(PositionClass.DrawHalfmove, repetitionCount);
        }
        if (game.IsDraw(game.ToMove))
            return new Classified(PositionClass.DrawInsufficient, repetitionCount);
        return new Classified(
            root ? PositionClass.RootNonterminal : PositionClass.ChildNonterminal,
            repetitionCount);
    }

    private static int CurrentRepetitionCount(Game game)
    {
        var fields = NormalizeOfen(game.GetFenString()).Split(' ');
        var key = string.Join(' ', fields.Take(4));
        return game.Positions.TryGetValue(key, out var count) ? count : 0;
    }

    private static PositionTranscript PositionOf(
        Game game,
        Classified classified,
        int legalMoveCount)
    {
        var ofen = NormalizeOfen(game.GetFenString());
        return new PositionTranscript(
            ofen, HashText(ofen), ClassName(classified.Classification),
            SideName(game.ToMove), game.HalfMoveClock,
            classified.RepetitionCount, legalMoveCount);
    }

    private static List<string> LegalMoves(Game game)
    {
        var moves = new HashSet<string>(StringComparer.Ordinal);
        foreach (var square in game.Board.Squares)
        {
            var piece = square.Piece;
            if (piece is null || piece.Color != game.ToMove)
                continue;
            foreach (var target in game.GetAvailableSquares(square))
            {
                if (target.Notation.Equals(
                        square.Notation, StringComparison.OrdinalIgnoreCase))
                    continue;
                var coordinate =
                    square.Notation.ToLowerInvariant() +
                    target.Notation.ToLowerInvariant();
                var promotion = piece.Type == Piece.Pieces.Pawn &&
                    ((piece.Color == Game.Colors.White && target.Rank == 9) ||
                     (piece.Color == Game.Colors.Black && target.Rank == 0));
                if (promotion)
                {
                    foreach (var suffix in "qrbncw")
                        moves.Add(coordinate + suffix);
                }
                else
                {
                    moves.Add(coordinate);
                }
            }
        }
        return moves.OrderBy(value => value, StringComparer.Ordinal).ToList();
    }

    private static Game CreateGame(string initialOfen)
    {
        var game = new Game();
        game.Init(new Game.GameSettings
        {
            Variant = ChessVariant.Omega,
            InitialFenPosition = initialOfen,
            DrawForRepetition = 3,
            Players = new List<Player>
            {
                new HumanPlayer(Game.Colors.White, "G6 white", null),
                new HumanPlayer(Game.Colors.Black, "G6 black", null)
            }
        });
        if (game.Settings.Variant != ChessVariant.Omega ||
            game.Settings.DrawForRepetition != 3)
            throw new InvalidDataException("Omega/repetition settings did not bind");
        return game;
    }

    private static string? SenpaiPositionCompatibilityFailure(Game game)
    {
        if (game.HalfMoveClock < 0 || game.FullMoveNumber < 1)
            return "senpai-ofen-counter-invalid";
        var whiteKings = 0;
        var blackKings = 0;
        foreach (var square in game.Board.Squares)
        {
            var piece = square.Piece;
            if (piece is null)
                continue;
            if (piece.Type == Piece.Pieces.King)
            {
                if (piece.Color == Game.Colors.White)
                    whiteKings++;
                else
                    blackKings++;
            }
            if (piece.Type == Piece.Pieces.Pawn &&
                (square.IsWizardSquare || square.Rank is 0 or 9))
                return "senpai-ofen-pawn-on-forbidden-square";
        }
        return whiteKings == 1 && blackKings == 1
            ? null
            : "senpai-ofen-king-count-invalid";
    }

    private static RootTranscript Seal(TranscriptPayload payload)
    {
        var payloadJson = JsonSerializer.Serialize(payload, Json);
        return new RootTranscript(
            payload.SchemaVersion, payload.Kind, payload.RootId,
            payload.GroupId, payload.TeacherEligible, payload.Rejection,
            payload.History, payload.Root, payload.Children,
            HashText($"{TranscriptHashDomain}\0{payloadJson}"));
    }

    private static SourceRecord ParseSource(
        string line,
        string input,
        int lineNumber)
    {
        using var document = JsonDocument.Parse(line);
        if (document.RootElement.ValueKind != JsonValueKind.Object)
            throw new InvalidDataException($"{input}:{lineNumber}: expected object");
        var allowed = new HashSet<string>(new[]
        {
            "schemaVersion", "kind", "rootId", "groupId", "initialOfen",
            "moves", "plyOfenSha256"
        }, StringComparer.Ordinal);
        var seen = new HashSet<string>(StringComparer.Ordinal);
        foreach (var property in document.RootElement.EnumerateObject())
        {
            if (!allowed.Contains(property.Name))
                throw new InvalidDataException(
                    $"{input}:{lineNumber}: unknown field {property.Name}");
            if (!seen.Add(property.Name))
                throw new InvalidDataException(
                    $"{input}:{lineNumber}: duplicate field {property.Name}");
        }
        if (!allowed.SetEquals(seen))
            throw new InvalidDataException(
                $"{input}:{lineNumber}: source fields are incomplete");
        var root = document.RootElement;
        var schema = root.GetProperty("schemaVersion");
        if (schema.ValueKind != JsonValueKind.Number ||
            !schema.TryGetInt32(out var version) || version != 1)
            throw new InvalidDataException(
                $"{input}:{lineNumber}: schemaVersion must be 1");
        var kind = RequiredString(root, "kind", input, lineNumber);
        if (kind != SourceKind)
            throw new InvalidDataException(
                $"{input}:{lineNumber}: kind must be {SourceKind}");
        var rootId = RequiredId(root, "rootId", input, lineNumber);
        var groupId = RequiredId(root, "groupId", input, lineNumber);
        var initial = RequiredString(root, "initialOfen", input, lineNumber);
        var moves = RequiredStringArray(root, "moves", input, lineNumber);
        if (moves.Count > 4096)
            throw new InvalidDataException(
                $"{input}:{lineNumber}: history exceeds 4096 plies");
        for (var index = 0; index < moves.Count; index++)
        {
            if (!IsCanonicalCoordinate(moves[index]))
                throw new InvalidDataException(
                    $"{input}:{lineNumber}: move {index + 1} is not canonical coordinate notation");
        }
        var hashes = RequiredStringArray(
            root, "plyOfenSha256", input, lineNumber);
        if (hashes.Count != moves.Count)
            throw new InvalidDataException(
                $"{input}:{lineNumber}: moves/hash count mismatch");
        if (hashes.Any(hash => !IsSha256(hash)))
            throw new InvalidDataException(
                $"{input}:{lineNumber}: OFEN hashes must be lowercase SHA-256");
        return new SourceRecord(
            version, kind, rootId, groupId, initial, moves, hashes);
    }

    private static string RequiredString(
        JsonElement root,
        string name,
        string input,
        int lineNumber)
    {
        var value = root.GetProperty(name);
        if (value.ValueKind != JsonValueKind.String ||
            string.IsNullOrWhiteSpace(value.GetString()))
            throw new InvalidDataException(
                $"{input}:{lineNumber}: {name} must be a nonempty string");
        return value.GetString()!;
    }

    private static string RequiredId(
        JsonElement root,
        string name,
        string input,
        int lineNumber)
    {
        var value = RequiredString(root, name, input, lineNumber);
        if (value.Length > 256 || value.Contains('\0'))
            throw new InvalidDataException(
                $"{input}:{lineNumber}: invalid {name}");
        return value;
    }

    private static IReadOnlyList<string> RequiredStringArray(
        JsonElement root,
        string name,
        string input,
        int lineNumber)
    {
        var value = root.GetProperty(name);
        if (value.ValueKind != JsonValueKind.Array)
            throw new InvalidDataException(
                $"{input}:{lineNumber}: {name} must be an array");
        var result = new List<string>();
        foreach (var item in value.EnumerateArray())
        {
            if (item.ValueKind != JsonValueKind.String || item.GetString() is null)
                throw new InvalidDataException(
                    $"{input}:{lineNumber}: {name} must contain strings");
            result.Add(item.GetString()!);
        }
        return result;
    }

    private static bool IsCanonicalCoordinate(string move)
    {
        if (move.Length is not (4 or 5) || move != move.ToLowerInvariant())
            return false;
        if (!IsSquare(move.AsSpan(0, 2)) || !IsSquare(move.AsSpan(2, 2)))
            return false;
        return move.Length == 4 || "qrbncw".Contains(move[4]);
    }

    private static bool IsSquare(ReadOnlySpan<char> square) =>
        square.Length == 2 &&
        ((square[0] is >= 'a' and <= 'j' && square[1] is >= '0' and <= '9') ||
         (square[0] == 'w' && square[1] is >= '1' and <= '4'));

    private static bool IsSha256(string value) =>
        value.Length == 64 && value.All(character =>
            character is >= '0' and <= '9' or >= 'a' and <= 'f');

    private static string NormalizeOfen(string value)
    {
        var fields = value.Split(
            (char[]?)null, StringSplitOptions.RemoveEmptyEntries);
        if (fields.Length != 6)
            throw new InvalidDataException("Omega OFEN must contain six fields");
        if (fields[1] is not ("w" or "b"))
            throw new InvalidDataException("Omega OFEN side must be w or b");
        if (!fields[0].Contains('[') || !fields[0].EndsWith(']'))
            throw new InvalidDataException("Omega OFEN must contain four corners");
        return string.Join(' ', fields);
    }

    private static string ClassName(PositionClass value) => value switch
    {
        PositionClass.RootNonterminal => "root-nonterminal",
        PositionClass.ChildNonterminal => "child-nonterminal",
        PositionClass.Checkmate => "checkmate",
        PositionClass.Stalemate => "stalemate",
        PositionClass.DrawRepetition => "draw-repetition",
        PositionClass.DrawHalfmove => "draw-halfmove",
        PositionClass.DrawInsufficient => "draw-insufficient",
        PositionClass.Illegal => "illegal",
        PositionClass.ReplayFailure => "replay-failure",
        _ => throw new ArgumentOutOfRangeException(nameof(value))
    };

    private static string SideName(Game.Colors side) =>
        side == Game.Colors.White ? "w" : "b";

    private static void Count(
        IDictionary<string, int> counts,
        string? classification)
    {
        if (classification is not null)
            counts[classification]++;
    }

    private static void AssertPinnedChessLib()
    {
        var actual = HashFile(typeof(Game).Assembly.Location);
        if (actual != ExpectedChessLibSha256)
            throw new InvalidDataException(
                $"ChessLib identity mismatch: expected {ExpectedChessLibSha256}, actual {actual}");
    }

    private static string HashText(string value) =>
        Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(value)))
            .ToLowerInvariant();

    private static string HashFile(string path)
    {
        using var stream = File.OpenRead(path);
        return Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();
    }

    private static ArtifactIdentity IdentityOf(string path)
    {
        using var stream = new FileStream(
            path, FileMode.Open, FileAccess.Read, FileShare.Read);
        return new ArtifactIdentity(
            stream.Length,
            Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant());
    }

    private static FileStream NewOutput(string path) =>
        new(path, FileMode.CreateNew, FileAccess.Write, FileShare.None,
            1 << 20, FileOptions.SequentialScan);

    private static StreamWriter NewWriter(Stream stream) =>
        new(stream, new UTF8Encoding(false), 1 << 20, leaveOpen: true);

    private static async Task FlushToDisk(StreamWriter writer, FileStream stream)
    {
        await writer.FlushAsync();
        await stream.FlushAsync();
        stream.Flush(flushToDisk: true);
    }

    private static async Task WriteJson(string path, object value)
    {
        var bytes = Encoding.UTF8.GetBytes(
            JsonSerializer.Serialize(value, new JsonSerializerOptions(Json)
            {
                WriteIndented = true
            }) + "\n");
        await using var stream = new FileStream(
            path, FileMode.CreateNew, FileAccess.Write, FileShare.None);
        await stream.WriteAsync(bytes);
        await stream.FlushAsync();
        stream.Flush(flushToDisk: true);
    }

    private static string AdjacentTemporary(string path) =>
        Path.Combine(
            Path.GetDirectoryName(path)!,
            $".{Path.GetFileName(path)}.{Guid.NewGuid():N}.tmp");

    private static void RequireDistinctPaths(params string[] paths)
    {
        if (paths.Distinct(StringComparer.OrdinalIgnoreCase).Count() != paths.Length)
            throw new ArgumentException("input and output paths must all differ");
    }

    private static void RefuseExisting(string path, string description)
    {
        if (File.Exists(path) || Directory.Exists(path))
            throw new IOException(
                $"refusing to overwrite existing {description}: {path}");
    }

    private static void TryDelete(string path)
    {
        try
        {
            if (File.Exists(path))
                File.Delete(path);
        }
        catch (IOException)
        {
        }
        catch (UnauthorizedAccessException)
        {
        }
    }

    private static void TryDeleteIfIdentity(
        string path,
        ArtifactIdentity? expected)
    {
        if (expected is null)
            return;
        try
        {
            if (File.Exists(path) && IdentityOf(path) == expected.Value)
                File.Delete(path);
        }
        catch (IOException)
        {
        }
        catch (UnauthorizedAccessException)
        {
        }
    }

    private static async Task SelfTest()
    {
        var accepted = await SourceFromHistory(
            "accepted", "fixture", OfficialInitialOfen,
            new[] { "f1f2", "f8f7" });
        var acceptedAnalysis = await Analyze(accepted);
        Require(acceptedAnalysis.Transcript.TeacherEligible,
            "ordinary opening root was rejected");
        Require(acceptedAnalysis.Transcript.Root?.Classification ==
                ClassName(PositionClass.RootNonterminal),
            "ordinary root was not root-nonterminal");
        Require(acceptedAnalysis.EligibleChildren.Count > 0 &&
                acceptedAnalysis.EligibleChildren.All(child =>
                    child.Classification == ClassName(PositionClass.ChildNonterminal)),
            "ordinary root did not publish only nonterminal children");

        var threefold = await SourceFromHistory(
            "threefold", "fixture", OfficialInitialOfen,
            new[]
            {
                "c0d2", "c9d7", "d2c0", "d7c9",
                "c0d2", "c9d7", "d2c0", "d7c9"
            });
        await RequireRootClass(threefold, PositionClass.DrawRepetition);

        const string halfmoveInitial =
            "4k5/10/10/10/10/10/10/10/10/4KR4[-/-/-/-] w - - 99 1";
        var halfmove = await SourceFromHistory(
            "halfmove", "fixture", halfmoveInitial, new[] { "f0f1" });
        await RequireRootClass(halfmove, PositionClass.DrawHalfmove);

        const string championInsufficient =
            "4k5/10/10/10/10/10/10/10/10/4KC4[-/-/-/-] w - - 0 1";
        const string wizardInsufficient =
            "4k5/10/10/10/10/10/10/10/10/4KW4[-/-/-/-] w - - 0 1";
        await RequireRootClass(
            new SourceRecord(1, SourceKind, "k-c-k", "fixture",
                championInsufficient, Array.Empty<string>(), Array.Empty<string>()),
            PositionClass.DrawInsufficient);
        await RequireRootClass(
            new SourceRecord(1, SourceKind, "k-w-k", "fixture",
                wizardInsufficient, Array.Empty<string>(), Array.Empty<string>()),
            PositionClass.DrawInsufficient);

        const string mate =
            "10/10/10/10/10/10/10/10/KC8/C9[k/-/-/-] b - - 0 1";
        const string stalemate =
            "10/10/10/10/10/10/10/C9/K9/C9[k/-/-/-] b - - 0 1";
        await RequireRootClass(
            new SourceRecord(1, SourceKind, "mate", "fixture", mate,
                Array.Empty<string>(), Array.Empty<string>()),
            PositionClass.Checkmate);
        await RequireRootClass(
            new SourceRecord(1, SourceKind, "stalemate", "fixture", stalemate,
                Array.Empty<string>(), Array.Empty<string>()),
            PositionClass.Stalemate);

        const string mateAndHalfmove =
            "10/10/10/10/10/10/10/10/KC8/C9[k/-/-/-] b - - 100 1";
        const string stalemateAndHalfmove =
            "10/10/10/10/10/10/10/C9/K9/C9[k/-/-/-] b - - 100 1";
        await RequireRootClass(
            new SourceRecord(1, SourceKind, "mate-over-halfmove", "fixture",
                mateAndHalfmove, Array.Empty<string>(), Array.Empty<string>()),
            PositionClass.Checkmate);
        await RequireRootClass(
            new SourceRecord(1, SourceKind, "stalemate-over-halfmove", "fixture",
                stalemateAndHalfmove, Array.Empty<string>(), Array.Empty<string>()),
            PositionClass.Stalemate);
        var repetitionAndHalfmove = await SourceFromHistory(
            "repetition-over-halfmove", "fixture",
            OfficialInitialOfen[..^3] + "92 1", threefold.Moves);
        await RequireRootClass(
            repetitionAndHalfmove, PositionClass.DrawRepetition);
        const string halfmoveAndInsufficient =
            "4k5/10/10/10/10/10/10/10/10/4KC4[-/-/-/-] w - - 100 1";
        await RequireRootClass(
            new SourceRecord(1, SourceKind, "halfmove-over-insufficient", "fixture",
                halfmoveAndInsufficient,
                Array.Empty<string>(), Array.Empty<string>()),
            PositionClass.DrawHalfmove);
        var terminalReplay = await Replay(
            halfmove, rejectTerminalContinuation: true);
        using (var replayedTerminal = terminalReplay.Game ??
               throw new InvalidDataException("terminal replay fixture failed"))
        {
            Require(!replayedTerminal.Ended,
                "checkEndGame:false unexpectedly marked replayed terminal history ended");
        }
        var terminalContinuation = await Analyze(new SourceRecord(
            1, SourceKind, "terminal-continuation", "fixture", mate,
            new[] { "w1a1" }, new[] { new string('0', 64) }));
        Require(
            terminalContinuation.Transcript.Rejection ==
                ClassName(PositionClass.ReplayFailure) &&
            terminalContinuation.Transcript.History.FailureCode ==
                "history-continues-after-terminal",
            "history continuation after a terminal position was accepted");

        const string mateInOne =
            "10/10/10/10/C9/10/10/k9/1C8/K9[-/-/-/-] w - - 0 1";
        var childTerminal = await Analyze(new SourceRecord(
            1, SourceKind, "child-mate", "fixture", mateInOne,
            Array.Empty<string>(), Array.Empty<string>()));
        Require(!childTerminal.Transcript.TeacherEligible &&
                childTerminal.Transcript.Root?.Classification ==
                    ClassName(PositionClass.RootNonterminal) &&
                childTerminal.Transcript.Children.Any(child =>
                    child.Classification == ClassName(PositionClass.Checkmate)),
            "mate-in-one root was not rejected because of its terminal child");

        var oneMove = await SourceFromHistory(
            "tamper-base", "fixture", OfficialInitialOfen,
            new[] { "c0d2" });
        var badHash = oneMove.PlyOfenSha256.ToArray();
        badHash[0] = (badHash[0][0] == '0' ? "1" : "0") + badHash[0][1..];
        var hashTamper = await Analyze(oneMove with
        {
            RootId = "tampered-ofen",
            PlyOfenSha256 = badHash
        });
        Require(hashTamper.Transcript.Rejection ==
                ClassName(PositionClass.ReplayFailure) &&
                hashTamper.Transcript.History.VerifiedPlies == 0 &&
                hashTamper.Transcript.History.ObservedPlyOfenSha256.Count == 1,
            "tampered per-ply OFEN hash was accepted");
        var lateBadHash = accepted.PlyOfenSha256.ToArray();
        lateBadHash[^1] = (lateBadHash[^1][0] == '0' ? "1" : "0") +
            lateBadHash[^1][1..];
        var lateHashTamper = await Analyze(accepted with
        {
            RootId = "late-tampered-ofen",
            PlyOfenSha256 = lateBadHash
        });
        Require(
            lateHashTamper.Transcript.Rejection ==
                ClassName(PositionClass.ReplayFailure) &&
            lateHashTamper.Transcript.History.FailureCode ==
                "ply-ofen-hash-mismatch" &&
            lateHashTamper.Transcript.History.FailurePly == accepted.Moves.Count &&
            lateHashTamper.Transcript.History.VerifiedPlies ==
                accepted.Moves.Count - 1 &&
            lateHashTamper.Transcript.History.ObservedPlyOfenSha256.Count ==
                accepted.Moves.Count &&
            lateHashTamper.Transcript.History.ObservedPlyOfenSha256[^1] !=
                lateBadHash[^1],
            "late OFEN-hash mismatch did not preserve evidence and prior-match count");
        var historyTamper = await Analyze(oneMove with
        {
            RootId = "tampered-history",
            Moves = new[] { "j0h2" }
        });
        Require(historyTamper.Transcript.Rejection ==
                ClassName(PositionClass.ReplayFailure),
            "tampered coordinate history was accepted");
        var illegal = await Analyze(oneMove with
        {
            RootId = "illegal-history",
            Moves = new[] { "c0c9" }
        });
        Require(illegal.Transcript.Rejection == ClassName(PositionClass.Illegal),
            "illegal coordinate history was not classified illegal");

        const string promotionInitial =
            "5k4/P9/10/10/10/10/10/10/10/5K4[-/-/-/-] w - - 0 1";
        foreach (var suffix in "qrbncw")
        {
            var explicitPromotion = await SourceFromHistory(
                $"promotion-{suffix}", "fixture", promotionInitial,
                new[] { $"a8a9{suffix}" });
            var promotionReplay = await Replay(
                explicitPromotion, rejectTerminalContinuation: true);
            Require(promotionReplay.Game is not null &&
                    promotionReplay.FailureClass is null,
                $"explicit Omega promotion suffix {suffix} was rejected");
            promotionReplay.Game!.Dispose();
        }
        var implicitPromotion = await SourceFromHistory(
            "promotion-implicit", "fixture", promotionInitial,
            new[] { "a8a9" });
        var implicitPromotionAnalysis = await Analyze(implicitPromotion);
        Require(
            implicitPromotionAnalysis.Transcript.Rejection ==
                ClassName(PositionClass.Illegal) &&
            implicitPromotionAnalysis.Transcript.History.FailureCode ==
                "chesslib-canonical-move-mismatch",
            "unsuffixed promotion was not rejected before teacher publication");

        const string duplicateKing =
            "4k5/10/10/10/10/10/10/10/10/4KK4[-/-/-/-] w - - 0 1";
        const string promotionRankPawn =
            "4k5/10/10/10/10/10/10/10/10/4KP4[-/-/-/-] w - - 0 1";
        const string cornerPawn =
            "4k5/10/10/10/10/10/10/10/10/4K5[P/-/-/-] w - - 0 1";
        await RequireReplayFailureCode(
            "duplicate-king", duplicateKing,
            "senpai-ofen-king-count-invalid");
        await RequireReplayFailureCode(
            "promotion-rank-pawn", promotionRankPawn,
            "senpai-ofen-pawn-on-forbidden-square");
        await RequireReplayFailureCode(
            "corner-pawn", cornerPawn,
            "senpai-ofen-pawn-on-forbidden-square");

        var maxFullmoveRoot = new SourceRecord(
            1, SourceKind, "max-fullmove-child", "fixture",
            OfficialInitialOfen[..(OfficialInitialOfen.LastIndexOf(' ') + 1)] +
                int.MaxValue,
            Array.Empty<string>(), Array.Empty<string>());
        var maxFullmoveAnalysis = await Analyze(maxFullmoveRoot);
        Require(
            !maxFullmoveAnalysis.Transcript.TeacherEligible &&
            maxFullmoveAnalysis.Transcript.Root?.Classification ==
                ClassName(PositionClass.RootNonterminal) &&
            maxFullmoveAnalysis.Transcript.Children.Count > 0 &&
            maxFullmoveAnalysis.Transcript.Children.All(child =>
                child.Classification == ClassName(PositionClass.ReplayFailure) &&
                child.FailureCode == "senpai-ofen-counter-invalid"),
            "INT_MAX fullmove root published wrapped child OFENs");
        var onePlyOverflow = await SourceFromHistory(
            "one-ply-fullmove-overflow", "fixture",
            maxFullmoveRoot.InitialOfen, new[] { "f1f2" });
        var onePlyOverflowAnalysis = await Analyze(onePlyOverflow);
        Require(
            onePlyOverflowAnalysis.Transcript.Rejection ==
                ClassName(PositionClass.ReplayFailure) &&
            onePlyOverflowAnalysis.Transcript.History.FailurePly == 1 &&
            onePlyOverflowAnalysis.Transcript.History.FailureCode ==
                "senpai-ofen-counter-invalid",
            "one-ply fullmove overflow was accepted");
        var twoPlyInitial =
            OfficialInitialOfen[..(OfficialInitialOfen.LastIndexOf(' ') + 1)] +
            (int.MaxValue - 1);
        var twoPlyOverflow = await SourceFromHistory(
            "two-ply-fullmove-overflow", "fixture", twoPlyInitial,
            new[] { "f1f2", "f8f7" });
        var twoPlyOverflowAnalysis = await Analyze(twoPlyOverflow);
        Require(
            twoPlyOverflowAnalysis.Transcript.Rejection ==
                ClassName(PositionClass.ReplayFailure) &&
            twoPlyOverflowAnalysis.Transcript.History.VerifiedPlies == 1 &&
            twoPlyOverflowAnalysis.Transcript.History.FailurePly == 2 &&
            twoPlyOverflowAnalysis.Transcript.History.FailureCode ==
                "senpai-ofen-counter-invalid",
            "multi-ply fullmove overflow was not rejected at its transition");

        var repeated = await Analyze(accepted);
        Require(
            JsonSerializer.Serialize(acceptedAnalysis.Transcript, Json) ==
            JsonSerializer.Serialize(repeated.Transcript, Json),
            "same source did not produce a byte-stable transcript");

        await PublicationSelfTest(accepted, threefold);
        Console.WriteLine(
            "OmegaTerminalPreclassifierG6 self-test passed: ordinary acceptance, " +
            "threefold, 99->100 halfmove, K+C/K, K+W/K, mate, stalemate, " +
            "ordered-overlap precedence, checkEndGame:false, terminal continuation/child, " +
            "illegal history, early/late history/OFEN tamper, deterministic " +
            "transcript, six explicit promotion suffixes, implicit-promotion rejection, " +
            "duplicate-king/rank-pawn/corner-pawn rejection, fullmove-overflow " +
            "root/history rejection, sealed manifest policy, and no-clobber publication.");
    }

    private static async Task RequireReplayFailureCode(
        string rootId,
        string initialOfen,
        string expectedCode)
    {
        var analysis = await Analyze(new SourceRecord(
            1, SourceKind, rootId, "fixture", initialOfen,
            Array.Empty<string>(), Array.Empty<string>()));
        Require(
            analysis.Transcript.Rejection ==
                ClassName(PositionClass.ReplayFailure) &&
            analysis.Transcript.History.FailureCode == expectedCode,
            $"{rootId} did not fail with {expectedCode}");
    }

    private static async Task RequireRootClass(
        SourceRecord source,
        PositionClass expected)
    {
        var analysis = await Analyze(source);
        Require(!analysis.Transcript.TeacherEligible,
            $"{source.RootId} terminal fixture was eligible");
        Require(
            analysis.Transcript.Root?.Classification == ClassName(expected),
            $"{source.RootId} expected {ClassName(expected)}, actual " +
            $"{analysis.Transcript.Root?.Classification ?? analysis.Transcript.Rejection}");
        using var game = (await Replay(source, true)).Game
            ?? throw new InvalidDataException($"{source.RootId} did not replay");
        if (expected == PositionClass.Checkmate)
            Require(await game.IsCheckmate(game.ToMove),
                "mate fixture disagrees with ChessLib");
        else if (expected == PositionClass.Stalemate)
            Require(await game.IsStalemate(game.ToMove),
                "stalemate fixture disagrees with ChessLib");
        else
            Require(game.IsDraw(game.ToMove),
                $"{source.RootId} draw fixture disagrees with ChessLib");
    }

    private static async Task<SourceRecord> SourceFromHistory(
        string rootId,
        string groupId,
        string initialOfen,
        IReadOnlyList<string> moves)
    {
        using var game = CreateGame(initialOfen);
        var hashes = new List<string>(moves.Count);
        foreach (var move in moves)
        {
            await game.DoMove(move, checkEndGame: false);
            hashes.Add(HashText(NormalizeOfen(game.GetFenString())));
        }
        return new SourceRecord(
            1, SourceKind, rootId, groupId, initialOfen,
            moves.ToArray(), hashes);
    }

    private static async Task PublicationSelfTest(
        SourceRecord accepted,
        SourceRecord rejected)
    {
        var root = Path.Combine(
            Path.GetTempPath(),
            $"omega-g6-terminal-preclassifier-{Guid.NewGuid():N}");
        Directory.CreateDirectory(root);
        try
        {
            var input = Path.Combine(root, "input.jsonl");
            var transcript = Path.Combine(root, "transcript.jsonl");
            var roots = Path.Combine(root, "roots.jsonl");
            var children = Path.Combine(root, "children.jsonl");
            var manifest = Path.Combine(root, "manifest.json");
            await File.WriteAllTextAsync(
                input,
                JsonSerializer.Serialize(accepted, Json) + "\n" +
                JsonSerializer.Serialize(rejected, Json) + "\n",
                new UTF8Encoding(false));
            var options = new Options(
                input, transcript, roots, children, manifest);
            await Run(options);
            var publishedManifest = JsonSerializer.Deserialize<Manifest>(
                await File.ReadAllTextAsync(manifest, Utf8), Json)
                ?? throw new InvalidDataException(
                    "published manifest did not deserialize");
            var policy = publishedManifest.Policy;
            var compatibility = policy.SenpaiOfenCompatibility;
            Require(
                policy.Variant == "omega" &&
                policy.Replay ==
                    "ChessLib.Game.DoMove(move, checkEndGame: false)" &&
                policy.OrderedSemantics.SequenceEqual(new[]
                {
                    "checkmate", "stalemate", "draw-repetition",
                    "draw-halfmove", "draw-insufficient", "nonterminal"
                }) &&
                policy.DrawForRepetition == 3 &&
                compatibility.ExactKingsPerSide == 1 &&
                compatibility.ForbiddenPawnLocations.SequenceEqual(new[]
                {
                    "W1-W4", "all board squares on ranks 0 and 9"
                }) &&
                compatibility.MinimumHalfmoveClock == 0 &&
                compatibility.MinimumFullmoveNumber == 1 &&
                compatibility.ValidationPoints.SequenceEqual(new[]
                {
                    "initial-position", "after-each-history-ply",
                    "after-each-legal-child"
                }) &&
                policy.Eligibility ==
                    "reject the root unless root and every legal child are nonterminal",
                "published manifest did not seal the exact classification policy");
            Require(File.ReadLines(transcript).Count() == 2,
                "publication transcript count changed");
            Require(File.ReadLines(roots).Count() == 1,
                "publication did not exclude the rejected root");
            Require(File.ReadLines(children).Any(),
                "publication emitted no eligible children");
            var identities = new[]
            {
                IdentityOf(transcript), IdentityOf(roots),
                IdentityOf(children), IdentityOf(manifest)
            };
            try
            {
                await Run(options);
                throw new InvalidDataException("no-clobber rerun succeeded");
            }
            catch (IOException)
            {
            }
            Require(identities.SequenceEqual(new[]
                {
                    IdentityOf(transcript), IdentityOf(roots),
                    IdentityOf(children), IdentityOf(manifest)
                }),
                "no-clobber rerun changed published bytes");
        }
        finally
        {
            if (Directory.Exists(root))
                Directory.Delete(root, recursive: true);
        }
    }

    private static void Require(bool condition, string message)
    {
        if (!condition)
            throw new InvalidDataException(message);
    }

    private enum PositionClass
    {
        RootNonterminal,
        ChildNonterminal,
        Checkmate,
        Stalemate,
        DrawRepetition,
        DrawHalfmove,
        DrawInsufficient,
        Illegal,
        ReplayFailure
    }

    private readonly record struct Classified(
        PositionClass Classification,
        int RepetitionCount);

    private readonly record struct ArtifactIdentity(long Bytes, string Sha256);

    private sealed record SourceRecord(
        int SchemaVersion,
        string Kind,
        string RootId,
        string GroupId,
        string InitialOfen,
        IReadOnlyList<string> Moves,
        IReadOnlyList<string> PlyOfenSha256);

    private sealed record HistoryTranscript(
        string InitialOfen,
        string InitialOfenSha256,
        IReadOnlyList<string> Moves,
        IReadOnlyList<string> ExpectedPlyOfenSha256,
        IReadOnlyList<string> ObservedPlyOfenSha256,
        int VerifiedPlies,
        int? FailurePly,
        string? FailureMove,
        string? FailureCode);

    private sealed record PositionTranscript(
        string Ofen,
        string OfenSha256,
        string Classification,
        string SideToMove,
        int HalfmoveClock,
        int RepetitionCount,
        int LegalMoveCount);

    private sealed record ChildTranscript(
        int Ordinal,
        string Move,
        string? Ofen,
        string? OfenSha256,
        string Classification,
        string? FailureCode,
        string? SideToMove,
        int? HalfmoveClock,
        int? RepetitionCount,
        int LegalMoveCount);

    private sealed record TranscriptPayload(
        int SchemaVersion,
        string Kind,
        string RootId,
        string GroupId,
        bool TeacherEligible,
        string? Rejection,
        HistoryTranscript History,
        PositionTranscript? Root,
        IReadOnlyList<ChildTranscript> Children);

    private sealed record RootTranscript(
        int SchemaVersion,
        string Kind,
        string RootId,
        string GroupId,
        bool TeacherEligible,
        string? Rejection,
        HistoryTranscript History,
        PositionTranscript? Root,
        IReadOnlyList<ChildTranscript> Children,
        string TranscriptSha256);

    private sealed record PendingChild(
        int Ordinal,
        string Move,
        string Ofen,
        string OfenSha256,
        PositionClass Classification);

    private sealed record EligibleRoot(
        int SchemaVersion,
        string Kind,
        string RootId,
        string GroupId,
        string InitialOfen,
        IReadOnlyList<string> Moves,
        IReadOnlyList<string> PlyOfenSha256,
        string RootOfen,
        string RootOfenSha256,
        string PreclassificationTranscriptSha256,
        int LegalChildCount);

    private sealed record EligibleChild(
        int SchemaVersion,
        string Kind,
        string RootId,
        string GroupId,
        string InitialOfen,
        IReadOnlyList<string> Moves,
        IReadOnlyList<string> PlyOfenSha256,
        string ParentOfen,
        string ParentOfenSha256,
        string ChildId,
        string Move,
        int MoveOrdinal,
        string ChildOfen,
        string ChildOfenSha256,
        string Classification,
        string PreclassificationTranscriptSha256);

    private sealed record ReplayOutcome(
        Game? Game,
        HistoryTranscript History,
        PositionClass? FailureClass);

    private sealed record Analysis(
        RootTranscript Transcript,
        EligibleRoot? EligibleRoot,
        IReadOnlyList<EligibleChild> EligibleChildren);

    private sealed record Policy(
        string Variant,
        string Replay,
        IReadOnlyList<string> OrderedSemantics,
        int DrawForRepetition,
        SenpaiOfenCompatibilityPolicy SenpaiOfenCompatibility,
        string Eligibility);

    private sealed record SenpaiOfenCompatibilityPolicy(
        int ExactKingsPerSide,
        IReadOnlyList<string> ForbiddenPawnLocations,
        int MinimumHalfmoveClock,
        int MinimumFullmoveNumber,
        IReadOnlyList<string> ValidationPoints);

    private sealed record Coverage(
        int SourceRecords,
        int AcceptedRoots,
        int RejectedRoots,
        int EligibleChildren,
        IReadOnlyDictionary<string, int> ClassificationCounts);

    private sealed record RuntimeEvidence(
        string ExpectedChessLibSha256,
        string ActualChessLibSha256,
        string ClassifierAssemblySha256);

    private sealed record Manifest(
        int SchemaVersion,
        string Kind,
        Policy Policy,
        Coverage Coverage,
        ArtifactIdentity Input,
        ArtifactIdentity Transcript,
        ArtifactIdentity EligibleRoots,
        ArtifactIdentity EligibleChildren,
        RuntimeEvidence Runtime);

    private sealed record Options(
        string Input,
        string Transcript,
        string EligibleRoots,
        string EligibleChildren,
        string Manifest)
    {
        public static Options Parse(string[] args)
        {
            string Required(string name)
            {
                var values = new List<string>();
                for (var index = 0; index < args.Length; index++)
                {
                    if (args[index] != name)
                        continue;
                    if (++index >= args.Length)
                        throw new ArgumentException($"{name} requires a value");
                    values.Add(args[index]);
                }
                return values.Count == 1
                    ? values[0]
                    : throw new ArgumentException(
                        $"{name} is required exactly once");
            }
            var known = new HashSet<string>(new[]
            {
                "--input", "--transcript", "--eligible-roots",
                "--eligible-children", "--manifest"
            }, StringComparer.Ordinal);
            for (var index = 0; index < args.Length; index += 2)
            {
                if (!known.Contains(args[index]))
                    throw new ArgumentException($"unknown option {args[index]}");
                if (index + 1 >= args.Length)
                    throw new ArgumentException($"{args[index]} requires a value");
            }
            return new Options(
                Required("--input"),
                Required("--transcript"),
                Required("--eligible-roots"),
                Required("--eligible-children"),
                Required("--manifest"));
        }
    }
}
