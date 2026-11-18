[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepoRoot
$LogDir = Join-Path $RepoRoot 'logs'
$LogFile = Join-Path $LogDir 'Git_LFS_Push.log'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Log([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Write-Host $line
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
}
function Run-Git([string[]]$GitArgs, [switch]$AllowFailure) {
    Log ("git " + ($GitArgs -join ' '))

    # Native programs such as Git can write harmless warnings to stderr.
    # Under Windows PowerShell, ErrorActionPreference=Stop can otherwise
    # turn those warning lines into terminating PowerShell errors.
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = & git @GitArgs 2>&1
        $code = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }

    foreach ($line in $output) {
        Write-Host ([string]$line)
        Add-Content -LiteralPath $LogFile -Value ([string]$line) -Encoding UTF8
    }
    if ($code -ne 0 -and -not $AllowFailure) { throw "Git command failed with exit code ${code}: git $($GitArgs -join ' ')" }
    return $code
}

Set-Content -LiteralPath $LogFile -Value "Emmre Git and LFS Push`nStarted: $(Get-Date -Format o)`nRepository: $RepoRoot`n" -Encoding UTF8

try {
    if (-not (Get-Command git.exe -ErrorAction SilentlyContinue)) { throw 'Git was not found in PATH. Install Git for Windows or open the repository through GitHub Desktop first.' }
    Run-Git @('rev-parse','--is-inside-work-tree') | Out-Null
    if (-not (Get-Command git-lfs.exe -ErrorAction SilentlyContinue)) { throw 'Git LFS was not found. Install Git LFS, then run git lfs install.' }

    Run-Git @('lfs','install','--local') | Out-Null
    $branch = (& git branch --show-current).Trim()
    if (-not $branch) { throw 'No active branch was detected.' }
    $remote = (& git remote get-url origin 2>$null).Trim()
    if (-not $remote) { throw 'Remote origin is not configured.' }
    Log "Branch: $branch"
    Log "Remote: $remote"

    Log 'Current Git LFS tracking patterns:'
    Run-Git @('lfs','track') | Out-Null

    $largeUntracked = New-Object System.Collections.Generic.List[string]
    Get-ChildItem -LiteralPath $RepoRoot -Recurse -File -Force -ErrorAction SilentlyContinue |
      Where-Object { $_.FullName -notmatch '[\\/]\.git[\\/]' -and $_.Length -ge 100MB } |
      ForEach-Object {
        $rel = $_.FullName.Substring($RepoRoot.Length + 1).Replace('\\','/')
        $attr = (& git check-attr filter -- $rel 2>$null) -join ''
        if ($attr -notmatch 'filter:\s+lfs') { $largeUntracked.Add("$rel ($([math]::Round($_.Length/1MB,1)) MB)") }
      }
    if ($largeUntracked.Count -gt 0) {
        Log 'ERROR: Files at least 100 MB are not covered by Git LFS:'
        $largeUntracked | ForEach-Object { Log "  $_" }
        throw 'Configure Git LFS for these files before committing. Example: git lfs track "*.onnx"'
    }

    Run-Git @('add','-A') | Out-Null
    Log 'Staged status:'
    Run-Git @('status','--short') | Out-Null
    Log 'Git LFS status:'
    Run-Git @('lfs','status') | Out-Null

    & git diff --cached --quiet
    if ($LASTEXITCODE -eq 0) {
        Log 'No staged changes were found. Nothing to commit.'
    } else {
        $message = Read-Host 'Enter commit message, or press Enter for the default'
        if ([string]::IsNullOrWhiteSpace($message)) { $message = 'Update tools and environment content' }
        Run-Git @('commit','-m',$message) | Out-Null
    }

    $upstream = (& git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>$null).Trim()
    if ($LASTEXITCODE -eq 0 -and $upstream) {
        Run-Git @('pull','--rebase','--autostash') | Out-Null
        Run-Git @('push') | Out-Null
    } else {
        Run-Git @('push','-u','origin',$branch) | Out-Null
    }

    Log 'Push completed. Normal git push also uploads referenced Git LFS objects.'
    Run-Git @('status','--short','--branch') | Out-Null
    Log "Log saved to: $LogFile"
    exit 0
}
catch {
    Log ("FAILED: " + $_.Exception.Message)
    Log "Log saved to: $LogFile"
    exit 1
}
