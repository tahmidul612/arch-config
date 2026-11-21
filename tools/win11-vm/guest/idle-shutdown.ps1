# Idle shutdown for the win11 VM. Runs in the logged-in user's session (scheduled task "IdleShutdown").
# "Idle" = no keyboard/mouse input (incl. Deskflow / Looking Glass) AND no network stream
# (PS Remote Play video keeps the NIC busy, which covers controller-only play).
# Manual overrides: create C:\Tools\idle-shutdown\inhibit to pause it; cancel a pending shutdown with `shutdown /a`.
param([int]$IdleMinutes = 60, [int]$NetKBps = 125, [int]$GraceSeconds = 300)
$dir = 'C:\Tools\idle-shutdown'; $log = "$dir\idle-shutdown.log"
Add-Type @"
using System; using System.Runtime.InteropServices;
public static class Idle {
  [StructLayout(LayoutKind.Sequential)] struct LII { public uint cbSize; public uint dwTime; }
  [DllImport("user32.dll")] static extern bool GetLastInputInfo(ref LII p);
  public static double Seconds() { var l = new LII(); l.cbSize = (uint)Marshal.SizeOf(l); GetLastInputInfo(ref l);
    return ((uint)Environment.TickCount - l.dwTime) / 1000.0; }
}
"@
function Log($m) { $line = "{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $m; Add-Content $log $line
  if ((Get-Item $log).Length -gt 1MB) { $k = Get-Content $log -Tail 2000; Set-Content $log $k } }
function RxBytes { (Get-NetAdapterStatistics -EA SilentlyContinue | Measure-Object ReceivedBytes -Sum).Sum }

Log "start: idle=$IdleMinutes min, net threshold=$NetKBps KB/s, grace=$GraceSeconds s"
$lastNet = Get-Date; $rx = RxBytes; $t = Get-Date; $lastReport = Get-Date 0
while ($true) {
  Start-Sleep 60
  $now = Get-Date; $rx2 = RxBytes; $kbps = ($rx2 - $rx) / 1KB / ($now - $t).TotalSeconds; $rx = $rx2; $t = $now
  if ($kbps -ge $NetKBps) { $lastNet = $now }
  $inputIdle = [Idle]::Seconds() / 60
  $netIdle = ($now - $lastNet).TotalMinutes
  $idle = [math]::Min($inputIdle, $netIdle)
  if (($now - $lastReport).TotalMinutes -ge 15) { Log ("status: input idle {0:N0} min, net {1:N0} KB/s (idle {2:N0} min)" -f $inputIdle, $kbps, $netIdle); $lastReport = $now }
  if (Test-Path "$dir\inhibit") { continue }
  if ($idle -ge $IdleMinutes) {
    Log ("idle {0:N0} min (input {1:N0}, net {2:N0}) -> shutdown in {3}s" -f $idle, $inputIdle, $netIdle, $GraceSeconds)
    & shutdown.exe /s /t $GraceSeconds /c "No activity for $IdleMinutes minutes: shutting down in $([int]($GraceSeconds/60)) minutes. Cancel with: shutdown /a"
    # if it gets cancelled, don't fire again until another full idle period has passed
    $lastNet = Get-Date
    Start-Sleep ($GraceSeconds + 30)
    Log "still running after grace period: shutdown was cancelled"
  }
}
