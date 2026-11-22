[CmdletBinding()] param([string]$RepositoryRoot='')
$ErrorActionPreference='Stop'
if([string]::IsNullOrWhiteSpace($RepositoryRoot)){$RepositoryRoot=Split-Path -Parent $PSScriptRoot}
$Generator=Join-Path $PSScriptRoot 'build_index.py';$Candidates=New-Object System.Collections.Generic.List[string]
function Add-Python([string]$p){if(!$p){return};try{$f=[IO.Path]::GetFullPath($p)}catch{return};if(!$Candidates.Contains($f)){$Candidates.Add($f)}}
$parent=Split-Path -Parent $RepositoryRoot
Add-Python (Join-Path $parent 'Local Environment\venv\Scripts\python.exe')
Add-Python (Join-Path (Split-Path -Parent $parent) 'Local Environment\venv\Scripts\python.exe')
Add-Python $env:EMMRES_PYTHON
Get-Command python.exe -All -ErrorAction SilentlyContinue|ForEach-Object{Add-Python $_.Source}
$Python=$null
foreach($c in $Candidates){if(!(Test-Path -LiteralPath $c -PathType Leaf)){continue};try{& $c -c "import sys; assert sys.version_info.major==3" 2>$null;if($LASTEXITCODE -eq 0){$Python=$c;break}}catch{}}
if(!$Python){throw "No usable Python 3 found. Set EMMRES_PYTHON to the full python.exe path, then rerun."}
Write-Host "Using Python: $Python" -ForegroundColor Green
& $Python $Generator
if($LASTEXITCODE -ne 0){throw "Index generator returned $LASTEXITCODE"}
