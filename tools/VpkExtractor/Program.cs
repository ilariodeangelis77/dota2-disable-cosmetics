using System.Text.Json;
using SteamDatabase.ValvePak;

namespace Dota2CosmeticDisabler.VpkExtractor;

internal static class Program
{
    private const string Version = "0.3.1";

    public static int Main(string[] args)
    {
        try
        {
            if (args.Length == 1 && args[0] == "--version")
            {
                Console.WriteLine($"Dota2VpkExtractor {Version} (ValvePak 4.0.0.142)");
                return 0;
            }

            if (args.Length > 0 && args[0] == "pack")
            {
                return PackDirectory(args[1..]);
            }
            if (args.Length > 0 && args[0] == "list")
            {
                return ListResources(args[1..]);
            }

            return ExtractResources(args);
        }
        catch (Exception exception)
        {
            Console.Error.WriteLine($"ERROR: {exception.Message}");
            return 1;
        }
    }

    private static int ExtractResources(string[] args)
    {
        var options = ParseOptions(args, "--vpk", "--output", "--paths-file");
        var vpkPath = RequireFile(options, "--vpk");
        var pathsFile = RequireFile(options, "--paths-file");
        var outputRoot = Path.GetFullPath(RequireOption(options, "--output"));
        Directory.CreateDirectory(outputRoot);

        var requested = File.ReadAllLines(pathsFile)
            .Select(NormalizeResourcePath)
            .Where(path => path.Length > 0)
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .Order(StringComparer.OrdinalIgnoreCase)
            .ToArray();

        if (requested.Length == 0)
        {
            throw new ArgumentException("The paths file does not contain any resource paths.");
        }

        var extracted = new List<string>(requested.Length);
        var missing = new List<string>();

        using var package = new Package();
        package.OptimizeEntriesForBinarySearch();
        package.Read(vpkPath);

        foreach (var resourcePath in requested)
        {
            var entry = package.FindEntry(resourcePath);
            if (entry is null)
            {
                missing.Add(resourcePath);
                continue;
            }

            package.ReadEntry(entry, out byte[] contents, validateCrc: true);
            var destination = ResolveOutputPath(outputRoot, resourcePath);
            Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
            WriteAtomically(destination, contents);
            extracted.Add(resourcePath);
        }

        Console.WriteLine(JsonSerializer.Serialize(new
        {
            requested = requested.Length,
            extracted = extracted.Count,
            missing,
        }));
        return 0;
    }

    private static int PackDirectory(string[] args)
    {
        var options = ParseOptions(args, "--input", "--output");
        var inputRoot = Path.GetFullPath(RequireOption(options, "--input"));
        if (!Directory.Exists(inputRoot))
        {
            throw new DirectoryNotFoundException($"Input directory not found: {inputRoot}");
        }

        var outputPath = Path.GetFullPath(RequireOption(options, "--output"));
        if (!outputPath.EndsWith("_dir.vpk", StringComparison.OrdinalIgnoreCase))
        {
            throw new ArgumentException("The packed output filename must end with _dir.vpk.");
        }

        var files = Directory.EnumerateFiles(inputRoot, "*", SearchOption.AllDirectories)
            .Select(filePath => new
            {
                FullPath = Path.GetFullPath(filePath),
                ResourcePath = NormalizeResourcePath(Path.GetRelativePath(inputRoot, filePath)),
            })
            .OrderBy(file => file.ResourcePath, StringComparer.Ordinal)
            .ToArray();
        if (files.Length == 0)
        {
            throw new ArgumentException("The input directory does not contain any files.");
        }

        foreach (var file in files)
        {
            if (!IsPackableResource(file.ResourcePath))
            {
                throw new ArgumentException(
                    $"Only compiled override and language-support text resources may be packed: {file.ResourcePath}");
            }
            if ((File.GetAttributes(file.FullPath) & FileAttributes.ReparsePoint) != 0)
            {
                throw new ArgumentException($"Refusing to pack a symbolic link or reparse point: {file.FullPath}");
            }
        }

        Directory.CreateDirectory(Path.GetDirectoryName(outputPath)!);
        var temporaryPath = Path.Combine(
            Path.GetDirectoryName(outputPath)!,
            $".{Path.GetFileNameWithoutExtension(outputPath)}.{Guid.NewGuid():N}_dir.vpk");
        try
        {
            using (var package = new Package())
            {
                foreach (var file in files)
                {
                    package.AddFile(file.ResourcePath, File.ReadAllBytes(file.FullPath));
                }
                package.Write(temporaryPath);
            }

            using (var verification = new Package())
            {
                verification.OptimizeEntriesForBinarySearch(StringComparison.Ordinal);
                verification.Read(temporaryPath);
                foreach (var file in files)
                {
                    var entry = verification.FindEntry(file.ResourcePath)
                        ?? throw new InvalidDataException($"Packed VPK is missing: {file.ResourcePath}");
                    verification.ReadEntry(entry, out byte[] contents, validateCrc: true);
                    if (contents.Length != new FileInfo(file.FullPath).Length)
                    {
                        throw new InvalidDataException($"Packed VPK length mismatch: {file.ResourcePath}");
                    }
                }
            }

            File.Move(temporaryPath, outputPath, overwrite: true);
        }
        finally
        {
            if (File.Exists(temporaryPath))
            {
                File.Delete(temporaryPath);
            }
        }

        Console.WriteLine(JsonSerializer.Serialize(new
        {
            packed = files.Length,
            verified = files.Length,
            output_bytes = new FileInfo(outputPath).Length,
        }));
        return 0;
    }

    private static int ListResources(string[] args)
    {
        var options = ParseOptions(args, "--vpk", "--suffixes");
        var vpkPath = RequireFile(options, "--vpk");
        var suffixes = RequireOption(options, "--suffixes")
            .Split(';', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        if (suffixes.Length == 0 || suffixes.Any(suffix => suffix.Contains('/') || suffix.Contains('\\')))
        {
            throw new ArgumentException("At least one filename suffix without path separators is required.");
        }

        using var package = new Package();
        package.Read(vpkPath);
        var entries = package.Entries ?? throw new InvalidDataException("The VPK directory tree is unavailable.");
        var resources = entries
            .SelectMany(group => group.Value)
            .Select(entry => NormalizeResourcePath(entry.GetFullPath()))
            .Where(path => suffixes.Any(suffix => path.EndsWith(suffix, StringComparison.OrdinalIgnoreCase)))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .Order(StringComparer.OrdinalIgnoreCase)
            .ToArray();
        Console.WriteLine(JsonSerializer.Serialize(new
        {
            count = resources.Length,
            resources,
        }));
        return 0;
    }

    private static bool IsPackableResource(string resourcePath)
    {
        if (resourcePath.EndsWith(".vmdl_c", StringComparison.Ordinal)
            || resourcePath.EndsWith(".vmat_c", StringComparison.Ordinal))
        {
            return true;
        }
        if (resourcePath.EndsWith(".vpcf_c", StringComparison.Ordinal)
            || resourcePath.EndsWith(".vsnap_c", StringComparison.Ordinal))
        {
            return true;
        }
        return resourcePath.EndsWith(".txt", StringComparison.Ordinal)
            || resourcePath.EndsWith(".vtt", StringComparison.Ordinal);
    }

    private static Dictionary<string, string> ParseOptions(string[] args, params string[] allowedOptions)
    {
        if (args.Length == 0 || args.Length % 2 != 0)
        {
            throw new ArgumentException("Options must be supplied as name/value pairs.");
        }

        var allowed = allowedOptions.ToHashSet(StringComparer.Ordinal);
        var options = new Dictionary<string, string>(StringComparer.Ordinal);
        for (var index = 0; index < args.Length; index += 2)
        {
            var name = args[index];
            if (!allowed.Contains(name))
            {
                throw new ArgumentException($"Unknown option: {name}");
            }
            if (!options.TryAdd(name, args[index + 1]))
            {
                throw new ArgumentException($"Option was supplied more than once: {name}");
            }
        }
        return options;
    }

    private static string RequireOption(IReadOnlyDictionary<string, string> options, string name)
    {
        if (!options.TryGetValue(name, out var value) || string.IsNullOrWhiteSpace(value))
        {
            throw new ArgumentException($"Required option is missing: {name}");
        }
        return value;
    }

    private static string RequireFile(IReadOnlyDictionary<string, string> options, string name)
    {
        var value = Path.GetFullPath(RequireOption(options, name));
        if (!File.Exists(value))
        {
            throw new FileNotFoundException($"File not found for {name}: {value}");
        }
        return value;
    }

    private static string NormalizeResourcePath(string input)
    {
        var path = input.Trim().Replace('\\', '/');
        if (path.Length == 0)
        {
            return string.Empty;
        }
        if (path.StartsWith('/') || path.Contains(':') || path.Contains('\0'))
        {
            throw new ArgumentException($"Unsafe resource path: {input}");
        }
        var parts = path.Split('/', StringSplitOptions.RemoveEmptyEntries);
        if (parts.Length == 0 || parts.Any(part => part is "." or ".."))
        {
            throw new ArgumentException($"Unsafe resource path: {input}");
        }
        return string.Join('/', parts).ToLowerInvariant();
    }

    private static string ResolveOutputPath(string outputRoot, string resourcePath)
    {
        var rootWithSeparator = outputRoot.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
            + Path.DirectorySeparatorChar;
        var destination = Path.GetFullPath(
            Path.Combine(outputRoot, resourcePath.Replace('/', Path.DirectorySeparatorChar)));
        if (!destination.StartsWith(rootWithSeparator, StringComparison.OrdinalIgnoreCase))
        {
            throw new ArgumentException($"Resource path escapes the output directory: {resourcePath}");
        }
        return destination;
    }

    private static void WriteAtomically(string destination, byte[] contents)
    {
        var temporary = destination + $".{Guid.NewGuid():N}.tmp";
        try
        {
            File.WriteAllBytes(temporary, contents);
            File.Move(temporary, destination, overwrite: true);
        }
        finally
        {
            if (File.Exists(temporary))
            {
                File.Delete(temporary);
            }
        }
    }
}
