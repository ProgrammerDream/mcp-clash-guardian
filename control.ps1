param(
    [ValidateSet("status","logs","start","stop","run","install","uninstall","rollback","update")]
    [string]$Action = "status",
    [int]$Tail = 30
)

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$DefaultConfigPath = Join-Path $Here "config\default.json"
$LocalConfigPath = Join-Path $Here "config\local.json"
$RuntimeDir = Join-Path $Here "runtime"
$StatusPath = Join-Path $RuntimeDir "status.json"
$LogPath = Join-Path $RuntimeDir "automation.jsonl"
$WatcherPath = Join-Path $Here "src\watcher.py"
$RequirementsPath = Join-Path $Here "requirements.txt"
New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null

function Read-JsonFile([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Read-Config {
    $config = Read-JsonFile $DefaultConfigPath
    if ($null -eq $config) { throw "Default config not found: $DefaultConfigPath" }
    $local = Read-JsonFile $LocalConfigPath
    if ($null -eq $local) {
        throw "Local config not found: $LocalConfigPath. Copy config\local.example.json to config\local.json first."
    }
    foreach ($property in $local.PSObject.Properties) {
        $config | Add-Member -NotePropertyName $property.Name -NotePropertyValue $property.Value -Force
    }
    foreach ($required in @("public_host","profile_path","python_exe","watcher_task_name")) {
        if ([string]::IsNullOrWhiteSpace([string]$config.$required)) { throw "Missing local config key: $required" }
    }
    return $config
}

function Set-LocalValue([string]$Name, $Value) {
    $local = Read-JsonFile $LocalConfigPath
    if ($null -eq $local) { $local = [pscustomobject]@{} }
    $local | Add-Member -NotePropertyName $Name -NotePropertyValue $Value -Force
    $local | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $LocalConfigPath -Encoding UTF8
}

function Append-ControlLog([string]$Event, [hashtable]$Data = @{}) {
    $record = [ordered]@{
        timestamp = (Get-Date).ToString("s")
        level = "INFO"
        event = $Event
        data = $Data
    }
    ($record | ConvertTo-Json -Depth 8 -Compress) | Add-Content -LiteralPath $LogPath -Encoding UTF8
}

function Task-State($Config) {
    Get-ScheduledTask -TaskName ([string]$Config.watcher_task_name) -ErrorAction SilentlyContinue
}

function Start-Watcher($Config) {
    $task = Task-State $Config
    if ($null -eq $task) { throw "Watcher task not installed. Run .\control.ps1 install" }
    Start-ScheduledTask -TaskName ([string]$Config.watcher_task_name)
}

function Stop-Watcher($Config) {
    Stop-ScheduledTask -TaskName ([string]$Config.watcher_task_name) -ErrorAction SilentlyContinue
}

function Install-Requirements($Config) {
    $pythonExe = [string]$Config.python_exe
    if (-not (Test-Path -LiteralPath $pythonExe)) { throw "Python not found: $pythonExe" }
    if (Test-Path -LiteralPath $RequirementsPath) {
        $args = "-m pip install -r `"$RequirementsPath`" --disable-pip-version-check"
        $process = Start-Process -FilePath $pythonExe -ArgumentList $args -Wait -PassThru -WindowStyle Hidden
        if ($process.ExitCode -ne 0) { throw "pip install failed: exit=$($process.ExitCode)" }
    }
}

function Install-Watcher($Config) {
    $taskName = [string]$Config.watcher_task_name
    $pythonExe = [string]$Config.python_exe
    $pythonwExe = Join-Path (Split-Path -Parent $pythonExe) "pythonw.exe"
    $watcherExe = if (Test-Path -LiteralPath $pythonwExe) { $pythonwExe } else { $pythonExe }
    if (-not (Test-Path -LiteralPath $WatcherPath)) { throw "Watcher not found: $WatcherPath" }

    $existing = Task-State $Config
    if ($null -ne $existing) {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }

    $userId = "$env:USERDOMAIN\$env:USERNAME"
    $taskAction = New-ScheduledTaskAction -Execute $watcherExe -Argument "`"$WatcherPath`""
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
    $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -MultipleInstances IgnoreNew

    Register-ScheduledTask `
        -TaskName $taskName `
        -Action $taskAction `
        -Trigger $trigger `
        -Principal $principal `
        -Settings $settings `
        -Description "MCP Clash Guardian: observe Mihomo/TUN and verify real MCP health; recover Cloudflared paths only after confirmed failure." | Out-Null

    Set-LocalValue "enabled" $true
    Start-ScheduledTask -TaskName $taskName
    Append-ControlLog "automation_installed" @{ task = $taskName; run_level = "Limited"; watcher_exe = $watcherExe }
}

$config = Read-Config

switch ($Action) {
    "status" {
        $task = Task-State $config
        $status = Read-JsonFile $StatusPath
        $v2ray = Get-Process -Name "v2rayN" -ErrorAction SilentlyContinue
        $singbox = Get-Process -Name "sing-box" -ErrorAction SilentlyContinue
        [pscustomobject]@{
            machine_name = [string]$config.machine_name
            strategy_version = if ($null -ne $status) { $status.strategy_version } else { [string]$config.strategy_version }
            enabled = [bool]$config.enabled
            watcher_task = [string]$config.watcher_task_name
            watcher_state = if ($null -ne $task) { [string]$task.State } else { "NotInstalled" }
            v2rayn_process = if ($null -ne $v2ray) { "Running" } else { "Stopped" }
            singbox_process = if ($null -ne $singbox) { "Running" } else { "Stopped" }
            phase = if ($null -ne $status) { $status.phase } else { "unknown" }
            trigger = if ($null -ne $status) { $status.trigger } else { $null }
            tun_up = if ($null -ne $status) { $status.tun_up } else { $null }
            mihomo_pid = if ($null -ne $status) { $status.mihomo_pid } else { $null }
            mihomo_api = if ($null -ne $status) { $status.mihomo_api.available } else { $null }
            selected_node = if ($null -ne $status) { $status.selected_node } else { $null }
            argotunnel_connections = if ($null -ne $status) { $status.argotunnel_connection_count } else { $null }
            mcp_ok = if ($null -ne $status) { $status.mcp_ok } else { $null }
            hot_median_ms = if ($null -ne $status) { $status.hot_median_ms } else { $null }
            cf_ray = if ($null -ne $status) { $status.cf_ray } else { $null }
            last_error = if ($null -ne $status) { $status.last_error } else { $null }
            local_config = $LocalConfigPath
            status_file = $StatusPath
            log_file = $LogPath
        } | Format-List
    }
    "logs" {
        if (Test-Path -LiteralPath $LogPath) {
            Get-Content -LiteralPath $LogPath -Tail $Tail -Encoding UTF8
        } else {
            Write-Output "NO_LOG_YET"
        }
    }
    "start" {
        Set-LocalValue "enabled" $true
        $config = Read-Config
        Start-Watcher $config
        Append-ControlLog "automation_started"
        Write-Output "AUTOMATION_STARTED"
    }
    "stop" {
        Set-LocalValue "enabled" $false
        Stop-Watcher $config
        Append-ControlLog "automation_stopped"
        Write-Output "AUTOMATION_STOPPED"
    }
    "run" {
        $pythonExe = [string]$config.python_exe
        Append-ControlLog "manual_run_started"
        $process = Start-Process -FilePath $pythonExe -ArgumentList "`"$WatcherPath`" --once" -Wait -PassThru -WindowStyle Hidden
        Append-ControlLog "manual_run_finished" @{ exit_code = $process.ExitCode }
        if ($process.ExitCode -ne 0) { throw "manual recovery failed: exit=$($process.ExitCode)" }
        & $PSCommandPath -Action status
    }
    "install" {
        Install-Requirements $config
        Install-Watcher $config
        Write-Output "AUTOMATION_INSTALLED=$($config.watcher_task_name)"
    }
    "uninstall" {
        Stop-Watcher $config
        Unregister-ScheduledTask -TaskName ([string]$config.watcher_task_name) -Confirm:$false -ErrorAction SilentlyContinue
        Set-LocalValue "enabled" $false
        Append-ControlLog "automation_uninstalled" @{ task = [string]$config.watcher_task_name }
        Write-Output "AUTOMATION_UNINSTALLED=$($config.watcher_task_name)"
    }
    "rollback" {
        Stop-Watcher $config
        Unregister-ScheduledTask -TaskName ([string]$config.watcher_task_name) -Confirm:$false -ErrorAction SilentlyContinue
        Set-LocalValue "enabled" $false
        Append-ControlLog "automation_rollback" @{ watcher = [string]$config.watcher_task_name }
        Write-Output "ROLLBACK_OK: watcher removed; Clash Verge/Mihomo configuration was not modified."
    }
    "update" {
        Stop-Watcher $config
        $git = Get-Command git.exe -ErrorAction Stop
        & $git.Source -C $Here pull --ff-only
        if ($LASTEXITCODE -ne 0) {
            Start-Watcher $config
            throw "git pull failed: exit=$LASTEXITCODE"
        }
        $config = Read-Config
        Install-Requirements $config
        Start-Watcher $config
        Append-ControlLog "automation_updated" @{ strategy_version = [string]$config.strategy_version }
        Start-Sleep -Seconds 3
        & $PSCommandPath -Action status
    }
}
