using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using ChessLib;

internal static class Program
{
    private const string PositionKind = "omega-history-replay-position-v1";
    private const string ManifestKind = "omega-history-replay-manifest-v1";

    private static readonly JsonSerializerOptions Json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        WriteIndented = false
    };

    private sealed record Identity(string Path, long Bytes, string Sha256);

    private sealed record PositionRecord(
        int SchemaVersion,
        string Kind,
        string SourcePath,
        long SourceBytes,
        string SourceSha256,
        string SourceFormat,
        int GameIndex,
        string GameId,
        string PositionRole,
        int Ply,
        string? Move,
        string Ofen);

    private static async Task<int> Main(string[] args)
    {
        try
        {
            if (args.Length == 1 &&
                args[0].Equals("--self-test", StringComparison.OrdinalIgnoreCase))
            {
                SelfTestRecursiveAnnotationVariationScanner();
                Console.WriteLine("OmegaHistorySnapshot self-test passed.");
                return 0;
            }

            var roots = Values(args, "--root").Select(Path.GetFullPath).ToArray();
            var ignored = Values(args, "--ignore-subtree").Select(Path.GetFullPath).ToArray();
            var output = Required(args, "--output");
            var manifest = Required(args, "--manifest");
            if (roots.Length == 0)
                throw new ArgumentException("At least one --root is required.");
            if (File.Exists(output) || File.Exists(manifest))
                throw new IOException("Snapshot output or manifest already exists.");

            var sources = Discover(roots, ignored);
            if (sources.Count == 0)
                throw new InvalidDataException("No PGN or CCSF history sources were found.");

            Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(output))!);
            var temporaryOutput = output + $".tmp-{Guid.NewGuid():N}";
            var temporaryManifest = manifest + $".tmp-{Guid.NewGuid():N}";
            var identities = sources.Select(IdentityOf).ToArray();
            var games = 0;
            var positions = 0;
            try
            {
                await using (var stream = new FileStream(
                    temporaryOutput, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                await using (var writer = new StreamWriter(
                    stream, new UTF8Encoding(encoderShouldEmitUTF8Identifier: false)))
                {
                    foreach (var source in sources)
                    {
                        var identity = identities.Single(item =>
                            StringComparer.OrdinalIgnoreCase.Equals(item.Path, source));
                        List<Game> sourceGames;
                        try
                        {
                            sourceGames = await LoadAndReplay(source);
                        }
                        catch (Exception error)
                        {
                            throw new InvalidDataException(
                                $"{source}: parse/replay failed: {error.Message}", error);
                        }
                        for (var index = 0; index < sourceGames.Count; index++)
                        {
                            games++;
                            positions += await EmitGame(
                                writer, identity, source, index + 1, sourceGames[index]);
                        }
                    }
                }

                var snapshotIdentity = IdentityOf(temporaryOutput);
                var manifestValue = new
                {
                    SchemaVersion = 1,
                    Kind = ManifestKind,
                    Parser = "RAV-rejecting ChessLib.PGN.LoadFile/Game.LoadFromPgn and Game.Load plus coordinate replay",
                    PgnRecursiveAnnotationVariations = "rejected-before-ChessLib.PGN.LoadFile",
                    Variant = "Omega",
                    Roots = roots,
                    IgnoredSubtrees = ignored,
                    SourceSet = identities,
                    SourceCount = identities.Length,
                    Games = games,
                    Positions = positions,
                    ParseOrReplayErrors = 0,
                    Snapshot = new
                    {
                        Path = Path.GetFullPath(output),
                        snapshotIdentity.Bytes,
                        snapshotIdentity.Sha256
                    }
                };
                await File.WriteAllTextAsync(
                    temporaryManifest,
                    JsonSerializer.Serialize(manifestValue, Json) + "\n",
                    new UTF8Encoding(encoderShouldEmitUTF8Identifier: false));

                File.Move(temporaryOutput, output, overwrite: false);
                File.Move(temporaryManifest, manifest, overwrite: false);
                Console.WriteLine(
                    $"Omega history snapshot: {identities.Length} sources, " +
                    $"{games} games, {positions} position records.");
                return 0;
            }
            catch
            {
                if (File.Exists(temporaryOutput)) File.Delete(temporaryOutput);
                if (File.Exists(temporaryManifest)) File.Delete(temporaryManifest);
                throw;
            }
        }
        catch (Exception error)
        {
            Console.Error.WriteLine($"OmegaHistorySnapshot: {error.Message}");
            return 2;
        }
    }

    private static List<string> Discover(IEnumerable<string> roots, IEnumerable<string> ignored)
    {
        var ignore = ignored.Select(NormalDirectory).ToArray();
        var result = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var raw in roots)
        {
            var root = Path.GetFullPath(raw);
            if (!Directory.Exists(root))
                throw new DirectoryNotFoundException(root);
            foreach (var path in Directory.EnumerateFiles(root, "*", SearchOption.AllDirectories))
            {
                var full = Path.GetFullPath(path);
                if (ignore.Any(item => IsWithin(full, item)))
                    continue;
                var extension = Path.GetExtension(full);
                if (extension.Equals(".pgn", StringComparison.OrdinalIgnoreCase) ||
                    extension.Equals(".ccsf", StringComparison.OrdinalIgnoreCase))
                    result.Add(full);
            }
        }
        return result.OrderBy(item => item, StringComparer.OrdinalIgnoreCase).ToList();
    }

    private static async Task<List<Game>> LoadAndReplay(string source)
    {
        if (Path.GetExtension(source).Equals(".pgn", StringComparison.OrdinalIgnoreCase))
        {
            RejectRecursiveAnnotationVariations(source);
            var parsed = await PGN.LoadFile(source);
            if (parsed.Count == 0)
                throw new InvalidDataException($"{source}: PGN contains no games.");
            var result = new List<Game>();
            foreach (var pgn in parsed)
            {
                // ChessLib's permissive PGN tokenizer can retain a terminal
                // result marker as a move in some generated files. It is
                // metadata, never a legal ply, and may only be stripped from
                // the tail.
                while (pgn.Moves.Count > 0 &&
                       IsResultMarker(pgn.Moves[^1].Notation))
                    pgn.Moves.RemoveAt(pgn.Moves.Count - 1);
                var replay = await Game.LoadFromPgn(pgn, starAsAborted: false);
                RequireOmega(replay, source);
                result.Add(replay);
            }
            return result;
        }

        using var loaded = await Game.Load(source);
        RequireOmega(loaded, source);
        var replayPgn = new PGN
        {
            Variant = "Omega Chess",
            FEN = loaded.InitialFenPosition,
            Result = "*"
        };
        foreach (var move in loaded.Moves)
        {
            if (string.IsNullOrWhiteSpace(move.Coordinate))
                throw new InvalidDataException($"{source}: CCSF has a move without coordinates.");
            replayPgn.Moves.Add(new PGN.Move { Notation = move.Coordinate });
        }
        var replayed = await Game.LoadFromPgn(replayPgn, starAsAborted: false);
        RequireOmega(replayed, source);
        if (loaded.Moves.Count != replayed.Moves.Count)
            throw new InvalidDataException($"{source}: CCSF replay move count changed.");
        for (var index = 0; index < loaded.Moves.Count; index++)
        {
            if (!StringComparer.OrdinalIgnoreCase.Equals(
                    loaded.Moves[index].Coordinate, replayed.Moves[index].Coordinate) ||
                NormalizeOfen(loaded.Moves[index].Fen) !=
                NormalizeOfen(replayed.Moves[index].Fen))
                throw new InvalidDataException(
                    $"{source}: CCSF replay diverges at ply {index + 1}.");
        }
        return new List<Game> { replayed };
    }

    private static void RejectRecursiveAnnotationVariations(string source)
    {
        var text = File.ReadAllText(source);
        if (ContainsRecursiveAnnotationVariation(text))
            throw new InvalidDataException(
                $"{source}: PGN recursive annotation variations are forbidden; " +
                "ChessLib would discard them before replay.");
    }

    private static bool ContainsRecursiveAnnotationVariation(string text)
    {
        var inTag = false;
        var inTagString = false;
        var inBraceComment = false;
        var inSemicolonComment = false;
        var escaped = false;

        foreach (var character in text)
        {
            if (inSemicolonComment)
            {
                if (character is '\r' or '\n')
                    inSemicolonComment = false;
                continue;
            }
            if (inBraceComment)
            {
                if (character == '}')
                    inBraceComment = false;
                continue;
            }
            if (inTag)
            {
                if (escaped)
                {
                    escaped = false;
                    continue;
                }
                if (inTagString && character == '\\')
                {
                    escaped = true;
                    continue;
                }
                if (character == '"')
                {
                    inTagString = !inTagString;
                    continue;
                }
                if (!inTagString && character == ']')
                    inTag = false;
                continue;
            }

            switch (character)
            {
                case '[':
                    inTag = true;
                    inTagString = false;
                    break;
                case '{':
                    inBraceComment = true;
                    break;
                case ';':
                    inSemicolonComment = true;
                    break;
                case '(':
                case ')':
                    return true;
            }
        }
        return false;
    }

    private static void SelfTestRecursiveAnnotationVariationScanner()
    {
        const string ravFree =
            "[Event \"Study (parentheses in tag are safe)\"]\n" +
            "1. f1f2 {comment (parentheses) is safe} ; and so is this (comment)\n" +
            "1... f8f7 *\n";
        const string withRav =
            "[Event \"Study\"]\n1. f1f2 (1. g1g2 f8f7) 1... f8f7 *\n";
        const string multilineRav =
            "[Event \"Study\"]\n1. f1f2 (\n1. g1g2\n) 1... f8f7 *\n";
        if (ContainsRecursiveAnnotationVariation(ravFree))
            throw new InvalidDataException(
                "RAV scanner rejected parentheses confined to metadata/comments.");
        if (!ContainsRecursiveAnnotationVariation(withRav) ||
            !ContainsRecursiveAnnotationVariation(multilineRav))
            throw new InvalidDataException("RAV scanner accepted a variation.");
    }

    private static async Task<int> EmitGame(
        StreamWriter writer,
        Identity source,
        string sourcePath,
        int gameIndex,
        Game game)
    {
        var gamePayload = $"{source.Sha256}\0{gameIndex}\0{game.InitialFenPosition}\0" +
                          string.Join(" ", game.Moves.Select(move => move.Coordinate));
        var gameId = Convert.ToHexString(
            SHA256.HashData(Encoding.UTF8.GetBytes(gamePayload))).ToLowerInvariant();
        var format = Path.GetExtension(sourcePath).TrimStart('.').ToLowerInvariant();
        var count = 0;

        async Task Emit(string role, int ply, string ofen, string? move = null)
        {
            if (string.IsNullOrWhiteSpace(ofen))
                throw new InvalidDataException(
                    $"{sourcePath}: game {gameIndex} {role} ply {ply} lacks OFEN.");
            var record = new PositionRecord(
                1, PositionKind, source.Path, source.Bytes, source.Sha256,
                format, gameIndex, gameId, role, ply, move, ofen);
            await writer.WriteLineAsync(JsonSerializer.Serialize(record, Json));
            count++;
        }

        var previous = game.InitialFenPosition;
        await Emit("initial", 0, previous);
        for (var index = 0; index < game.Moves.Count; index++)
        {
            var move = game.Moves[index];
            await Emit("pre", index + 1, previous, move.Coordinate);
            await Emit("post", index + 1, move.Fen, move.Coordinate);
            previous = move.Fen;
        }
        await Emit("final", game.Moves.Count, previous);
        return count;
    }

    private static void RequireOmega(Game game, string source)
    {
        if (game.Settings.Variant != ChessVariant.Omega)
            throw new InvalidDataException($"{source}: history game is not Omega Chess.");
    }

    private static bool IsResultMarker(string? value) =>
        value is "1-0" or "0-1" or "1/2-1/2" or "*";

    private static string NormalizeOfen(string value) =>
        string.Join(" ", value.Split(
            (char[]?)null, StringSplitOptions.RemoveEmptyEntries));

    private static Identity IdentityOf(string path)
    {
        var full = Path.GetFullPath(path);
        var info = new FileInfo(full);
        using var stream = File.OpenRead(full);
        return new Identity(
            full,
            info.Length,
            Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant());
    }

    private static string Required(string[] args, string name) =>
        Values(args, name).SingleOrDefault() ??
        throw new ArgumentException($"{name} is required exactly once.");

    private static IEnumerable<string> Values(string[] args, string name)
    {
        for (var index = 0; index < args.Length; index++)
        {
            if (!args[index].Equals(name, StringComparison.OrdinalIgnoreCase))
                continue;
            if (++index >= args.Length)
                throw new ArgumentException($"{name} requires a value.");
            yield return args[index];
        }
    }

    private static string NormalDirectory(string path) =>
        Path.TrimEndingDirectorySeparator(Path.GetFullPath(path));

    private static bool IsWithin(string path, string root)
    {
        var relative = Path.GetRelativePath(root, path);
        return relative != ".." &&
               !relative.StartsWith($"..{Path.DirectorySeparatorChar}") &&
               !Path.IsPathRooted(relative);
    }
}
