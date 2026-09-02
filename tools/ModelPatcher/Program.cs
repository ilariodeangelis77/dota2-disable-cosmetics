using System.Text;
using System.Text.RegularExpressions;
using ValveResourceFormat;
using ValveResourceFormat.ResourceTypes;
using ValveResourceFormat.Serialization.KeyValues;
using KVValueType = ValveKeyValue.KVValueType;

namespace Dota2CosmeticDisabler.ModelPatcher;

internal static partial class Program
{
    private const string Version = "0.5.0";

    public static int Main(string[] args)
    {
        try
        {
            if (args.Length == 1 && args[0] == "--version")
            {
                Console.WriteLine(
                    $"Dota2ModelSkinPatcher {Version} (ValveResourceFormat 15.0.4937)");
                return 0;
            }
            if (args.Length > 0 && args[0] == "patch")
            {
                return PatchModel(args[1..]);
            }
            if (args.Length > 0 && args[0] == "patch-batch")
            {
                return PatchBatch(args[1..]);
            }
            if (args.Length > 0 && args[0] == "compose")
            {
                return ComposeModels(args[1..]);
            }
            if (args.Length > 0 && args[0] == "offset-attachments")
            {
                return OffsetModelAttachments(args[1..]);
            }
            throw new ArgumentException(
                "Expected --version, patch, patch-batch, compose, or offset-attachments.");
        }
        catch (Exception exception)
        {
            Console.Error.WriteLine($"ERROR: {exception.Message}");
            return 1;
        }
    }

    private static int OffsetModelAttachments(string[] args)
    {
        var options = ParseOptions(
            args,
            "--input",
            "--output",
            "--attachments",
            "--offset-x",
            "--offset-y",
            "--offset-z");
        var inputPath = RequireFile(options, "--input", ".vmdl_c");
        var outputPath = Path.GetFullPath(RequireOption(options, "--output"));
        if (!outputPath.EndsWith(".vmdl_c", StringComparison.OrdinalIgnoreCase))
        {
            throw new ArgumentException("The model output filename must end with .vmdl_c.");
        }
        if (string.Equals(inputPath, outputPath, StringComparison.OrdinalIgnoreCase))
        {
            throw new ArgumentException("Input and output model paths must be different.");
        }

        var attachments = RequireOption(options, "--attachments")
            .Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .Distinct(StringComparer.Ordinal)
            .ToArray();
        if (attachments.Length == 0 || attachments.Any(name => name.Length == 0))
        {
            throw new ArgumentException("At least one attachment name is required.");
        }
        var offset = new[]
        {
            ParseFiniteDouble(options, "--offset-x"),
            ParseFiniteDouble(options, "--offset-y"),
            ParseFiniteDouble(options, "--offset-z"),
        };
        if (offset.All(value => value == 0.0))
        {
            throw new ArgumentException("The attachment offset must not be zero.");
        }

        var result = ModelAttachmentOffsetter.Offset(
            inputPath,
            outputPath,
            attachments,
            offset);
        Console.WriteLine(
            $"{{\"attachments\":{result.Attachments},"
            + $"\"offset_x\":{result.OffsetX.ToString(System.Globalization.CultureInfo.InvariantCulture)},"
            + $"\"offset_y\":{result.OffsetY.ToString(System.Globalization.CultureInfo.InvariantCulture)},"
            + $"\"offset_z\":{result.OffsetZ.ToString(System.Globalization.CultureInfo.InvariantCulture)},"
            + $"\"output_bytes\":{result.OutputBytes}}}");
        return 0;
    }

    private static double ParseFiniteDouble(
        IReadOnlyDictionary<string, string> options,
        string name)
    {
        if (!double.TryParse(
                RequireOption(options, name),
                System.Globalization.NumberStyles.Float,
                System.Globalization.CultureInfo.InvariantCulture,
                out var value)
            || !double.IsFinite(value))
        {
            throw new ArgumentException($"Required option is not a finite number: {name}");
        }
        return value;
    }

    private static int ComposeModels(string[] args)
    {
        var options = ParseOptions(
            args,
            "--primary",
            "--secondary",
            "--output",
            "--mode");
        var primaryPath = RequireFile(options, "--primary", ".vmdl_c");
        var secondaryPath = RequireFile(options, "--secondary", ".vmdl_c");
        var outputPath = Path.GetFullPath(RequireOption(options, "--output"));
        if (!outputPath.EndsWith(".vmdl_c", StringComparison.OrdinalIgnoreCase))
        {
            throw new ArgumentException("The model output filename must end with .vmdl_c.");
        }
        if (string.Equals(primaryPath, secondaryPath, StringComparison.OrdinalIgnoreCase))
        {
            throw new ArgumentException("The primary and secondary models must be different files.");
        }
        if (string.Equals(primaryPath, outputPath, StringComparison.OrdinalIgnoreCase)
            || string.Equals(secondaryPath, outputPath, StringComparison.OrdinalIgnoreCase))
        {
            throw new ArgumentException("The composed output must not overwrite an input model.");
        }

        var modeName = options.GetValueOrDefault("--mode", "shared-root");
        var mode = modeName switch
        {
            "shared-root" => ModelCompositionMode.SharedRoot,
            "skeleton-overlay" => ModelCompositionMode.SkeletonOverlay,
            "skeleton-union" => ModelCompositionMode.SkeletonUnion,
            _ => throw new ArgumentException($"Unsupported composition mode: {modeName}"),
        };
        var result = ModelComposer.Compose(primaryPath, secondaryPath, outputPath, mode);
        Console.WriteLine(
            $"{{\"mode\":\"{modeName}\","
            + $"\"primary_meshes\":{result.PrimaryMeshes},"
            + $"\"secondary_meshes\":{result.SecondaryMeshes},"
            + $"\"output_meshes\":{result.OutputMeshes},"
            + $"\"primary_bones\":{result.PrimaryBones},"
            + $"\"secondary_bones\":{result.SecondaryBones},"
            + $"\"shared_bones\":{result.SharedBones},"
            + $"\"output_bones\":{result.OutputBones},"
            + $"\"remapped_bone_references\":{result.RemappedBoneReferences},"
            + $"\"output_references\":{result.OutputReferences},"
            + $"\"output_bytes\":{result.OutputBytes}}}");
        return 0;
    }

    private static int PatchBatch(string[] args)
    {
        var (filteredArgs, progressEnabled) = RemoveFlag(args, "--progress");
        args = filteredArgs;
        var options = ParseOptions(args, "--manifest");
        var manifestPath = RequireFile(options, "--manifest", ".tsv");
        var lines = File.ReadAllLines(manifestPath);
        if (lines.Length == 0)
        {
            throw new ArgumentException("The patch manifest is empty.");
        }
        var patched = 0;
        foreach (var line in lines)
        {
            var fields = line.Split('\t');
            if (fields.Length != 3 || fields.Any(field => string.IsNullOrWhiteSpace(field)))
            {
                throw new ArgumentException("Each patch manifest line must contain input, output, and group count.");
            }
            var result = PatchModel(
                ["--input", fields[0], "--output", fields[1], "--groups", fields[2]]);
            if (result != 0)
            {
                return result;
            }
            patched++;
            ReportProgress(progressEnabled, "patch", patched, lines.Length);
        }
        Console.WriteLine($"{{\"patched\":{patched}}}");
        return 0;
    }

    private static int PatchModel(string[] args)
    {
        var options = ParseOptions(args, "--input", "--output", "--groups");
        var inputPath = RequireFile(options, "--input", ".vmdl_c");
        var outputPath = Path.GetFullPath(RequireOption(options, "--output"));
        if (!outputPath.EndsWith(".vmdl_c", StringComparison.OrdinalIgnoreCase))
        {
            throw new ArgumentException("The model output filename must end with .vmdl_c.");
        }
        if (string.Equals(inputPath, outputPath, StringComparison.OrdinalIgnoreCase))
        {
            throw new ArgumentException("Input and output model paths must be different.");
        }
        if (!int.TryParse(RequireOption(options, "--groups"), out var requiredGroups)
            || requiredGroups is < 2 or > 16)
        {
            throw new ArgumentException("Required material groups must be between 2 and 16.");
        }

        var originalBytes = File.ReadAllBytes(inputPath);
        using var resource = new Resource();
        resource.Read(inputPath);
        if (resource.DataBlock is not Model model)
        {
            throw new InvalidDataException("The input DATA block is not a compiled Source 2 model.");
        }

        var groups = GetOrCreateMaterialGroups(model);
        var originalGroupCount = groups.Count;
        if (originalGroupCount < requiredGroups)
        {
            AddBaseMaterialGroups(model, groups, originalBytes, requiredGroups);
        }

        var outputDirectory = Path.GetDirectoryName(outputPath)
            ?? throw new InvalidOperationException("The output path has no parent directory.");
        Directory.CreateDirectory(outputDirectory);
        var temporaryPath = Path.Combine(
            outputDirectory,
            $".{Path.GetFileNameWithoutExtension(outputPath)}.{Guid.NewGuid():N}.vmdl_c");
        try
        {
            if (originalGroupCount >= requiredGroups)
            {
                File.WriteAllBytes(temporaryPath, originalBytes);
            }
            else
            {
                SerializePreservingOpaqueBlocks(
                    resource,
                    model,
                    originalBytes,
                    temporaryPath);
            }
            var finalGroupCount = VerifyPatchedModel(
                resource,
                originalBytes,
                temporaryPath,
                requiredGroups);
            File.Move(temporaryPath, outputPath, overwrite: true);
            Console.WriteLine(
                $"{{\"input_groups\":{originalGroupCount},"
                + $"\"output_groups\":{finalGroupCount},"
                + $"\"required_groups\":{requiredGroups},"
                + $"\"modified\":{(originalGroupCount < requiredGroups ? "true" : "false")},"
                + $"\"output_bytes\":{new FileInfo(outputPath).Length}}}");
            return 0;
        }
        finally
        {
            if (File.Exists(temporaryPath))
            {
                File.Delete(temporaryPath);
            }
        }
    }

    private static KVObject GetOrCreateMaterialGroups(Model model)
    {
        if (model.Data.Properties.TryGetValue("m_materialGroups", out var value))
        {
            if (value.Value is not KVObject existing || !existing.IsArray)
            {
                throw new InvalidDataException("The model material-group field is malformed.");
            }
            return existing;
        }

        var created = new KVObject(null, isArray: true);
        model.Data.AddProperty("m_materialGroups", new KVValue(created));
        return created;
    }

    private static void AddBaseMaterialGroups(
        Model model,
        KVObject groups,
        byte[] originalBytes,
        int requiredGroups)
    {
        KVObject baseGroup;
        if (groups.Count > 0)
        {
            baseGroup = groups[0].Value as KVObject
                ?? throw new InvalidDataException("The base material group is malformed.");
        }
        else
        {
            var materialPaths = MaterialReferencePattern()
                .Matches(Encoding.ASCII.GetString(originalBytes))
                .Select(match => NormalizeMaterialPath(match.Value))
                .Distinct(StringComparer.Ordinal)
                .ToArray();
            if (materialPaths.Length == 0)
            {
                throw new InvalidDataException(
                    "The model has no explicit group table or base material references.");
            }
            baseGroup = CreateMaterialGroup("0", materialPaths);
            groups.AddProperty(null, new KVValue(baseGroup));
        }

        while (groups.Count < requiredGroups)
        {
            groups.AddProperty(
                null,
                new KVValue(CloneMaterialGroup(baseGroup, groups.Count.ToString())));
        }
    }

    private static KVObject CreateMaterialGroup(string name, IEnumerable<string> materialPaths)
    {
        var materials = new KVObject(null, isArray: true);
        foreach (var materialPath in materialPaths)
        {
            materials.AddProperty(
                null,
                new KVValue(KVValueType.String, KVFlag.Resource, materialPath));
        }
        return new KVObject(null, ("m_name", name), ("m_materials", materials));
    }

    private static KVObject CloneMaterialGroup(KVObject source, string name)
    {
        var clone = new KVObject(null);
        foreach (var property in source.Properties)
        {
            clone.AddProperty(property.Key, property.Value);
        }
        if (!clone.Properties.ContainsKey("m_name"))
        {
            throw new InvalidDataException("The base material group has no name.");
        }
        clone.Properties["m_name"] = new KVValue(name);
        return clone;
    }

    private static string NormalizeMaterialPath(string input)
    {
        var normalized = input.Replace('\\', '/').ToLowerInvariant();
        return normalized.EndsWith(".vmat_c", StringComparison.Ordinal)
            ? normalized[..^2]
            : normalized;
    }

    private static void SerializePreservingOpaqueBlocks(
        Resource resource,
        Model model,
        byte[] originalBytes,
        string outputPath)
    {
        using var output = File.Create(outputPath);
        using var writer = new BinaryWriter(output, Encoding.UTF8, leaveOpen: true);

        writer.Write(0xDEADBEEF);
        writer.Write(resource.HeaderVersion);
        writer.Write(resource.Version);
        writer.Write(8);
        writer.Write(resource.Blocks.Count);
        var blocksStart = output.Position + sizeof(uint);
        foreach (var block in resource.Blocks)
        {
            writer.Write((uint)block.Type);
            writer.Write(0xDEADBEEF);
            writer.Write(0xDEADBEEF);
        }
        writer.Flush();

        for (var index = 0; index < resource.Blocks.Count; index++)
        {
            var padding = checked((int)((16 - output.Position % 16) % 16));
            writer.Write(new byte[padding]);
            var blockOffset = output.Position;
            var block = resource.Blocks[index];
            if (block.Type == BlockType.DATA)
            {
                model.Serialize(output);
            }
            else
            {
                output.Write(
                    originalBytes,
                    checked((int)block.Offset),
                    checked((int)block.Size));
            }
            output.Flush();

            var blockEnd = output.Position;
            var metadataOffset = blocksStart + index * 12;
            output.Position = metadataOffset;
            writer.Write(checked((uint)(blockOffset - metadataOffset)));
            writer.Write(checked((uint)(blockEnd - blockOffset)));
            writer.Flush();
            output.Position = blockEnd;
        }

        var fileSize = output.Position;
        output.SetLength(fileSize);
        output.Position = 0;
        writer.Write(checked((uint)fileSize));
        writer.Flush();
    }

    private static int VerifyPatchedModel(
        Resource original,
        byte[] originalBytes,
        string outputPath,
        int requiredGroups)
    {
        var outputBytes = File.ReadAllBytes(outputPath);
        using var patched = new Resource();
        patched.Read(outputPath);
        if (patched.DataBlock is not Model patchedModel)
        {
            throw new InvalidDataException("The patched output no longer parses as a model.");
        }
        var finalGroupCount = patchedModel.GetMaterialGroups().Count();
        if (finalGroupCount < requiredGroups)
        {
            throw new InvalidDataException("The patched output has too few material groups.");
        }
        if (original.Blocks.Count != patched.Blocks.Count)
        {
            throw new InvalidDataException("The patched output changed the resource block count.");
        }
        for (var index = 0; index < original.Blocks.Count; index++)
        {
            var before = original.Blocks[index];
            var after = patched.Blocks[index];
            if (before.Type != after.Type)
            {
                throw new InvalidDataException("The patched output changed a resource block type.");
            }
            if (before.Type == BlockType.DATA)
            {
                continue;
            }
            var beforeBytes = originalBytes.AsSpan(
                checked((int)before.Offset),
                checked((int)before.Size));
            var afterBytes = outputBytes.AsSpan(
                checked((int)after.Offset),
                checked((int)after.Size));
            if (!beforeBytes.SequenceEqual(afterBytes))
            {
                throw new InvalidDataException(
                    $"The patched output changed opaque block {before.Type}.");
            }
        }
        return finalGroupCount;
    }

    private static Dictionary<string, string> ParseOptions(
        string[] args,
        params string[] allowedOptions)
    {
        if (args.Length == 0 || args.Length % 2 != 0)
        {
            throw new ArgumentException("Options must be supplied as name/value pairs.");
        }
        var allowed = allowedOptions.ToHashSet(StringComparer.Ordinal);
        var options = new Dictionary<string, string>(StringComparer.Ordinal);
        for (var index = 0; index < args.Length; index += 2)
        {
            if (!allowed.Contains(args[index]))
            {
                throw new ArgumentException($"Unknown option: {args[index]}");
            }
            if (!options.TryAdd(args[index], args[index + 1]))
            {
                throw new ArgumentException($"Option supplied more than once: {args[index]}");
            }
        }
        return options;
    }

    private static (string[] Arguments, bool Found) RemoveFlag(string[] args, string flag)
    {
        var count = args.Count(argument => string.Equals(argument, flag, StringComparison.Ordinal));
        if (count > 1)
        {
            throw new ArgumentException($"Flag was supplied more than once: {flag}");
        }
        return (
            args.Where(argument => !string.Equals(argument, flag, StringComparison.Ordinal)).ToArray(),
            count == 1);
    }

    private static void ReportProgress(bool enabled, string phase, int completed, int total)
    {
        if (!enabled)
        {
            return;
        }
        Console.WriteLine(
            $"{{\"progress\":{{\"phase\":\"{phase}\",\"completed\":{completed},\"total\":{total}}}}}");
        Console.Out.Flush();
    }

    private static string RequireOption(IReadOnlyDictionary<string, string> options, string name)
    {
        if (!options.TryGetValue(name, out var value) || string.IsNullOrWhiteSpace(value))
        {
            throw new ArgumentException($"Required option is missing: {name}");
        }
        return value;
    }

    private static string RequireFile(
        IReadOnlyDictionary<string, string> options,
        string name,
        string extension)
    {
        var value = Path.GetFullPath(RequireOption(options, name));
        if (!value.EndsWith(extension, StringComparison.OrdinalIgnoreCase))
        {
            throw new ArgumentException($"Input filename must end with {extension}.");
        }
        if (!File.Exists(value))
        {
            throw new FileNotFoundException($"File not found for {name}: {value}");
        }
        return value;
    }

    [GeneratedRegex(
        @"materials[/\\][a-zA-Z0-9_./\\-]+\.vmat(?:_c)?",
        RegexOptions.IgnoreCase | RegexOptions.CultureInvariant)]
    private static partial Regex MaterialReferencePattern();
}
