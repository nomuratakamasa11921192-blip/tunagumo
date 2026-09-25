param(
    [datetime]$TargetDate = '2026-09-26',
    [switch]$SkipRemote
)

# Read-only verification; never starts QA, sends messages, or changes the VPS.
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$target = $TargetDate.Date
$now = Get-Date
$reportDir = Join-Path $repo 'saas\workspace\scheduled-verification'
New-Item -ItemType Directory -Path $reportDir -Force | Out-Null
$checks = @()
foreach ($spec in @(
    @{ Name = 'tsunagumo-daily-qa'; Hour = 4 },
    @{ Name = 'tsunagumo-ops-check'; Hour = 9 }
)) {
    try {
        $task = Get-ScheduledTask -TaskName $spec.Name
        $info = $task | Get-ScheduledTaskInfo
        $due = $target.AddHours($spec.Hour)
        $modelOK = ($task.Actions.Arguments -join ' ') -match 'CODEX_SCHEDULED_MODEL=gpt-6-sol(?:\s|$)'
        $outcome = 'NOT_RUN'
        if ($now -lt $due) { $outcome = 'WAITING' }
        elseif ($task.State.ToString() -eq 'Running') { $outcome = 'RUNNING' }
        elseif ($info.LastRunTime -ge $due -and $info.LastRunTime -lt $target.AddDays(1)) {
            $outcome = if ($info.LastTaskResult -eq 0 -and $modelOK) { 'PASS' } else { 'FAIL' }
        }
        $checks += [ordered]@{
            name = $spec.Name; outcome = $outcome; state = $task.State.ToString()
            last_run = $info.LastRunTime.ToString('o'); next_run = $info.NextRunTime.ToString('o')
            exit_code = $info.LastTaskResult; model_is_sol = $modelOK
        }
    } catch {
        $checks += [ordered]@{ name = $spec.Name; outcome = 'UNAVAILABLE'; error_type = $_.Exception.GetType().Name }
    }
}

$remote = [ordered]@{ outcome = 'WAITING'; note = 'Scheduled result is not yet available.' }
if ($SkipRemote) { $remote.outcome = 'SKIPPED' }
elseif ($now -ge $target.AddHours(9)) {
    $bash = Join-Path $env:ProgramFiles 'Git\bin\bash.exe'
    if (Test-Path -LiteralPath $bash) {
        Push-Location $repo
        try {
            $ErrorActionPreference = 'Continue'
            $health = (& $bash -c 'bash scripts/check_vps_health.sh' 2>&1 | Out-String).Trim()
            $healthExit = $LASTEXITCODE
            $remote = [ordered]@{
                outcome = if ($healthExit -eq 0) { 'HEALTH_PASS' } else { 'HEALTH_FAIL' }
                exit_code = $healthExit; details = $health
                note = 'Health checks do not prove that the 03:00 and 08:00 cron jobs ran. Check dated VPS job logs separately.'
            }
        } finally {
            $ErrorActionPreference = 'Stop'
            Pop-Location
        }
    } else { $remote.outcome = 'BASH_UNAVAILABLE' }
}

$report = [ordered]@{
    checked_at = $now.ToString('o'); target_date = $target.ToString('yyyy-MM-dd')
    windows_tasks = $checks; vps_health = $remote
    cron_execution = 'UNVERIFIED: inspect dated backup and disk-monitor logs on VPS'
}
$json = $report | ConvertTo-Json -Depth 7
$filename = 'verification-' + $now.ToString('yyyyMMdd-HHmmss-fff') + '.json'
$path = Join-Path $reportDir $filename
[System.IO.File]::WriteAllText($path, $json, (New-Object System.Text.UTF8Encoding($false)))
Write-Output $json
Write-Output ('Report: ' + $path)
if (@($checks | Where-Object { $_.outcome -ne 'PASS' }).Count -gt 0 -or $remote.outcome -ne 'HEALTH_PASS') { exit 1 }
exit 0
