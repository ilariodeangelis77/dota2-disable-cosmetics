using System.Buffers.Binary;
using System.Text;
using System.Text.RegularExpressions;
using ValveResourceFormat;
using ValveResourceFormat.Blocks;
using ValveResourceFormat.ResourceTypes;
using ValveResourceFormat.Serialization.KeyValues;

namespace Dota2CosmeticDisabler.ModelPatcher;

internal sealed record ParticleBridgeResult(
    int InputReferences,
    int OutputReferences,
    int ParticleConfigs,
    long OutputBytes);

internal static class ModelParticleBridge
{
    private const ulong ResourceIdSeed = 0xEDABCDEF;

    public static ParticleBridgeResult Build(
        string inputPath,
        string templatePath,
        string outputPath,
        string templateParticle,
        string privateParticle)
    {
        templateParticle = NormalizeParticlePath(templateParticle);
        privateParticle = NormalizeParticlePath(privateParticle);
        if (string.Equals(templateParticle, privateParticle, StringComparison.Ordinal))
        {
            throw new ArgumentException(
                "The private particle path must differ from the template particle path.");
        }

        var originalBytes = File.ReadAllBytes(inputPath);
        using var resource = new Resource();
        resource.Read(inputPath);
        var model = RequireModel(resource, "input");
        var references = RequireReferences(resource, "input");

        using var templateResource = new Resource();
        templateResource.Read(templatePath);
        var templateModel = RequireModel(templateResource, "template");
        var templateReferences = RequireReferences(templateResource, "template");

        var inputTextProperty = RequireKeyValueText(model, "input");
        if (!string.IsNullOrWhiteSpace((string?)inputTextProperty.Value))
        {
            throw new InvalidDataException(
                "The input model already has model key-value configuration.");
        }

        var templateTextProperty = RequireKeyValueText(templateModel, "template");
        var templateText = (string?)templateTextProperty.Value ?? string.Empty;
        var particleMatches = Regex.Matches(
            templateText,
            "resource:\\s*\\\"(?<path>particles[/a-zA-Z0-9_.-]+\\.vpcf)\\\"",
            RegexOptions.CultureInvariant);
        var assignmentNames = Regex.Matches(
                templateText,
                "^\\s*(?<name>[a-zA-Z0-9_]+)\\s*=",
                RegexOptions.CultureInvariant | RegexOptions.Multiline)
            .Select(match => match.Groups["name"].Value)
            .ToArray();
        if (particleMatches.Count != 1
            || !assignmentNames.SequenceEqual(
                ["particle_cfg_list", "name", "config"],
                StringComparer.Ordinal)
            || !templateText.Contains("particle_cfg_list", StringComparison.Ordinal)
            || !templateText.Contains("config = \"preview\"", StringComparison.Ordinal))
        {
            throw new InvalidDataException(
                "The template model does not have one reviewed preview particle configuration.");
        }
        var configuredTemplateParticle = NormalizeParticlePath(
            particleMatches[0].Groups["path"].Value);
        if (!string.Equals(
                configuredTemplateParticle,
                templateParticle,
                StringComparison.Ordinal))
        {
            throw new InvalidDataException(
                "The template model particle configuration changed.");
        }
        RequireReference(templateReferences, templateParticle, "template");

        var privateText = templateText.Replace(
            particleMatches[0].Groups["path"].Value,
            privateParticle,
            StringComparison.Ordinal);
        var inputModelInfo = model.Data.GetSubCollection("m_modelInfo")
            ?? throw new InvalidDataException("The input model has no model-info object.");
        inputModelInfo.Properties["m_keyValueText"] = new KVValue(
            inputTextProperty.Type,
            inputTextProperty.Flag,
            privateText);

        var outputReferences = CloneReferencesWithParticle(references, privateParticle);
        var outputDirectory = Path.GetDirectoryName(outputPath)
            ?? throw new InvalidOperationException("The output path has no parent directory.");
        Directory.CreateDirectory(outputDirectory);
        var temporaryPath = Path.Combine(
            outputDirectory,
            $".{Path.GetFileNameWithoutExtension(outputPath)}.{Guid.NewGuid():N}.vmdl_c");
        try
        {
            WriteResource(
                resource,
                model,
                references,
                outputReferences,
                originalBytes,
                temporaryPath);
            Verify(
                inputPath,
                resource,
                originalBytes,
                temporaryPath,
                privateText,
                inputTextProperty,
                outputReferences);
            File.Move(temporaryPath, outputPath, overwrite: true);
            return new ParticleBridgeResult(
                references.ResourceRefInfoList.Count,
                outputReferences.ResourceRefInfoList.Count,
                1,
                new FileInfo(outputPath).Length);
        }
        finally
        {
            if (File.Exists(temporaryPath))
            {
                File.Delete(temporaryPath);
            }
        }
    }

    private static Model RequireModel(Resource resource, string label) =>
        resource.DataBlock as Model
            ?? throw new InvalidDataException(
                $"The {label} DATA block is not a compiled Source 2 model.");

    private static ResourceExtRefList RequireReferences(Resource resource, string label)
    {
        var matches = resource.Blocks.OfType<ResourceExtRefList>().ToArray();
        if (matches.Length != 1)
        {
            throw new InvalidDataException(
                $"The {label} model must have exactly one resource-reference block.");
        }
        return matches[0];
    }

    private static KVValue RequireKeyValueText(Model model, string label)
    {
        var modelInfo = model.Data.GetSubCollection("m_modelInfo")
            ?? throw new InvalidDataException($"The {label} model has no model-info object.");
        if (!modelInfo.Properties.TryGetValue("m_keyValueText", out var value)
            || value.Value is not string)
        {
            throw new InvalidDataException(
                $"The {label} model has no string model key-value field.");
        }
        return value;
    }

    private static void RequireReference(
        ResourceExtRefList references,
        string particle,
        string label)
    {
        var id = ComputeResourceId(particle);
        if (!references.ResourceRefInfoList.Any(reference =>
                reference.Id == id
                && string.Equals(reference.Name, particle, StringComparison.Ordinal)))
        {
            throw new InvalidDataException(
                $"The {label} model does not reference the reviewed particle resource.");
        }
    }

    private static ResourceExtRefList CloneReferencesWithParticle(
        ResourceExtRefList input,
        string particle)
    {
        var byId = new Dictionary<ulong, string>();
        var byName = new Dictionary<string, ulong>(StringComparer.Ordinal);
        foreach (var reference in input.ResourceRefInfoList)
        {
            if (!byId.TryAdd(reference.Id, reference.Name)
                || !byName.TryAdd(reference.Name, reference.Id))
            {
                throw new InvalidDataException(
                    "The input model contains duplicate resource references.");
            }
        }

        var id = ComputeResourceId(particle);
        if (byId.TryGetValue(id, out var existingName)
            && !string.Equals(existingName, particle, StringComparison.Ordinal))
        {
            throw new InvalidDataException(
                "The private particle resource id collides with an input reference.");
        }
        if (byName.TryGetValue(particle, out var existingId) && existingId != id)
        {
            throw new InvalidDataException(
                "The private particle path has an inconsistent resource id.");
        }
        byId[id] = particle;

        var result = new ResourceExtRefList();
        foreach (var pair in byId.OrderBy(pair => pair.Key))
        {
            result.ResourceRefInfoList.Add(new ResourceExtRefList.ResourceReferenceInfo
            {
                Id = pair.Key,
                Name = pair.Value,
            });
        }
        return result;
    }

    private static string NormalizeParticlePath(string input)
    {
        var normalized = input.Replace('\\', '/').Trim().ToLowerInvariant();
        var segments = normalized.Split('/');
        if (!normalized.StartsWith("particles/", StringComparison.Ordinal)
            || !normalized.EndsWith(".vpcf", StringComparison.Ordinal)
            || normalized.Contains(':', StringComparison.Ordinal)
            || segments.Any(segment => segment is "" or "." or ".."))
        {
            throw new ArgumentException($"Unsafe particle resource path: {input}");
        }
        return normalized;
    }

    internal static ulong ComputeResourceId(string resourcePath)
    {
        var data = Encoding.UTF8.GetBytes(resourcePath.ToLowerInvariant());
        const uint multiplier = 0x5bd1e995;
        const int shift = 24;
        unchecked
        {
            var h1 = (uint)ResourceIdSeed ^ (uint)data.Length;
            var h2 = (uint)(ResourceIdSeed >> 32);
            var offset = 0;
            var remaining = data.Length;
            while (remaining >= 8)
            {
                var k1 = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(offset, 4));
                k1 *= multiplier;
                k1 ^= k1 >> shift;
                k1 *= multiplier;
                h1 *= multiplier;
                h1 ^= k1;
                offset += 4;
                remaining -= 4;

                var k2 = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(offset, 4));
                k2 *= multiplier;
                k2 ^= k2 >> shift;
                k2 *= multiplier;
                h2 *= multiplier;
                h2 ^= k2;
                offset += 4;
                remaining -= 4;
            }
            if (remaining >= 4)
            {
                var k1 = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(offset, 4));
                k1 *= multiplier;
                k1 ^= k1 >> shift;
                k1 *= multiplier;
                h1 *= multiplier;
                h1 ^= k1;
                offset += 4;
                remaining -= 4;
            }
            if (remaining == 3)
            {
                h2 ^= (uint)data[offset + 2] << 16;
            }
            if (remaining >= 2)
            {
                h2 ^= (uint)data[offset + 1] << 8;
            }
            if (remaining >= 1)
            {
                h2 ^= data[offset];
                h2 *= multiplier;
            }
            h1 ^= h2 >> 18;
            h1 *= multiplier;
            h2 ^= h1 >> 22;
            h2 *= multiplier;
            h1 ^= h2 >> 17;
            h1 *= multiplier;
            h2 ^= h1 >> 19;
            h2 *= multiplier;
            return ((ulong)h1 << 32) | h2;
        }
    }

    private static void WriteResource(
        Resource resource,
        Model model,
        ResourceExtRefList inputReferences,
        ResourceExtRefList outputReferences,
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
            writer.Write(new byte[checked((int)((16 - output.Position % 16) % 16))]);
            var blockOffset = output.Position;
            var block = resource.Blocks[index];
            if (block.Type == BlockType.DATA)
            {
                model.Serialize(output);
            }
            else if (ReferenceEquals(block, inputReferences))
            {
                outputReferences.Serialize(output);
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

        output.SetLength(output.Position);
        output.Position = 0;
        writer.Write(checked((uint)output.Length));
        writer.Flush();
    }

    private static void Verify(
        string inputPath,
        Resource original,
        byte[] originalBytes,
        string outputPath,
        string expectedKeyValueText,
        KVValue originalKeyValueText,
        ResourceExtRefList expectedReferences)
    {
        var outputBytes = File.ReadAllBytes(outputPath);
        using var source = new Resource();
        source.Read(inputPath);
        using var output = new Resource();
        output.Read(outputPath);
        var outputModel = RequireModel(output, "output");
        var outputReferences = RequireReferences(output, "output");
        var outputText = RequireKeyValueText(outputModel, "output");
        if (!string.Equals(
                (string?)outputText.Value,
                expectedKeyValueText,
                StringComparison.Ordinal))
        {
            throw new InvalidDataException(
                "The particle-bridge output has the wrong model key-value configuration.");
        }

        var actualReferenceValues = outputReferences.ResourceRefInfoList
            .Select(reference => (reference.Id, reference.Name))
            .OrderBy(reference => reference.Id)
            .ToArray();
        var expectedReferenceValues = expectedReferences.ResourceRefInfoList
            .Select(reference => (reference.Id, reference.Name))
            .OrderBy(reference => reference.Id)
            .ToArray();
        if (!actualReferenceValues.SequenceEqual(expectedReferenceValues))
        {
            throw new InvalidDataException(
                "The particle-bridge output has inconsistent resource references.");
        }
        foreach (var reference in outputReferences.ResourceRefInfoList)
        {
            if (reference.Id != ComputeResourceId(reference.Name))
            {
                throw new InvalidDataException(
                    "The particle-bridge output contains an invalid resource id.");
            }
        }

        if (original.HeaderVersion != output.HeaderVersion
            || original.Version != output.Version
            || original.Blocks.Count != output.Blocks.Count)
        {
            throw new InvalidDataException(
                "The particle-bridge output changed the compiled-resource structure.");
        }
        for (var index = 0; index < original.Blocks.Count; index++)
        {
            var before = original.Blocks[index];
            var after = output.Blocks[index];
            if (before.Type != after.Type)
            {
                throw new InvalidDataException(
                    "The particle-bridge output changed a resource block type.");
            }
            if (before.Type is BlockType.DATA or BlockType.RERL)
            {
                continue;
            }
            if (!originalBytes.AsSpan(checked((int)before.Offset), checked((int)before.Size))
                .SequenceEqual(outputBytes.AsSpan(
                    checked((int)after.Offset),
                    checked((int)after.Size))))
            {
                throw new InvalidDataException(
                    $"The particle-bridge output changed opaque block {before.Type}.");
            }
        }

        var sourceModel = RequireModel(source, "source verification");
        var outputModelInfo = outputModel.Data.GetSubCollection("m_modelInfo")
            ?? throw new InvalidDataException("The output model has no model-info object.");
        outputModelInfo.Properties["m_keyValueText"] = new KVValue(
            originalKeyValueText.Type,
            originalKeyValueText.Flag,
            (string)originalKeyValueText.Value!);
        if (!SerializeKeyValues(sourceModel.Data).Equals(
                SerializeKeyValues(outputModel.Data),
                StringComparison.Ordinal))
        {
            throw new InvalidDataException(
                "The particle-bridge output changed unrelated model data.");
        }
    }

    private static string SerializeKeyValues(KVObject data)
    {
        using var writer = new IndentedTextWriter();
        data.Serialize(writer);
        return writer.ToString();
    }
}
