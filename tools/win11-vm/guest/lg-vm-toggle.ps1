# Runs in the VM (task LgInput-Toggle, <WINDOWS_USER>, elevated) from the desktop shortcut / Ctrl+Alt+F11.
# Hands the request to the host watcher (lg-input watch polls request.txt through the guest agent),
# because only the host can move the LG to HDMI. If the host doesn't pick it up within 6 s,
# falls back to switching locally (the VM can only send "to DP", and only while the LG shows HDMI).
param([ValidateSet('toggle','dp','hdmi','rescue')][string]$Action='toggle')
$dir='C:\Tools\monitor'; $req="$dir\request.txt"; $log="$dir\vm-toggle.log"
function Log($m){ "$(Get-Date -Format s) $m" | Out-File $log -Append -Encoding utf8 }
Set-Content -LiteralPath $req -Value $Action -Encoding ASCII
foreach($i in 1..30){ Start-Sleep -Milliseconds 200; if(-not (Test-Path -LiteralPath $req)){ Log "$Action handed to host"; exit 0 } }
Remove-Item -LiteralPath $req -Force -EA SilentlyContinue
Log "host did not pick up '$Action'; trying locally"
if($Action -in 'toggle','dp','rescue'){
  $o = (& "$dir\lg-igcl.ps1" dp) -join ' '
  Log "local: $o"
  if($o -match 'result=0x0'){ exit 0 }
}
(New-Object -ComObject WScript.Shell).Popup("The host's lg-input watcher is not answering, and the VM can't switch the LG from here (it can only send 'to DisplayPort', and only while the LG shows the VM).`n`nUse the LG's joystick > Input, or run 'lg-input rescue' on the host.", 15, 'LG monitor', 48) | Out-Null
exit 1
