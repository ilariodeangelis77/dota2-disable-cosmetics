[CmdletBinding()]
param(
    [string]$Python = "python",
    [ValidatePattern('^[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})*$')]
    [string[]]$AddLocale = @()
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$localesRoot = Join-Path $projectRoot "dota_disabler/locales"
$templatePath = Join-Path $localesRoot "ui.pot"
$versionFile = Join-Path $projectRoot "dota_disabler/version.py"
$versionLine = Select-String -LiteralPath $versionFile -Pattern '^VERSION = "([^"]+)"$'
if (-not $versionLine) {
    throw "Could not read the application version from dota_disabler/version.py."
}
$version = $versionLine.Matches[0].Groups[1].Value

function Invoke-Babel {
    param(
        [string[]]$BabelArguments,
        [string]$FailureMessage
    )

    & $Python -m babel.messages.frontend @BabelArguments
    if ($LASTEXITCODE -ne 0) {
        throw $FailureMessage
    }
}

function Normalize-CatalogEnd {
    param([string]$Path)

    $content = [IO.File]::ReadAllText($Path)
    $content = [Text.RegularExpressions.Regex]::Replace(
        $content,
        '[\r\n\t ]+\z',
        ''
    ) + [Environment]::NewLine
    [IO.File]::WriteAllText($Path, $content, [Text.UTF8Encoding]::new($false))
}

Push-Location $projectRoot
try {
    & $Python -c "import babel"
    if ($LASTEXITCODE -ne 0) {
        throw "Babel is unavailable. Install requirements-build.txt with the selected Python."
    }

    Invoke-Babel -BabelArguments @(
        "extract",
        "--no-default-keywords",
        "--keyword=_tr",
        "--keyword=translate",
        "--keyword=N_",
        "--keyword=ngettext:1,2",
        "--keyword=_set_translated_text:2",
        "--keyword=_show_status_error:2",
        "--sort-by-file",
        "--project=Dota 2 Cosmetic Disabler",
        "--version=$version",
        "--copyright-holder=Dota 2 Cosmetic Disabler contributors",
        "--msgid-bugs-address=https://github.com/ilariodeangelis77/dota2-disable-cosmetics/issues",
        "--output-file=dota_disabler/locales/ui.pot",
        "dota_disabler"
    ) -FailureMessage "Could not extract the GUI translation template."

    Invoke-Babel -BabelArguments @(
        "update",
        "--input-file=dota_disabler/locales/ui.pot",
        "--output-dir=dota_disabler/locales",
        "--domain=ui",
        "--no-fuzzy-matching",
        "--ignore-obsolete"
    ) -FailureMessage "Could not update the existing GUI translation catalogs."

    $initializedCatalogs = @()
    foreach ($localeCode in @($AddLocale | Sort-Object -Unique)) {
        $babelLocale = $localeCode.Replace("-", "_")
        $catalogPath = Join-Path $localesRoot "$babelLocale/LC_MESSAGES/ui.po"
        if (Test-Path -LiteralPath $catalogPath -PathType Leaf) {
            Write-Host "Translation catalog already exists: $catalogPath"
            continue
        }
        Invoke-Babel -BabelArguments @(
            "init",
            "--input-file=dota_disabler/locales/ui.pot",
            "--output-dir=dota_disabler/locales",
            "--domain=ui",
            "--locale=$babelLocale"
        ) -FailureMessage "Could not initialize GUI locale '$localeCode'."
        $initializedCatalogs += $catalogPath
    }

    Normalize-CatalogEnd -Path $templatePath
    Get-ChildItem -LiteralPath $localesRoot -Recurse -Filter "ui.po" -File |
        ForEach-Object { Normalize-CatalogEnd -Path $_.FullName }

    if ($initializedCatalogs.Count -gt 0) {
        Write-Host "Initialized untranslated catalog(s):"
        $initializedCatalogs | ForEach-Object { Write-Host "  $_" }
        Write-Host "Translate every empty msgstr, then rerun this command without -AddLocale."
        return
    }

    Invoke-Babel -BabelArguments @(
        "compile",
        "--directory=dota_disabler/locales",
        "--domain=ui",
        "--statistics"
    ) -FailureMessage "Could not compile the GUI translation catalogs."

    & $Python -m unittest tests.test_ui_i18n -v
    if ($LASTEXITCODE -ne 0) {
        throw "GUI catalogs are incomplete or no longer match the application source."
    }
    Write-Host "GUI translation template, catalogs, and compiled runtime files are current."
}
finally {
    Pop-Location
}
