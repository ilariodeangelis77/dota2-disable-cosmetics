# Dota 2 Cosmetic Disabler

An experimental, client-side tool that replaces many equipped Dota 2 cosmetic **model and particle resources** with their corresponding defaults. It reads the current local Dota economy schema on every build, so the mapping can be regenerated after game updates instead of relying on a fixed list of cosmetics.

[Download the latest Windows release](https://github.com/ilariodeangelis77/dota2-disable-cosmetics/releases/latest)

It does not yet restore sounds, icons, hero scaling, animation/activity modifiers, map cosmetics,
control-point-only particle rules, or every unusual Arcana/Persona edge case.

## Safety boundary

The generator does not patch `dota2.exe`, inject code, alter VAC, or use `-override_vpk`. It copies
existing compiled resources from Dota's own VPK into a new manifest-owned VPK under a recognized
Dota language search path. For skin-sensitive replacements, it can add duplicate base-material
groups to the copied model's KV3 data while byte-preserving every opaque mesh, skeleton, animation,
and dependency block. When a cosmetic hides geometry integrated into the hero model, it also
uses a model-only compatibility fallback; the original Dota files and economy schema are never
edited or repackaged.

That does **not** make this an officially supported Dota feature or provide an anti-cheat guarantee. Valve can change search-path behavior or game policy at any time. Review the generated report, test in a demo/custom lobby first, and stop using the output if the client rejects it.

Cleanup is manifest-based: the tool removes only the numbered VPK recorded in its own marker. It
shares the recognized language directory safely, preserves existing language packs and unrelated
files, selects a free `pak90`–`pak98` slot, and prevents overlapping build/cleanup operations for
the same Dota installation. Deployment keeps the previous owned archive available until the
replacement marker is safely written and rolls the archive back if marker persistence fails.

## What v0.8.1 maps

- Normal `model_player` wearables to the hero/slot `baseitem` model.
- Wearables whose default slot is integrated into the hero body to Dota's existing invisible model,
  allowing the underlying default geometry to show.
- Bodygroup-sensitive wearables to a full default-hero compatibility model. This is intended to avoid the missing
  heads/body sections produced when Dota continued applying the equipped item's bodygroup hide but
  ignored an otherwise valid language-VPK schema overlay.
- Alternate-style `model_player` resources to the default slot model.
- Persona wearables to Dota's existing invisible model when they cannot attach safely to the normal hero.
- Hero, summon, ward, transformed-unit, and other supported `entity_model`, `base_model`, and
  `entity_clientside_model` overrides back to defaults derived from `npc_heroes.txt`,
  `npc_units.txt`, and base items. Multi-model heroes such as Tiny expose every declared growth
  variant (`Model` through `Model3`) to entity replacement rules.
- `hero_model_change` and model-to-model asset replacements. Refit targets inherit an already
  inferred hero/slot default when possible; narrowly reviewed item/path exceptions cover stale
  schema references, and unknown rules retain the conservative original-asset fallback.
- `additional_wearable` models to a matching default additional wearable, or to the existing invisible model when no default exists.
- Cosmetic `pet` models and their pickup props to the existing invisible model, including all
  Frost Avalanche wolf styles.
- Schema `particle`, `particle_clientside`, and `particle_combined` targets to their declared default
  particle, including transitive replacement chains.
- Non-base `particle_create` additions in dedicated cosmetic namespaces to Dota's own neutral
  particle, while preserving resources referenced by base items and shared hero/UI paths.
- `particle_snapshot` targets to their declared default snapshot.
- Global schema particle replacements to their declared defaults.
- Cosmetic-created particles back to a matching default particle when the same item explicitly
  suppresses that default or when its base slot declares a corresponding default effect, instead
  of blanking the effect. This covers Ember Spirit's primary/offhand sword ambient layers.
- Confidently matched alternate skin/material groups back to the base material, including current
  Fall 2020/Diretide palette variants. Skin state is scoped to the item/style record that declares
  it; a styled set piece no longer marks unrelated bundle siblings as skin-sensitive. When an
  equipped style selects material group 1 or 2 but the visible default model only has its implicit
  base group, the copied model receives explicit duplicate base groups instead of rendering Valve's
  violet error material. True material-only no-ops remain conservative and reported.

Ambiguous mappings are skipped or resolved deterministically and written to `.work/model-plan.json`
for review. Particle control-point and terrain-selector rules do not expose an owned resource target
that this strategy can safely replace, so they remain reported but unchanged.

The 0.6 generator completed an all-category VPK build against installed Dota Steam build `24812551`
on 2026-08-20: 9,650 model overrides and 234 locally derived English-language compatibility files
were packed and CRC-validated with no missing required source models. The installed archive checksum
matches its marker, and the user confirmed that the Dutch mount applies the overrides in Armory and
Demo Hero. A representative category-by-category visual matrix is still pending.

The 0.8.1 planner was regenerated from installed Dota Steam build `24869441` on 2026-08-22 and
passed an isolated 16,440-entry VPK pack/reopen/CRC round trip without deploying or missing a final
source resource. It contains 10,860 model, 112 material, 5,227 particle, and 241 snapshot mappings.
The item-scoped skin fix restores 1,147 valid default-model mappings that the previous bundle-wide
fallback had incorrectly discarded. Five reviewed stale schema particle paths are translated to
verified current resources; 19 explicit Valve placeholder or retired ambient-layer defaults use
Dota's neutral particle by design, leaving zero unexpected missing default particles. Sixty-seven
cosmetic-created particles are restored from an explicitly suppressed default, two cyclic mappings
are skipped, 25 additive rules targeting shared paths are preserved, 253 cosmetic-created particles
are restored from slot defaults, and 143 unsupported particle rules remain reported. This is
structural validation; the fixes still need another in-game visual pass.

The same live gate verifies that every model-bearing item in the Whitewind Battlemage, Flame of
Origin, Roost of the Winter Raven, Abominable Snowbeast, and Spirit of the Dark Wood regression
groups still has a model mapping, with no reappearance of the discarded-model skin diagnostic.

All 34 currently identified bodygroup-sensitive wearable targets now use the model-only full-hero
fallback, and no `items_game.txt` overlay is shipped. Twelve retired schema records are ignored
before conflict resolution, preventing the obsolete Tendrillar record from replacing Keeper of the
Light's mount with a staff. The plan also contains 18 pet/pickup rules and 1,050 entity-default
rules. For 1,041 skin-sensitive visible targets that can be selected with a nonzero material-group
index, the build adds duplicate base-material groups to the copied default model and verifies that
every non-DATA resource block is byte-identical. Intentionally invisible targets need no material
group; only two true material-only model no-ops remain deliberately skipped. The 112 confidently
matched material redirects handle variants that can be neutralized without model rewriting.

The earlier 0.5.1 loose `dota_defaultmodels` deployment generated correct files but was not loaded
by current Dota. Version 0.6 migrates and safely removes that owned legacy output after installing
the recognized-language VPK.

The desktop UI exposes five working categories: standard wearables, Persona models, hero and model
swaps, standalone additional attachments, and particles/effects. The hero/model-swap category is based on
Dota's schema mechanics (`entity_model`, `base_model`, `hero_model_change`, and model-to-model
rules), not names or file paths. It includes many Arcana structures and some non-Arcana special
items, but it is not a perfect "all Arcanas" filter: Arcana parts using ordinary wearable fields
remain under standard wearables. A Persona or special item's related mappings—including its extra
attachments—stay with the parent category to avoid applying only half of a transformation.

## End-user requirements

- 64-bit Windows.
- An installed Dota 2 client.

The packaged release includes Python plus self-contained VPK and model-skin helpers. End users do not install Python, .NET, ValvePak, ValveResourceFormat, or Source 2 Viewer separately.

## Use the Windows release

Extract the release ZIP and double-click `Dota2CosmeticDisabler.exe`. With no command-line
arguments, it opens a wide native dashboard that:

- Auto-detects the Dota 2 installation and lets you browse when detection is unavailable.
- Shows the installed Dota build, the build used for the existing overrides, and a clear status badge.
- Lets you select the recognized Dota language used as the compatibility mount; Dutch is recommended.
- Provides working toggles for the five supported model/effect categories.
- Shows animation/audio, icons/UI, and couriers/world as disabled planned categories.
- Builds and cleans on a background worker while streaming progress into the activity panel.
- Opens the report/output folders and copies the required Steam launch option.

The mount-language and category selections are saved locally in `.work/ui-settings.json`. The
selected language is recorded in the generated marker and version history, while categories are
also recorded in the mapping report. Keep the dashboard open while a build or cleanup is running.

The existing CLI remains available. Open PowerShell in the extracted folder and run:

```powershell
.\Dota2CosmeticDisabler.exe build
```

Steam libraries are auto-detected. If Dota is installed somewhere unusual, pass its root explicitly:

```powershell
.\Dota2CosmeticDisabler.exe build `
  --dota "D:\SteamLibrary\steamapps\common\dota 2 beta"
```

To build only selected categories, repeat `--category` with one or more of
`standard_wearables`, `persona_models`, `special_models`, `additional_wearables`, and
`particle_effects`. Omitting the flag enables every supported category. The VPK is
replaced as one unit, so narrowing the selection cannot leave disabled-category files active.

The default output is:

```text
<dota 2 beta>\game\dota_dutch\pak98_dir.vpk
```

`pak98` is preferred, but the tool automatically uses the next free owned slot down to `pak90`
without overwriting another mod or language pack.

The desktop selector offers every recognized compatibility mount and keeps Dutch first as the
recommended, live-tested default. The selected name is only a mount identifier: the build derives
matching English localization resources from the installed game so the interface stays English.
When you rebuild after changing the selection, the tool removes only its marker-owned archive from
the previous recognized-language mount and preserves every unrelated file.

After a successful build, add this Dota launch option in Steam:

```text
-language dutch
```

Remove that launch option to disable the override immediately.

For the CLI, choose a different recognized mount with `--language`, for example:

```powershell
.\Dota2CosmeticDisabler.exe build --language finnish
```

Then use the matching `-language finnish` Steam option. The `english` CLI alias resolves to the
recommended `dutch` compatibility mount; arbitrary names such as `defaultmodels` are rejected.

Every successful build records the installed Dota version. The preferred identity is Steam's
`buildid` from `steamapps\appmanifest_570.acf`; when that metadata is unavailable, the tool records
the size and modification time of `pak01_dir.vpk` as a fallback.

## Check whether Dota changed

Run this at any time without rebuilding:

```powershell
.\Dota2CosmeticDisabler.exe status
```

The result is:

- `CURRENT` when the installed Dota version matches the version used for the generated overrides.
- `STALE` when Dota changed after the last successful disabler build; run `build` again.
- `UNKNOWN` for an older build that has no version record; run `build` once to add one.
- `NOT BUILT` when no owned generated-output marker exists for that language.
- `LEGACY` when only the obsolete loose `dota_defaultmodels` build exists; rebuild to migrate it.
- `BROKEN` when the owned VPK is missing or its SHA-256 no longer matches the marker.

Use `--dota` and `--language` with `status` the same way as with `build`. Add `--json` for a
machine-readable result. Status compares the installed Steam build ID (or VPK stamp fallback) and
verifies the SHA-256 of the complete owned archive.

Successful runs are also appended to `.work\dota-version-history.json`. To show the ten most
recent entries:

```powershell
.\Dota2CosmeticDisabler.exe history
```

Use `history --limit 25`, `history --json`, or `history --work <path>` when needed. `clean` removes
only generated Dota files and their owned marker; it intentionally retains this history.

## Rebuild after a Dota update

Run the same `build` command again. The current schemas and required override resources are
re-extracted, packed, reopened, CRC-validated, and installed over the previously owned VPK with
archive/marker rollback on persistence failure.

Before rebuilding, the tool reports whether the previous disabler run used the same Dota build.
The generator aborts before modifying the Dota directory if Dota changes while the build is
running or if any required replacement source resources are missing. Reviewed virtual/retired
schema particle defaults use the installed game's neutral particle and are counted separately in
the report; an unexpected missing particle also uses neutral but emits a notice for investigation.
Models and particle snapshots still fail closed. For investigation only, `--allow-missing` opts
into a partial build and records the omissions in `.work/missing-resources.json`.

## Remove generated files

```powershell
.\Dota2CosmeticDisabler.exe clean
```

If Dota was not auto-detected during the build, pass the same `--dota` and optional `--language` values to `clean`. You can also remove the Steam launch option first; the generated files are inert unless that language path is selected.

## Analyze extracted schemas without touching Dota

```powershell
.\Dota2CosmeticDisabler.exe analyze `
  --items-game .\scripts\items\items_game.txt `
  --npc-heroes .\scripts\npc\npc_heroes.txt `
  --npc-units .\scripts\npc\npc_units.txt `
  --report .\model-plan.json
```

The report defines each mapping as `source -> target`: the compiled source model, material,
particle, or particle snapshot is copied over the target cosmetic resource path. Supplying
`--npc-units` enables summon and ward defaults during standalone analysis.

## Internal compiled-resource helpers

The application extracts only explicitly requested resources from Dota's `pak01_dir.vpk`, plus the
English localization files needed to keep the selected recognized-language mount English-compatible. Its
narrow, path-hardened helper is backed by [ValvePak](https://github.com/ValveResourceFormat/ValvePak),
pinned to version 4.0.0.142. The helper creates the output VPK and reopens every packed entry with
CRC validation before deployment.

Skin-sensitive model copies are handled by a separate narrow helper backed by
[ValveResourceFormat](https://github.com/ValveResourceFormat/ValveResourceFormat) 15.0.4937. It
changes only the model's material-group KV3 data, reparses the result, and rejects the output unless
all non-DATA compiled blocks remain byte-identical. ValvePak and ValveResourceFormat are included
under the MIT license; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). No Valve game assets are distributed with this project or its release package.

## Development from source

Maintained Python code lives in the layered `dota_disabler` package. Schema parsing, planning rules,
conflict resolution, resource-aware fallbacks, VPK operations, deployment/version history,
application services, CLI, and GUI adapters have separate modules. `disable_cosmetics.py` and
`disabler_gui.py` are compatibility launchers only; architecture tests keep maintained behavior
from drifting back into them or creating adapter-to-core dependency cycles.

Source development requires Python 3.10+, the .NET 8 SDK for the VPK helper, and the .NET 9 SDK for
the model-skin helper. Build both helpers, then run the tests:

```powershell
dotnet build .\tools\VpkExtractor\VpkExtractor.csproj --configuration Release
dotnet build .\tools\ModelPatcher\ModelPatcher.csproj --configuration Release
python -m unittest discover -s tests -v
```

The Python source automatically locates the Release-mode extractor. Run without arguments for the
desktop dashboard, or use an explicit CLI command:

```powershell
python .\disable_cosmetics.py
python .\disable_cosmetics.py build
```

The source tests cover package boundaries and compatibility facades, deterministic conflicts,
item-scoped skin regressions, KeyValues parsing, Dota version comparison and history, schema-driven
category selection, particle defaults/suppression/chains, UI settings/view state, path traversal
rejection, manifest-owned deployment and rollback, unowned-target protection, conservative cleanup, and real
VPK extraction against a generated fixture. The release workflow additionally runs the hidden-window
GUI smoke test and the packaged end-to-end test. A Dota installation is still required for a full
live build because Valve schemas and compiled override resources are not committed here.

Maintainers can run the non-deploying current-install gate on a drive with several GB free:

```powershell
python .\scripts\audit_live_install.py --pack --temp-root .\.work
```

It uses an automatically removed temporary directory, verifies every final mapping source, and
packs/reopens the complete temporary VPK with CRC validation without writing under the Dota folder.

## Build the self-contained release

Install the pinned build-only packager, then run the release script:

```powershell
python -m pip install -r .\requirements-build.txt
.\scripts\build_release.ps1 -Python python
```

The script verifies Tcl/Tk, publishes both self-contained .NET helpers, runs the source tests, embeds
the helpers into a single-file Windows application, and runs both the packaged GUI smoke test and
the isolated synthetic packaged build/status/history/clean lifecycle. It then writes the executable
checksum, creates `artifacts\Dota2CosmeticDisabler-<version>-win-x64.zip`, and writes an adjacent
SHA-256 file for the ZIP.

## Why the recognized-language VPK path is expected to work

Dota's current `gameinfo.gi` mounts `dota_*LANGUAGE*` ahead of the normal `dota` game path. Current
maintained mod tooling uses a recognized language name and numbered VPK rather than an arbitrary
loose-file language folder. This project defaults English users to the recognized but otherwise
unused `dutch` mount by default, lets users choose another recognized mount, and derives matching
English localization resources from their own installed Dota data. Compiled particle, snapshot,
material, and ordinary model resources are copied unchanged. Selected skin-sensitive `.vmdl_c`
copies have only their material-group KV3 data reserialized; geometry and every other compiled block
are preserved byte-for-byte.

- [Tracked Dota `gameinfo.gi`](https://github.com/SteamDatabase/GameTracking-Dota2/blob/master/game/dota/gameinfo.gi)
- [ValvePak VPK library](https://github.com/ValveResourceFormat/ValvePak)
- [ValveResourceFormat Source 2 resource library](https://github.com/ValveResourceFormat/ValveResourceFormat)

## License and affiliation

The project source is available under the [MIT License](LICENSE). Bundled dependencies retain their
own licenses as recorded in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

This project is not affiliated with, endorsed by, or supported by Valve Corporation. Dota and Dota 2
are trademarks of Valve Corporation. No Valve game assets are distributed by this repository or in
its release packages.
