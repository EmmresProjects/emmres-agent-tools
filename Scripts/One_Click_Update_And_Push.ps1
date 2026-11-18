[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepoRoot
$LogDir = Join-Path $RepoRoot 'logs'
$LogFile = Join-Path $LogDir 'One_Click_Update_And_Push.log'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Log([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Write-Host $line
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
}
function Run-Native([string]$Program, [string[]]$Arguments, [switch]$AllowFailure) {
    Log ("$Program " + ($Arguments -join ' '))
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = & $Program @Arguments 2>&1
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previousPreference }
    foreach ($line in $output) {
        Write-Host ([string]$line)
        Add-Content -LiteralPath $LogFile -Value ([string]$line) -Encoding UTF8
    }
    if ($code -ne 0 -and -not $AllowFailure) {
        throw "Command failed with exit code ${code}: $Program $($Arguments -join ' ')"
    }
    return $code
}
function Run-DocUpdater {
    $toolsUpdater = Join-Path $PSScriptRoot 'Update_Tools_Docs.ps1'
    $environmentUpdater = Join-Path $PSScriptRoot 'Update_Environment_Docs.ps1'
    if ((Test-Path -LiteralPath (Join-Path $RepoRoot 'Tools')) -and (Test-Path -LiteralPath $toolsUpdater)) {
        Log 'Detected Tools repository. Updating Markdown documentation.'
        Run-Native 'powershell.exe' @('-NoLogo','-NoProfile','-ExecutionPolicy','Bypass','-File',$toolsUpdater) | Out-Null
    } elseif ((Test-Path -LiteralPath (Join-Path $RepoRoot 'Components')) -and (Test-Path -LiteralPath $environmentUpdater)) {
        Log 'Detected Environment repository. Updating Markdown documentation.'
        Run-Native 'powershell.exe' @('-NoLogo','-NoProfile','-ExecutionPolicy','Bypass','-File',$environmentUpdater) | Out-Null
    } else {
        Log 'No matching documentation updater found. Continuing with Git push only.'
    }
}

Set-Content -LiteralPath $LogFile -Value "Emmre One-Click Update and Push`nStarted: $(Get-Date -Format o)`nRepository: $RepoRoot`n" -Encoding UTF8

try {
    if (-not (Get-Command git.exe -ErrorAction SilentlyContinue)) { throw 'Git was not found in PATH.' }
    Run-Native 'git' @('rev-parse','--is-inside-work-tree') | Out-Null
    if (-not (Get-Command git-lfs.exe -ErrorAction SilentlyContinue)) { throw 'Git LFS was not found. Install Git LFS first.' }

    Run-DocUpdater
    Run-Native 'git' @('lfs','install','--local') | Out-Null

    $branch = (& git branch --show-current).Trim()
    if (-not $branch) { throw 'No active branch was detected.' }
    $remote = (& git remote get-url origin 2>$null).Trim()
    if (-not $remote) { throw 'Remote origin is not configured.' }
    Log "Branch: $branch"
    Log "Remote: $remote"

    $largeNotLfs = New-Object System.Collections.Generic.List[string]
    Get-ChildItem -LiteralPath $RepoRoot -Recurse -File -Force -ErrorAction SilentlyContinue |
      Where-Object { $_.FullName -notmatch '[\\/]\.git[\\/]' -and $_.Length -ge 100MB } |
      ForEach-Object {
        $rel = $_.FullName.Substring($RepoRoot.Length + 1).Replace('\\','/')
        $attr = (& git check-attr filter -- $rel 2>$null) -join ''
        if ($attr -notmatch 'filter:\s+lfs') { $largeNotLfs.Add("$rel ($([math]::Round($_.Length/1MB,1)) MB)") }
      }
    if ($largeNotLfs.Count) {
        Log 'Files at least 100 MB are not covered by Git LFS:'
        $largeNotLfs | ForEach-Object { Log "  $_" }
        throw 'Configure Git LFS tracking for the listed files, then run again.'
    }

    Run-Native 'git' @('add','-A') | Out-Null
    Log 'Staged status:'
    Run-Native 'git' @('status','--short') | Out-Null
    Log 'Git LFS status:'
    Run-Native 'git' @('lfs','status') | Out-Null

    & git diff --cached --quiet
    if ($LASTEXITCODE -eq 0) {
        Log 'No new changes to commit.'
    } else {
        $repoName = Split-Path -Leaf $RepoRoot
        $message = "Automatic update: $repoName $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
        Run-Native 'git' @('commit','-m',$message) | Out-Null
    }

    $upstream = (& git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>$null).Trim()
    if ($LASTEXITCODE -eq 0 -and $upstream) {
        Run-Native 'git' @('pull','--rebase','--autostash') | Out-Null
        Run-Native 'git' @('push') | Out-Null
    } else {
        Run-Native 'git' @('push','-u','origin',$branch) | Out-Null
    }

    Log 'Completed: documentation update, commit, Git LFS upload, and push.'
    Run-Native 'git' @('status','--short','--branch') | Out-Null
    Log "Log saved to: $LogFile"
    exit 0
} catch {
    Log ("FAILED: " + $_.Exception.Message)
    Log "Log saved to: $LogFile"
    exit 1
}
