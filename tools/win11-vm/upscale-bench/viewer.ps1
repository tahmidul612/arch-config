# Guest side of the upscaling test: a borderless, topmost, per-monitor-DPI-aware window on the primary screen that
# draws one image 1:1 at (0,0) (no scaling, no colour management). Runs in the logged-in user's session (scheduled
# task UT-Viewer). The host drives it through files written by the guest agent:
#   C:\Tools\upscale\cmd.txt  "<token> <image path>"  or  "<token> quit"
#   C:\Tools\upscale\ack.txt  "<token> <imgW>x<imgH> screen=<W>x<H>"  written after the frame is painted
param([string]$Dir = 'C:\Tools\upscale')
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices;
public static class UT {
  [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr v);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
}
"@
[void][UT]::SetProcessDpiAwarenessContext([IntPtr]-4)   # PER_MONITOR_AWARE_V2: no DWM bitmap stretching
$log = "$Dir\viewer.log"
function Log($m) { Add-Content $log ("{0:HH:mm:ss.fff} {1}" -f (Get-Date), $m) }
function Ack($t) {   # the host may be reading ack.txt through the guest agent at this moment: retry
  for ($i = 0; $i -lt 80; $i++) { try { [IO.File]::WriteAllText("$Dir\ack.txt", $t); return } catch { Start-Sleep -Milliseconds 25 } }
  Log "could not write ack: $t" }
$screen = [Windows.Forms.Screen]::PrimaryScreen.Bounds
$form = New-Object Windows.Forms.Form
$form.FormBorderStyle = 'None'; $form.StartPosition = 'Manual'; $form.Bounds = $screen
$form.TopMost = $true; $form.BackColor = [Drawing.Color]::Black; $form.ShowInTaskbar = $false
$form.GetType().GetProperty('DoubleBuffered', [Reflection.BindingFlags]'NonPublic,Instance').SetValue($form, $true)
$script:img = $null; $script:last = ''
$form.Add_Paint({ param($s, $e)
  if ($script:img) {
    $g = $e.Graphics
    $g.CompositingMode = 'SourceCopy'; $g.InterpolationMode = 'NearestNeighbor'; $g.PixelOffsetMode = 'Half'
    $g.DrawImage($script:img, (New-Object Drawing.Rectangle 0, 0, $script:img.Width, $script:img.Height),
                 0, 0, $script:img.Width, $script:img.Height, [Drawing.GraphicsUnit]::Pixel)
  } })
$timer = New-Object Windows.Forms.Timer; $timer.Interval = 25
$timer.Add_Tick({
  try { $c = [IO.File]::ReadAllText("$Dir\cmd.txt").Trim() } catch { return }
  if (-not $c -or $c -eq $script:last) { return }
  $p = $c.Split(' ', 2); if ($p.Count -lt 2) { return }
  $script:last = $c
  if ($p[1] -eq 'quit') { Log 'quit'; $form.Close(); return }
  try {
    $bytes = [IO.File]::ReadAllBytes($p[1])
    $ms = New-Object IO.MemoryStream(, $bytes)
    $tmp = [Drawing.Image]::FromStream($ms, $false, $true)
    $bmp = New-Object Drawing.Bitmap $tmp.Width, $tmp.Height, ([Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $gb = [Drawing.Graphics]::FromImage($bmp); $gb.CompositingMode = 'SourceCopy'; $gb.InterpolationMode = 'NearestNeighbor'
    $gb.DrawImage($tmp, (New-Object Drawing.Rectangle 0, 0, $tmp.Width, $tmp.Height), 0, 0, $tmp.Width, $tmp.Height, [Drawing.GraphicsUnit]::Pixel)
    $gb.Dispose(); $tmp.Dispose(); $ms.Dispose()
    $old = $script:img; $script:img = $bmp
    $form.Invalidate(); $form.Update(); if ($old) { $old.Dispose() }
    [void][UT]::SetCursorPos($screen.Width - 1, $screen.Height - 1)
    Ack "$($p[0]) $($bmp.Width)x$($bmp.Height) screen=$($screen.Width)x$($screen.Height)"
  } catch { Log "error on '$c': $_"; Ack "$($p[0]) error $_" }
})
$form.Add_Shown({ [Windows.Forms.Cursor]::Hide(); $form.Activate(); $timer.Start(); Log "shown, screen $($screen.Width)x$($screen.Height)" })
Log 'start'
[Windows.Forms.Application]::Run($form)
