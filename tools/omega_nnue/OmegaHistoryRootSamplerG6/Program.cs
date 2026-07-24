using System.Globalization;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using ChessLib;

namespace OmegaHistoryRootSamplerG6;

internal static class Program
{
    private const string SourceKind = "omega-g6-history-root";
    private const string ManifestKind = "omega-g6-history-root-manifest";
    private const string ExpectedChessLibSha256 =
        "16a01414c9f486561aac0b48cebb7c485621d804a73c00803ec0aff55f572f4c";
    private const string ExpectedNewtonsoftJsonSha256 =
        "a28c251dfe36d881e9e2462e171441b8b0ec156fe3f452602c9149b1b9efe05b";
    private const string ExpectedSystemIoPortsSha256 =
        "2767e21f384cca9004b1266ec4b71d3b8a76898594382c377c726c780aa34508";
    private const string ExpectedDotnetHostSha256 =
        "a5ccdc3a41d5e5c6014ff64509aed176db39f4f14caffff3dd1997f8907e94d7";
    private const string ExpectedRuntimeManifestSha256 =
        "c8543f22f4b353ee461f2e417c3d06ea2f05de2622fab49b789923f9944e00ee";
    private const string ExpectedRuntimeBundleSha256 =
        "0ce194480dfb9a58a59c79bf94f19cb2eb571635a00547094a9c8ff28bb5f8f8";
    private const string ExpectedRuntimeManifestKind =
        "omega-nnue-king-state-v5-dotnet-runtime-bundle";
    private const string ExpectedRuntimeVersion = "10.0.9";
    private const string OfficialInitialOfen =
        "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/" +
        "PPPPPPPPPP/CRNBQKBNRC[W/W/w/w] w KQkq - 0 1";
    private static readonly JsonSerializerOptions Json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase
    };
    private static readonly string[] SourceFields =
    {
        "schemaVersion", "kind", "rootId", "groupId", "initialOfen",
        "moves", "plyOfenSha256"
    };
    private static readonly string[] ForbiddenExecutionEnvironmentVariables =
    {
        "DOTNET_STARTUP_HOOKS",
        "DOTNET_ADDITIONAL_DEPS",
        "DOTNET_SHARED_STORE",
        "DOTNET_HOST_PATH",
        "DOTNET_ROOT",
        "DOTNET_ROOT_X64",
        "DOTNET_ROOT_X86",
        "DOTNET_ROOT(x86)",
        "DOTNET_MULTILEVEL_LOOKUP",
        "DOTNET_ROLL_FORWARD",
        "DOTNET_ROLL_FORWARD_ON_NO_CANDIDATE_FX",
        "DOTNET_ROLL_FORWARD_TO_PRERELEASE",
        "DOTNET_BUNDLE_EXTRACT_BASE_DIR"
    };

    public static async Task<int> Main(string[] args)
    {
        CultureInfo.CurrentCulture = CultureInfo.InvariantCulture;
        CultureInfo.CurrentUICulture = CultureInfo.InvariantCulture;
        var closure = AssertExecutionClosure();

        if (args.Length == 1 && args[0] == "--self-test")
        {
            await SelfTest(closure);
            Console.WriteLine(
                "OmegaHistoryRootSamplerG6 self-test passed: exact history replay " +
                "hashes, nonterminal roots, pair/group provenance, six explicit " +
                "promotion suffixes, multi-batch worker-count byte parity, " +
                "mutation-sensitive SHA/terminal checks, sealed runtime closure, " +
                "manifest-last publication, and no-clobber behavior.");
            return 0;
        }

        var options = Options.Parse(args);
        await Run(options, closure, verifyClosureAtPublication: true);
        return 0;
    }

    private static async Task Run(
        Options options,
        ExecutionClosure closure,
        bool verifyClosureAtPublication,
        Action<PublicationStage>? publicationObserver = null)
    {
        var output = Path.GetFullPath(options.Output);
        var manifest = output + ".manifest.json";
        AssertNoReparsePath(output);
        AssertNoReparsePath(manifest);
        RefuseExisting(output, "output");
        RefuseExisting(manifest, "manifest");
        RequireDistinctPaths(output, manifest);
        var outputDirectory = Path.GetDirectoryName(output)
            ?? throw new IOException("output has no parent directory");
        AssertNoReparsePath(outputDirectory);
        Directory.CreateDirectory(outputDirectory);
        AssertNoReparsePath(outputDirectory);

        var outputTemporary = AdjacentTemporary(output);
        var manifestTemporary = AdjacentTemporary(manifest);
        var outputPublished = false;
        var manifestPublished = false;
        try
        {
            GenerationCoverage coverage;
            await using (var stream = NewOutput(outputTemporary))
            await using (var writer = new StreamWriter(
                stream, new UTF8Encoding(false), 1 << 20, leaveOpen: true))
            {
                coverage = await Generate(
                    options,
                    record => writer.WriteLineAsync(
                        JsonSerializer.Serialize(record, Json)));
                if (coverage.Records <= 0)
                    throw new InvalidDataException(
                        "history-root generation emitted no records");
                await writer.FlushAsync();
                stream.Flush(flushToDisk: true);
            }

            var closureAtPublication = verifyClosureAtPublication
                ? AssertExecutionClosure()
                : closure;
            RequireSameClosure(closure, closureAtPublication);
            var stagedOutput = IdentityOf(outputTemporary, output);
            var manifestValue = new
            {
                schemaVersion = 1,
                kind = ManifestKind,
                createdUtc = DateTime.UtcNow,
                policy = new
                {
                    variant = "omega",
                    officialInitialOfen = OfficialInitialOfen,
                    sourceKind = SourceKind,
                    rowFieldInventory = SourceFields,
                    sourceRowsAreHistoryOnly = true,
                    phaseRoutingAuthority =
                        "recompute phase from the replayed root OFEN before target-free routing",
                    deterministicPrng = "SplitMix64",
                    seed = options.Seed.ToString(CultureInfo.InvariantCulture),
                    trajectoryPairs = options.TrajectoryPairs,
                    independentTrajectoriesPerPair = 2,
                    trajectoryFlavors = new[] { "ab", "ba" },
                    groupProvenance =
                        "groupId is the trajectory-pair id; rootId also binds flavor, phase, side, ply, and selection rank",
                    workers = options.Workers,
                    deterministicOrdering =
                        "pair index, flavor ab/ba, phase ordinal, side ordinal, selection-rank ordinal; independent of workers",
                    maxPlies = options.MaxPlies,
                    positionsPerPhaseAndSide = options.PositionsPerPhaseSide,
                    captureSelectionPercent = options.CapturePercent,
                    completeCoordinateHistory = true,
                    promotionSuffixes = "qrbncw",
                    plyHash =
                        "lowercase SHA-256 of normalized six-field ChessLib OFEN after every ply",
                    replay = "ChessLib.Game.DoMove(move, checkEndGame: false)",
                    terminalRootsEmitted = 0,
                    maximumHalfmoveClock = 89,
                    minimumPieces = 7,
                    minimumPiecesPerSide = 2,
                    phasePlyWindows = Phase.Windows,
                    rulesOnly = true,
                    externalInputs = 0,
                    publicationCommitPoint = "manifest"
                },
                coverage,
                runtime = new
                {
                    framework =
                        System.Runtime.InteropServices.RuntimeInformation.FrameworkDescription,
                    expectedRuntimeVersion = ExpectedRuntimeVersion,
                    expectedRuntimeBundleSha256 = ExpectedRuntimeBundleSha256,
                    expectedRuntimeManifestSha256 = ExpectedRuntimeManifestSha256,
                    expectedDotnetHostSha256 = ExpectedDotnetHostSha256,
                    expectedChessLibSha256 = ExpectedChessLibSha256,
                    expectedNewtonsoftJsonSha256 = ExpectedNewtonsoftJsonSha256,
                    expectedSystemIoPortsSha256 = ExpectedSystemIoPortsSha256,
                    executionClosure = closureAtPublication
                },
                output = stagedOutput,
                finalStageSeal = true
            };
            await WriteTemporaryJson(manifestTemporary, manifestValue);

            RefuseExisting(output, "output");
            RefuseExisting(manifest, "manifest");
            File.Move(outputTemporary, output, overwrite: false);
            outputPublished = true;
            publicationObserver?.Invoke(PublicationStage.OutputPublished);
            File.Move(manifestTemporary, manifest, overwrite: false);
            manifestPublished = true;
            publicationObserver?.Invoke(PublicationStage.ManifestPublished);

            Console.WriteLine(
                $"Wrote {coverage.Records} history roots from " +
                $"{options.TrajectoryPairs * 2L} legal trajectories.");
            Console.WriteLine(
                "Phases: " + string.Join(
                    ", ", coverage.PhaseCounts.Select(
                        item => $"{item.Key}={item.Value}")));
            Console.WriteLine($"Output: {output}");
            Console.WriteLine($"Completion manifest: {manifest}");
        }
        catch
        {
            if (manifestPublished)
                TryDelete(manifest);
            if (outputPublished)
                TryDelete(output);
            TryDelete(outputTemporary);
            TryDelete(manifestTemporary);
            throw;
        }
    }

    private static async Task<GenerationCoverage> Generate(
        Options options,
        Func<SourceRecord, Task> emit)
    {
        var accumulator = new CoverageAccumulator();
        var batchCapacity = BatchCapacity(options);
        for (var start = 0; start < options.TrajectoryPairs;
             start += batchCapacity)
        {
            var count = Math.Min(
                batchCapacity, options.TrajectoryPairs - start);
            var results = new PairResult?[count];
            await Parallel.ForEachAsync(
                Enumerable.Range(0, count),
                new ParallelOptions
                {
                    MaxDegreeOfParallelism = options.Workers
                },
                async (offset, _) =>
                {
                    results[offset] = await GeneratePair(start + offset, options);
                });

            for (var offset = 0; offset < count; offset++)
            {
                var pair = results[offset]
                    ?? throw new InvalidOperationException(
                        "parallel generation left an empty pair slot");
                accumulator.Add(pair);
                foreach (var root in pair.Records)
                    await emit(root.Source);
            }
        }
        return accumulator.Freeze();
    }

    private static int BatchCapacity(Options options) =>
        Math.Max(1, Math.Min(
            options.TrajectoryPairs, options.Workers * 4));

    private static async Task<PairResult> GeneratePair(
        int pairIndex,
        Options options)
    {
        var records = new List<SampledRoot>();
        var terminalTrajectories = 0;
        var maxPlyReached = 0;
        var promotionSelections = NewPromotionCounts();
        var pairId = $"random-pair-{pairIndex + 1:D6}";

        foreach (var flavor in new[] { "ab", "ba" })
        {
            var trajectorySeed = Mix(
                options.Seed
                ^ ((ulong)(pairIndex + 1) * 0x9E3779B97F4A7C15UL)
                ^ (flavor == "ab"
                    ? 0xA0761D6478BD642FUL
                    : 0xE7037ED1A0B428DBUL));
            var random = new SplitMix64(trajectorySeed);
            using var game = CreateGame(OfficialInitialOfen);
            var moves = new List<string>(options.MaxPlies);
            var hashes = new List<string>(options.MaxPlies);
            var retained =
                new Dictionary<(string Phase, string Side), List<Sample>>();
            var ended = false;

            for (var ply = 0; ply <= options.MaxPlies; ply++)
            {
                var legalMoves = LegalMoves(game);
                if (IsSamplingTerminal(game, legalMoves))
                {
                    ended = true;
                    break;
                }
                Consider(
                    retained, game, pairIndex, flavor, trajectorySeed, ply,
                    options.PositionsPerPhaseSide);
                if (ply == options.MaxPlies)
                    break;

                var chosen = ChooseMove(
                    legalMoves, ref random, options.CapturePercent);
                if (chosen.Coordinate.Length == 5)
                    promotionSelections[chosen.Coordinate[4].ToString()]++;
                var parentOfen = NormalizeOfen(game.GetFenString());
                try
                {
                    await game.DoMove(chosen.Coordinate, checkEndGame: false);
                }
                catch (Exception error)
                {
                    throw new InvalidOperationException(
                        $"random trajectory failed at pair={pairIndex + 1}, " +
                        $"flavor={flavor}, ply={ply}, " +
                        $"move={chosen.Coordinate}, parentOfen={parentOfen}",
                        error);
                }
                var canonicalMove =
                    game.Moves[^1].Coordinate.ToLowerInvariant();
                if (canonicalMove != chosen.Coordinate)
                    throw new InvalidDataException(
                        $"ChessLib canonicalized {chosen.Coordinate} to " +
                        $"{canonicalMove} at pair={pairIndex + 1}, " +
                        $"flavor={flavor}, ply={ply + 1}");
                moves.Add(canonicalMove);
                hashes.Add(HashText(NormalizeOfen(game.GetFenString())));
                maxPlyReached = Math.Max(maxPlyReached, ply + 1);
            }

            if (ended)
                terminalTrajectories++;
            foreach (var sample in retained.Values
                         .SelectMany(value => value)
                         .OrderBy(value => value.Phase, StringComparer.Ordinal)
                         .ThenBy(value => value.Side, StringComparer.Ordinal)
                         .ThenBy(value => value.Rank, StringComparer.Ordinal))
            {
                if (sample.Ply > moves.Count || sample.Ply > hashes.Count)
                    throw new InvalidDataException(
                        "sample history exceeds its trajectory");
                var rootId =
                    $"{pairId}-{flavor}-{sample.Phase}-{sample.Side}-" +
                    $"ply-{sample.Ply:D4}-{sample.Rank}";
                records.Add(new SampledRoot(
                    new SourceRecord(
                        1,
                        SourceKind,
                        rootId,
                        pairId,
                        OfficialInitialOfen,
                        moves.Take(sample.Ply).ToArray(),
                        hashes.Take(sample.Ply).ToArray()),
                    sample.Phase,
                    sample.Side));
            }
        }

        return new PairResult(
            records, terminalTrajectories, maxPlyReached,
            promotionSelections);
    }

    private static void Consider(
        Dictionary<(string Phase, string Side), List<Sample>> retained,
        Game game,
        int pairIndex,
        string flavor,
        ulong trajectorySeed,
        int ply,
        int limit)
    {
        var pieces = game.Board.Squares
            .Where(square => square.Piece is not null)
            .Select(square => square.Piece!)
            .ToList();
        var phase = Phase.Of(pieces.Count);
        if (phase is null || !Phase.InWindow(phase, ply))
            return;
        var white = pieces.Count(
            piece => piece.Color == Game.Colors.White);
        var black = pieces.Count - white;
        if (pieces.Count < 7 || white < 2 || black < 2 ||
            game.HalfMoveClock >= 90)
            return;

        var side = game.ToMove == Game.Colors.White ? "w" : "b";
        var ofen = NormalizeOfen(game.GetFenString());
        var rankPayload =
            $"omega-root-sampler-v1\0{trajectorySeed}\0{pairIndex}\0" +
            $"{flavor}\0{ply}\0{ofen}";
        var rank = HashText(rankPayload);
        var sample = new Sample(rank, ply, phase, side);
        var key = (phase, side);
        if (!retained.TryGetValue(key, out var bucket))
        {
            bucket = new List<Sample>();
            retained[key] = bucket;
        }
        bucket.Add(sample);
        bucket.Sort((left, right) =>
            string.CompareOrdinal(left.Rank, right.Rank));
        if (bucket.Count > limit)
            bucket.RemoveAt(bucket.Count - 1);
    }

    private static Game CreateGame(string initialOfen)
    {
        var settings = new Game.GameSettings
        {
            Variant = ChessVariant.Omega,
            InitialFenPosition = initialOfen,
            DrawForRepetition = 3
        };
        settings.Players.Add(new HumanPlayer(
            Game.Colors.White, "Rules-only White", null));
        settings.Players.Add(new HumanPlayer(
            Game.Colors.Black, "Rules-only Black", null));
        var game = new Game();
        game.Init(settings);
        return game;
    }

    private static List<LegalMove> LegalMoves(Game game)
    {
        var result = new List<LegalMove>();
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
                if (target.Piece?.Type == Piece.Pieces.King)
                    continue;
                var capture =
                    target.Piece is not null && target.Piece.Color != piece.Color;
                var coordinate =
                    square.Notation.ToLowerInvariant() +
                    target.Notation.ToLowerInvariant();
                var promotion = piece.Type == Piece.Pieces.Pawn &&
                    ((piece.Color == Game.Colors.White && target.Rank == 9) ||
                     (piece.Color == Game.Colors.Black && target.Rank == 0));
                if (promotion)
                {
                    foreach (var suffix in "qrbncw")
                        result.Add(new LegalMove(
                            coordinate + suffix, capture));
                }
                else
                {
                    result.Add(new LegalMove(coordinate, capture));
                }
            }
        }
        return result;
    }

    private static bool IsSamplingTerminal(
        Game game,
        IReadOnlyCollection<LegalMove>? legalMoves = null) =>
        (legalMoves ?? LegalMoves(game)).Count == 0 || game.IsDraw();

    private static LegalMove ChooseMove(
        IReadOnlyList<LegalMove> moves,
        ref SplitMix64 random,
        int capturePercent)
    {
        var captures = moves.Where(move => move.Capture).ToList();
        IReadOnlyList<LegalMove> pool =
            captures.Count > 0 &&
            random.Next(100) < (ulong)capturePercent
                ? captures
                : moves;
        return pool[(int)random.Next((ulong)pool.Count)];
    }

    private static async Task SelfTest(ExecutionClosure closure)
    {
        VerifyIndependentShaVectors();
        RequireThrows<ArgumentException>(
            () => Options.Parse(new[]
            {
                "--output", "unused", "--max-plies", "5"
            }),
            "--max-plies below the first sampling window was accepted");
        const string injectedHook = "DOTNET_STARTUP_HOOKS";
        var previousHook = Environment.GetEnvironmentVariable(injectedHook);
        try
        {
            Environment.SetEnvironmentVariable(
                injectedHook, "forbidden-self-test-hook.dll");
            RequireThrows<InvalidDataException>(
                AssertNoForbiddenExecutionEnvironment,
                "forbidden DOTNET_STARTUP_HOOKS was accepted");
        }
        finally
        {
            Environment.SetEnvironmentVariable(injectedHook, previousHook);
        }
        var oneWorker = new Options(
            "unused", 2026072306UL, 13, 72, 2, 72, 1);
        var threeWorkers = oneWorker with { Workers = 3 };
        Require(BatchCapacity(oneWorker) == 4 &&
                BatchCapacity(threeWorkers) == 12 &&
                oneWorker.TrajectoryPairs > BatchCapacity(threeWorkers),
            "worker parity fixture does not exercise distinct multi-batch layouts");
        var first = await GenerateInMemory(oneWorker);
        var second = await GenerateInMemory(threeWorkers);
        var firstBytes = SerializeRows(first.Records);
        var secondBytes = SerializeRows(second.Records);
        Require(firstBytes.SequenceEqual(secondBytes),
            "worker count changed history-root bytes");
        Require(
            JsonSerializer.Serialize(first.Coverage, Json) ==
            JsonSerializer.Serialize(second.Coverage, Json),
            "worker count changed coverage");
        Require(first.Records.Count > 0,
            "tiny deterministic sample emitted no roots");
        Require(
            first.Records.Select(record => record.RootId)
                .Distinct(StringComparer.Ordinal).Count() ==
            first.Records.Count,
            "tiny sample emitted duplicate root ids");

        foreach (var record in first.Records)
            await VerifyRecord(record);
        var mutationBase = first.Records.First(record => record.Moves.Count > 0);
        var mutatedHashes = mutationBase.PlyOfenSha256.ToArray();
        mutatedHashes[^1] =
            (mutatedHashes[^1][0] == '0' ? "1" : "0") +
            mutatedHashes[^1][1..];
        await RequireThrowsAsync<InvalidDataException>(
            () => VerifyRecord(mutationBase with
            {
                RootId = mutationBase.RootId + "-mutated",
                PlyOfenSha256 = mutatedHashes
            }),
            "a mutated per-ply OFEN hash passed replay verification");
        var groups = first.Records.GroupBy(
            record => record.GroupId, StringComparer.Ordinal).ToArray();
        Require(groups.Length == oneWorker.TrajectoryPairs,
            "trajectory-pair group count changed");
        foreach (var group in groups)
        {
            Require(
                group.Any(record => record.RootId.Contains(
                    "-ab-", StringComparison.Ordinal)) &&
                group.Any(record => record.RootId.Contains(
                    "-ba-", StringComparison.Ordinal)),
                $"group {group.Key} lost an independent trajectory flavor");
        }

        await VerifyKnownTerminalBoundaries();
        await VerifyPromotionSuffixes();
        await PublicationSelfTest(closure);
        RequireSameClosure(closure, AssertExecutionClosure());
    }

    private static void VerifyIndependentShaVectors()
    {
        Require(SourceKind == "omega-g6-history-root",
            "source kind changed from the downstream schema");
        Require(
            HashText("abc") ==
                "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            "SHA-256 implementation failed the independent abc vector");
        Require(
            HashText(OfficialInitialOfen) ==
                "ed25d75f28de6c9603e1b7771f75d7dd6d9973746fe0c45d66904d64d1b4c207",
            "canonical Omega initial OFEN hash changed");
    }

    private static async Task VerifyKnownTerminalBoundaries()
    {
        using (var ordinary = CreateGame(OfficialInitialOfen))
            Require(!IsSamplingTerminal(ordinary),
                "canonical Omega initial position became terminal");

        const string mate =
            "10/10/10/10/10/10/10/10/KC8/C9[k/-/-/-] b - - 0 1";
        using (var game = CreateGame(mate))
        {
            Require(await game.IsCheckmate(game.ToMove),
                "checkmate oracle fixture changed");
            Require(IsSamplingTerminal(game),
                "sampler accepted a known checkmate");
        }

        const string stalemate =
            "10/10/10/10/10/10/10/C9/K9/C9[k/-/-/-] b - - 0 1";
        using (var game = CreateGame(stalemate))
        {
            Require(await game.IsStalemate(game.ToMove),
                "stalemate oracle fixture changed");
            Require(IsSamplingTerminal(game),
                "sampler accepted a known stalemate");
        }

        const string insufficient =
            "4k5/10/10/10/10/10/10/10/10/4KC4[-/-/-/-] w - - 0 1";
        using (var game = CreateGame(insufficient))
        {
            Require(game.IsDraw(),
                "K+C/K insufficient-material oracle fixture changed");
            Require(IsSamplingTerminal(game),
                "sampler accepted known insufficient material");
        }

        const string halfmove =
            "4k5/10/10/10/10/10/10/10/10/4KR4[-/-/-/-] w - - 100 1";
        using (var game = CreateGame(halfmove))
        {
            Require(game.HalfMoveClock == 100 && game.IsDraw(),
                "halfmove-draw oracle fixture changed");
            Require(IsSamplingTerminal(game),
                "sampler accepted a known halfmove draw");
        }

        using (var game = CreateGame(OfficialInitialOfen))
        {
            foreach (var move in new[]
                     {
                         "c0d2", "c9d7", "d2c0", "d7c9",
                         "c0d2", "c9d7", "d2c0", "d7c9"
                     })
                await game.DoMove(move, checkEndGame: false);
            Require(game.IsDraw(),
                "threefold-repetition oracle fixture changed");
            Require(IsSamplingTerminal(game),
                "sampler accepted a known repetition draw");
        }
    }

    private static async Task<InMemoryGeneration> GenerateInMemory(
        Options options)
    {
        var records = new List<SourceRecord>();
        var coverage = await Generate(options, record =>
        {
            records.Add(record);
            return Task.CompletedTask;
        });
        return new InMemoryGeneration(records, coverage);
    }

    private static byte[] SerializeRows(
        IReadOnlyList<SourceRecord> records)
    {
        var builder = new StringBuilder();
        foreach (var record in records)
            builder.Append(JsonSerializer.Serialize(record, Json)).Append('\n');
        return new UTF8Encoding(false).GetBytes(builder.ToString());
    }

    private static async Task VerifyRecord(SourceRecord record)
    {
        Require(record.SchemaVersion == 1 && record.Kind == SourceKind,
            $"{record.RootId}: source envelope changed");
        Require(record.InitialOfen == OfficialInitialOfen,
            $"{record.RootId}: initial OFEN changed");
        Require(record.RootId.StartsWith(
                record.GroupId + "-", StringComparison.Ordinal),
            $"{record.RootId}: pair/group provenance changed");
        Require(record.Moves.Count == record.PlyOfenSha256.Count,
            $"{record.RootId}: history/hash lengths differ");

        using (var document = JsonDocument.Parse(
                   JsonSerializer.Serialize(record, Json)))
        {
            var names = document.RootElement.EnumerateObject()
                .Select(property => property.Name).ToArray();
            Require(names.SequenceEqual(SourceFields),
                $"{record.RootId}: exact source field inventory changed");
        }

        using var game = CreateGame(OfficialInitialOfen);
        for (var index = 0; index < record.Moves.Count; index++)
        {
            Require(LegalMoves(game).Count > 0 && !game.IsDraw(),
                $"{record.RootId}: history continued after terminal ply {index}");
            var move = record.Moves[index];
            Require(IsCanonicalCoordinate(move),
                $"{record.RootId}: noncanonical move {move}");
            await game.DoMove(move, checkEndGame: false);
            Require(
                game.Moves[^1].Coordinate.ToLowerInvariant() == move,
                $"{record.RootId}: ChessLib canonical move mismatch at " +
                $"ply {index + 1}");
            var hash = HashText(NormalizeOfen(game.GetFenString()));
            Require(hash == record.PlyOfenSha256[index],
                $"{record.RootId}: OFEN hash mismatch at ply {index + 1}");
        }
        Require(LegalMoves(game).Count > 0 && !game.IsDraw(),
            $"{record.RootId}: terminal root was emitted");
    }

    private static async Task VerifyPromotionSuffixes()
    {
        const string whitePromotionInitial =
            "5k4/P9/10/10/10/10/10/10/10/5K4[-/-/-/-] w - - 0 1";
        const string blackPromotionInitial =
            "5k4/10/10/10/10/10/10/10/p9/5K4[-/-/-/-] b - - 0 1";
        foreach (var fixture in new[]
                 {
                     (Ofen: whitePromotionInitial, Prefix: "a8a9"),
                     (Ofen: blackPromotionInitial, Prefix: "a1a0")
                 })
        {
            using (var game = CreateGame(fixture.Ofen))
            {
                var promotions = LegalMoves(game)
                    .Select(move => move.Coordinate)
                    .Where(move => move.StartsWith(
                        fixture.Prefix, StringComparison.Ordinal))
                    .OrderBy(move => move, StringComparer.Ordinal)
                    .ToArray();
                var expected = "qrbncw".Select(
                        suffix => $"{fixture.Prefix}{suffix}")
                    .OrderBy(move => move, StringComparer.Ordinal)
                    .ToArray();
                Require(promotions.SequenceEqual(expected),
                    $"{fixture.Prefix}: legal move generator did not emit " +
                    "exactly six promotion suffixes");
            }
            foreach (var suffix in "qrbncw")
            {
                var move = $"{fixture.Prefix}{suffix}";
                using var game = CreateGame(fixture.Ofen);
                await game.DoMove(move, checkEndGame: false);
                Require(game.Moves[^1].Coordinate.ToLowerInvariant() == move,
                    $"ChessLib rejected canonical promotion {move}");
            }
        }
    }

    private static async Task PublicationSelfTest(ExecutionClosure closure)
    {
        var directory = Path.Combine(
            Path.GetTempPath(),
            $"omega-g6-history-root-sampler-{Guid.NewGuid():N}");
        Directory.CreateDirectory(directory);
        try
        {
            var output = Path.Combine(directory, "histories.jsonl");
            var manifest = output + ".manifest.json";
            var options = new Options(
                output, 2026072306UL, 2, 64, 1, 72, 2);
            var stages = new List<PublicationStage>();
            await Run(
                options, closure, verifyClosureAtPublication: true,
                stage =>
                {
                    stages.Add(stage);
                    if (stage == PublicationStage.OutputPublished)
                    {
                        Require(File.Exists(output) && !File.Exists(manifest),
                            "manifest was visible before the output commit");
                    }
                    else
                    {
                        Require(File.Exists(output) && File.Exists(manifest),
                            "manifest commit did not follow its output");
                    }
                });
            Require(stages.SequenceEqual(new[]
                {
                    PublicationStage.OutputPublished,
                    PublicationStage.ManifestPublished
                }),
                "publication stages were not output-first and manifest-last");
            var outputIdentity = IdentityOf(output);
            var manifestIdentity = IdentityOf(manifest);
            using (var document = JsonDocument.Parse(
                       await File.ReadAllTextAsync(manifest, Encoding.UTF8)))
            {
                var root = document.RootElement;
                Require(
                    root.GetProperty("kind").GetString() == ManifestKind &&
                    root.GetProperty("finalStageSeal").GetBoolean() &&
                    root.GetProperty("policy")
                        .GetProperty("publicationCommitPoint")
                        .GetString() == "manifest",
                    "manifest is not the final completion commit point");
                Require(
                    root.GetProperty("output").GetProperty("sha256")
                        .GetString() == outputIdentity.Sha256 &&
                    root.GetProperty("runtime")
                        .GetProperty("expectedChessLibSha256")
                        .GetString() == ExpectedChessLibSha256 &&
                    root.GetProperty("runtime")
                        .GetProperty("expectedRuntimeBundleSha256")
                        .GetString() == ExpectedRuntimeBundleSha256 &&
                    root.GetProperty("runtime")
                        .GetProperty("executionClosure")
                        .GetProperty("chessLibAssembly")
                        .GetProperty("sha256")
                        .GetString() == ExpectedChessLibSha256 &&
                    root.GetProperty("runtime")
                        .GetProperty("executionClosure")
                        .GetProperty("dotnetHost")
                        .GetProperty("sha256")
                        .GetString() == ExpectedDotnetHostSha256,
                    "manifest did not pin output and execution closure identities");
            }

            var published = await ReadRows(output);
            Require(published.Count > 0,
                "publication self-test emitted no rows");
            foreach (var record in published)
                await VerifyRecord(record);

            try
            {
                await Run(options, closure, verifyClosureAtPublication: false);
                throw new InvalidDataException(
                    "no-clobber rerun unexpectedly succeeded");
            }
            catch (IOException)
            {
            }
            Require(
                IdentityOf(output) == outputIdentity &&
                IdentityOf(manifest) == manifestIdentity,
                "no-clobber rerun changed a published artifact");

            var interruptedOutput = Path.Combine(
                directory, "interrupted.jsonl");
            var interruptedManifest = interruptedOutput + ".manifest.json";
            await RequireThrowsAsync<InjectedPublicationException>(
                () => Run(
                    options with { Output = interruptedOutput }, closure,
                    verifyClosureAtPublication: false,
                    stage =>
                    {
                        if (stage == PublicationStage.OutputPublished)
                            throw new InjectedPublicationException();
                    }),
                "injected post-output publication failure was not observed");
            Require(!File.Exists(interruptedOutput) &&
                    !File.Exists(interruptedManifest),
                "post-output failure left a partly committed publication");

            var blockedOutput = Path.Combine(directory, "blocked.jsonl");
            var blockedManifest = blockedOutput + ".manifest.json";
            await File.WriteAllTextAsync(
                blockedManifest, "manifest-sentinel\n", new UTF8Encoding(false));
            var blockedManifestIdentity = IdentityOf(blockedManifest);
            await RequireThrowsAsync<IOException>(
                () => Run(
                    options with { Output = blockedOutput }, closure,
                    verifyClosureAtPublication: false),
                "pre-existing manifest did not block publication");
            Require(!File.Exists(blockedOutput) &&
                    IdentityOf(blockedManifest) == blockedManifestIdentity,
                "manifest-only no-clobber changed an existing artifact");

            var blockedOutputOnly = Path.Combine(
                directory, "blocked-output.jsonl");
            await File.WriteAllTextAsync(
                blockedOutputOnly, "output-sentinel\n", new UTF8Encoding(false));
            var blockedOutputIdentity = IdentityOf(blockedOutputOnly);
            await RequireThrowsAsync<IOException>(
                () => Run(
                    options with { Output = blockedOutputOnly }, closure,
                    verifyClosureAtPublication: false),
                "pre-existing output did not block publication");
            Require(IdentityOf(blockedOutputOnly) == blockedOutputIdentity &&
                    !File.Exists(blockedOutputOnly + ".manifest.json"),
                "output-only no-clobber changed an existing artifact");

            var emptyOutput = Path.Combine(directory, "empty.jsonl");
            await RequireThrowsAsync<InvalidDataException>(
                () => Run(
                    options with { Output = emptyOutput, MaxPlies = 5 }, closure,
                    verifyClosureAtPublication: false),
                "zero-record generation was published");
            Require(!File.Exists(emptyOutput) &&
                    !File.Exists(emptyOutput + ".manifest.json"),
                "zero-record rejection left a published artifact");
        }
        finally
        {
            if (Directory.Exists(directory))
                Directory.Delete(directory, recursive: true);
        }
    }

    private static async Task<IReadOnlyList<SourceRecord>> ReadRows(
        string path)
    {
        var records = new List<SourceRecord>();
        await using var stream = new FileStream(
            path, FileMode.Open, FileAccess.Read, FileShare.Read,
            1 << 16, FileOptions.SequentialScan);
        using var reader = new StreamReader(
            stream, Encoding.UTF8, detectEncodingFromByteOrderMarks: true,
            1 << 16, leaveOpen: true);
        while (await reader.ReadLineAsync() is { } line)
        {
            if (string.IsNullOrWhiteSpace(line))
                throw new InvalidDataException("published output has a blank row");
            records.Add(
                JsonSerializer.Deserialize<SourceRecord>(line, Json)
                ?? throw new InvalidDataException(
                    "published history row did not deserialize"));
        }
        return records;
    }

    private static bool IsCanonicalCoordinate(string move)
    {
        if (move.Length is not (4 or 5) ||
            move != move.ToLowerInvariant())
            return false;
        if (!IsSquare(move.AsSpan(0, 2)) ||
            !IsSquare(move.AsSpan(2, 2)))
            return false;
        return move.Length == 4 || "qrbncw".Contains(move[4]);
    }

    private static bool IsSquare(ReadOnlySpan<char> square) =>
        square.Length == 2 &&
        ((square[0] is >= 'a' and <= 'j' &&
          square[1] is >= '0' and <= '9') ||
         (square[0] == 'w' && square[1] is >= '1' and <= '4'));

    private static string NormalizeOfen(string value)
    {
        var fields = value.Split(
            (char[]?)null, StringSplitOptions.RemoveEmptyEntries);
        if (fields.Length != 6)
            throw new InvalidDataException(
                "Omega OFEN must contain six fields");
        if (fields[1] is not ("w" or "b"))
            throw new InvalidDataException(
                "Omega OFEN side must be w or b");
        if (!fields[0].Contains('[') || !fields[0].EndsWith(']'))
            throw new InvalidDataException(
                "Omega OFEN must contain four corners");
        return string.Join(' ', fields);
    }

    private static ulong Mix(ulong value)
    {
        value += 0x9E3779B97F4A7C15UL;
        value =
            (value ^ (value >> 30)) * 0xBF58476D1CE4E5B9UL;
        value =
            (value ^ (value >> 27)) * 0x94D049BB133111EBUL;
        return value ^ (value >> 31);
    }

    private static Dictionary<string, long> NewPromotionCounts() =>
        "qrbncw".ToDictionary(
            value => value.ToString(), _ => 0L, StringComparer.Ordinal);

    private static ExecutionClosure AssertExecutionClosure()
    {
        AssertNoForbiddenExecutionEnvironment();
        var processPath = Environment.ProcessPath
            ?? throw new InvalidDataException("runtime process path is unavailable");
        var dotnetHost = RequireIdentity(
            processPath, ExpectedDotnetHostSha256, "frozen dotnet host");
        if (!Path.GetFileName(processPath).Equals(
                "dotnet.exe", StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException(
                $"sampler must run under the frozen dotnet.exe host: {processPath}");
        var runtimeRoot = Path.GetDirectoryName(Path.GetFullPath(processPath))
            ?? throw new InvalidDataException("dotnet host has no runtime root");
        var runtimeManifestPath = Path.Combine(
            Path.GetDirectoryName(runtimeRoot)
                ?? throw new InvalidDataException("runtime root has no parent"),
            "dotnet-runtime.manifest.json");
        var runtimeManifest = RequireIdentity(
            runtimeManifestPath, ExpectedRuntimeManifestSha256,
            "frozen dotnet runtime manifest");
        var runtimeFiles = VerifyRuntimeBundle(
            runtimeRoot, runtimeManifestPath);

        var framework =
            System.Runtime.InteropServices.RuntimeInformation.FrameworkDescription;
        if (framework != $".NET {ExpectedRuntimeVersion}" ||
            Environment.Version.ToString() != ExpectedRuntimeVersion)
            throw new InvalidDataException(
                $"runtime version mismatch: framework={framework}, " +
                $"environment={Environment.Version}");
        var coreLibraryPath = typeof(object).Assembly.Location;
        var expectedCoreDirectory = Path.GetFullPath(Path.Combine(
            runtimeRoot, "shared", "Microsoft.NETCore.App",
            ExpectedRuntimeVersion));
        if (!IsWithin(coreLibraryPath, expectedCoreDirectory))
            throw new InvalidDataException(
                $"core library was not loaded from the frozen runtime: {coreLibraryPath}");
        var coreLibrary = IdentityOf(coreLibraryPath);

        var samplerAssembly = Assembly.GetExecutingAssembly();
        var samplerPath = samplerAssembly.Location;
        var applicationDirectory = Path.GetDirectoryName(
            Path.GetFullPath(samplerPath))
            ?? throw new InvalidDataException("sampler assembly has no directory");
        var sampler = IdentityOf(samplerPath);
        var chessLib = RequireLoadedAssembly(
            typeof(Game).Assembly, applicationDirectory, "ChessLib.dll",
            ExpectedChessLibSha256);
        var newtonsoft = RequireLoadedAssembly(
            Assembly.Load(new AssemblyName("Newtonsoft.Json")),
            applicationDirectory, "Newtonsoft.Json.dll",
            ExpectedNewtonsoftJsonSha256);
        var ports = RequireLoadedAssembly(
            Assembly.Load(new AssemblyName("System.IO.Ports")),
            applicationDirectory, "System.IO.Ports.dll",
            ExpectedSystemIoPortsSha256);

        return new ExecutionClosure(
            ExpectedRuntimeVersion,
            ExpectedRuntimeBundleSha256,
            runtimeFiles,
            ForbiddenExecutionEnvironmentVariables,
            0,
            dotnetHost,
            runtimeManifest,
            coreLibrary,
            sampler,
            chessLib,
            newtonsoft,
            ports);
    }

    private static void AssertNoForbiddenExecutionEnvironment()
    {
        var present = ForbiddenExecutionEnvironmentVariables
            .Where(name => !string.IsNullOrEmpty(
                Environment.GetEnvironmentVariable(name)))
            .ToArray();
        if (present.Length != 0)
            throw new InvalidDataException(
                "forbidden .NET execution environment variables are set: " +
                string.Join(", ", present));
    }

    private static int VerifyRuntimeBundle(
        string runtimeRoot,
        string manifestPath)
    {
        using var document = JsonDocument.Parse(
            File.ReadAllText(manifestPath, Encoding.UTF8));
        var root = document.RootElement;
        if (root.GetProperty("schemaVersion").GetInt32() != 1 ||
            root.GetProperty("kind").GetString() != ExpectedRuntimeManifestKind ||
            root.GetProperty("bundleSha256").GetString() !=
                ExpectedRuntimeBundleSha256 ||
            root.GetProperty("dotnetHostRelativePath").GetString() !=
                "dotnet.exe" ||
            root.GetProperty("runtimeVersion").GetString() !=
                ExpectedRuntimeVersion)
            throw new InvalidDataException("frozen runtime manifest header changed");

        var expected = new Dictionary<string, ArtifactIdentity>(
            StringComparer.Ordinal);
        foreach (var entry in root.GetProperty("files").EnumerateArray())
        {
            var relative = entry.GetProperty("relativePath").GetString()
                ?? throw new InvalidDataException(
                    "runtime manifest has a null relative path");
            if (!IsCanonicalRelativePath(relative) ||
                !expected.TryAdd(relative, new ArtifactIdentity(
                    relative,
                    entry.GetProperty("bytes").GetInt64(),
                    entry.GetProperty("sha256").GetString()
                        ?? throw new InvalidDataException(
                            "runtime manifest has a null SHA-256"))))
                throw new InvalidDataException(
                    $"runtime manifest has an invalid/duplicate path: {relative}");
        }
        if (expected.Count != 191)
            throw new InvalidDataException(
                $"runtime manifest file count changed: {expected.Count}");

        var actualPaths = EnumerateRegularFilesNoLinks(runtimeRoot)
            .ToDictionary(
                path => Path.GetRelativePath(runtimeRoot, path)
                    .Replace('\\', '/'),
                path => path,
                StringComparer.Ordinal);
        if (!actualPaths.Keys.OrderBy(value => value, StringComparer.Ordinal)
                .SequenceEqual(expected.Keys.OrderBy(
                    value => value, StringComparer.Ordinal)))
            throw new InvalidDataException(
                "frozen runtime file inventory differs from its manifest");
        foreach (var item in expected)
        {
            var actual = IdentityOf(actualPaths[item.Key]);
            if (actual.Bytes != item.Value.Bytes ||
                actual.Sha256 != item.Value.Sha256)
                throw new InvalidDataException(
                    $"frozen runtime file identity mismatch: {item.Key}");
        }
        return expected.Count;
    }

    private static IReadOnlyList<string> EnumerateRegularFilesNoLinks(
        string root)
    {
        var result = new List<string>();
        var pending = new Stack<string>();
        pending.Push(Path.GetFullPath(root));
        while (pending.Count > 0)
        {
            var directory = pending.Pop();
            AssertNoReparsePath(directory);
            foreach (var entry in Directory.EnumerateFileSystemEntries(directory))
            {
                var attributes = File.GetAttributes(entry);
                if ((attributes & FileAttributes.ReparsePoint) != 0)
                    throw new InvalidDataException(
                        $"runtime bundle contains a link/reparse point: {entry}");
                if ((attributes & FileAttributes.Directory) != 0)
                    pending.Push(entry);
                else
                    result.Add(Path.GetFullPath(entry));
            }
        }
        return result;
    }

    private static bool IsCanonicalRelativePath(string value)
    {
        if (string.IsNullOrWhiteSpace(value) ||
            Path.IsPathRooted(value) || value.Contains('\\'))
            return false;
        var normalized = Path.GetFullPath(Path.Combine("C:\\", value))
            .Replace('\\', '/');
        return normalized == $"C:/{value}" &&
            value.Split('/').All(part => part is not ("" or "." or ".."));
    }

    private static ArtifactIdentity RequireLoadedAssembly(
        Assembly assembly,
        string applicationDirectory,
        string fileName,
        string expectedSha256)
    {
        var expectedPath = Path.GetFullPath(Path.Combine(
            applicationDirectory, fileName));
        var actualPath = Path.GetFullPath(assembly.Location);
        if (!StringComparer.OrdinalIgnoreCase.Equals(
                expectedPath, actualPath))
            throw new InvalidDataException(
                $"{fileName} loaded from an unexpected path: {actualPath}");
        return RequireIdentity(actualPath, expectedSha256, fileName);
    }

    private static ArtifactIdentity RequireIdentity(
        string path,
        string expectedSha256,
        string label)
    {
        var identity = IdentityOf(path);
        if (identity.Sha256 != expectedSha256)
            throw new InvalidDataException(
                $"{label} identity mismatch: expected {expectedSha256}, " +
                $"actual {identity.Sha256}");
        return identity;
    }

    private static bool IsWithin(string path, string directory)
    {
        var fullPath = Path.GetFullPath(path);
        var fullDirectory = Path.GetFullPath(directory)
            .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        return fullPath.StartsWith(
            fullDirectory + Path.DirectorySeparatorChar,
            StringComparison.OrdinalIgnoreCase);
    }

    private static void RequireSameClosure(
        ExecutionClosure expected,
        ExecutionClosure actual)
    {
        if (JsonSerializer.Serialize(expected, Json) !=
            JsonSerializer.Serialize(actual, Json))
            throw new InvalidDataException(
                "execution closure changed during generation");
    }

    private static string HashText(string value) =>
        Convert.ToHexString(
                SHA256.HashData(Encoding.UTF8.GetBytes(value)))
            .ToLowerInvariant();

    private static ArtifactIdentity IdentityOf(
        string path,
        string? reportedPath = null)
    {
        var fullPath = Path.GetFullPath(path);
        AssertNoReparsePath(fullPath);
        var before = new FileInfo(fullPath);
        before.Refresh();
        if (!before.Exists)
            throw new FileNotFoundException("identity artifact is missing", fullPath);
        using var stream = new FileStream(
            fullPath, FileMode.Open, FileAccess.Read, FileShare.Read);
        if (!GetFileInformationByHandle(
                stream.SafeFileHandle, out var linkInformation))
            throw new IOException(
                $"could not inspect artifact links: {fullPath}",
                new System.ComponentModel.Win32Exception(
                    System.Runtime.InteropServices.Marshal.GetLastWin32Error()));
        if (linkInformation.NumberOfLinks != 1)
            throw new InvalidDataException(
                $"hard-linked authority artifact is forbidden: {fullPath}");
        var identity = new ArtifactIdentity(
            Path.GetFullPath(reportedPath ?? fullPath),
            stream.Length,
            Convert.ToHexString(SHA256.HashData(stream))
                .ToLowerInvariant());
        var after = new FileInfo(fullPath);
        after.Refresh();
        if (!after.Exists || before.Length != after.Length ||
            before.LastWriteTimeUtc != after.LastWriteTimeUtc ||
            after.Length != identity.Bytes)
            throw new InvalidDataException(
                $"artifact changed while its identity was read: {fullPath}");
        return identity;
    }

    private static void AssertNoReparsePath(string path)
    {
        FileSystemInfo? current = File.Exists(path)
            ? new FileInfo(Path.GetFullPath(path))
            : new DirectoryInfo(Path.GetFullPath(path));
        while (current is not null)
        {
            if (current.Exists &&
                (current.Attributes & FileAttributes.ReparsePoint) != 0)
                throw new InvalidDataException(
                    $"link/junction/reparse path is forbidden: {current.FullName}");
            current = current switch
            {
                FileInfo file => file.Directory,
                DirectoryInfo directory => directory.Parent,
                _ => null
            };
        }
    }

    [System.Runtime.InteropServices.StructLayout(
        System.Runtime.InteropServices.LayoutKind.Sequential)]
    private struct ByHandleFileInformation
    {
        public uint FileAttributes;
        public System.Runtime.InteropServices.ComTypes.FILETIME CreationTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastAccessTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWriteTime;
        public uint VolumeSerialNumber;
        public uint FileSizeHigh;
        public uint FileSizeLow;
        public uint NumberOfLinks;
        public uint FileIndexHigh;
        public uint FileIndexLow;
    }

    [System.Runtime.InteropServices.DllImport(
        "kernel32.dll", SetLastError = true)]
    [return: System.Runtime.InteropServices.MarshalAs(
        System.Runtime.InteropServices.UnmanagedType.Bool)]
    private static extern bool GetFileInformationByHandle(
        Microsoft.Win32.SafeHandles.SafeFileHandle file,
        out ByHandleFileInformation information);

    private static FileStream NewOutput(string path) =>
        new(
            path, FileMode.CreateNew, FileAccess.Write, FileShare.None,
            1 << 20,
            FileOptions.SequentialScan | FileOptions.WriteThrough);

    private static string AdjacentTemporary(string path) =>
        Path.Combine(
            Path.GetDirectoryName(path)
            ?? throw new IOException("artifact has no parent directory"),
            $".{Path.GetFileName(path)}.{Guid.NewGuid():N}.tmp");

    private static void RefuseExisting(string path, string label)
    {
        if (File.Exists(path) || Directory.Exists(path))
            throw new IOException(
                $"refusing to replace existing {label}: {path}");
    }

    private static void RequireDistinctPaths(
        string output,
        string manifest)
    {
        if (StringComparer.OrdinalIgnoreCase.Equals(output, manifest))
            throw new IOException(
                "output and manifest paths must differ");
    }

    private static async Task WriteTemporaryJson(
        string path,
        object value)
    {
        var payload = JsonSerializer.Serialize(
            value,
            new JsonSerializerOptions(Json)
            {
                WriteIndented = true
            }) + "\n";
        await using var stream = new FileStream(
            path, FileMode.CreateNew, FileAccess.Write, FileShare.None,
            1 << 16, FileOptions.WriteThrough);
        await using var writer = new StreamWriter(
            stream, new UTF8Encoding(false), 1 << 16, leaveOpen: true);
        await writer.WriteAsync(payload);
        await writer.FlushAsync();
        stream.Flush(flushToDisk: true);
    }

    private static void TryDelete(string path)
    {
        try
        {
            if (File.Exists(path))
                File.Delete(path);
        }
        catch
        {
            // Preserve the original publication failure.
        }
    }

    private static void Require(bool condition, string message)
    {
        if (!condition)
            throw new InvalidDataException(message);
    }

    private static void RequireThrows<TException>(
        Action action,
        string message)
        where TException : Exception
    {
        try
        {
            action();
        }
        catch (TException)
        {
            return;
        }
        throw new InvalidDataException(message);
    }

    private static async Task RequireThrowsAsync<TException>(
        Func<Task> action,
        string message)
        where TException : Exception
    {
        try
        {
            await action();
        }
        catch (TException)
        {
            return;
        }
        throw new InvalidDataException(message);
    }

    private enum PublicationStage
    {
        OutputPublished,
        ManifestPublished
    }

    private sealed class InjectedPublicationException : Exception
    {
    }

    private readonly record struct LegalMove(
        string Coordinate,
        bool Capture);

    private readonly record struct Sample(
        string Rank,
        int Ply,
        string Phase,
        string Side);

    private sealed record SourceRecord(
        int SchemaVersion,
        string Kind,
        string RootId,
        string GroupId,
        string InitialOfen,
        IReadOnlyList<string> Moves,
        IReadOnlyList<string> PlyOfenSha256);

    private sealed record SampledRoot(
        SourceRecord Source,
        string Phase,
        string Side);

    private sealed record PairResult(
        IReadOnlyList<SampledRoot> Records,
        int TerminalTrajectories,
        int MaxPlyReached,
        IReadOnlyDictionary<string, long> PromotionSelections);

    private sealed record InMemoryGeneration(
        IReadOnlyList<SourceRecord> Records,
        GenerationCoverage Coverage);

    private sealed record ArtifactIdentity(
        string Path,
        long Bytes,
        string Sha256);

    private sealed record ExecutionClosure(
        string RuntimeVersion,
        string RuntimeBundleSha256,
        int RuntimeFilesVerified,
        IReadOnlyList<string> ForbiddenEnvironmentVariablesChecked,
        int ForbiddenEnvironmentVariablesPresent,
        ArtifactIdentity DotnetHost,
        ArtifactIdentity RuntimeManifest,
        ArtifactIdentity CoreLibraryAssembly,
        ArtifactIdentity SamplerAssembly,
        ArtifactIdentity ChessLibAssembly,
        ArtifactIdentity NewtonsoftJsonAssembly,
        ArtifactIdentity SystemIoPortsAssembly);

    private sealed record GenerationCoverage(
        long Records,
        IReadOnlyDictionary<string, long> PhaseCounts,
        IReadOnlyDictionary<string, long> SideToMoveCounts,
        long TrajectoryGroups,
        long IndependentTrajectories,
        long TerminalTrajectories,
        int MaxPlyReached,
        IReadOnlyDictionary<string, long> PromotionSelections);

    private sealed class CoverageAccumulator
    {
        private readonly Dictionary<string, long> _phaseCounts =
            new(StringComparer.Ordinal)
            {
                ["opening"] = 0,
                ["middlegame"] = 0,
                ["late"] = 0,
                ["endgame"] = 0
            };
        private readonly Dictionary<string, long> _sideCounts =
            new(StringComparer.Ordinal)
            {
                ["w"] = 0,
                ["b"] = 0
            };
        private readonly Dictionary<string, long> _promotionSelections =
            NewPromotionCounts();
        private long _records;
        private long _pairs;
        private long _terminalTrajectories;
        private int _maxPlyReached;

        public void Add(PairResult pair)
        {
            _pairs++;
            _terminalTrajectories += pair.TerminalTrajectories;
            _maxPlyReached = Math.Max(
                _maxPlyReached, pair.MaxPlyReached);
            foreach (var item in pair.PromotionSelections)
                _promotionSelections[item.Key] += item.Value;
            foreach (var root in pair.Records)
            {
                _records++;
                _phaseCounts[root.Phase]++;
                _sideCounts[root.Side]++;
            }
        }

        public GenerationCoverage Freeze() => new(
            _records,
            new Dictionary<string, long>(
                _phaseCounts, StringComparer.Ordinal),
            new Dictionary<string, long>(
                _sideCounts, StringComparer.Ordinal),
            _pairs,
            _pairs * 2,
            _terminalTrajectories,
            _maxPlyReached,
            new Dictionary<string, long>(
                _promotionSelections, StringComparer.Ordinal));
    }

    private struct SplitMix64
    {
        private ulong _state;

        public SplitMix64(ulong seed)
        {
            _state = seed;
        }

        public ulong Next(ulong exclusiveMaximum)
        {
            if (exclusiveMaximum == 0)
                throw new ArgumentOutOfRangeException(
                    nameof(exclusiveMaximum));
            return Next() % exclusiveMaximum;
        }

        private ulong Next()
        {
            _state += 0x9E3779B97F4A7C15UL;
            var value = _state;
            value =
                (value ^ (value >> 30)) * 0xBF58476D1CE4E5B9UL;
            value =
                (value ^ (value >> 27)) * 0x94D049BB133111EBUL;
            return value ^ (value >> 31);
        }
    }

    private static class Phase
    {
        public static readonly IReadOnlyDictionary<string, int[]> Windows =
            new Dictionary<string, int[]>(StringComparer.Ordinal)
            {
                ["opening"] = new[] { 6, 48 },
                ["middlegame"] = new[] { 20, 140 },
                ["late"] = new[] { 40, 260 },
                ["endgame"] = new[] { 60, 400 }
            };

        public static string? Of(int pieces) =>
            pieces >= 37 ? "opening" :
            pieces >= 25 ? "middlegame" :
            pieces >= 13 ? "late" :
            pieces >= 7 ? "endgame" :
            null;

        public static bool InWindow(string phase, int ply)
        {
            var window = Windows[phase];
            return ply >= window[0] && ply <= window[1];
        }
    }

    private sealed record Options(
        string Output,
        ulong Seed,
        int TrajectoryPairs,
        int MaxPlies,
        int PositionsPerPhaseSide,
        int CapturePercent,
        int Workers)
    {
        public static Options Parse(string[] args)
        {
            var known = new HashSet<string>(StringComparer.Ordinal)
            {
                "--output", "--seed", "--trajectory-pairs", "--max-plies",
                "--positions-per-phase-side", "--capture-percent", "--workers"
            };
            var values = new Dictionary<string, string>(StringComparer.Ordinal);
            for (var index = 0; index < args.Length; index += 2)
            {
                var name = args[index];
                if (!known.Contains(name))
                    throw new ArgumentException($"unknown option {name}");
                if (index + 1 >= args.Length)
                    throw new ArgumentException($"{name} requires a value");
                if (!values.TryAdd(name, args[index + 1]))
                    throw new ArgumentException(
                        $"{name} may be supplied only once");
            }

            string Value(string name, string fallback) =>
                values.TryGetValue(name, out var value) ? value : fallback;
            var output = Value("--output", string.Empty);
            if (string.IsNullOrWhiteSpace(output))
                throw new ArgumentException("--output FILE is required");
            var seed = ulong.Parse(
                Value("--seed", "2026072201"),
                NumberStyles.None, CultureInfo.InvariantCulture);
            var pairs = int.Parse(
                Value("--trajectory-pairs", "8192"),
                NumberStyles.None, CultureInfo.InvariantCulture);
            var maxPlies = int.Parse(
                Value("--max-plies", "220"),
                NumberStyles.None, CultureInfo.InvariantCulture);
            var retained = int.Parse(
                Value("--positions-per-phase-side", "2"),
                NumberStyles.None, CultureInfo.InvariantCulture);
            var capture = int.Parse(
                Value("--capture-percent", "72"),
                NumberStyles.None, CultureInfo.InvariantCulture);
            var workers = int.Parse(
                Value("--workers", "4"),
                NumberStyles.None, CultureInfo.InvariantCulture);
            if (pairs <= 0 || maxPlies <= 0 || retained <= 0 || workers <= 0)
                throw new ArgumentException(
                    "numeric counts must be positive");
            if (maxPlies < 6)
                throw new ArgumentException(
                    "--max-plies must reach the first sampling window at ply 6");
            if (maxPlies > 4096)
                throw new ArgumentException(
                    "--max-plies must not exceed the downstream 4096-ply limit");
            if (workers > 1024)
                throw new ArgumentException("--workers must be at most 1024");
            if (capture is < 0 or > 100)
                throw new ArgumentException(
                    "--capture-percent must be 0..100");
            return new Options(
                output, seed, pairs, maxPlies, retained, capture, workers);
        }
    }
}
