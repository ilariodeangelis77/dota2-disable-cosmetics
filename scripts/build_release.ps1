[CmdletBinding()]
param(
    [string]$Python = "python",
    [ValidateSet("win-x64")]
    [string]$Runtime = "win-x64"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$buildRoot = Join-Path $projectRoot "build"
$extractorOutput = Join-Path $buildRoot "vpk-extractor"
$modelPatcherOutput = Join-Path $buildRoot "model-patcher"
$pyInstallerWork = Join-Path $buildRoot "pyinstaller"
$pyInstallerDist = Join-Path $buildRoot "package"

$versionFile = Join-Path $projectRoot "dota_disabler\version.py"
$versionLine = Select-String -LiteralPath $versionFile -Pattern '^VERSION = "([^"]+)"$'
if (-not $versionLine) {
    throw "Could not read the application version from dota_disabler\version.py."
}
$version = $versionLine.Matches[0].Groups[1].Value
$releaseName = "Dota2CosmeticDisabler-$version-$Runtime"
$releaseDirectory = Join-Path (Join-Path $projectRoot "artifacts") $releaseName
$releaseArchive = "$releaseDirectory.zip"
$localDotnet9 = Join-Path $projectRoot ".work\dotnet9\dotnet.exe"
$dotnet9 = if (Test-Path -LiteralPath $localDotnet9 -PathType Leaf) {
    $localDotnet9
} else {
    (Get-Command dotnet -ErrorAction Stop).Source
}
$previousTestExtractor = $env:DOTA2_COSMETIC_DISABLER_TEST_EXTRACTOR

Push-Location $projectRoot
try {
    & $Python -c "import tkinter as tk; root = tk.Tk(); root.withdraw(); root.update_idletasks(); root.destroy()"
    if ($LASTEXITCODE -ne 0) {
        throw "The selected Python runtime does not have a working Tcl/Tk installation."
    }

    & dotnet publish "tools\VpkExtractor\VpkExtractor.csproj" `
        --configuration Release `
        --runtime $Runtime `
        --self-contained true `
        --output $extractorOutput `
        --configfile "NuGet.Config" `
        -p:PublishSingleFile=true `
        -p:PublishTrimmed=false `
        -p:DebugType=None
    if ($LASTEXITCODE -ne 0) {
        throw "The VPK extractor publish failed."
    }

    & $dotnet9 publish "tools\ModelPatcher\ModelPatcher.csproj" `
        --configuration Release `
        --runtime $Runtime `
        --self-contained true `
        --output $modelPatcherOutput `
        --configfile "NuGet.Config" `
        --packages (Join-Path $projectRoot ".work\nuget-model-patcher") `
        -p:PublishSingleFile=true `
        -p:PublishTrimmed=true `
        -p:TrimMode=link `
        -p:DebugType=None
    if ($LASTEXITCODE -ne 0) {
        throw "The model skin patcher publish failed. Install the .NET 9 SDK."
    }

    $extractorBinary = Join-Path $extractorOutput "Dota2VpkExtractor.exe"
    if (-not (Test-Path -LiteralPath $extractorBinary -PathType Leaf)) {
        throw "Published VPK extractor was not found: $extractorBinary"
    }
    $env:DOTA2_COSMETIC_DISABLER_TEST_EXTRACTOR = $extractorBinary

    & $Python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) {
        throw "The source test suite failed."
    }

    & $Python -m PyInstaller --version *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller is missing. Run: $Python -m pip install -r requirements-build.txt"
    }

    $modelPatcherBinary = Join-Path $modelPatcherOutput "Dota2ModelSkinPatcher.exe"
    if (-not (Test-Path -LiteralPath $modelPatcherBinary -PathType Leaf)) {
        throw "Published model skin patcher was not found: $modelPatcherBinary"
    }
    & $modelPatcherBinary --version
    if ($LASTEXITCODE -ne 0) {
        throw "The published model skin patcher failed its version smoke test."
    }

    & $Python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --hide-console hide-early `
        --noupx `
        --name "Dota2CosmeticDisabler" `
        --distpath $pyInstallerDist `
        --workpath $pyInstallerWork `
        --specpath $buildRoot `
        --add-binary "$extractorBinary;tools" `
        --add-binary "$modelPatcherBinary;tools" `
        "disable_cosmetics.py"
    if ($LASTEXITCODE -ne 0) {
        throw "The application packaging step failed."
    }

    if (Test-Path -LiteralPath $releaseDirectory) {
        Remove-Item -LiteralPath $releaseDirectory -Recurse -Force
    }
    if (Test-Path -LiteralPath $releaseArchive) {
        Remove-Item -LiteralPath $releaseArchive -Force
    }
    New-Item -ItemType Directory -Path $releaseDirectory | Out-Null

    $applicationBinary = Join-Path $pyInstallerDist "Dota2CosmeticDisabler.exe"
    Copy-Item -LiteralPath $applicationBinary -Destination $releaseDirectory
    Copy-Item -LiteralPath "README.md", "THIRD_PARTY_NOTICES.md" -Destination $releaseDirectory

    $releaseBinary = Join-Path $releaseDirectory "Dota2CosmeticDisabler.exe"
    & $releaseBinary --version
    if ($LASTEXITCODE -ne 0) {
        throw "The packaged application failed its version smoke test."
    }

    & $releaseBinary gui --smoke-test
    if ($LASTEXITCODE -ne 0) {
        throw "The packaged desktop UI failed its smoke test."
    }

    $previousPackagedApplication = $env:DOTA2_COSMETIC_DISABLER_EXE
    try {
        $env:DOTA2_COSMETIC_DISABLER_EXE = $releaseBinary
        & $Python -m unittest discover -s tests -v
        if ($LASTEXITCODE -ne 0) {
            throw "The packaged end-to-end test suite failed."
        }
    }
    finally {
        $env:DOTA2_COSMETIC_DISABLER_EXE = $previousPackagedApplication
    }

    $hash = Get-FileHash -Algorithm SHA256 -LiteralPath $releaseBinary
    "$($hash.Hash.ToLowerInvariant())  Dota2CosmeticDisabler.exe" |
        Set-Content -LiteralPath (Join-Path $releaseDirectory "SHA256SUMS.txt") -Encoding ascii

    Compress-Archive -LiteralPath $releaseDirectory -DestinationPath $releaseArchive -CompressionLevel Optimal
    $archiveHash = Get-FileHash -Algorithm SHA256 -LiteralPath $releaseArchive
    "$($archiveHash.Hash.ToLowerInvariant())  $([IO.Path]::GetFileName($releaseArchive))" |
        Set-Content -LiteralPath "$releaseArchive.sha256" -Encoding ascii
    Write-Host "Release created: $releaseArchive"
}
finally {
    $env:DOTA2_COSMETIC_DISABLER_TEST_EXTRACTOR = $previousTestExtractor
    Pop-Location
}
