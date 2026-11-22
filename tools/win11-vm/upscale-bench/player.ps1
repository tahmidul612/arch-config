# Guest side of the playback test: loop a video fullscreen (borderless, topmost) with WPF's MediaElement
# (Media Foundation decode). With the guest at 1080p the 1080p clip is drawn 1:1; at 4K, Windows scales it.
# Runs in the user's session (scheduled task UT-Player). Stops when C:\Tools\upscale\player.stop exists.
param([string]$File = 'C:\Tools\upscale\play.mp4')
Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase
$stop = 'C:\Tools\upscale\player.stop'; Remove-Item $stop -EA SilentlyContinue
$w = New-Object Windows.Window
$w.WindowStyle = 'None'; $w.ResizeMode = 'NoResize'; $w.WindowState = 'Maximized'; $w.Topmost = $true
$w.Background = [Windows.Media.Brushes]::Black; $w.Cursor = [Windows.Input.Cursors]::None
$m = New-Object Windows.Controls.MediaElement
$m.LoadedBehavior = 'Manual'; $m.Stretch = 'Uniform'; $m.Source = [Uri]$File; $m.IsMuted = $true
$m.Add_MediaEnded({ $m.Position = [TimeSpan]::Zero; $m.Play() })
$m.Add_MediaFailed({ param($s, $e) Add-Content 'C:\Tools\upscale\player.log' "failed: $($e.ErrorException.Message)"; $w.Close() })
$m.Add_MediaOpened({ Add-Content 'C:\Tools\upscale\player.log' ("opened {0}x{1} {2}" -f $m.NaturalVideoWidth, $m.NaturalVideoHeight, (Get-Date -f HH:mm:ss)) })
$w.Content = $m
$t = New-Object Windows.Threading.DispatcherTimer; $t.Interval = [TimeSpan]::FromMilliseconds(500)
$t.Add_Tick({ if (Test-Path $stop) { $w.Close() } })
$w.Add_Loaded({ $m.Play(); $t.Start() })
[void]$w.ShowDialog()
