using System.Globalization;
using System.Text;
using ValveResourceFormat;
using ValveResourceFormat.ResourceTypes;
using ValveResourceFormat.Serialization.KeyValues;

namespace Dota2CosmeticDisabler.ModelPatcher;

internal sealed record AttachmentOffsetResult(
    int Attachments,
    double OffsetX,
    double OffsetY,
    double OffsetZ,
    long OutputBytes);

internal static class ModelAttachmentOffsetter
{
    public static AttachmentOffsetResult Offset(
        string inputPath,
        string outputPath,
        IReadOnlyCollection<string> attachmentNames,
        IReadOnlyList<double> offset)
    {
        var originalBytes = File.ReadAllBytes(inputPath);
        using var resource = new Resource();
        resource.Read(inputPath);
        var meshBlocks = resource.Blocks
            .Where(block => block.Type == BlockType.MDAT)
            .OfType<Mesh>()
            .ToArray();
        if (meshBlocks.Length != 1)
        {
            throw new InvalidDataException(
                "Attachment offsets require a model with exactly one embedded mesh.");
        }

        var mesh = meshBlocks[0];
        var attachmentSet = attachmentNames.ToHashSet(StringComparer.Ordinal);
        var adjusted = OffsetAttachments(mesh, attachmentSet, offset);
        if (adjusted != attachmentSet.Count)
        {
            var missing = attachmentSet
                .Except(AttachmentNames(mesh), StringComparer.Ordinal)
                .Order(StringComparer.Ordinal);
            throw new InvalidDataException(
                $"The model is missing reviewed attachment(s): {string.Join(", ", missing)}");
        }

        var outputDirectory = Path.GetDirectoryName(outputPath)
            ?? throw new InvalidOperationException("The output path has no parent directory.");
        Directory.CreateDirectory(outputDirectory);
        var temporaryPath = Path.Combine(
            outputDirectory,
            $".{Path.GetFileNameWithoutExtension(outputPath)}.{Guid.NewGuid():N}.vmdl_c");
        try
        {
            WriteAdjustedResource(resource, mesh, originalBytes, temporaryPath);
            Verify(
                inputPath,
                resource,
                originalBytes,
                temporaryPath,
                attachmentSet,
                offset);
            File.Move(temporaryPath, outputPath, overwrite: true);
            return new AttachmentOffsetResult(
                adjusted,
                offset[0],
                offset[1],
                offset[2],
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

    private static int OffsetAttachments(
        Mesh mesh,
        IReadOnlySet<string> attachmentNames,
        IReadOnlyList<double> offset)
    {
        var adjusted = 0;
        foreach (var entry in GetArray(mesh.Data, "m_attachments").Properties.Values)
        {
            var wrapper = RequireObject(entry, "attachment");
            var attachment = wrapper.GetSubCollection("value") ?? wrapper;
            var name = attachment.GetStringProperty("m_name");
            if (!attachmentNames.Contains(name))
            {
                continue;
            }

            var influenceOffsets = GetArray(attachment, "m_vInfluenceOffsets");
            var influenceCount = checked((int)attachment.GetIntegerProperty("m_nInfluences"));
            if (influenceCount < 1 || influenceOffsets.Count < influenceCount)
            {
                throw new InvalidDataException(
                    $"Attachment {name} has no usable influence offset.");
            }
            for (var influenceIndex = 0; influenceIndex < influenceCount; influenceIndex++)
            {
                var vector = RequireObject(
                    influenceOffsets[influenceIndex],
                    $"attachment {name} influence offset");
                if (vector.Count != 3)
                {
                    throw new InvalidDataException(
                        $"Attachment {name} has a malformed influence offset.");
                }
                for (var axis = 0; axis < 3; axis++)
                {
                    var key = axis.ToString(CultureInfo.InvariantCulture);
                    var coordinate = vector.Properties[key];
                    var value = Convert.ToDouble(
                        coordinate.Value,
                        CultureInfo.InvariantCulture) + offset[axis];
                    vector.Properties[key] = coordinate.Type switch
                    {
                        ValveKeyValue.KVValueType.FloatingPoint => new KVValue(
                            coordinate.Type,
                            coordinate.Flag,
                            Convert.ToSingle(value, CultureInfo.InvariantCulture)),
                        ValveKeyValue.KVValueType.FloatingPoint64 => new KVValue(
                            coordinate.Type,
                            coordinate.Flag,
                            value),
                        _ => throw new InvalidDataException(
                            $"Attachment {name} uses a non-floating-point offset."),
                    };
                }
            }
            adjusted++;
        }
        return adjusted;
    }

    private static IEnumerable<string> AttachmentNames(Mesh mesh) =>
        GetArray(mesh.Data, "m_attachments").Properties.Values.Select(entry =>
        {
            var wrapper = RequireObject(entry, "attachment");
            return (wrapper.GetSubCollection("value") ?? wrapper)
                .GetStringProperty("m_name");
        });

    private static void WriteAdjustedResource(
        Resource resource,
        Mesh adjustedMesh,
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
            if (ReferenceEquals(block, adjustedMesh))
            {
                adjustedMesh.Serialize(output);
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
        IReadOnlySet<string> attachmentNames,
        IReadOnlyList<double> offset)
    {
        var outputBytes = File.ReadAllBytes(outputPath);
        using var source = new Resource();
        source.Read(inputPath);
        using var adjusted = new Resource();
        adjusted.Read(outputPath);
        if (adjusted.HeaderVersion != original.HeaderVersion
            || adjusted.Version != original.Version
            || adjusted.Blocks.Count != original.Blocks.Count)
        {
            throw new InvalidDataException(
                "The adjusted output changed the compiled-resource structure.");
        }

        for (var index = 0; index < original.Blocks.Count; index++)
        {
            var before = original.Blocks[index];
            var after = adjusted.Blocks[index];
            if (before.Type != after.Type)
            {
                throw new InvalidDataException(
                    "The adjusted output changed a resource block type.");
            }
            if (before.Type == BlockType.MDAT)
            {
                continue;
            }
            if (!originalBytes.AsSpan(checked((int)before.Offset), checked((int)before.Size))
                .SequenceEqual(outputBytes.AsSpan(
                    checked((int)after.Offset),
                    checked((int)after.Size))))
            {
                throw new InvalidDataException(
                    $"The adjusted output changed opaque block {before.Type}.");
            }
        }

        var adjustedMesh = adjusted.Blocks
            .Where(block => block.Type == BlockType.MDAT)
            .OfType<Mesh>()
            .Single();
        var sourceMesh = source.Blocks
            .Where(block => block.Type == BlockType.MDAT)
            .OfType<Mesh>()
            .Single();
        foreach (var name in attachmentNames)
        {
            if (!adjustedMesh.Attachments.TryGetValue(name, out var attachment))
            {
                throw new InvalidDataException(
                    $"The adjusted output lost attachment {name}.");
            }
            if (attachment.Length < 1)
            {
                throw new InvalidDataException(
                    $"The adjusted output has no influence for attachment {name}.");
            }
            if (!sourceMesh.Attachments.TryGetValue(name, out var sourceAttachment)
                || sourceAttachment.Length != attachment.Length)
            {
                throw new InvalidDataException(
                    $"The adjusted output changed attachment influences for {name}.");
            }
            for (var index = 0; index < attachment.Length; index++)
            {
                var before = sourceAttachment[index].Offset;
                var after = attachment[index].Offset;
                if (Math.Abs(after.X - (before.X + offset[0])) > 0.001
                    || Math.Abs(after.Y - (before.Y + offset[1])) > 0.001
                    || Math.Abs(after.Z - (before.Z + offset[2])) > 0.001)
                {
                    throw new InvalidDataException(
                        $"The adjusted output has the wrong offset for attachment {name}.");
                }
            }
        }
        if (offset.All(value => value == 0.0))
        {
            throw new InvalidDataException("The verified attachment offset is empty.");
        }
    }

    private static KVObject GetArray(KVObject owner, string key) =>
        owner.Properties.TryGetValue(key, out var value)
            && value.Value is KVObject array
            && array.IsArray
            ? array
            : throw new InvalidDataException($"Expected array property {key}.");

    private static KVObject RequireObject(KVValue value, string label) =>
        value.Value as KVObject
            ?? throw new InvalidDataException($"Expected object for {label}.");
}
