# Recreate the guest's display and LG-input scheduled tasks and the desktop shortcut, as they were
# found on the working VM (2026-11-21). Run elevated in the guest after copying display-layout.ps1 to
# C:\Tools\display\ and lg-igcl.ps1 + lg-vm-toggle.ps1 to C:\Tools\monitor\.
# Usage: register-tasks.ps1 -User <WINDOWS_USER>
# This script itself was written for the docs and has not been run; the tasks it creates were.
param([Parameter(Mandatory)][string]$User)
$ErrorActionPreference = 'Stop'
$ps = 'powershell.exe'
function Settings { New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 2) }
$userPrincipal  = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
$adminPrincipal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Highest
$system         = New-ScheduledTaskPrincipal -UserId SYSTEM -LogonType ServiceAccount -RunLevel Highest

# Display layouts: started on demand by the host (vm-display, through the guest agent)
foreach ($l in 'lgonly','hdmi','extended','status') {
  $a = New-ScheduledTaskAction -Execute $ps -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command `"& C:\Tools\display\display-layout.ps1 -Layout $l *> C:\Tools\display\last-$l.out`""
  Register-ScheduledTask -TaskName "Display-$l" -Action $a -Principal $userPrincipal -Settings (Settings) -Force | Out-Null
}
# Back to the Looking Glass-only layout 15 s after each login
$a = New-ScheduledTaskAction -Execute $ps -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command `"& C:\Tools\display\display-layout.ps1 -Layout lgonly *> C:\Tools\display\last-logon.out`""
$t = New-ScheduledTaskTrigger -AtLogOn -User $User; $t.Delay = 'PT15S'
Register-ScheduledTask -TaskName 'Display-lgonly-AtLogon' -Action $a -Trigger $t -Principal $userPrincipal -Settings (Settings) -Force | Out-Null

# LG input: the VM's hotkey asks the host to toggle; IGCL I2C writes need admin
$a = New-ScheduledTaskAction -Execute $ps -Argument '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\Tools\monitor\lg-vm-toggle.ps1 toggle'
Register-ScheduledTask -TaskName 'LgInput-Toggle' -Action $a -Principal $adminPrincipal -Settings (Settings) -Force | Out-Null

# On Windows shutdown/restart (System log, User32 event 1074): hand the LG back to the host's DP input
$cls = Get-CimClass -Namespace Root/Microsoft/Windows/TaskScheduler -ClassName MSFT_TaskEventTrigger
$t = New-CimInstance -CimClass $cls -ClientOnly
$t.Subscription = "<QueryList><Query Id=`"0`" Path=`"System`"><Select Path=`"System`">*[System[Provider[@Name='User32'] and EventID=1074]]</Select></Query></QueryList>"
$t.Enabled = $true
$a = New-ScheduledTaskAction -Execute $ps -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command `"& C:\Tools\monitor\lg-igcl.ps1 dp *> C:\Tools\monitor\on-shutdown.log`""
Register-ScheduledTask -TaskName 'LgInput-OnShutdown' -Action $a -Trigger $t -Principal $system -Settings (New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 1)) -Force | Out-Null

# Public desktop shortcut with the Ctrl+Alt+F11 hotkey
$s = (New-Object -ComObject WScript.Shell).CreateShortcut('C:\Users\Public\Desktop\Switch LG monitor (host - VM).lnk')
$s.TargetPath = 'C:\Windows\System32\schtasks.exe'; $s.Arguments = '/run /tn LgInput-Toggle'; $s.Hotkey = 'Ctrl+Alt+F11'; $s.Save()

Get-ScheduledTask | ? { $_.TaskName -match '^(Display-|LgInput-)' } | Select TaskName, State | Format-Table -AutoSize
