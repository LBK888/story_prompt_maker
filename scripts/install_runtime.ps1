[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $projectRoot "runtime"
$llamaTarget = Join-Path $runtimeRoot "llama.cpp"
$ffmpegTarget = Join-Path $runtimeRoot "ffmpeg"
$downloadRoot = Join-Path $env:TEMP ("vid-auto-prompt-runtime-" + [guid]::NewGuid().ToString("N"))

$llamaVersion = "b10375"
$llamaAssets = @(
    @{
        Name = "llama-b10375-bin-win-cuda-13.3-x64.zip"
        Url = "https://github.com/ggml-org/llama.cpp/releases/download/b10375/llama-b10375-bin-win-cuda-13.3-x64.zip"
        Sha256 = "5e352df7d32abe99427160d26069e8eedab79ae08fbfe737616c6cd62837975a"
    },
    @{
        Name = "cudart-llama-bin-win-cuda-13.3-x64.zip"
        Url = "https://github.com/ggml-org/llama.cpp/releases/download/b10375/cudart-llama-bin-win-cuda-13.3-x64.zip"
        Sha256 = "1462a050eb4c684921ba51dcc4cc488a036674c3e73e9945ee705b854808d03e"
    }
)

function Get-VerifiedDownload {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][string]$ExpectedSha256
    )

    Write-Host "Downloading $([IO.Path]::GetFileName($Destination))..."
    Invoke-WebRequest -Uri $Url -OutFile $Destination -UseBasicParsing
    $actual = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLowerInvariant()
    $expected = $ExpectedSha256.ToLowerInvariant().Replace("sha256:", "")
    if ($actual -ne $expected) {
        throw "SHA-256 mismatch for $Destination. Expected $expected, got $actual."
    }
}

function Copy-ArchiveContents {
    param(
        [Parameter(Mandatory = $true)][string]$Archive,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][string]$ExtractName
    )

    $extractPath = Join-Path $downloadRoot $ExtractName
    Expand-Archive -LiteralPath $Archive -DestinationPath $extractPath -Force
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    Get-ChildItem -LiteralPath $extractPath -Force | Copy-Item -Destination $Destination -Recurse -Force
}

try {
    New-Item -ItemType Directory -Path $downloadRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null

    $llamaMarker = Join-Path $llamaTarget ".version"
    $llamaReady = (Test-Path (Join-Path $llamaTarget "llama-server.exe")) -and
        (Test-Path $llamaMarker) -and
        ((Get-Content -LiteralPath $llamaMarker -Raw).Trim() -eq $llamaVersion)
    if ($llamaReady) {
        Write-Host "llama.cpp $llamaVersion is already installed."
    }
    else {
        foreach ($asset in $llamaAssets) {
            $archivePath = Join-Path $downloadRoot $asset.Name
            Get-VerifiedDownload -Url $asset.Url -Destination $archivePath -ExpectedSha256 $asset.Sha256
            Copy-ArchiveContents -Archive $archivePath -Destination $llamaTarget -ExtractName ([IO.Path]::GetFileNameWithoutExtension($asset.Name))
        }
        if (-not (Test-Path (Join-Path $llamaTarget "llama-server.exe"))) {
            throw "llama-server.exe was not found after extracting llama.cpp."
        }
        Set-Content -LiteralPath $llamaMarker -Value $llamaVersion -Encoding ascii
        Write-Host "Installed llama.cpp $llamaVersion."
    }

    if ((Test-Path (Join-Path $ffmpegTarget "bin\ffmpeg.exe")) -and
        (Test-Path (Join-Path $ffmpegTarget "bin\ffprobe.exe"))) {
        Write-Host "FFmpeg is already installed."
    }
    else {
        Write-Host "Resolving the verified BtbN FFmpeg Windows build..."
        $headers = @{ "User-Agent" = "vid-auto-prompt-installer" }
        $release = Invoke-RestMethod -Uri "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest" -Headers $headers
        $assetName = "ffmpeg-master-latest-win64-gpl-shared.zip"
        $asset = $release.assets | Where-Object { $_.name -eq $assetName } | Select-Object -First 1
        if (-not $asset) {
            throw "The expected FFmpeg asset '$assetName' was not found in the latest BtbN release."
        }
        if (-not $asset.digest -or -not $asset.digest.StartsWith("sha256:")) {
            throw "GitHub did not provide a SHA-256 digest for the FFmpeg asset."
        }

        $ffmpegArchive = Join-Path $downloadRoot $assetName
        Get-VerifiedDownload -Url $asset.browser_download_url -Destination $ffmpegArchive -ExpectedSha256 $asset.digest
        $ffmpegExtract = Join-Path $downloadRoot "ffmpeg-extracted"
        Expand-Archive -LiteralPath $ffmpegArchive -DestinationPath $ffmpegExtract -Force
        $ffmpegExe = Get-ChildItem -LiteralPath $ffmpegExtract -Filter "ffmpeg.exe" -File -Recurse | Select-Object -First 1
        $ffprobeExe = Get-ChildItem -LiteralPath $ffmpegExtract -Filter "ffprobe.exe" -File -Recurse | Select-Object -First 1
        if (-not $ffmpegExe -or -not $ffprobeExe) {
            throw "ffmpeg.exe or ffprobe.exe was not found in the downloaded archive."
        }

        $distributionRoot = Split-Path -Parent (Split-Path -Parent $ffmpegExe.FullName)
        New-Item -ItemType Directory -Path $ffmpegTarget -Force | Out-Null
        Get-ChildItem -LiteralPath $distributionRoot -Force | Copy-Item -Destination $ffmpegTarget -Recurse -Force
        Set-Content -LiteralPath (Join-Path $ffmpegTarget ".version") -Value ("BtbN/{0}`n{1}`n{2}" -f $release.tag_name, $asset.updated_at, $asset.digest) -Encoding ascii
        Write-Host "Installed verified FFmpeg build."
    }
}
finally {
    if (Test-Path -LiteralPath $downloadRoot) {
        Remove-Item -LiteralPath $downloadRoot -Recurse -Force
    }
}
