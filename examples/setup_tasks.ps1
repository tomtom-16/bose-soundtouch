# Bose SoundTouch services on Windows: proxy + button listener as scheduled tasks
# Run elevated. Equivalent of the launchd/systemd examples.
$ErrorActionPreference = 'Stop'

$py   = 'C:\Program Files\Python312\python.exe'
$node = 'C:\Program Files\nodejs\node.exe'
$dir  = 'C:\bose-soundtouch'

if (-not (Test-Path $py))   { throw "python not found at $py" }
if (-not (Test-Path $node)) { throw "node not found at $node" }

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

$trigger   = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest

# --- proxy (serve) ---
$actProxy = New-ScheduledTaskAction -WorkingDirectory $dir -Execute 'cmd.exe' `
    -Argument "/c `"`"$py`" `"$dir\bose_radio.py`" serve >> `"$dir\proxy.log`" 2>&1`""
Register-ScheduledTask -TaskName 'BoseRadioProxy' -Action $actProxy `
    -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null

# --- button listener ---
$actBtn = New-ScheduledTaskAction -WorkingDirectory $dir -Execute 'cmd.exe' `
    -Argument "/c `"`"$node`" `"$dir\button_listener.js`" >> `"$dir\buttons.log`" 2>&1`""
Register-ScheduledTask -TaskName 'BoseRadioButtons' -Action $actBtn `
    -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null

Start-ScheduledTask -TaskName 'BoseRadioProxy'
Start-ScheduledTask -TaskName 'BoseRadioButtons'
Start-Sleep -Seconds 3

Get-ScheduledTask -TaskName 'BoseRadio*' | Select-Object TaskName,State | Format-Table -AutoSize
Get-Content "$dir\proxy.log" -Tail 5 -ErrorAction SilentlyContinue
Get-Content "$dir\buttons.log" -Tail 5 -ErrorAction SilentlyContinue
