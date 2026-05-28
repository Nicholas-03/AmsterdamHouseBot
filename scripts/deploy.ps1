[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [Alias("Host", "DropletIp")]
    [string]$DropletHost,

    [string]$User = "root",

    [string]$IdentityFile
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Copy-FilteredProject {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Source,

        [Parameter(Mandatory = $true)]
        [string]$Destination
    )

    $excludedDirectories = @(
        ".git",
        ".venv",
        "venv",
        "env",
        "ENV",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache"
    )
    $excludedFileNames = @(
        ".env",
        "listings.db",
        "kamernet_reply_message.txt",
        "kamernet_storage_state.json",
        "funda_reply_message.txt",
        "funda_storage_state.json",
        "roofz_reply_message.txt"
    )
    $excludedExtensions = @(".db", ".sqlite3")

    New-Item -ItemType Directory -Force -Path $Destination | Out-Null

    foreach ($item in Get-ChildItem -LiteralPath $Source -Force) {
        if ($item.PSIsContainer) {
            if ($excludedDirectories -contains $item.Name) {
                continue
            }

            Copy-FilteredProject `
                -Source $item.FullName `
                -Destination (Join-Path $Destination $item.Name)
            continue
        }

        if ($excludedFileNames -contains $item.Name) {
            continue
        }

        if ($excludedExtensions -contains $item.Extension) {
            continue
        }

        Copy-Item -LiteralPath $item.FullName -Destination (Join-Path $Destination $item.Name)
    }
}

function Require-Command {
    param([Parameter(Mandatory = $true)][string]$Name)

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found. Install OpenSSH client or add it to PATH."
    }
}

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$ArgumentList
    )

    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "'$FilePath' failed with exit code $LASTEXITCODE."
    }
}

function New-PortableZip {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SourceDirectory,

        [Parameter(Mandatory = $true)]
        [string]$ArchivePath
    )

    $sourceFullPath = (Resolve-Path -LiteralPath $SourceDirectory).Path
    if (-not $sourceFullPath.EndsWith([System.IO.Path]::DirectorySeparatorChar)) {
        $sourceFullPath += [System.IO.Path]::DirectorySeparatorChar
    }

    $zip = [System.IO.Compression.ZipFile]::Open(
        $ArchivePath,
        [System.IO.Compression.ZipArchiveMode]::Create
    )

    try {
        foreach ($file in Get-ChildItem -LiteralPath $SourceDirectory -Recurse -File -Force) {
            $relativePath = $file.FullName.Substring($sourceFullPath.Length).Replace("\", "/")
            [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                $zip,
                $file.FullName,
                $relativePath,
                [System.IO.Compression.CompressionLevel]::Optimal
            ) | Out-Null
        }
    }
    finally {
        $zip.Dispose()
    }
}

Require-Command "ssh"
Require-Command "scp"
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$repoRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $repoRoot ".env"
$bootstrapPath = Join-Path $repoRoot "scripts\vps_bootstrap.sh"

if (-not (Test-Path -LiteralPath $envPath)) {
    throw "Missing .env in project root. Create it before deploying."
}

if (-not (Test-Path -LiteralPath $bootstrapPath)) {
    throw "Missing VPS bootstrap script at $bootstrapPath."
}

$identityArgs = @()
if ($IdentityFile) {
    $resolvedIdentity = (Resolve-Path -LiteralPath $IdentityFile).Path
    $identityArgs = @("-i", $resolvedIdentity)
}

$timestamp = Get-Date -Format "yyyyMMddHHmmss"
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) "amsterdam-house-bot-deploy-$timestamp"
$staging = Join-Path $tempRoot "package"
$archive = Join-Path $tempRoot "amsterdam-house-bot.zip"
$envLfPath = Join-Path $tempRoot "bot.env"
$bootstrapLfPath = Join-Path $tempRoot "vps_bootstrap.sh"
$target = "${User}@${DropletHost}"
$remoteArchive = "/tmp/amsterdam-house-bot-$timestamp.zip"
$remoteEnv = "/tmp/amsterdam-house-bot-$timestamp.env"
$remoteBootstrap = "/tmp/amsterdam-house-bot-bootstrap-$timestamp.sh"

try {
    Write-Host "Packaging project from $repoRoot"
    Copy-FilteredProject -Source $repoRoot -Destination $staging
    New-PortableZip -SourceDirectory $staging -ArchivePath $archive

    Write-Host "Uploading package to $target"
    Invoke-Native "scp" @identityArgs $archive "${target}:$remoteArchive"

    Write-Host "Uploading environment file"
    $envContent = [System.IO.File]::ReadAllText($envPath).Replace("`r`n", "`n").Replace("`r", "`n")
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($envLfPath, $envContent, $utf8NoBom)
    Invoke-Native "scp" @identityArgs $envLfPath "${target}:$remoteEnv"

    Write-Host "Uploading bootstrap script"
    $bootstrapContent = [System.IO.File]::ReadAllText($bootstrapPath).Replace("`r`n", "`n")
    [System.IO.File]::WriteAllText($bootstrapLfPath, $bootstrapContent, $utf8NoBom)
    Invoke-Native "scp" @identityArgs $bootstrapLfPath "${target}:$remoteBootstrap"

    Write-Host "Running remote bootstrap"
    Invoke-Native "ssh" @identityArgs $target "chmod +x '$remoteBootstrap' && bash '$remoteBootstrap' '$remoteArchive' '$remoteEnv'"

    Write-Host ""
    Write-Host "Deployment complete."
    Write-Host "Check logs with: ssh $target `"journalctl -u amsterdam-house-bot -f`""
}
finally {
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}
