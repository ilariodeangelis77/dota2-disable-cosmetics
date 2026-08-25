[CmdletBinding()]
param(
    [string]$Python = "python",
    [ValidateSet("auto", "win-x64", "linux-x64", "osx-x64", "osx-arm64")]
    [string]$Runtime = "auto"
)

$ErrorActionPreference = "Stop"
$runtimeInformation = [System.Runtime.InteropServices.RuntimeInformation]
$osPlatform = [System.Runtime.InteropServices.OSPlatform]
$onWindows = $runtimeInformation::IsOSPlatform($osPlatform::Windows)
$onLinux = $runtimeInformation::IsOSPlatform($osPlatform::Linux)
$onMacOS = $runtimeInformation::IsOSPlatform($osPlatform::OSX)
$hostArchitecture = $runtimeInformation::OSArchitecture.ToString().ToLowerInvariant()

$expectedRuntime = if ($onWindows -and $hostArchitecture -eq "x64") {
    "win-x64"
} elseif ($onLinux -and $hostArchitecture -eq "x64") {
    "linux-x64"
} elseif ($onMacOS -and $hostArchitecture -eq "x64") {
    "osx-x64"
} elseif ($onMacOS -and $hostArchitecture -eq "arm64") {
    "osx-arm64"
} else {
    throw "Unsupported release host: $($runtimeInformation::OSDescription) $hostArchitecture."
}

if ($Runtime -eq "auto") {
    $Runtime = $expectedRuntime
} elseif ($Runtime -ne $expectedRuntime) {
    throw "Runtime '$Runtime' must be built on its native '$expectedRuntime' host."
}

$executableSuffix = if ($onWindows) { ".exe" } else { "" }
$archiveExtension = if ($onWindows) { ".zip" } else { ".tar.gz" }
$binarySeparator = if ($onWindows) { ";" } else { ":" }
$applicationFilename = "Dota2CosmeticDisabler$executableSuffix"
$extractorFilename = "Dota2VpkExtractor$executableSuffix"
$modelPatcherFilename = "Dota2ModelSkinPatcher$executableSuffix"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$buildRoot = Join-Path $projectRoot "build"
$extractorOutput = Join-Path $buildRoot "vpk-extractor"
$modelPatcherOutput = Join-Path $buildRoot "model-patcher"
$pyInstallerWork = Join-Path $buildRoot "pyinstaller"
$pyInstallerDist = Join-Path $buildRoot "package"

$versionFile = Join-Path $projectRoot "dota_disabler/version.py"
$versionLine = Select-String -LiteralPath $versionFile -Pattern '^VERSION = "([^"]+)"$'
if (-not $versionLine) {
    throw "Could not read the application version from dota_disabler/version.py."
}
$version = $versionLine.Matches[0].Groups[1].Value
$releaseName = "Dota2CosmeticDisabler-$version-$Runtime"
$artifactsRoot = Join-Path $projectRoot "artifacts"
$releaseDirectory = Join-Path $artifactsRoot $releaseName
$releaseArchive = "$releaseDirectory$archiveExtension"
$localDotnet9 = Join-Path $projectRoot ".work/dotnet9/dotnet$executableSuffix"
$dotnet = (Get-Command dotnet -ErrorAction Stop).Source
$dotnet9 = if (Test-Path -LiteralPath $localDotnet9 -PathType Leaf) {
    $localDotnet9
} else {
    $dotnet
}
$previousTestExtractor = $env:DOTA2_COSMETIC_DISABLER_TEST_EXTRACTOR

Push-Location $projectRoot
try {
    & $Python -c "import tkinter as tk; root = tk.Tk(); root.withdraw(); root.update_idletasks(); root.destroy()"
    if ($LASTEXITCODE -ne 0) {
        throw "The selected Python runtime does not have a working Tcl/Tk installation."
    }

    & $dotnet publish "tools/VpkExtractor/VpkExtractor.csproj" `
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

    & $dotnet9 publish "tools/ModelPatcher/ModelPatcher.csproj" `
        --configuration Release `
        --runtime $Runtime `
        --self-contained true `
        --output $modelPatcherOutput `
        --configfile "NuGet.Config" `
        --packages (Join-Path $projectRoot ".work/nuget-model-patcher") `
        -p:PublishSingleFile=true `
        -p:PublishTrimmed=true `
        -p:TrimMode=link `
        -p:DebugType=None
    if ($LASTEXITCODE -ne 0) {
        throw "The model skin patcher publish failed. Install the .NET 9 SDK."
    }

    $extractorBinary = Join-Path $extractorOutput $extractorFilename
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

    $modelPatcherBinary = Join-Path $modelPatcherOutput $modelPatcherFilename
    if (-not (Test-Path -LiteralPath $modelPatcherBinary -PathType Leaf)) {
        throw "Published model skin patcher was not found: $modelPatcherBinary"
    }
    & $modelPatcherBinary --version
    if ($LASTEXITCODE -ne 0) {
        throw "The published model skin patcher failed its version smoke test."
    }

    $pyInstallerArguments = @(
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile"
    )
    if ($onWindows) {
        $pyInstallerArguments += @("--hide-console", "hide-early")
    }
    $pyInstallerArguments += @(
        "--noupx",
        "--name", "Dota2CosmeticDisabler",
        "--distpath", $pyInstallerDist,
        "--workpath", $pyInstallerWork,
        "--specpath", $buildRoot,
        "--add-binary", "${extractorBinary}${binarySeparator}tools",
        "--add-binary", "${modelPatcherBinary}${binarySeparator}tools",
        "disable_cosmetics.py"
    )
    & $Python @pyInstallerArguments
    if ($LASTEXITCODE -ne 0) {
        throw "The application packaging step failed."
    }

    if (Test-Path -LiteralPath $releaseDirectory) {
        Remove-Item -LiteralPath $releaseDirectory -Recurse -Force
    }
    if (Test-Path -LiteralPath $releaseArchive) {
        Remove-Item -LiteralPath $releaseArchive -Force
    }
    if (Test-Path -LiteralPath "$releaseArchive.sha256") {
        Remove-Item -LiteralPath "$releaseArchive.sha256" -Force
    }
    New-Item -ItemType Directory -Path $releaseDirectory | Out-Null

    $applicationBinary = Join-Path $pyInstallerDist $applicationFilename
    Copy-Item -LiteralPath $applicationBinary -Destination $releaseDirectory
    foreach ($document in ("README.md", "THIRD_PARTY_NOTICES.md")) {
        Copy-Item -LiteralPath $document -Destination $releaseDirectory
    }
    $readmeImageSource = Join-Path $projectRoot "docs/images/dashboard.png"
    $readmeImageDirectory = Join-Path $releaseDirectory "docs/images"
    New-Item -ItemType Directory -Path $readmeImageDirectory -Force | Out-Null
    Copy-Item -LiteralPath $readmeImageSource -Destination $readmeImageDirectory

    $releaseBinary = Join-Path $releaseDirectory $applicationFilename
    if (-not $onWindows) {
        & chmod +x $releaseBinary
        if ($LASTEXITCODE -ne 0) {
            throw "Could not make the packaged application executable."
        }
    }
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
    "$($hash.Hash.ToLowerInvariant())  $applicationFilename" |
        Set-Content -LiteralPath (Join-Path $releaseDirectory "SHA256SUMS.txt") -Encoding ascii

    if ($onWindows) {
        Compress-Archive -LiteralPath $releaseDirectory -DestinationPath $releaseArchive -CompressionLevel Optimal
    } else {
        & tar -czf $releaseArchive -C $artifactsRoot $releaseName
        if ($LASTEXITCODE -ne 0) {
            throw "Could not create the release archive."
        }
    }
    $archiveHash = Get-FileHash -Algorithm SHA256 -LiteralPath $releaseArchive
    "$($archiveHash.Hash.ToLowerInvariant())  $([IO.Path]::GetFileName($releaseArchive))" |
        Set-Content -LiteralPath "$releaseArchive.sha256" -Encoding ascii
    Write-Host "Release created: $releaseArchive"
}
finally {
    $env:DOTA2_COSMETIC_DISABLER_TEST_EXTRACTOR = $previousTestExtractor
    Pop-Location
}
