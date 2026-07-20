using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using ChessLib;

internal static class Program
{
    private const string RequestKind =
        "omega-opening-prefix-replay-request-v1";
    private const string PrefixKind =
        "omega-opening-prefix-replay-position-v1";
    private const string ManifestKind =
        "omega-opening-prefix-replay-manifest-v1";
    private const string OfficialInitialOfen =
        "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/" +
        "PPPPPPPPPP/CRNBQKBNRC[W/W/w/w] w KQkq - 0 1";

    private static readonly JsonSerializerOptions Json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        PropertyNameCaseInsensitive = false,
        WriteIndented = false
    };

    private sealed record Identity(string Path, long Bytes, string Sha256);

    private sealed record ReplayRequest(
        int SchemaVersion,
        string Kind,
        int RequestId,
        string SourcePath,
        long SourceBytes,
        string SourceSha256,
        int SourceRecord,
        int SourceObjectOrdinal,
        string SourceObjectIdentity,
        string Schema,
        string InitialSource,
        string ContainerProof,
        string InitialOfen,
        List<string> Moves,
        List<string>? ExpectedPositions);

    private sealed record PrefixRecord(
        int SchemaVersion,
        string Kind,
        int RequestId,
        string SourcePath,
        long SourceBytes,
        string SourceSha256,
        int SourceRecord,
        int SourceObjectOrdinal,
        string SourceObjectIdentity,
        string Schema,
        string InitialSource,
        string ContainerProof,
        int Ply,
        string? Move,
        string Ofen);

    private static async Task<int> Main(string[] args)
    {
        try
        {
            if (args.Length == 1 &&
                args[0].Equals("--self-test", StringComparison.Ordinal))
            {
                await SelfTest();
                Console.WriteLine(
                    "OmegaOpeningPrefixReplay self-test passed.");
                return 0;
            }
            var input = Required(args, "--input");
            var output = Required(args, "--output");
            var manifest = Required(args, "--manifest");
            if (args.Length != 6)
                throw new ArgumentException(
                    "Normal mode requires exactly --input, --output, and " +
                    "--manifest.");
            await Run(input, output, manifest);
            return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(
                $"OmegaOpeningPrefixReplay: {error.Message}");
            return 2;
        }
    }

    private static async Task Run(
        string rawInput,
        string rawOutput,
        string rawManifest)
    {
        var input = Path.GetFullPath(rawInput);
        var output = Path.GetFullPath(rawOutput);
        var manifest = Path.GetFullPath(rawManifest);
        if (!File.Exists(input))
            throw new FileNotFoundException(
                "Replay projection is absent.", input);
        if (File.Exists(output) || File.Exists(manifest))
            throw new IOException(
                "Replay output or provenance manifest already exists.");
        if (StringComparer.OrdinalIgnoreCase.Equals(input, output) ||
            StringComparer.OrdinalIgnoreCase.Equals(input, manifest) ||
            StringComparer.OrdinalIgnoreCase.Equals(output, manifest))
            throw new IOException(
                "Replay input, output, and manifest paths must differ.");

        Directory.CreateDirectory(
            Path.GetDirectoryName(output)
            ?? throw new IOException("Replay output has no directory."));
        Directory.CreateDirectory(
            Path.GetDirectoryName(manifest)
            ?? throw new IOException("Replay manifest has no directory."));
        var temporaryOutput = output + $".tmp-{Guid.NewGuid():N}";
        var temporaryManifest = manifest + $".tmp-{Guid.NewGuid():N}";
        var inputBefore = IdentityOf(input);
        var sources = new Dictionary<string, Identity>(
            StringComparer.OrdinalIgnoreCase);
        var sourceObjects = new HashSet<string>(
            StringComparer.OrdinalIgnoreCase);
        var requests = 0;
        var prefixes = 0;
        var outputPublished = false;
        try
        {
            await using (var outputStream = new FileStream(
                temporaryOutput,
                FileMode.CreateNew,
                FileAccess.Write,
                FileShare.None))
            await using (var writer = new StreamWriter(
                outputStream,
                new UTF8Encoding(false, true)))
            using (var inputStream = new FileStream(
                input,
                FileMode.Open,
                FileAccess.Read,
                FileShare.Read))
            using (var reader = new StreamReader(
                inputStream,
                new UTF8Encoding(false, true),
                detectEncodingFromByteOrderMarks: false))
            {
                string? line;
                while ((line = await reader.ReadLineAsync()) != null)
                {
                    if (string.IsNullOrWhiteSpace(line))
                        throw new InvalidDataException(
                            "Blank projection records are forbidden.");
                    var request = ParseRequest(line, requests);
                    VerifySource(request, sources);
                    var sourceObjectKey =
                        $"{Path.GetFullPath(request.SourcePath)}\u001f" +
                        $"{request.SourceRecord}\u001f" +
                        $"{request.SourceObjectOrdinal}\u001f" +
                        request.SourceObjectIdentity;
                    if (!sourceObjects.Add(sourceObjectKey))
                        throw new InvalidDataException(
                            $"Request {request.RequestId} duplicates a " +
                            "source-record/object replay identity.");
                    prefixes += await Replay(request, writer);
                    requests++;
                }
            }
            if (requests == 0)
                throw new InvalidDataException(
                    "The replay projection is empty.");
            if (!SameIdentity(inputBefore, IdentityOf(input)))
                throw new IOException(
                    "Replay projection changed while consumed.");
            foreach (var source in sources.Values)
                if (!SameIdentity(source, IdentityOf(source.Path)))
                    throw new IOException(
                        $"Replay source changed while consumed: " +
                        $"{source.Path}");

            var temporaryOutputIdentity = IdentityOf(temporaryOutput);
            var outputIdentity = new Identity(
                output,
                temporaryOutputIdentity.Bytes,
                temporaryOutputIdentity.Sha256);
            var manifestValue = new
            {
                SchemaVersion = 1,
                Kind = ManifestKind,
                Variant = "Omega",
                Rules = "ChessLib legal coordinate replay",
                Input = inputBefore,
                SourceSet = sources.Values
                    .OrderBy(item => item.Path, StringComparer.OrdinalIgnoreCase)
                    .ToArray(),
                SourceCount = sources.Count,
                Requests = requests,
                Prefixes = prefixes,
                ParseOrReplayErrors = 0,
                Output = outputIdentity,
                Runtime = new
                {
                    Helper = IdentityOf(
                        Assembly.GetExecutingAssembly().Location),
                    Rules = IdentityOf(typeof(Game).Assembly.Location)
                }
            };
            await File.WriteAllTextAsync(
                temporaryManifest,
                JsonSerializer.Serialize(manifestValue, Json) + "\n",
                new UTF8Encoding(false, true));
            File.Move(temporaryOutput, output, overwrite: false);
            outputPublished = true;
            File.Move(temporaryManifest, manifest, overwrite: false);
        }
        catch
        {
            if (File.Exists(temporaryOutput))
                File.Delete(temporaryOutput);
            if (File.Exists(temporaryManifest))
                File.Delete(temporaryManifest);
            if (outputPublished && File.Exists(output))
                File.Delete(output);
            throw;
        }
    }

    private static ReplayRequest ParseRequest(
        string line,
        int expectedId)
    {
        using var document = JsonDocument.Parse(line);
        if (document.RootElement.ValueKind != JsonValueKind.Object)
            throw new InvalidDataException(
                $"Request {expectedId} is not an object.");
        var names = document.RootElement.EnumerateObject()
            .Select(property => property.Name)
            .ToArray();
        var expectedNames = new[]
        {
            "schemaVersion",
            "kind",
            "requestId",
            "sourcePath",
            "sourceBytes",
            "sourceSha256",
            "sourceRecord",
            "sourceObjectOrdinal",
            "sourceObjectIdentity",
            "schema",
            "initialSource",
            "containerProof",
            "initialOfen",
            "moves",
            "expectedPositions"
        };
        if (names.Length != expectedNames.Length ||
            names.Distinct(StringComparer.OrdinalIgnoreCase).Count()
                != names.Length ||
            !names.Order(StringComparer.Ordinal)
                .SequenceEqual(
                    expectedNames.Order(StringComparer.Ordinal),
                    StringComparer.Ordinal))
            throw new InvalidDataException(
                $"Request {expectedId} field inventory changed.");
        var request = JsonSerializer.Deserialize<ReplayRequest>(line, Json)
            ?? throw new InvalidDataException(
                $"Request {expectedId} is null.");
        ValidateRequest(request, expectedId);
        return request;
    }

    private static void ValidateRequest(
        ReplayRequest request,
        int expectedId)
    {
        if (request.SchemaVersion != 1 ||
            request.Kind != RequestKind ||
            request.RequestId != expectedId ||
            !Path.IsPathFullyQualified(request.SourcePath) ||
            request.SourceBytes < 0 ||
            !IsSha256(request.SourceSha256) ||
            request.SourceRecord < 1 ||
            request.SourceObjectOrdinal < 1 ||
            !IsJsonPointer(request.SourceObjectIdentity) ||
            request.Schema is not (
                "schedule-moves"
                or "event-opening-moves"
                or "transcript") ||
            request.InitialSource is not (
                "explicit" or "official-default") ||
            request.ContainerProof is not (
                "openings-container"
                or "direct-object"
                or "event-game-start"
                or "transcript-game") ||
            string.IsNullOrWhiteSpace(request.InitialOfen) ||
            request.Moves == null ||
            request.Moves.Count > 512 ||
            request.Moves.Any(
                move => string.IsNullOrWhiteSpace(move) ||
                        move != move.Trim()))
            throw new InvalidDataException(
                $"Request {expectedId} violates the replay contract.");
        _ = NormalizeFullOfen(request.InitialOfen);
        if (request.Schema == "event-opening-moves" &&
            (request.InitialSource != "explicit" ||
             request.ContainerProof != "event-game-start" ||
             request.ExpectedPositions != null))
            throw new InvalidDataException(
                $"Request {expectedId} has invalid event proof.");
        if (request.Schema == "schedule-moves" &&
            (request.ContainerProof is not (
                "openings-container" or "direct-object") ||
             request.ExpectedPositions != null))
            throw new InvalidDataException(
                $"Request {expectedId} has invalid schedule proof.");
        if (request.Schema == "transcript" &&
            (request.InitialSource != "explicit" ||
             request.ContainerProof != "transcript-game" ||
             request.ExpectedPositions == null ||
             request.ExpectedPositions.Count != request.Moves.Count ||
             request.ExpectedPositions.Any(
                 position => string.IsNullOrWhiteSpace(position))))
            throw new InvalidDataException(
                $"Request {expectedId} has invalid transcript proof.");
        if (request.ExpectedPositions != null)
            foreach (var position in request.ExpectedPositions)
                _ = NormalizeFullOfen(position);
        if (request.InitialSource == "official-default" &&
            (request.Schema != "schedule-moves" ||
             request.ContainerProof != "openings-container" ||
             NormalizeFullOfen(request.InitialOfen)
                != OfficialInitialOfen))
            throw new InvalidDataException(
                $"Request {expectedId} has unproved official default.");
    }

    private static void VerifySource(
        ReplayRequest request,
        IDictionary<string, Identity> cache)
    {
        if (!cache.TryGetValue(request.SourcePath, out var actual))
        {
            actual = IdentityOf(request.SourcePath);
            cache.Add(request.SourcePath, actual);
        }
        var declared = new Identity(
            Path.GetFullPath(request.SourcePath),
            request.SourceBytes,
            request.SourceSha256);
        if (!SameIdentity(actual, declared))
            throw new InvalidDataException(
                $"Request {request.RequestId} source identity changed.");
    }

    private static async Task<int> Replay(
        ReplayRequest request,
        TextWriter writer)
    {
        using var game = CreateGame(request.InitialOfen);
        await WritePrefix(request, 0, null, game.GetFenString(), writer);
        var count = 1;
        for (var index = 0; index < request.Moves.Count; index++)
        {
            await EnsureCanContinue(game, request, index);
            var move = request.Moves[index];
            try
            {
                await game.DoMove(move, checkEndGame: true);
            }
            catch (Exception error)
            {
                throw new InvalidDataException(
                    $"{request.SourcePath}:{request.SourceRecord}" +
                    $"{request.SourceObjectIdentity}: illegal or malformed " +
                    $"{request.Schema} move {index + 1} ('{move}'): " +
                    $"{error.Message}",
                    error);
            }
            if (game.Moves.Count != index + 1 ||
                !StringComparer.OrdinalIgnoreCase.Equals(
                    game.Moves[^1].Coordinate,
                    move))
                throw new InvalidDataException(
                    $"Request {request.RequestId} coordinate diverged at " +
                    $"move {index + 1}.");
            var generated = game.GetFenString();
            if (request.ExpectedPositions != null &&
                NormalizeFullOfen(generated) != NormalizeFullOfen(
                    request.ExpectedPositions[index]))
                throw new InvalidDataException(
                    $"{request.SourcePath}:{request.SourceRecord}" +
                    $"{request.SourceObjectIdentity}: transcript position " +
                    $"parity diverged after move {index + 1}.");
            await WritePrefix(
                request,
                index + 1,
                move,
                generated,
                writer);
            count++;
        }
        return count;
    }

    private static async Task EnsureCanContinue(
        Game game,
        ReplayRequest request,
        int moveIndex)
    {
        var terminal = moveIndex > 0
            ? game.Ended
            : await game.IsCheckmate(game.ToMove) ||
              await game.IsStalemate(game.ToMove) ||
              game.IsDraw(game.ToMove);
        if (terminal)
            throw new InvalidDataException(
                $"{request.SourcePath}:{request.SourceRecord}" +
                $"{request.SourceObjectIdentity}: replay continues after " +
                (moveIndex == 0
                    ? "a terminal initial position."
                    : $"terminal move {moveIndex} ({game.Result})."));
    }

    private static async Task WritePrefix(
        ReplayRequest request,
        int ply,
        string? move,
        string ofen,
        TextWriter writer)
    {
        var record = new PrefixRecord(
            1,
            PrefixKind,
            request.RequestId,
            Path.GetFullPath(request.SourcePath),
            request.SourceBytes,
            request.SourceSha256,
            request.SourceRecord,
            request.SourceObjectOrdinal,
            request.SourceObjectIdentity,
            request.Schema,
            request.InitialSource,
            request.ContainerProof,
            ply,
            move,
            ofen);
        await writer.WriteLineAsync(JsonSerializer.Serialize(record, Json));
    }

    private static Game CreateGame(string initialOfen)
    {
        var game = new Game();
        game.Init(new Game.GameSettings
        {
            Variant = ChessVariant.Omega,
            InitialFenPosition = initialOfen,
            DrawForRepetition = 3,
            Players =
            [
                new HumanPlayer(Game.Colors.White, "White", null),
                new HumanPlayer(Game.Colors.Black, "Black", null)
            ]
        });
        if (game.Settings.Variant != ChessVariant.Omega)
            throw new InvalidDataException(
                "Replay initialized a non-Omega game.");
        return game;
    }

    private static async Task SelfTest()
    {
        var root = Path.Combine(
            Path.GetTempPath(),
            $"omega-opening-prefix-replay-{Guid.NewGuid():N}");
        Directory.CreateDirectory(root);
        try
        {
            var source = Path.Combine(root, "source.json");
            var input = Path.Combine(root, "projection.jsonl");
            var output = Path.Combine(root, "prefixes.jsonl");
            var manifest = Path.Combine(root, "manifest.json");
            await File.WriteAllTextAsync(
                source,
                "{}\n",
                new UTF8Encoding(false, true));
            var identity = IdentityOf(source);
            var request = new ReplayRequest(
                1,
                RequestKind,
                0,
                identity.Path,
                identity.Bytes,
                identity.Sha256,
                1,
                1,
                "/openings/0",
                "schedule-moves",
                "official-default",
                "openings-container",
                OfficialInitialOfen,
                ["e1e3", "e8e6"],
                null);
            var explicitSchedule = request with
            {
                RequestId = 1,
                SourceObjectOrdinal = 2,
                SourceObjectIdentity = "/schedule",
                InitialSource = "explicit",
                ContainerProof = "direct-object"
            };
            var eventRequest = request with
            {
                RequestId = 2,
                SourceObjectOrdinal = 3,
                SourceObjectIdentity = "",
                Schema = "event-opening-moves",
                InitialSource = "explicit",
                ContainerProof = "event-game-start"
            };
            var transcriptPositions = new List<string>();
            using (var transcriptGame = CreateGame(OfficialInitialOfen))
            {
                foreach (var move in request.Moves)
                {
                    await transcriptGame.DoMove(
                        move, checkEndGame: true);
                    transcriptPositions.Add(
                        transcriptGame.GetFenString());
                }
            }
            var transcriptRequest = request with
            {
                RequestId = 3,
                SourceObjectOrdinal = 4,
                SourceObjectIdentity = "/transcript",
                Schema = "transcript",
                InitialSource = "explicit",
                ContainerProof = "transcript-game",
                ExpectedPositions = transcriptPositions
            };
            var requests = new[]
            {
                request,
                explicitSchedule,
                eventRequest,
                transcriptRequest
            };
            var lines = requests
                .Select(item => JsonSerializer.Serialize(item, Json))
                .ToArray();
            for (var index = 0; index < lines.Length; index++)
                _ = ParseRequest(lines[index], index);
            await File.WriteAllTextAsync(
                input,
                string.Join("\n", lines) + "\n",
                new UTF8Encoding(false, true));
            await Run(input, output, manifest);
            if (File.ReadLines(output).Count() != 12)
                throw new InvalidDataException(
                    "Self-test did not emit ply zero plus every prefix.");
            using (var value = JsonDocument.Parse(
                await File.ReadAllTextAsync(manifest)))
            {
                if (
                    value.RootElement.GetProperty("requests").GetInt32()
                        != 4 ||
                    value.RootElement.GetProperty("prefixes").GetInt32()
                        != 12)
                    throw new InvalidDataException(
                        "Self-test manifest counts changed.");
            }
            try
            {
                await Run(input, output, manifest);
                throw new InvalidDataException(
                    "No-clobber self-test overwrote output.");
            }
            catch (IOException)
            {
            }
            var duplicateInput = Path.Combine(
                root, "duplicate-projection.jsonl");
            var duplicateOutput = Path.Combine(
                root, "duplicate-prefixes.jsonl");
            var duplicateManifest = Path.Combine(
                root, "duplicate-manifest.json");
            var duplicateObject = request with { RequestId = 1 };
            await File.WriteAllTextAsync(
                duplicateInput,
                JsonSerializer.Serialize(request, Json) + "\n" +
                JsonSerializer.Serialize(duplicateObject, Json) + "\n",
                new UTF8Encoding(false, true));
            try
            {
                await Run(
                    duplicateInput,
                    duplicateOutput,
                    duplicateManifest);
                throw new InvalidDataException(
                    "Duplicate source object self-test was accepted.");
            }
            catch (InvalidDataException)
            {
                if (File.Exists(duplicateOutput) ||
                    File.Exists(duplicateManifest))
                    throw new InvalidDataException(
                        "Failed duplicate replay published output.");
            }
            var emptyInput = Path.Combine(root, "empty-projection.jsonl");
            var emptyOutput = Path.Combine(root, "empty-prefixes.jsonl");
            var emptyManifest = Path.Combine(root, "empty-manifest.json");
            await File.WriteAllTextAsync(
                emptyInput,
                "",
                new UTF8Encoding(false, true));
            try
            {
                await Run(emptyInput, emptyOutput, emptyManifest);
                throw new InvalidDataException(
                    "Empty projection self-test was accepted.");
            }
            catch (InvalidDataException)
            {
                if (File.Exists(emptyOutput) ||
                    File.Exists(emptyManifest))
                    throw new InvalidDataException(
                        "Failed empty replay published output.");
            }
            var duplicate = lines[0][..^1] + ",\"moves\":[]}";
            var duplicateRejected = false;
            try
            {
                _ = ParseRequest(duplicate, 0);
            }
            catch (InvalidDataException)
            {
                duplicateRejected = true;
            }
            if (!duplicateRejected)
                throw new InvalidDataException(
                    "Duplicate-field self-test was accepted.");
            var unproved = request with
            {
                ContainerProof = "direct-object"
            };
            var unprovedRejected = false;
            try
            {
                ValidateRequest(unproved, 0);
            }
            catch (InvalidDataException)
            {
                unprovedRejected = true;
            }
            if (!unprovedRejected)
                throw new InvalidDataException(
                    "Unproved-default self-test was accepted.");
            var parityRejected = false;
            try
            {
                ValidateRequest(
                    transcriptRequest with
                    {
                        ExpectedPositions = []
                    },
                    3);
            }
            catch (InvalidDataException)
            {
                parityRejected = true;
            }
            if (!parityRejected)
                throw new InvalidDataException(
                    "Transcript count mismatch self-test was accepted.");
            var wrongParityInput = Path.Combine(
                root, "wrong-parity-projection.jsonl");
            var wrongParityOutput = Path.Combine(
                root, "wrong-parity-prefixes.jsonl");
            var wrongParityManifest = Path.Combine(
                root, "wrong-parity-manifest.json");
            var wrongPositions = transcriptPositions.ToList();
            wrongPositions[0] = OfficialInitialOfen;
            var wrongParityRequest = transcriptRequest with
            {
                RequestId = 0,
                SourceObjectOrdinal = 5,
                SourceObjectIdentity = "/wrong-parity",
                ExpectedPositions = wrongPositions
            };
            await File.WriteAllTextAsync(
                wrongParityInput,
                JsonSerializer.Serialize(wrongParityRequest, Json) + "\n",
                new UTF8Encoding(false, true));
            try
            {
                await Run(
                    wrongParityInput,
                    wrongParityOutput,
                    wrongParityManifest);
                throw new InvalidDataException(
                    "Same-length wrong transcript parity self-test was " +
                    "accepted.");
            }
            catch (InvalidDataException)
            {
                if (File.Exists(wrongParityOutput) ||
                    File.Exists(wrongParityManifest))
                    throw new InvalidDataException(
                        "Failed parity replay published output.");
            }
            var rollbackInput = Path.Combine(
                root, "rollback-projection.jsonl");
            var rollbackOutput = Path.Combine(
                root, "rollback-prefixes.jsonl");
            var rollbackManifest = Path.Combine(
                root, "rollback-manifest-target");
            Directory.CreateDirectory(rollbackManifest);
            var rollbackMarker = Path.Combine(
                rollbackManifest, "preexisting-marker.txt");
            await File.WriteAllTextAsync(
                rollbackMarker,
                "must remain unchanged",
                new UTF8Encoding(false, true));
            await File.WriteAllTextAsync(
                rollbackInput,
                JsonSerializer.Serialize(request, Json) + "\n",
                new UTF8Encoding(false, true));
            var rollbackRejected = false;
            try
            {
                await Run(
                    rollbackInput, rollbackOutput, rollbackManifest);
            }
            catch (IOException)
            {
                rollbackRejected = true;
            }
            if (!rollbackRejected ||
                File.Exists(rollbackOutput) ||
                !Directory.Exists(rollbackManifest) ||
                !File.Exists(rollbackMarker) ||
                await File.ReadAllTextAsync(rollbackMarker)
                    != "must remain unchanged")
                throw new InvalidDataException(
                    "Second-publication rollback self-test failed.");
            using (var ended = CreateGame(OfficialInitialOfen))
            {
                ended.Status = Game.Statuses.Ended;
                var terminalRejected = false;
                try
                {
                    await EnsureCanContinue(ended, request, 1);
                }
                catch (InvalidDataException)
                {
                    terminalRejected = true;
                }
                if (!terminalRejected)
                    throw new InvalidDataException(
                        "Terminal continuation self-test was accepted.");
            }
            var terminalInput = Path.Combine(
                root, "terminal-projection.jsonl");
            var terminalOutput = Path.Combine(
                root, "terminal-prefixes.jsonl");
            var terminalManifest = Path.Combine(
                root, "terminal-manifest.json");
            var terminalRequest = request with
            {
                InitialSource = "explicit",
                ContainerProof = "direct-object",
                InitialOfen =
                    "10/RK8/10/10/10/10/10/10/10/10[-/-/-/k] " +
                    "w - - 0 1",
                Moves = ["a8a9", "b8b9"]
            };
            await File.WriteAllTextAsync(
                terminalInput,
                JsonSerializer.Serialize(terminalRequest, Json) + "\n",
                new UTF8Encoding(false, true));
            try
            {
                await Run(
                    terminalInput, terminalOutput, terminalManifest);
                throw new InvalidDataException(
                    "Actual terminal-continuation self-test was accepted.");
            }
            catch (InvalidDataException)
            {
                if (File.Exists(terminalOutput) ||
                    File.Exists(terminalManifest))
                    throw new InvalidDataException(
                        "Failed terminal replay published output.");
            }
            string terminalInitialOfen;
            using (var terminalSeed = CreateGame(
                "10/RK8/10/10/10/10/10/10/10/10[-/-/-/k] " +
                "w - - 0 1"))
            {
                await terminalSeed.DoMove(
                    "a8a9", checkEndGame: true);
                if (!terminalSeed.Ended)
                    throw new InvalidDataException(
                        "Terminal-initial seed did not end the game.");
                terminalInitialOfen = terminalSeed.GetFenString();
            }
            var terminalInitialInput = Path.Combine(
                root, "terminal-initial-projection.jsonl");
            var terminalInitialOutput = Path.Combine(
                root, "terminal-initial-prefixes.jsonl");
            var terminalInitialManifest = Path.Combine(
                root, "terminal-initial-manifest.json");
            var terminalInitialRequest = request with
            {
                InitialSource = "explicit",
                ContainerProof = "direct-object",
                InitialOfen = terminalInitialOfen,
                Moves = ["b8b9"]
            };
            await File.WriteAllTextAsync(
                terminalInitialInput,
                JsonSerializer.Serialize(terminalInitialRequest, Json) +
                "\n",
                new UTF8Encoding(false, true));
            try
            {
                await Run(
                    terminalInitialInput,
                    terminalInitialOutput,
                    terminalInitialManifest);
                throw new InvalidDataException(
                    "Terminal initial-position self-test was accepted.");
            }
            catch (InvalidDataException)
            {
                if (File.Exists(terminalInitialOutput) ||
                    File.Exists(terminalInitialManifest))
                    throw new InvalidDataException(
                        "Failed terminal-initial replay published output.");
            }
            using var game = CreateGame(OfficialInitialOfen);
            try
            {
                await game.DoMove("e1e9", checkEndGame: false);
            }
            catch
            {
                return;
            }
            throw new InvalidDataException(
                "Illegal-move self-test was accepted.");
        }
        finally
        {
            if (Directory.Exists(root))
                Directory.Delete(root, recursive: true);
        }
    }

    private static string Required(string[] args, string name)
    {
        var values = new List<string>();
        for (var index = 0; index < args.Length; index++)
        {
            if (!args[index].Equals(name, StringComparison.Ordinal))
                continue;
            if (++index >= args.Length)
                throw new ArgumentException($"{name} requires a value.");
            values.Add(args[index]);
        }
        return values.Count == 1
            ? values[0]
            : throw new ArgumentException(
                $"{name} is required exactly once.");
    }

    private static bool IsJsonPointer(string value)
    {
        if (value.Length == 0)
            return true;
        if (value[0] != '/')
            return false;
        for (var index = 0; index < value.Length; index++)
        {
            if (value[index] != '~')
                continue;
            if (++index >= value.Length ||
                value[index] is not ('0' or '1'))
                return false;
        }
        return true;
    }

    private static bool IsSha256(string value) =>
        value.Length == 64 &&
        value.All(character =>
            character is >= '0' and <= '9'
            or >= 'a' and <= 'f');

    private static string NormalizeFullOfen(string value)
    {
        var fields = value.Split(
            (char[]?)null,
            StringSplitOptions.RemoveEmptyEntries);
        if (fields.Length != 6)
            throw new InvalidDataException(
                "Omega OFEN must have exactly six whitespace-delimited " +
                "fields.");
        return string.Join(" ", fields);
    }

    private static Identity IdentityOf(string rawPath)
    {
        var path = Path.GetFullPath(rawPath);
        var info = new FileInfo(path);
        using var stream = new FileStream(
            path,
            FileMode.Open,
            FileAccess.Read,
            FileShare.Read);
        return new Identity(
            path,
            info.Length,
            Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant());
    }

    private static bool SameIdentity(Identity left, Identity right) =>
        StringComparer.OrdinalIgnoreCase.Equals(left.Path, right.Path) &&
        left.Bytes == right.Bytes &&
        StringComparer.Ordinal.Equals(left.Sha256, right.Sha256);
}
