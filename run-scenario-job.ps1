param(
    [string]$RepoPath = $PSScriptRoot,
    [string]$JobsSubPath = "jobs",
    [string]$Pattern = "*-v1.json",
    [ValidateSet("Auto", "DryRun", "Live")]
    [string]$Mode = "Auto",
    [string]$Job,
    [switch]$ListOnly
)

$ErrorActionPreference = "Stop"

function Write-Label {
    param(
        [string]$Text,
        [ValidateSet("Info", "Success", "Warn", "Error", "Title")]
        [string]$Kind = "Info"
    )

    switch ($Kind) {
        "Info"    { Write-Host "[i] $Text" -ForegroundColor Cyan }
        "Success" { Write-Host "[+] $Text" -ForegroundColor Green }
        "Warn"    { Write-Host "[!] $Text" -ForegroundColor Yellow }
        "Error"   { Write-Host "[x] $Text" -ForegroundColor Red }
        "Title"   { Write-Host "[>] $Text" -ForegroundColor Magenta }
    }
}

function Get-ScenarioTag {
    param([psobject]$JobDoc)

    if ($JobDoc.PSObject.Properties.Name -contains "scenario") {
        $s = $JobDoc.scenario
        if ($null -ne $s -and $s.PSObject.Properties.Name -contains "type") {
            return [string]$s.type
        }
    }

    switch -Regex ($JobDoc.job_id) {
        '^ma-'      { return "multi_agent_solution" }
        '^content-' { return "content_development" }
        '^harness-' { return "agent_harness_regression" }
        '^rag-'     { return "rag_index_and_validation" }
        default     { return "general" }
    }
}

function Get-JobCatalog {
    param(
        [string]$JobDir,
        [string]$JobPattern
    )

    if (-not (Test-Path -LiteralPath $JobDir)) {
        throw "Jobs directory not found: $JobDir"
    }

    $files = Get-ChildItem -LiteralPath $JobDir -Filter $JobPattern -File | Sort-Object Name
    $catalog = @()

    foreach ($f in $files) {
        try {
            $doc = Get-Content -LiteralPath $f.FullName -Raw | ConvertFrom-Json
            if (-not $doc.job_id) {
                Write-Label "Skipping $($f.Name): missing job_id" "Warn"
                continue
            }

            $catalog += [pscustomobject]@{
                FileName  = $f.Name
                JobId     = [string]$doc.job_id
                Title     = [string]$doc.title
                Status    = [string]$doc.status
                Scenario  = Get-ScenarioTag -JobDoc $doc
                Languages = (($doc.languages | ForEach-Object { [string]$_ }) -join ",")
                Formats   = (($doc.formats | ForEach-Object { [string]$_ }) -join ",")
                Raw       = $doc
            }
        }
        catch {
            Write-Label "Skipping $($f.Name): invalid JSON ($($_.Exception.Message))" "Warn"
        }
    }

    return $catalog
}

function Show-Catalog {
    param([array]$Catalog)

    Write-Host ""
    Write-Label "Available Scenario Jobs" "Title"
    Write-Host ""

    $i = 1
    foreach ($item in $Catalog) {
        Write-Host ("[{0}] {1}" -f $i, $item.FileName) -ForegroundColor White
        Write-Host ("    - id:       {0}" -f $item.JobId) -ForegroundColor DarkGray
        Write-Host ("    - title:    {0}" -f $item.Title) -ForegroundColor DarkGray
        Write-Host ("    - scenario: {0}" -f $item.Scenario) -ForegroundColor DarkGray
        Write-Host ("    - status:   {0}" -f $item.Status) -ForegroundColor DarkGray
        Write-Host ("    - langs:    {0}" -f $item.Languages) -ForegroundColor DarkGray
        Write-Host ("    - formats:  {0}" -f $item.Formats) -ForegroundColor DarkGray
        Write-Host ""
        $i++
    }
}

function Select-JobInteractive {
    param([array]$Catalog)

    while ($true) {
        $answer = Read-Host "Enter job number to run (or q to quit)"
        if ($answer -match '^[Qq]$') {
            return $null
        }
        if ($answer -notmatch '^\d+$') {
            Write-Label "Please enter a number." "Warn"
            continue
        }

        $idx = [int]$answer
        if ($idx -lt 1 -or $idx -gt $Catalog.Count) {
            Write-Label "Selection out of range." "Warn"
            continue
        }

        return $Catalog[$idx - 1]
    }
}

function Select-ModeInteractive {
    while ($true) {
        $answer = Read-Host "Choose mode: [D]ry-run or [L]ive"
        switch -Regex ($answer) {
            '^[Dd]$' { return "DryRun" }
            '^[Ll]$' { return "Live" }
            default  { Write-Label "Please enter D or L." "Warn" }
        }
    }
}

function Resolve-JobFromArg {
    param(
        [array]$Catalog,
        [string]$JobArg
    )

    if (-not $JobArg) {
        return $null
    }

    $exactFile = $Catalog | Where-Object { $_.FileName -ieq $JobArg } | Select-Object -First 1
    if ($exactFile) { return $exactFile }

    $exactId = $Catalog | Where-Object { $_.JobId -ieq $JobArg } | Select-Object -First 1
    if ($exactId) { return $exactId }

    $partial = $Catalog | Where-Object { $_.FileName -like "*$JobArg*" -or $_.JobId -like "*$JobArg*" }
    if ($partial.Count -eq 1) {
        return $partial[0]
    }

    if ($partial.Count -gt 1) {
        Write-Label "Multiple jobs matched '$JobArg'. Use a full file name or job_id." "Warn"
        return $null
    }

    Write-Label "No job matched '$JobArg'." "Error"
    return $null
}

Write-Host ""
Write-Label "Nexetra Scenario Job Launcher" "Title"
Write-Host "   Color legend: cyan=info, green=success, yellow=warning, red=error" -ForegroundColor DarkCyan
Write-Host ""

$jobsDir = Join-Path $RepoPath $JobsSubPath
$catalog = Get-JobCatalog -JobDir $jobsDir -JobPattern $Pattern

if (-not $catalog -or $catalog.Count -eq 0) {
    Write-Label "No jobs found with pattern '$Pattern' in '$jobsDir'." "Error"
    exit 1
}

Show-Catalog -Catalog $catalog

if ($ListOnly) {
    Write-Label "List-only mode complete." "Success"
    exit 0
}

$selected = Resolve-JobFromArg -Catalog $catalog -JobArg $Job
if (-not $selected) {
    $selected = Select-JobInteractive -Catalog $catalog
}

if (-not $selected) {
    Write-Label "No job selected. Exiting." "Warn"
    exit 0
}

$runMode = $Mode
if ($runMode -eq "Auto") {
    $runMode = Select-ModeInteractive
}

if ($runMode -eq "Live") {
    $confirm = Read-Host "Confirm LIVE run for '$($selected.JobId)'? (y/N)"
    if ($confirm -notmatch '^[Yy]$') {
        Write-Label "Live run cancelled." "Warn"
        exit 0
    }
}

$jobArg = (Join-Path $JobsSubPath $selected.FileName).Replace('\\', '/')
$cmdArgs = @("pipeline/run_batch_pool.py", "--job", $jobArg)
if ($runMode -eq "DryRun") {
    $cmdArgs += "--dry-run"
}

Push-Location $RepoPath
try {
    Write-Host ""
    Write-Label ("Selected: {0} ({1})" -f $selected.JobId, $selected.FileName) "Info"
    Write-Label ("Mode: {0}" -f $runMode) "Info"
    Write-Label ("Running: python {0}" -f ($cmdArgs -join " ")) "Title"
    Write-Host ""

    & python @cmdArgs
    $exitCode = $LASTEXITCODE

    if ($exitCode -eq 0) {
        Write-Host ""
        Write-Label "Job command completed successfully." "Success"
        Write-Label "Open dashboard: http://10.0.0.200:7800" "Info"
    }
    else {
        Write-Host ""
        Write-Label ("Job command failed with exit code {0}." -f $exitCode) "Error"
    }

    exit $exitCode
}
finally {
    Pop-Location
}
