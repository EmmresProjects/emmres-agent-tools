[CmdletBinding()]
param(
    [string]$RepositoryRoot = '',
    [switch]$NoPreview,
    [int]$PreviewPort = 8765
)
$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($RepositoryRoot)) { $RepositoryRoot = Split-Path -Parent $PSScriptRoot }
$RepositoryRoot = [IO.Path]::GetFullPath($RepositoryRoot)
$Generator = Join-Path $PSScriptRoot 'build_index.py'
$Candidates = New-Object System.Collections.Generic.List[string]
function Add-Python([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return }
    try { $Full = [IO.Path]::GetFullPath($Path) } catch { return }
    if (-not $Candidates.Contains($Full)) { $Candidates.Add($Full) }
}
$Parent = Split-Path -Parent $RepositoryRoot
Add-Python (Join-Path $Parent 'Local Environment\venv\Scripts\python.exe')
Add-Python (Join-Path (Split-Path -Parent $Parent) 'Local Environment\venv\Scripts\python.exe')
Add-Python $env:EMMRES_PYTHON
Get-Command python.exe -All -ErrorAction SilentlyContinue | ForEach-Object { Add-Python $_.Source }
$Python = $null
foreach ($Candidate in $Candidates) {
    if (-not (Test-Path -LiteralPath $Candidate -PathType Leaf)) { continue }
    try {
        & $Candidate -c "import sys; assert sys.version_info.major == 3" 2>$null
        if ($LASTEXITCODE -eq 0) { $Python = $Candidate; break }
    } catch { }
}
if (-not $Python) { throw 'No usable Python 3 found. Set EMMRES_PYTHON to the full python.exe path, then rerun.' }
Write-Host "Using Python: $Python" -ForegroundColor Green
& $Python $Generator
if ($LASTEXITCODE -ne 0) { throw "Index generator returned $LASTEXITCODE" }

if (-not $NoPreview) {
    $PreviewRoot = Join-Path $RepositoryRoot '_site_preview'
    New-Item -ItemType Directory -Force -Path $PreviewRoot | Out-Null
    Get-ChildItem -LiteralPath $PreviewRoot -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
    Copy-Item -Path (Join-Path $RepositoryRoot 'docs\*') -Destination $PreviewRoot -Recurse -Force
    foreach ($Section in @('Tools','Knowledge','Skills')) {
        Copy-Item -LiteralPath (Join-Path $RepositoryRoot $Section) -Destination (Join-Path $PreviewRoot $Section) -Recurse -Force
    }
    $PreviewUrl = "http://127.0.0.1:$PreviewPort/downloads.html"
    $Listening = $false
    try { $Listening = Test-NetConnection -ComputerName 127.0.0.1 -Port $PreviewPort -InformationLevel Quiet -WarningAction SilentlyContinue } catch { }
    if (-not $Listening) {
        $Args = @('-m','http.server',[string]$PreviewPort,'--bind','127.0.0.1','--directory',$PreviewRoot)
        Start-Process -FilePath $Python -ArgumentList $Args -WorkingDirectory $PreviewRoot -WindowStyle Minimized | Out-Null
        Start-Sleep -Seconds 2
    }
    Write-Host "Local preview: $PreviewUrl" -ForegroundColor Cyan
    Start-Process $PreviewUrl
}
