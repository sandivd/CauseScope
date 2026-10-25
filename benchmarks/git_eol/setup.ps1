# Download an upstream binary distribution into this workspace; no global install.
$ErrorActionPreference = 'Stop'
$benchmarkRoot = $PSScriptRoot
$workspaceRoot = [IO.Path]::GetFullPath((Join-Path $benchmarkRoot '..\..'))
$targetLock = Get-Content -LiteralPath (Join-Path $benchmarkRoot 'target.lock.json') -Raw | ConvertFrom-Json
if ($env:OS -ne 'Windows_NT' -or -not [Environment]::Is64BitOperatingSystem) {
    throw 'This exact benchmark pin supports Windows x86-64 only.'
}
$toolsDirectory = Join-Path $workspaceRoot '.tools'
$archivePath = Join-Path $toolsDirectory 'MinGit-2.45.2-64-bit.zip'
$installationDirectory = Join-Path $workspaceRoot $targetLock.installation_directory
New-Item -ItemType Directory -Path $toolsDirectory -Force | Out-Null
if (-not (Test-Path -LiteralPath $archivePath)) {
    Invoke-WebRequest -Uri $targetLock.archive_url -OutFile $archivePath -UseBasicParsing -TimeoutSec 120
}
$actualHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualHash -ne $targetLock.archive_sha256) {
    throw "Archive SHA256 mismatch: $actualHash. No extraction performed."
}
Expand-Archive -LiteralPath $archivePath -DestinationPath $installationDirectory -Force
$gitExecutable = Join-Path $installationDirectory $targetLock.executable
$actualVersion = & $gitExecutable --version
if ($LASTEXITCODE -ne 0 -or $actualVersion -ne $targetLock.version_stdout) {
    throw "Unsupported Git binary: $actualVersion"
}
Write-Output "Verified upstream archive SHA256: $actualHash"
Write-Output "$actualVersion at $gitExecutable"
