# Stop "Get an app to open this 'ms-gamebar' link" popping up when a controller connects (the virtual DS4 attaching
# for Remote Play, or its PS button). Windows opens ms-gamebar: on controller arrival / guide button even when the
# Xbox Game Bar app is not installed, and with no handler for the link it shows the Open-With dialog.
#   1. turn off "Open Game Bar using this button on a controller" for the user (UseNexusForGameBarEnabled = 0)
#   2. register do-nothing handlers for ms-gamebar, ms-gamebarservices and ms-gamingoverlay (systray.exe, which exits
#      at once), so any other trigger opens nothing either
# Run elevated (or as SYSTEM through the guest agent). -User defaults to the user of the Display-lgonly task.
# Undo: no-gamebar-popup.ps1 -Undo
param([string]$User, [switch]$Undo)
$ErrorActionPreference = 'Stop'
if (-not $User) { $User = (Get-ScheduledTask -TaskName Display-lgonly).Principal.UserId }
$sid = (New-Object System.Security.Principal.NTAccount($User)).Translate([System.Security.Principal.SecurityIdentifier]).Value
# the user's hive is loaded while they are logged in; otherwise load it from their profile
$hku = "Registry::HKEY_USERS\$sid"
$loaded = $false
if (-not (Test-Path $hku)) {
  $prof = (Get-CimInstance Win32_UserProfile | ? SID -eq $sid).LocalPath
  reg.exe load "HKU\$sid" "$prof\NTUSER.DAT" | Out-Null; $loaded = $true
}
try {
  $gb = "$hku\Software\Microsoft\GameBar"
  if (-not (Test-Path $gb)) { New-Item $gb -Force | Out-Null }
  Set-ItemProperty $gb UseNexusForGameBarEnabled ([int][bool]$Undo) -Type DWord
  foreach ($p in 'ms-gamebar', 'ms-gamebarservices', 'ms-gamingoverlay') {
    $k = "HKLM:\SOFTWARE\Classes\$p"
    if ($Undo) { if (Test-Path $k) { Remove-Item $k -Recurse -Force }; continue }
    New-Item "$k\shell\open\command" -Force | Out-Null
    Set-ItemProperty $k '(default)' "URL:$p"
    Set-ItemProperty $k 'URL Protocol' ''
    Set-ItemProperty $k 'NoOpenWith' ''
    Set-ItemProperty "$k\shell\open\command" '(default)' "`"$env:SystemRoot\System32\systray.exe`""
  }
} finally {
  if ($loaded) { [gc]::Collect(); reg.exe unload "HKU\$sid" | Out-Null }
}
"UseNexusForGameBarEnabled=" + (Get-ItemProperty "Registry::HKEY_USERS\$sid\Software\Microsoft\GameBar" -ErrorAction SilentlyContinue).UseNexusForGameBarEnabled
foreach ($p in 'ms-gamebar', 'ms-gamebarservices', 'ms-gamingoverlay') { "${p}: " + (Test-Path "HKLM:\SOFTWARE\Classes\$p") }
