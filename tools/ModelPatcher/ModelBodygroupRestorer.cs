using System.Globalization;
using System.Text.RegularExpressions;
using ValveResourceFormat.ResourceTypes;
using ValveResourceFormat.Serialization.KeyValues;

namespace Dota2CosmeticDisabler.ModelPatcher;

internal sealed record BodygroupRestoreResult(
    IReadOnlyList<string> RestoredBodygroups,
    IReadOnlyList<string> MissingBodygroups,
    int ChangedMeshes);

/// <summary>
/// Locks named bodygroups to their default choice by editing only the
/// per-mesh mesh-group masks in a compiled model's DATA block.
/// </summary>
/// <remarks>
/// Source 2 compiles a bodygroup choice to a mesh group named
/// <c>&lt;bodygroup&gt;_@&lt;choice&gt;_#&amp;&lt;n&gt;</c>, and every embedded mesh
/// carries a bit mask of the mesh groups it belongs to. Cosmetic items hide a
/// default hero part by selecting a non-default choice, which usually has no
/// meshes. Giving every non-default choice exactly the meshes of choice 0
/// keeps the default part visible whichever choice an item selects, while the
/// default choice and every unrelated mesh group stay byte-for-byte equivalent.
/// </remarks>
internal static partial class ModelBodygroupRestorer
{
    public static BodygroupRestoreResult Restore(
        Model model,
        IReadOnlyCollection<string> bodygroups)
    {
        if (bodygroups.Count == 0)
        {
            throw new ArgumentException("At least one bodygroup must be restored.");
        }

        var meshGroups = model.Data.Properties.TryGetValue("m_meshGroups", out var groupsValue)
            && groupsValue.Value is KVObject groupArray
            && groupArray.IsArray
            ? groupArray.Properties.Values
                .Select(value => value.Value as string
                    ?? throw new InvalidDataException("A model mesh-group name is malformed."))
                .ToArray()
            : [];
        if (meshGroups.Length > 64)
        {
            throw new InvalidDataException(
                "The model has more mesh groups than a 64-bit mesh-group mask can address.");
        }

        var choicesByBodygroup = new Dictionary<string, SortedDictionary<int, int>>(
            StringComparer.OrdinalIgnoreCase);
        for (var bit = 0; bit < meshGroups.Length; bit++)
        {
            var match = MeshGroupChoicePattern().Match(meshGroups[bit]);
            if (!match.Success)
            {
                continue;
            }
            var name = match.Groups["name"].Value;
            var choice = int.Parse(match.Groups["choice"].Value, CultureInfo.InvariantCulture);
            if (!choicesByBodygroup.TryGetValue(name, out var choices))
            {
                choices = [];
                choicesByBodygroup[name] = choices;
            }
            if (!choices.TryAdd(choice, bit))
            {
                throw new InvalidDataException(
                    $"Bodygroup {name} declares choice {choice} more than once.");
            }
        }

        var restored = new List<string>();
        var missing = new List<string>();
        var plans = new List<(ulong DefaultBit, ulong AlternateBits)>();
        foreach (var bodygroup in bodygroups
            .Select(name => name.Trim())
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .Order(StringComparer.OrdinalIgnoreCase))
        {
            if (bodygroup.Length == 0)
            {
                throw new ArgumentException("Bodygroup names may not be empty.");
            }
            if (!choicesByBodygroup.TryGetValue(bodygroup, out var choices)
                || !choices.TryGetValue(0, out var defaultBit)
                || choices.Count < 2)
            {
                missing.Add(bodygroup);
                continue;
            }
            var alternateBits = choices
                .Where(choice => choice.Key != 0)
                .Aggregate(0UL, (bits, choice) => bits | (1UL << choice.Value));
            plans.Add((1UL << defaultBit, alternateBits));
            restored.Add(bodygroup);
        }

        var changedMeshes = 0;
        if (plans.Count == 0)
        {
            return new BodygroupRestoreResult(restored, missing, changedMeshes);
        }

        if (!model.Data.Properties.TryGetValue("m_refMeshGroupMasks", out var masksValue)
            || masksValue.Value is not KVObject masks
            || !masks.IsArray)
        {
            throw new InvalidDataException("The model has no per-mesh mesh-group masks.");
        }
        foreach (var key in masks.Properties.Keys.ToArray())
        {
            var entry = masks.Properties[key];
            var original = ToMask(entry.Value);
            var updated = original;
            foreach (var (defaultBit, alternateBits) in plans)
            {
                updated = (updated & defaultBit) != 0
                    ? updated | alternateBits
                    : updated & ~alternateBits;
            }
            if (updated == original)
            {
                continue;
            }
            masks.Properties[key] = entry.Value switch
            {
                ulong => new KVValue(entry.Type, entry.Flag, updated),
                long => new KVValue(entry.Type, entry.Flag, unchecked((long)updated)),
                uint when updated <= uint.MaxValue => new KVValue(
                    entry.Type,
                    entry.Flag,
                    (uint)updated),
                int when unchecked((long)updated) is >= int.MinValue and <= int.MaxValue =>
                    new KVValue(entry.Type, entry.Flag, unchecked((int)(long)updated)),
                _ => throw new InvalidDataException(
                    $"Mesh-group masks use an unsupported value type: {entry.Type}."),
            };
            changedMeshes++;
        }
        return new BodygroupRestoreResult(restored, missing, changedMeshes);
    }

    public static ulong[] ReadMeshGroupMasks(Model model) =>
        model.Data.Properties.TryGetValue("m_refMeshGroupMasks", out var masksValue)
            && masksValue.Value is KVObject masks
            && masks.IsArray
            ? masks.Properties.Values
                .Select(value => ToMask(value.Value))
                .ToArray()
            : [];

    // Masks are bit sets. Signed storage is sign-extended so that -1 means
    // "every group" whatever width the compiler or KV3 writer chose.
    private static ulong ToMask(object? value) => value switch
    {
        ulong unsigned => unsigned,
        long signed => unchecked((ulong)signed),
        uint unsigned => unsigned,
        int signed => unchecked((ulong)(long)signed),
        ushort unsigned => unsigned,
        short signed => unchecked((ulong)(long)signed),
        byte unsigned => unsigned,
        sbyte signed => unchecked((ulong)(long)signed),
        _ => throw new InvalidDataException(
            $"A mesh-group mask has an unsupported value: {value?.GetType().Name ?? "null"}."),
    };

    [GeneratedRegex(
        @"^(?<name>.+)_@(?<choice>\d+)(?:_#.*)?$",
        RegexOptions.CultureInvariant)]
    private static partial Regex MeshGroupChoicePattern();
}
