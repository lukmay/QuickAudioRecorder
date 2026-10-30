# Build proc-tap from complete, verified upstream source on a native Windows runner.
param(
    [Parameter(Mandatory=$true)][ValidateSet('x64','arm64')][string]$Architecture,
    [string]$Python = 'python',
    [string]$Output = 'build/proctap-wheels'
)
$ErrorActionPreference = 'Stop'
$Commit = 'b7979443b554edc341f8a190a475714edf062b29'
$Sha256 = '8c1738d5c5ca4926b649a363d8f4e0ac0c1f2786c9f0d12ac65d0247d8b5d056'
& $Python -c "import sysconfig; assert sysconfig.get_platform() == {'x64':'win-amd64','arm64':'win-arm64'}['$Architecture'], sysconfig.get_platform()"
if ($LASTEXITCODE) { throw 'Python architecture does not match requested build' }
$Root = Join-Path (Get-Location) 'build/proctap-source'
New-Item -ItemType Directory -Force $Root, $Output | Out-Null
$Archive = Join-Path $Root 'source.tar.gz'
Invoke-WebRequest "https://api.github.com/repos/m96-chan/ProcTap/tarball/$Commit" -OutFile $Archive
if ((Get-FileHash $Archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Sha256) {
    throw 'proc-tap source archive checksum mismatch'
}
# A unique extraction directory avoids reusing stale compiled objects.
$Source = Join-Path $Root ([guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $Source | Out-Null
# Python extraction avoids PATH-dependent GNU tar drive-letter handling.
& $Python -c "import tarfile; t=tarfile.open(r'$Archive'); t.extractall(r'$Source', filter='data')"
if ($LASTEXITCODE) { throw 'proc-tap source extraction failed' }
$Directories = @(Get-ChildItem $Source -Directory)
if ($Directories.Count -ne 1) { throw 'Unexpected proc-tap archive layout' }
$Source = $Directories[0].FullName
& $Python -m pip wheel $Source --no-deps --no-build-isolation --wheel-dir $Output
if ($LASTEXITCODE) { throw 'proc-tap native wheel build failed' }
$Platform = if ($Architecture -eq 'arm64') { 'win_arm64' } else { 'win_amd64' }
$Wheels = @(Get-ChildItem $Output -Filter "proc_tap-1.1.1-*-*-$Platform.whl")
if ($Wheels.Count -ne 1) { throw 'Expected exactly one architecture-specific proc-tap wheel; use a clean output directory' }
& $Python -m pip install --no-deps --force-reinstall $Wheels[0].FullName
if ($LASTEXITCODE) { throw 'proc-tap wheel installation failed' }
$Extension = & $Python -c 'import proctap._native as n; print(n.__file__)'
if ($LASTEXITCODE) { throw 'proc-tap native extension failed to load' }
& $Python scripts/check_pe_arch.py $Architecture $Extension
if ($LASTEXITCODE) { throw 'proc-tap extension architecture mismatch' }
Get-FileHash $Archive, $Wheels[0].FullName -Algorithm SHA256 | Format-Table -AutoSize
