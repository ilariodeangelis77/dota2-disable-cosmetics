using System.Globalization;
using System.Numerics;
using System.Text;
using ValveResourceFormat;
using ValveResourceFormat.Blocks;
using ValveResourceFormat.ResourceTypes;
using ValveResourceFormat.Serialization.KeyValues;

namespace Dota2CosmeticDisabler.ModelPatcher;

internal enum ModelCompositionMode
{
    SharedRoot,
    SkeletonOverlay,
    SkeletonUnion,
}

internal sealed record CompositionResult(
    int PrimaryMeshes,
    int SecondaryMeshes,
    int OutputMeshes,
    int PrimaryBones,
    int SecondaryBones,
    int SharedBones,
    int OutputBones,
    int RemappedBoneReferences,
    int OutputReferences,
    long OutputBytes);

internal static class ModelComposer
{
    private static readonly string[] PerMeshArrayKeys =
    [
        "m_refMeshes",
        "m_refMeshGroupMasks",
        "m_refLODGroupMasks",
    ];

    private static readonly string[] BoneArrayKeys =
    [
        "m_boneName",
        "m_nParent",
        "m_boneSphere",
        "m_nFlag",
        "m_bonePosParent",
        "m_boneRotParent",
        "m_boneScaleParent",
    ];

    private static readonly string[] UnsupportedArrayKeys =
    [
        "m_ExtParts",
        "m_refPhysGroupMasks",
        "m_refPhysicsData",
        "m_refPhysicsHitboxData",
        "m_refAnimGroups",
        "m_refSequenceGroups",
        "m_meshGroups",
        "m_materialGroups",
        "m_boneFlexDrivers",
        "m_BodyGroupsHiddenInTools",
        "m_refAnimIncludeModels",
        "m_AnimatedMaterialAttributes",
    ];

    public static CompositionResult Compose(
        string primaryPath,
        string secondaryPath,
        string outputPath,
        ModelCompositionMode mode)
    {
        using var primary = ModelInput.Open(
            primaryPath,
            "primary",
            allowPrimaryPayload: true);
        using var secondary = ModelInput.Open(
            secondaryPath,
            "secondary",
            allowExternalRemapping: mode != ModelCompositionMode.SharedRoot,
            requireSingleRoot: mode != ModelCompositionMode.SkeletonOverlay);
        ValidatePair(primary, secondary, mode);

        var primaryMeshCount = primary.Meshes.Length;
        var secondaryMeshCount = secondary.Meshes.Length;
        var primaryBoneCount = primary.BoneNames.Length;
        var secondaryBoneCount = secondary.BoneNames.Length;
        var expectedMeshes = primary.Meshes
            .Concat(secondary.Meshes.Select(mesh => mesh with
            {
                Index = mesh.Index + primaryMeshCount,
            }))
            .ToArray();
        var expectedOpaqueBlocks = primary.MeshBlocks
            .Select(block => new OpaqueBlock(primary.Bytes, block))
            .Concat(secondary.MeshBlocks.Select(block => new OpaqueBlock(secondary.Bytes, block)))
            .ToArray();

        var secondaryBlockMap = BuildOutputBlockMap(
            secondary.MeshBlocks,
            primary.MeshBlocks.Count);
        var secondaryToCombined = MergeSkeleton(primary, secondary);
        var remappedBoneReferences = MergeModelData(
            primary,
            secondary,
            secondaryToCombined,
            mode);
        MergeControlData(
            primary,
            secondary,
            secondaryBlockMap);
        var references = MergeReferences(primary, secondary);

        var expectedBones = StringValues(
            GetArray(primary.Model.Data.GetSubCollection("m_modelSkeleton"), "m_boneName"));
        var expectedRemapping = IntValues(GetArray(primary.Model.Data, "m_remappingTable"));
        var expectedRemappingStarts = IntValues(
            GetArray(primary.Model.Data, "m_remappingTableStarts"));

        var outputDirectory = Path.GetDirectoryName(outputPath)
            ?? throw new InvalidOperationException("The composed output path has no parent directory.");
        Directory.CreateDirectory(outputDirectory);
        var temporaryPath = Path.Combine(
            outputDirectory,
            $".{Path.GetFileNameWithoutExtension(outputPath)}.{Guid.NewGuid():N}.vmdl_c");
        try
        {
            WriteComposite(
                temporaryPath,
                primary,
                references,
                expectedOpaqueBlocks);
            var outputBytes = VerifyComposite(
                temporaryPath,
                primary,
                expectedMeshes,
                expectedBones,
                expectedRemapping,
                expectedRemappingStarts,
                references,
                expectedOpaqueBlocks,
                mode);
            File.Move(temporaryPath, outputPath, overwrite: true);
            return new CompositionResult(
                primaryMeshCount,
                secondaryMeshCount,
                expectedMeshes.Length,
                primaryBoneCount,
                secondaryBoneCount,
                primaryBoneCount + secondaryBoneCount - expectedBones.Length,
                expectedBones.Length,
                remappedBoneReferences,
                references.ResourceRefInfoList.Count,
                outputBytes);
        }
        finally
        {
            if (File.Exists(temporaryPath))
            {
                File.Delete(temporaryPath);
            }
        }
    }

    private static void ValidatePair(
        ModelInput primary,
        ModelInput secondary,
        ModelCompositionMode mode)
    {
        if (primary.Resource.HeaderVersion != secondary.Resource.HeaderVersion
            || primary.Resource.Version != secondary.Resource.Version)
        {
            throw new InvalidDataException(
                "The models use different compiled-resource versions.");
        }
        if (!WriteKeyValues(primary.Model.Data.GetSubCollection("m_modelInfo")).Equals(
                WriteKeyValues(secondary.Model.Data.GetSubCollection("m_modelInfo")),
                StringComparison.Ordinal))
        {
            throw new InvalidDataException("The models have incompatible model-info metadata.");
        }
        if (!FloatValues(GetArray(primary.Model.Data, "m_lodGroupSwitchDistances"))
            .SequenceEqual(FloatValues(GetArray(secondary.Model.Data, "m_lodGroupSwitchDistances"))))
        {
            throw new InvalidDataException("The models use different LOD switch distances.");
        }
        if (primary.Model.Data.GetUnsignedIntegerProperty("m_nDefaultMeshGroupMask")
            != secondary.Model.Data.GetUnsignedIntegerProperty("m_nDefaultMeshGroupMask"))
        {
            throw new InvalidDataException("The models use different default mesh-group masks.");
        }
        if (mode == ModelCompositionMode.SharedRoot)
        {
            var primaryNames = primary.BoneNames.ToHashSet(StringComparer.Ordinal);
            var shared = secondary.BoneNames.Where(primaryNames.Contains).ToArray();
            if (shared.Length != 1
                || !string.Equals(shared[0], primary.RootBoneName, StringComparison.Ordinal)
                || !string.Equals(shared[0], secondary.RootBoneName, StringComparison.Ordinal))
            {
                throw new InvalidDataException(
                    "Compatible models must share exactly one identically named root bone.");
            }
            ValidateSharedRoot(primary, secondary);
        }
        else if (mode == ModelCompositionMode.SkeletonOverlay)
        {
            ValidateSkeletonOverlay(primary, secondary);
        }
        else
        {
            ValidateSkeletonUnion(primary, secondary);
        }

        var duplicateMeshName = primary.Meshes.Select(mesh => mesh.Name)
            .Intersect(secondary.Meshes.Select(mesh => mesh.Name), StringComparer.Ordinal)
            .FirstOrDefault();
        if (duplicateMeshName is not null)
        {
            throw new InvalidDataException(
                $"The models contain the same embedded mesh name: {duplicateMeshName}.");
        }
    }

    private static void ValidateSkeletonOverlay(ModelInput primary, ModelInput secondary)
    {
        var primaryIndexes = primary.BoneNames
            .Select((name, index) => (name, index))
            .ToDictionary(pair => pair.name, pair => pair.index, StringComparer.Ordinal);
        if (secondary.BoneNames.Any(name => !primaryIndexes.ContainsKey(name)))
        {
            throw new InvalidDataException(
                "A skeleton overlay must use a subset of the primary model bones.");
        }

        var primarySkeleton = primary.Model.Data.GetSubCollection("m_modelSkeleton");
        var secondarySkeleton = secondary.Model.Data.GetSubCollection("m_modelSkeleton");
        var primaryParents = IntValues(GetArray(primarySkeleton, "m_nParent"));
        var secondaryParents = IntValues(GetArray(secondarySkeleton, "m_nParent"));
        var secondaryNames = secondary.BoneNames.ToHashSet(StringComparer.Ordinal);
        var primaryTransforms = BuildWorldBoneTransforms(primary);
        var secondaryTransforms = BuildWorldBoneTransforms(secondary);
        for (var secondaryIndex = 0; secondaryIndex < secondary.BoneNames.Length; secondaryIndex++)
        {
            var name = secondary.BoneNames[secondaryIndex];
            var primaryIndex = primaryIndexes[name];
            var secondaryParent = secondaryParents[secondaryIndex];
            var primaryParent = primaryParents[primaryIndex];
            if (secondaryParent >= 0)
            {
                var expectedParentName = secondary.BoneNames[secondaryParent];
                if (primaryParent < 0
                    || !string.Equals(
                        primary.BoneNames[primaryParent],
                        expectedParentName,
                        StringComparison.Ordinal))
                {
                    throw new InvalidDataException(
                        $"Overlay bone {name} has an incompatible parent.");
                }
            }
            else if (primaryParent >= 0
                && secondaryNames.Contains(primary.BoneNames[primaryParent]))
            {
                throw new InvalidDataException(
                    $"Overlay bone {name} omits a parent that is present in both models.");
            }

            var primaryTransform = primaryTransforms[primaryIndex];
            var secondaryTransform = secondaryTransforms[secondaryIndex];
            var rotationDifference = Math.Min(
                QuaternionDistance(
                    primaryTransform.Rotation,
                    secondaryTransform.Rotation),
                QuaternionDistance(
                    primaryTransform.Rotation,
                    Quaternion.Negate(secondaryTransform.Rotation)));
            if (Vector3.Distance(
                    primaryTransform.Position,
                    secondaryTransform.Position) > 0.1f
                || rotationDifference > 0.001f
                || Math.Abs(primaryTransform.Scale - secondaryTransform.Scale) > 0.001f)
            {
                throw new InvalidDataException(
                    $"Overlay bone {name} has an incompatible bind transform.");
            }
        }
    }

    private static void ValidateSkeletonUnion(ModelInput primary, ModelInput secondary)
    {
        var primaryIndexes = primary.BoneNames
            .Select((name, index) => (name, index))
            .ToDictionary(pair => pair.name, pair => pair.index, StringComparer.Ordinal);
        var secondaryIndexes = secondary.BoneNames
            .Select((name, index) => (name, index))
            .ToDictionary(pair => pair.name, pair => pair.index, StringComparer.Ordinal);
        var sharedNames = primary.BoneNames
            .Where(secondaryIndexes.ContainsKey)
            .ToArray();
        if (sharedNames.Length == 0
            || !primaryIndexes.ContainsKey(secondary.RootBoneName))
        {
            throw new InvalidDataException(
                "A skeleton union must attach at a shared secondary root bone.");
        }

        var primaryTransforms = BuildWorldBoneTransforms(primary);
        var secondaryTransforms = BuildWorldBoneTransforms(secondary);
        foreach (var name in sharedNames)
        {
            var primaryIndex = primaryIndexes[name];
            var secondaryIndex = secondaryIndexes[name];
            var primaryTransform = primaryTransforms[primaryIndex];
            var secondaryTransform = secondaryTransforms[secondaryIndex];
            var rotationDifference = Math.Min(
                QuaternionDistance(primaryTransform.Rotation, secondaryTransform.Rotation),
                QuaternionDistance(
                    primaryTransform.Rotation,
                    Quaternion.Negate(secondaryTransform.Rotation)));
            if (Vector3.Distance(
                    primaryTransform.Position,
                    secondaryTransform.Position) > 0.1f
                || rotationDifference > 0.001f
                || Math.Abs(primaryTransform.Scale - secondaryTransform.Scale) > 0.001f)
            {
                throw new InvalidDataException(
                    $"Shared union bone {name} has an incompatible bind transform.");
            }
        }
    }

    private static BoneTransform[] BuildWorldBoneTransforms(ModelInput input)
    {
        var skeleton = input.Model.Data.GetSubCollection("m_modelSkeleton");
        var parents = IntValues(GetArray(skeleton, "m_nParent"));
        var positions = GetArray(skeleton, "m_bonePosParent");
        var rotations = GetArray(skeleton, "m_boneRotParent");
        var scales = GetArray(skeleton, "m_boneScaleParent");
        var result = new BoneTransform[input.BoneNames.Length];
        for (var index = 0; index < result.Length; index++)
        {
            var localPosition = ReadVector3(positions[index], "bone position");
            var localRotation = ReadQuaternion(rotations[index], "bone rotation");
            var localScale = Convert.ToSingle(
                scales[index].Value,
                CultureInfo.InvariantCulture);
            var parent = parents[index];
            if (parent < 0)
            {
                result[index] = new BoneTransform(
                    localPosition,
                    localRotation,
                    localScale);
                continue;
            }
            var parentTransform = result[parent];
            result[index] = new BoneTransform(
                parentTransform.Position
                    + Vector3.Transform(
                        localPosition * parentTransform.Scale,
                        parentTransform.Rotation),
                Quaternion.Normalize(parentTransform.Rotation * localRotation),
                parentTransform.Scale * localScale);
        }
        return result;
    }

    private static Vector3 ReadVector3(KVValue value, string label)
    {
        var components = DoubleValues(RequireObject(value, label));
        if (components.Length != 3)
        {
            throw new InvalidDataException($"Expected three components for {label}.");
        }
        return new Vector3(
            (float)components[0],
            (float)components[1],
            (float)components[2]);
    }

    private static Quaternion ReadQuaternion(KVValue value, string label)
    {
        var components = DoubleValues(RequireObject(value, label));
        if (components.Length != 4)
        {
            throw new InvalidDataException($"Expected four components for {label}.");
        }
        return Quaternion.Normalize(new Quaternion(
            (float)components[0],
            (float)components[1],
            (float)components[2],
            (float)components[3]));
    }

    private static float QuaternionDistance(Quaternion left, Quaternion right) =>
        MathF.Sqrt(
            MathF.Pow(left.X - right.X, 2)
            + MathF.Pow(left.Y - right.Y, 2)
            + MathF.Pow(left.Z - right.Z, 2)
            + MathF.Pow(left.W - right.W, 2));

    private static void ValidateSharedRoot(ModelInput primary, ModelInput secondary)
    {
        var primarySkeleton = primary.Model.Data.GetSubCollection("m_modelSkeleton");
        var secondarySkeleton = secondary.Model.Data.GetSubCollection("m_modelSkeleton");
        CompareVector(
            GetArray(primarySkeleton, "m_bonePosParent")[primary.RootBoneIndex],
            GetArray(secondarySkeleton, "m_bonePosParent")[secondary.RootBoneIndex],
            0.1,
            "root positions");
        CompareVector(
            GetArray(primarySkeleton, "m_boneRotParent")[primary.RootBoneIndex],
            GetArray(secondarySkeleton, "m_boneRotParent")[secondary.RootBoneIndex],
            0.001,
            "root rotations");
        var primaryScale = Convert.ToDouble(
            GetArray(primarySkeleton, "m_boneScaleParent")[primary.RootBoneIndex].Value,
            CultureInfo.InvariantCulture);
        var secondaryScale = Convert.ToDouble(
            GetArray(secondarySkeleton, "m_boneScaleParent")[secondary.RootBoneIndex].Value,
            CultureInfo.InvariantCulture);
        if (Math.Abs(primaryScale - secondaryScale) > 0.001)
        {
            throw new InvalidDataException("The models use incompatible root scales.");
        }
    }

    private static int[] MergeSkeleton(ModelInput primary, ModelInput secondary)
    {
        var primarySkeleton = primary.Model.Data.GetSubCollection("m_modelSkeleton");
        var secondarySkeleton = secondary.Model.Data.GetSubCollection("m_modelSkeleton");
        var primaryNames = GetArray(primarySkeleton, "m_boneName");
        var secondaryNames = GetArray(secondarySkeleton, "m_boneName");
        var nameToIndex = StringValues(primaryNames)
            .Select((name, index) => (name, index))
            .ToDictionary(pair => pair.name, pair => pair.index, StringComparer.Ordinal);
        var secondaryToCombined = new int[secondaryNames.Count];

        for (var secondaryIndex = 0; secondaryIndex < secondaryNames.Count; secondaryIndex++)
        {
            var name = (string)secondaryNames[secondaryIndex].Value!;
            if (nameToIndex.TryGetValue(name, out var existingIndex))
            {
                secondaryToCombined[secondaryIndex] = existingIndex;
                continue;
            }
            var combinedIndex = primaryNames.Count;
            secondaryToCombined[secondaryIndex] = combinedIndex;
            nameToIndex.Add(name, combinedIndex);
            foreach (var key in BoneArrayKeys)
            {
                var destination = GetArray(primarySkeleton, key);
                var source = GetArray(secondarySkeleton, key);
                var value = source[secondaryIndex];
                if (key == "m_nParent")
                {
                    var originalParent = Convert.ToInt32(
                        value.Value,
                        CultureInfo.InvariantCulture);
                    if (originalParent < 0 || originalParent >= secondaryIndex)
                    {
                        throw new InvalidDataException(
                            $"Secondary bone {name} does not follow parent-before-child order.");
                    }
                    value = new KVValue(secondaryToCombined[originalParent]);
                }
                destination.AddProperty(null, value);
            }
        }
        return secondaryToCombined;
    }

    private static int MergeModelData(
        ModelInput primary,
        ModelInput secondary,
        int[] secondaryToCombined,
        ModelCompositionMode mode)
    {
        foreach (var key in PerMeshArrayKeys)
        {
            AppendArray(
                GetArray(primary.Model.Data, key),
                GetArray(secondary.Model.Data, key));
        }

        var primaryRemapping = GetArray(primary.Model.Data, "m_remappingTable");
        var remappingOffset = primaryRemapping.Count;
        var remappedBoneReferences = 0;
        foreach (var value in GetArray(secondary.Model.Data, "m_remappingTable").Properties.Values)
        {
            var secondaryBone = Convert.ToInt32(value.Value, CultureInfo.InvariantCulture);
            var combinedBone = secondaryBone >= 0
                && secondaryBone < secondaryToCombined.Length
                ? secondaryToCombined[secondaryBone]
                : mode != ModelCompositionMode.SharedRoot
                    ? secondaryBone
                    : throw new InvalidDataException(
                        $"Secondary mesh remapping references invalid bone {secondaryBone}.");
            if (combinedBone != secondaryBone)
            {
                remappedBoneReferences++;
            }
            primaryRemapping.AddProperty(
                null,
                new KVValue(combinedBone));
        }
        var primaryStarts = GetArray(primary.Model.Data, "m_remappingTableStarts");
        foreach (var value in GetArray(
            secondary.Model.Data,
            "m_remappingTableStarts").Properties.Values)
        {
            primaryStarts.AddProperty(
                null,
                new KVValue(
                    checked(remappingOffset
                        + Convert.ToInt32(value.Value, CultureInfo.InvariantCulture))));
        }
        return remappedBoneReferences;
    }

    private static void MergeControlData(
        ModelInput primary,
        ModelInput secondary,
        IReadOnlyDictionary<int, int> secondaryBlockMap)
    {
        var destination = GetArray(primary.Control.Data, "embedded_meshes");
        var source = GetArray(secondary.Control.Data, "embedded_meshes");
        for (var index = 0; index < source.Count; index++)
        {
            var definition = CloneObject(
                RequireObject(source[index], "secondary embedded mesh"));
            definition.Properties["mesh_index"] = new KVValue(destination.Count);
            RemapMeshBlocks(definition, secondaryBlockMap);
            destination.AddProperty(null, new KVValue(definition));
        }
        SumInteger(primary.Control.Data, secondary.Control.Data, "pool_size_hint_mesh");
        SumInteger(primary.Control.Data, secondary.Control.Data, "pool_size_hint");
    }

    private static ResourceExtRefList MergeReferences(
        ModelInput primary,
        ModelInput secondary)
    {
        var byId = new Dictionary<ulong, string>();
        var byName = new Dictionary<string, ulong>(StringComparer.Ordinal);
        foreach (var reference in primary.References.ResourceRefInfoList
            .Concat(secondary.References.ResourceRefInfoList))
        {
            if (byId.TryGetValue(reference.Id, out var existingName)
                && !string.Equals(existingName, reference.Name, StringComparison.Ordinal))
            {
                throw new InvalidDataException(
                    $"Resource id 0x{reference.Id:X16} maps to multiple paths.");
            }
            if (byName.TryGetValue(reference.Name, out var existingId)
                && existingId != reference.Id)
            {
                throw new InvalidDataException(
                    $"Resource path {reference.Name} maps to multiple ids.");
            }
            byId[reference.Id] = reference.Name;
            byName[reference.Name] = reference.Id;
        }

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

    private static void WriteComposite(
        string outputPath,
        ModelInput primary,
        ResourceExtRefList references,
        IReadOnlyList<OpaqueBlock> opaqueBlocks)
    {
        var blockSources = opaqueBlocks
            .Select(block => new BlockSource(
                block.Block.Type,
                stream => CopyBlock(stream, block.Bytes, block.Block)))
            .ToList();
        blockSources.Add(new BlockSource(BlockType.CTRL, primary.Control.Serialize));
        blockSources.Add(new BlockSource(BlockType.RERL, references.Serialize));
        blockSources.Add(new BlockSource(
            BlockType.RED2,
            stream => CopyBlock(stream, primary.Bytes, primary.EditInfo)));
        blockSources.Add(new BlockSource(BlockType.DATA, primary.Model.Serialize));
        WriteResource(outputPath, primary.Resource, blockSources);
    }

    private static long VerifyComposite(
        string outputPath,
        ModelInput primary,
        IReadOnlyList<MeshSnapshot> expectedMeshes,
        IReadOnlyList<string> expectedBones,
        IReadOnlyList<int> expectedRemapping,
        IReadOnlyList<int> expectedRemappingStarts,
        ResourceExtRefList expectedReferences,
        IReadOnlyList<OpaqueBlock> expectedOpaqueBlocks,
        ModelCompositionMode mode)
    {
        var outputBytes = File.ReadAllBytes(outputPath);
        using var output = ModelInput.Open(
            outputPath,
            "composed output",
            allowExternalRemapping: mode != ModelCompositionMode.SharedRoot,
            allowPrimaryPayload: true);
        if (output.Resource.HeaderVersion != primary.Resource.HeaderVersion
            || output.Resource.Version != primary.Resource.Version)
        {
            throw new InvalidDataException("The composed output changed the resource version.");
        }
        if (output.Meshes.Length != expectedMeshes.Count)
        {
            throw new InvalidDataException("The composed output has the wrong mesh count.");
        }
        for (var index = 0; index < expectedMeshes.Count; index++)
        {
            if (!MeshMatches(expectedMeshes[index], output.Meshes[index]))
            {
                throw new InvalidDataException(
                    $"The composed output changed embedded mesh {index}.");
            }
        }
        if (!output.BoneNames.SequenceEqual(expectedBones, StringComparer.Ordinal))
        {
            throw new InvalidDataException("The composed output has an inconsistent skeleton.");
        }
        if (!IntValues(GetArray(output.Model.Data, "m_remappingTable"))
                .SequenceEqual(expectedRemapping)
            || !IntValues(GetArray(output.Model.Data, "m_remappingTableStarts"))
                .SequenceEqual(expectedRemappingStarts))
        {
            throw new InvalidDataException("The composed output has inconsistent bone remapping.");
        }
        var actualReferences = output.References.ResourceRefInfoList
            .Select(reference => (reference.Id, reference.Name))
            .OrderBy(reference => reference.Id)
            .ToArray();
        var references = expectedReferences.ResourceRefInfoList
            .Select(reference => (reference.Id, reference.Name))
            .OrderBy(reference => reference.Id)
            .ToArray();
        if (!actualReferences.SequenceEqual(references))
        {
            throw new InvalidDataException("The composed output changed resource references.");
        }
        if (output.MeshBlocks.Count != expectedOpaqueBlocks.Count)
        {
            throw new InvalidDataException("The composed output has the wrong opaque-block count.");
        }
        for (var index = 0; index < expectedOpaqueBlocks.Count; index++)
        {
            var expected = expectedOpaqueBlocks[index];
            var actual = output.MeshBlocks[index];
            var expectedBytes = expected.Bytes.AsSpan(
                checked((int)expected.Block.Offset),
                checked((int)expected.Block.Size));
            var actualBytes = outputBytes.AsSpan(
                checked((int)actual.Offset),
                checked((int)actual.Size));
            if (actual.Type != expected.Block.Type || !actualBytes.SequenceEqual(expectedBytes))
            {
                throw new InvalidDataException(
                    $"The composed output changed opaque mesh block {index}.");
            }
        }
        return outputBytes.LongLength;
    }

    private static bool MeshMatches(MeshSnapshot expected, MeshSnapshot actual) =>
        expected.Index == actual.Index
        && expected.Name == actual.Name
        && expected.LoDMask == actual.LoDMask
        && expected.VertexCounts.SequenceEqual(actual.VertexCounts)
        && expected.IndexCounts.SequenceEqual(actual.IndexCounts);

    private static Dictionary<int, int> BuildOutputBlockMap(
        IReadOnlyList<Block> blocks,
        int outputStart)
    {
        var result = new Dictionary<int, int>();
        for (var index = 0; index < blocks.Count; index++)
        {
            var resource = blocks[index].Resource
                ?? throw new InvalidDataException("An opaque mesh block has no owner resource.");
            var sourceIndex = resource.Blocks.IndexOf(blocks[index]);
            result.Add(sourceIndex, outputStart + index);
        }
        return result;
    }

    private static void RemapMeshBlocks(
        KVObject definition,
        IReadOnlyDictionary<int, int> blockMap)
    {
        foreach (var key in new[] { "data_block", "vbib_block", "morph_block" })
        {
            var sourceIndex = checked((int)definition.GetIntegerProperty(key));
            if (key == "morph_block" && sourceIndex < 0)
            {
                continue;
            }
            if (!blockMap.TryGetValue(sourceIndex, out var outputIndex))
            {
                throw new InvalidDataException(
                    $"Embedded mesh references an unmapped {key}: {sourceIndex}.");
            }
            if (outputIndex != sourceIndex)
            {
                definition.Properties[key] = new KVValue(outputIndex);
            }
        }
    }

    private static void WriteResource(
        string outputPath,
        Resource template,
        IReadOnlyList<BlockSource> blocks)
    {
        using var output = File.Create(outputPath);
        using var writer = new BinaryWriter(output, Encoding.UTF8, leaveOpen: true);
        writer.Write(0xDEADBEEF);
        writer.Write(template.HeaderVersion);
        writer.Write(template.Version);
        writer.Write(8);
        writer.Write(blocks.Count);
        var blocksStart = output.Position + sizeof(uint);
        foreach (var block in blocks)
        {
            writer.Write((uint)block.Type);
            writer.Write(0xDEADBEEF);
            writer.Write(0xDEADBEEF);
        }
        writer.Flush();

        for (var index = 0; index < blocks.Count; index++)
        {
            writer.Write(new byte[checked((int)((16 - output.Position % 16) % 16))]);
            var blockOffset = output.Position;
            blocks[index].Write(output);
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

    private static void CopyBlock(Stream destination, byte[] source, Block block) =>
        destination.Write(source, checked((int)block.Offset), checked((int)block.Size));

    private static void CompareVector(
        KVValue primaryValue,
        KVValue secondaryValue,
        double tolerance,
        string label)
    {
        var primary = DoubleValues(RequireObject(primaryValue, label));
        var secondary = DoubleValues(RequireObject(secondaryValue, label));
        if (primary.Length != secondary.Length
            || primary.Where((value, index) => Math.Abs(value - secondary[index]) > tolerance).Any())
        {
            throw new InvalidDataException($"The models use incompatible {label}.");
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

    private static KVObject CloneObject(KVObject source)
    {
        var clone = new KVObject(null);
        foreach (var property in source.Properties)
        {
            clone.AddProperty(property.Key, property.Value);
        }
        return clone;
    }

    private static string WriteKeyValues(KVObject data)
    {
        using var writer = new IndentedTextWriter();
        data.Serialize(writer);
        return writer.ToString();
    }

    private static string[] StringValues(KVObject array) =>
        array.Properties.Values.Select(value => (string)value.Value!).ToArray();

    private static int[] IntValues(KVObject array) =>
        array.Properties.Values
            .Select(value => Convert.ToInt32(value.Value, CultureInfo.InvariantCulture))
            .ToArray();

    private static float[] FloatValues(KVObject array) =>
        array.Properties.Values
            .Select(value => Convert.ToSingle(value.Value, CultureInfo.InvariantCulture))
            .ToArray();

    private static double[] DoubleValues(KVObject array) =>
        array.Properties.Values
            .Select(value => Convert.ToDouble(value.Value, CultureInfo.InvariantCulture))
            .ToArray();

    private static void AppendArray(KVObject destination, KVObject source)
    {
        foreach (var value in source.Properties.Values)
        {
            destination.AddProperty(null, value);
        }
    }

    private static void SumInteger(KVObject primary, KVObject secondary, string key)
    {
        var combined = checked(
            checked((int)primary.GetIntegerProperty(key))
            + checked((int)secondary.GetIntegerProperty(key)));
        primary.Properties[key] = new KVValue(combined);
    }

    private sealed class ModelInput : IDisposable
    {
        public required byte[] Bytes { get; init; }
        public required Resource Resource { get; init; }
        public required Model Model { get; init; }
        public required BinaryKV3 Control { get; init; }
        public required ResourceExtRefList References { get; init; }
        public required Block EditInfo { get; init; }
        public required List<Block> MeshBlocks { get; init; }
        public required MeshSnapshot[] Meshes { get; init; }
        public required string[] BoneNames { get; init; }
        public required int RootBoneIndex { get; init; }
        public required string RootBoneName { get; init; }

        public static ModelInput Open(
            string path,
            string label,
            bool allowExternalRemapping = false,
            bool requireSingleRoot = true,
            bool allowPrimaryPayload = false)
        {
            var bytes = File.ReadAllBytes(path);
            var resource = new Resource();
            try
            {
                resource.Read(path);
                var model = RequireSingleBlock(resource, BlockType.DATA) as Model
                    ?? throw new InvalidDataException(
                        $"The {label} DATA block is not a compiled Source 2 model.");
                var control = RequireSingleBlock(resource, BlockType.CTRL) as BinaryKV3
                    ?? throw new InvalidDataException($"The {label} CTRL block is malformed.");
                var references = RequireSingleBlock(resource, BlockType.RERL) as ResourceExtRefList
                    ?? throw new InvalidDataException($"The {label} RERL block is malformed.");
                var editInfo = RequireSingleBlock(resource, BlockType.RED2);
                var definitions = ValidateModel(
                    model,
                    control,
                    label,
                    allowExternalRemapping);
                var meshBlocks = allowPrimaryPayload
                    ? CollectPrimaryPayloadBlocks(
                        resource,
                        control,
                        references,
                        editInfo,
                        model,
                        label)
                    : CollectMeshBlocks(resource, definitions, label);
                if (!allowPrimaryPayload)
                {
                    ValidateBlockLayout(
                        resource,
                        meshBlocks,
                        control,
                        references,
                        editInfo,
                        model,
                        label);
                }
                var meshes = model.GetEmbeddedMeshesAndLoD()
                    .Select(mesh =>
                    {
                        mesh.Mesh.GetBounds();
                        return new MeshSnapshot(
                            mesh.MeshIndex,
                            mesh.Name,
                            mesh.LoDMask,
                            mesh.Mesh.VBIB.VertexBuffers
                                .Select(buffer => buffer.ElementCount)
                                .ToArray(),
                            mesh.Mesh.VBIB.IndexBuffers
                                .Select(buffer => buffer.ElementCount)
                                .ToArray());
                    })
                    .ToArray();
                if (meshes.Length != definitions.Count)
                {
                    throw new InvalidDataException(
                        $"The {label} embedded-mesh count is inconsistent.");
                }

                var skeleton = model.Data.GetSubCollection("m_modelSkeleton");
                var boneNames = StringValues(GetArray(skeleton, "m_boneName"));
                var parents = IntValues(GetArray(skeleton, "m_nParent"));
                var roots = parents
                    .Select((parent, index) => (parent, index))
                    .Where(pair => pair.parent < 0)
                    .Select(pair => pair.index)
                    .ToArray();
                if (roots.Length == 0 || (requireSingleRoot && roots.Length != 1))
                {
                    throw new InvalidDataException(
                        requireSingleRoot
                            ? $"The {label} model must have exactly one root bone."
                            : $"The {label} model has no root bone.");
                }
                return new ModelInput
                {
                    Bytes = bytes,
                    Resource = resource,
                    Model = model,
                    Control = control,
                    References = references,
                    EditInfo = editInfo,
                    MeshBlocks = meshBlocks,
                    Meshes = meshes,
                    BoneNames = boneNames,
                    RootBoneIndex = roots[0],
                    RootBoneName = boneNames[roots[0]],
                };
            }
            catch
            {
                resource.Dispose();
                throw;
            }
        }

        public void Dispose() => Resource.Dispose();
    }

    private static KVObject ValidateModel(
        Model model,
        BinaryKV3 control,
        string label,
        bool allowExternalRemapping)
    {
        if (model.GetReferenceMeshNamesAndLoD().Any())
        {
            throw new InvalidDataException(
                $"The {label} model uses external meshes, which cannot be composed safely.");
        }
        foreach (var key in UnsupportedArrayKeys)
        {
            if (GetArray(model.Data, key).Count != 0)
            {
                throw new InvalidDataException(
                    $"The {label} model has unsupported non-empty metadata: {key}.");
            }
        }
        if (!model.Data.Properties.TryGetValue("m_pModelConfigList", out var config)
            || config.Value is not null)
        {
            throw new InvalidDataException(
                $"The {label} model has an unsupported model configuration.");
        }

        var definitions = GetArray(control.Data, "embedded_meshes");
        if (definitions.Count == 0)
        {
            throw new InvalidDataException($"The {label} model has no embedded meshes.");
        }
        foreach (var key in PerMeshArrayKeys)
        {
            if (GetArray(model.Data, key).Count != definitions.Count)
            {
                throw new InvalidDataException(
                    $"The {label} per-mesh array {key} has an inconsistent size.");
            }
        }
        if (StringValues(GetArray(model.Data, "m_refMeshes")).Any(path => path.Length != 0))
        {
            throw new InvalidDataException(
                $"The {label} model contains a non-empty external mesh reference.");
        }

        var skeleton = model.Data.GetSubCollection("m_modelSkeleton");
        var boneCount = GetArray(skeleton, "m_boneName").Count;
        if (boneCount == 0)
        {
            throw new InvalidDataException($"The {label} model has no bones.");
        }
        foreach (var key in BoneArrayKeys)
        {
            if (GetArray(skeleton, key).Count != boneCount)
            {
                throw new InvalidDataException(
                    $"The {label} skeleton array {key} has an inconsistent size.");
            }
        }
        var boneNames = StringValues(GetArray(skeleton, "m_boneName"));
        if (boneNames.Distinct(StringComparer.Ordinal).Count() != boneNames.Length)
        {
            throw new InvalidDataException($"The {label} model has duplicate bone names.");
        }
        var parents = IntValues(GetArray(skeleton, "m_nParent"));
        for (var index = 0; index < parents.Length; index++)
        {
            if (parents[index] >= index)
            {
                throw new InvalidDataException(
                    $"The {label} skeleton does not follow parent-before-child order.");
            }
        }

        var remapping = IntValues(GetArray(model.Data, "m_remappingTable"));
        var starts = IntValues(GetArray(model.Data, "m_remappingTableStarts"));
        if (starts.Length != definitions.Count
            || starts[0] != 0
            || !starts.SequenceEqual(starts.Order()))
        {
            throw new InvalidDataException(
                $"The {label} model has invalid remapping-table starts.");
        }
        var invalidRemapping = remapping
            .Where(index => index < 0 || index >= boneCount)
            .Distinct()
            .Order()
            .ToArray();
        if (!allowExternalRemapping && invalidRemapping.Length != 0)
        {
            throw new InvalidDataException(
                $"The {label} model remaps a mesh to invalid bone index value(s) "
                + $"{string.Join(", ", invalidRemapping)} for {boneCount} bones.");
        }
        for (var meshIndex = 0; meshIndex < definitions.Count; meshIndex++)
        {
            var start = starts[meshIndex];
            var end = meshIndex + 1 < starts.Length ? starts[meshIndex + 1] : remapping.Length;
            if (start >= end || end > remapping.Length)
            {
                throw new InvalidDataException(
                    $"The {label} model has an empty or invalid mesh remapping segment.");
            }
        }

        for (var index = 0; index < definitions.Count; index++)
        {
            var definition = RequireObject(definitions[index], $"{label} embedded mesh");
            if (definition.GetIntegerProperty("mesh_index") != index)
            {
                throw new InvalidDataException(
                    $"The {label} embedded mesh indexes are not contiguous.");
            }
            if (!definition.ContainsKey("data_block") || !definition.ContainsKey("vbib_block"))
            {
                throw new InvalidDataException(
                    $"The {label} model uses an unsupported embedded-mesh format.");
            }
        }
        return definitions;
    }

    private static List<Block> CollectPrimaryPayloadBlocks(
        Resource resource,
        BinaryKV3 control,
        ResourceExtRefList references,
        Block editInfo,
        Model model,
        string label)
    {
        var coreBlocks = new HashSet<Block> { control, references, editInfo, model };
        var payloadBlocks = resource.Blocks
            .Where(block => !coreBlocks.Contains(block))
            .ToList();
        if (payloadBlocks.Count == 0
            || !resource.Blocks.Take(payloadBlocks.Count).SequenceEqual(payloadBlocks)
            || resource.Blocks.Skip(payloadBlocks.Count).Any(block => !coreBlocks.Contains(block)))
        {
            throw new InvalidDataException(
                $"The {label} model has an unsupported compiled-resource block order.");
        }
        return payloadBlocks;
    }

    private static List<Block> CollectMeshBlocks(
        Resource resource,
        KVObject definitions,
        string label)
    {
        var indexes = new SortedSet<int>();
        foreach (var value in definitions.Properties.Values)
        {
            var definition = RequireObject(value, $"{label} embedded mesh");
            var dataIndex = checked((int)definition.GetIntegerProperty("data_block"));
            var bufferIndex = checked((int)definition.GetIntegerProperty("vbib_block"));
            if (!indexes.Add(dataIndex) || !indexes.Add(bufferIndex))
            {
                throw new InvalidDataException(
                    $"The {label} embedded meshes unexpectedly share opaque blocks.");
            }
            if (resource.GetBlockByIndex(dataIndex).Type != BlockType.MDAT
                || resource.GetBlockByIndex(bufferIndex).Type != BlockType.MBUF)
            {
                throw new InvalidDataException(
                    $"The {label} embedded mesh references unexpected block types.");
            }
            var morphIndex = checked((int)definition.GetIntegerProperty("morph_block"));
            if (morphIndex >= 0)
            {
                indexes.Add(morphIndex);
                if (resource.GetBlockByIndex(morphIndex).Type != BlockType.MRPH)
                {
                    throw new InvalidDataException(
                        $"The {label} embedded mesh references an unexpected morph block type.");
                }
            }
        }
        return indexes.Select(resource.GetBlockByIndex).ToList();
    }

    private static void ValidateBlockLayout(
        Resource resource,
        IReadOnlyCollection<Block> meshBlocks,
        BinaryKV3 control,
        ResourceExtRefList references,
        Block editInfo,
        Model model,
        string label)
    {
        var allowed = meshBlocks
            .Concat([control, references, editInfo, model])
            .ToHashSet();
        if (allowed.Count != resource.Blocks.Count
            || resource.Blocks.Any(block => !allowed.Contains(block)))
        {
            throw new InvalidDataException(
                $"The {label} model contains unsupported compiled-resource blocks.");
        }
    }

    private static Block RequireSingleBlock(Resource resource, BlockType type)
    {
        var blocks = resource.Blocks.Where(block => block.Type == type).ToArray();
        if (blocks.Length != 1)
        {
            throw new InvalidDataException(
                $"Expected exactly one {type} block, found {blocks.Length}.");
        }
        return blocks[0];
    }

    private sealed record MeshSnapshot(
        int Index,
        string Name,
        long LoDMask,
        uint[] VertexCounts,
        uint[] IndexCounts);

    private readonly record struct BoneTransform(
        Vector3 Position,
        Quaternion Rotation,
        float Scale);

    private sealed record OpaqueBlock(byte[] Bytes, Block Block);

    private sealed record BlockSource(BlockType Type, Action<Stream> Write);
}
