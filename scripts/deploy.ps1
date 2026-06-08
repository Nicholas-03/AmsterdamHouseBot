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
        ".pytest_cache",
        "output",
        "assets"
    )
    $excludedFileNames = @(
        ".env",
        "listings.db",
        "kamernet_reply_message.txt",
        "kamernet_storage_state.json",
        "funda_reply_message.txt",
        "funda_storage_state.json",
        "pararius_reply_message.txt",
        "pararius_storage_state.json",
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

function New-SafeRemoteDocumentName {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Prefix,

        [Parameter(Mandatory = $true)]
        [int]$Index,

        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $baseName = [System.IO.Path]::GetFileName($Path)
    $safeBaseName = [regex]::Replace($baseName, "[^A-Za-z0-9._-]", "_")
    return "$Prefix-$Index-$safeBaseName"
}

function Copy-RoofzDocumentForDeploy {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RawPath,

        [Parameter(Mandatory = $true)]
        [string]$Prefix,

        [Parameter(Mandatory = $true)]
        [int]$Index,

        [Parameter(Mandatory = $true)]
        [string]$DocumentsDirectory,

        [Parameter(Mandatory = $true)]
        [string]$RemoteDocumentsDirectory
    )

    $trimmedPath = $RawPath.Trim().Trim('"')
    if (-not $trimmedPath) {
        return ""
    }

    if ($trimmedPath.StartsWith("/")) {
        return @{
            RemotePath = $trimmedPath
            Copied = $false
        }
    }

    $resolvedPath = (Resolve-Path -LiteralPath $trimmedPath).Path
    $remoteName = New-SafeRemoteDocumentName -Prefix $Prefix -Index $Index -Path $resolvedPath
    Copy-Item -LiteralPath $resolvedPath -Destination (Join-Path $DocumentsDirectory $remoteName)
    return @{
        RemotePath = "$RemoteDocumentsDirectory/$remoteName"
        Copied = $true
    }
}

function Convert-EnvForRemoteRoofzDocuments {
    param(
        [Parameter(Mandatory = $true)]
        [string]$EnvContent,

        [Parameter(Mandatory = $true)]
        [string]$DocumentsDirectory,

        [Parameter(Mandatory = $true)]
        [string]$RemoteDocumentsDirectory
    )

    $singleDocumentKeys = @{
        "ROOFZ_COMPLETE_ID_DOCUMENT_PATH" = "identity-document"
        "ROOFZ_COMPLETE_EDUCATIONAL_REGISTRATION_PATH" = "educational-registration"
        "ROOFZ_COMPLETE_DEED_OF_GUARANTEE_PATH" = "deed-of-guarantee"
    }
    $listDocumentKeys = @{
        "ROOFZ_COMPLETE_SALARY_SLIP_PATHS" = "salary-slip"
        "ROOFZ_COMPLETE_BANK_STATEMENT_PATHS" = "bank-statement"
    }

    New-Item -ItemType Directory -Force -Path $DocumentsDirectory | Out-Null

    $copiedCount = 0
    $rewrittenLines = foreach ($line in ($EnvContent -split "`n")) {
        if ($line -notmatch "^([^#=\s]+)=(.*)$") {
            $line
            continue
        }

        $key = $Matches[1]
        $value = $Matches[2]

        if ($singleDocumentKeys.ContainsKey($key)) {
            $document = Copy-RoofzDocumentForDeploy `
                -RawPath $value `
                -Prefix $singleDocumentKeys[$key] `
                -Index 1 `
                -DocumentsDirectory $DocumentsDirectory `
                -RemoteDocumentsDirectory $RemoteDocumentsDirectory
            if ($document.RemotePath) {
                if ($document.Copied) {
                    $copiedCount += 1
                }
                "$key=$($document.RemotePath)"
            }
            else {
                $line
            }
            continue
        }

        if ($listDocumentKeys.ContainsKey($key)) {
            $remotePaths = @()
            $index = 1
            foreach ($path in ($value -split ",")) {
                $trimmedPath = $path.Trim()
                if (-not $trimmedPath) {
                    continue
                }
                $document = Copy-RoofzDocumentForDeploy `
                    -RawPath $trimmedPath `
                    -Prefix $listDocumentKeys[$key] `
                    -Index $index `
                    -DocumentsDirectory $DocumentsDirectory `
                    -RemoteDocumentsDirectory $RemoteDocumentsDirectory
                if ($document.RemotePath) {
                    $remotePaths += $document.RemotePath
                    if (-not $document.RemotePath.StartsWith("/")) {
                        throw "Unexpected non-absolute remote document path for $key."
                    }
                    if ($document.Copied) {
                        $copiedCount += 1
                    }
                }
                $index += 1
            }
            if ($remotePaths.Count -gt 0) {
                "$key=$($remotePaths -join ',')"
            }
            else {
                $line
            }
            continue
        }

        $line
    }

    return @{
        Content = ($rewrittenLines -join "`n")
        CopiedCount = $copiedCount
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
$documentsStaging = Join-Path $tempRoot "roofz-documents"
$archive = Join-Path $tempRoot "amsterdam-house-bot.zip"
$documentsArchive = Join-Path $tempRoot "roofz-documents.zip"
$envLfPath = Join-Path $tempRoot "bot.env"
$bootstrapLfPath = Join-Path $tempRoot "vps_bootstrap.sh"
$target = "${User}@${DropletHost}"
$remoteArchive = "/tmp/amsterdam-house-bot-$timestamp.zip"
$remoteDocumentsArchive = "/tmp/amsterdam-house-bot-documents-$timestamp.zip"
$remoteEnv = "/tmp/amsterdam-house-bot-$timestamp.env"
$remoteBootstrap = "/tmp/amsterdam-house-bot-bootstrap-$timestamp.sh"
$remoteDocumentsDirectory = "/var/lib/amsterdam-house-bot/roofz-documents"

try {
    Write-Host "Packaging project from $repoRoot"
    Copy-FilteredProject -Source $repoRoot -Destination $staging
    New-PortableZip -SourceDirectory $staging -ArchivePath $archive

    Write-Host "Uploading package to $target"
    Invoke-Native "scp" @identityArgs $archive "${target}:$remoteArchive"

    Write-Host "Uploading environment file"
    $envContent = [System.IO.File]::ReadAllText($envPath).Replace("`r`n", "`n").Replace("`r", "`n")
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $documentEnv = Convert-EnvForRemoteRoofzDocuments `
        -EnvContent $envContent `
        -DocumentsDirectory $documentsStaging `
        -RemoteDocumentsDirectory $remoteDocumentsDirectory
    [System.IO.File]::WriteAllText($envLfPath, $documentEnv.Content, $utf8NoBom)
    Invoke-Native "scp" @identityArgs $envLfPath "${target}:$remoteEnv"

    if ($documentEnv.CopiedCount -gt 0) {
        Write-Host "Uploading Roofz application documents"
        New-PortableZip -SourceDirectory $documentsStaging -ArchivePath $documentsArchive
        Invoke-Native "scp" @identityArgs $documentsArchive "${target}:$remoteDocumentsArchive"
    }

    Write-Host "Uploading bootstrap script"
    $bootstrapContent = [System.IO.File]::ReadAllText($bootstrapPath).Replace("`r`n", "`n")
    [System.IO.File]::WriteAllText($bootstrapLfPath, $bootstrapContent, $utf8NoBom)
    Invoke-Native "scp" @identityArgs $bootstrapLfPath "${target}:$remoteBootstrap"

    Write-Host "Running remote bootstrap"
    if ($documentEnv.CopiedCount -gt 0) {
        Invoke-Native "ssh" @identityArgs $target "chmod +x '$remoteBootstrap' && bash '$remoteBootstrap' '$remoteArchive' '$remoteEnv' '$remoteDocumentsArchive'"
    }
    else {
        Invoke-Native "ssh" @identityArgs $target "chmod +x '$remoteBootstrap' && bash '$remoteBootstrap' '$remoteArchive' '$remoteEnv'"
    }

    Write-Host ""
    Write-Host "Deployment complete."
    Write-Host "Check logs with: ssh $target `"journalctl -u amsterdam-house-bot -f`""
}
finally {
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}
