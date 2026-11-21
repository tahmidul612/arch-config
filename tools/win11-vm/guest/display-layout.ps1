# Windows display layout for the win11 VM (Looking Glass IDD + LG on the Intel HDMI).
#   -Layout lgonly    Looking Glass monitor only (default "lg" mode: the LG shows the VM through the host client)
#   -Layout hdmi      LG (Intel HDMI) primary at (0,0), Looking Glass extended at (Width,0)  ("hdmi" mode)
#   -Layout extended  Looking Glass primary at (0,0), LG extended at (Width,0)
#   -Layout status    print the active layout
# Failsafe: if the result has no active Looking Glass monitor, it re-applies lgonly. Log: C:\Tools\display\display-layout.log
param([ValidateSet('lgonly','hdmi','extended','status')][string]$Layout='status', [int]$Width=3840, [int]$Height=2160, [int]$Hz=60, [int]$Scale=150)
$ErrorActionPreference='Stop'
$logFile='C:\Tools\display\display-layout.log'
function Log($m){ Add-Content $logFile ("{0:yyyy-MM-dd HH:mm:ss} [{1}] {2}" -f (Get-Date),$Layout,$m) }
Add-Type @"
using System; using System.Runtime.InteropServices; using System.Collections.Generic;
public static class CCD {
 [StructLayout(LayoutKind.Sequential)] public struct LUID { public uint Lo; public int Hi; }
 [StructLayout(LayoutKind.Sequential)] public struct RATIONAL { public uint N, D; }
 [StructLayout(LayoutKind.Sequential)] public struct PATH_SOURCE { public LUID adapterId; public uint id; public uint modeInfoIdx; public uint statusFlags; }
 [StructLayout(LayoutKind.Sequential)] public struct PATH_TARGET { public LUID adapterId; public uint id; public uint modeInfoIdx; public uint outputTechnology; public uint rotation; public uint scaling; public RATIONAL refreshRate; public uint scanLineOrdering; public int targetAvailable; public uint statusFlags; }
 [StructLayout(LayoutKind.Sequential)] public struct PATH { public PATH_SOURCE src; public PATH_TARGET tgt; public uint flags; }
 [StructLayout(LayoutKind.Explicit, Size=64)] public struct MODE { [FieldOffset(0)] public uint infoType; [FieldOffset(4)] public uint id; [FieldOffset(8)] public LUID adapterId;
   [FieldOffset(16)] public uint srcWidth; [FieldOffset(20)] public uint srcHeight; [FieldOffset(24)] public uint srcFormat; [FieldOffset(28)] public int posX; [FieldOffset(32)] public int posY; }
 [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] public struct TARGET_NAME { public uint type; public uint size; public LUID adapterId; public uint id;
   public uint flags; public uint outputTechnology; public ushort mfr; public ushort product; public uint connectorInstance;
   [MarshalAs(UnmanagedType.ByValTStr, SizeConst=64)] public string friendly; [MarshalAs(UnmanagedType.ByValTStr, SizeConst=128)] public string path; }
 [DllImport("user32.dll")] public static extern int GetDisplayConfigBufferSizes(uint f, out uint np, out uint nm);
 [DllImport("user32.dll")] public static extern int QueryDisplayConfig(uint f, ref uint np, [Out] PATH[] p, ref uint nm, [Out] MODE[] m, IntPtr topo);
 [DllImport("user32.dll")] public static extern int SetDisplayConfig(uint np, PATH[] p, uint nm, MODE[] m, uint f);
 [DllImport("user32.dll")] public static extern int DisplayConfigGetDeviceInfo(ref TARGET_NAME t);
 public static PATH[] Paths; public static MODE[] Modes;
 public static int Query(uint flags){ uint np, nm; int r = GetDisplayConfigBufferSizes(flags, out np, out nm); if (r != 0) return r;
   Paths = new PATH[np]; Modes = new MODE[nm]; r = QueryDisplayConfig(flags, ref np, Paths, ref nm, Modes, IntPtr.Zero);
   Array.Resize(ref Paths, (int)np); Array.Resize(ref Modes, (int)nm); return r; }
 public static string Name(LUID a, uint id){ var t = new TARGET_NAME(); t.type = 2; t.size = (uint)Marshal.SizeOf(typeof(TARGET_NAME)); t.adapterId = a; t.id = id;
   return DisplayConfigGetDeviceInfo(ref t) == 0 ? t.friendly : "?"; }
}
"@

Add-Type @"
using System; using System.Runtime.InteropServices;
public static class Dpi {
  // undocumented DisplayConfig DPI calls (types -3 get / -4 set), scale is relative to the recommended step
  [StructLayout(LayoutKind.Sequential)] public struct HDR { public int type; public uint size; public uint lo; public int hi; public uint id; }
  [StructLayout(LayoutKind.Sequential)] public struct GET { public HDR h; public int minRel; public int curRel; public int maxRel; }
  [StructLayout(LayoutKind.Sequential)] public struct SET { public HDR h; public int rel; }
  [DllImport("user32.dll")] public static extern int DisplayConfigGetDeviceInfo(ref GET g);
  [DllImport("user32.dll")] public static extern int DisplayConfigSetDeviceInfo(ref SET s);
  public static readonly int[] Steps = {100,125,150,175,200,225,250,300,350,400,450,500};
  public static int Get(uint lo, int hi, uint src, out int min, out int cur, out int max){ var g = new GET(); g.h.type=-3; g.h.size=(uint)Marshal.SizeOf(typeof(GET)); g.h.lo=lo; g.h.hi=hi; g.h.id=src;
    int r = DisplayConfigGetDeviceInfo(ref g); min=g.minRel; cur=g.curRel; max=g.maxRel; return r; }
  public static int Set(uint lo, int hi, uint src, int pct){ int min, cur, max; int r = Get(lo, hi, src, out min, out cur, out max); if (r != 0) return r;
    int rec = Math.Abs(min); int idx = Array.IndexOf(Steps, pct); if (idx < 0) return 87;
    var s = new SET(); s.h.type=-4; s.h.size=(uint)Marshal.SizeOf(typeof(SET)); s.h.lo=lo; s.h.hi=hi; s.h.id=src; s.rel = idx - rec; return DisplayConfigSetDeviceInfo(ref s); }
  public static int Current(uint lo, int hi, uint src){ int min, cur, max; if (Get(lo, hi, src, out min, out cur, out max) != 0) return -1; int i = Math.Abs(min) + cur; return (i >= 0 && i < Steps.Length) ? Steps[i] : -1; }
}
"@
function SetScaleAll(){ [void][CCD]::Query($QDC_ACTIVE)
  foreach($p in [CCD]::Paths){ $n=TargetName $p; $before=[Dpi]::Current($p.src.adapterId.Lo,$p.src.adapterId.Hi,$p.src.id)
    if($before -ne $Scale){ $r=[Dpi]::Set($p.src.adapterId.Lo,$p.src.adapterId.Hi,$p.src.id,$Scale); Log "scale ${n}: $before% -> $Scale% (r=$r, now $([Dpi]::Current($p.src.adapterId.Lo,$p.src.adapterId.Hi,$p.src.id))%)" } } }
$QDC_ALL=1; $QDC_ACTIVE=2; $INVALID=[uint32]::MaxValue
$IDD='Looking Glass'; $LGMON='LG HDR 4K'
function TargetName($path){ [CCD]::Name($path.tgt.adapterId, $path.tgt.id) }
function Status(){ [void][CCD]::Query($QDC_ACTIVE); $s=@()
  foreach($p in [CCD]::Paths){ $m=[CCD]::Modes[$p.src.modeInfoIdx]
    $s += "{0}: {1}x{2} at ({3},{4}) {5:N2}Hz {6}%" -f (TargetName $p),$m.srcWidth,$m.srcHeight,$m.posX,$m.posY,($p.tgt.refreshRate.N/[math]::Max(1,$p.tgt.refreshRate.D)),[Dpi]::Current($p.src.adapterId.Lo,$p.src.adapterId.Hi,$p.src.id) }
  $s -join ' | ' }
function IddActive(){ [void][CCD]::Query($QDC_ACTIVE); [bool]([CCD]::Paths | ? { (TargetName $_) -like "*$IDD*" }) }
function Apply($plan){  # $plan = ordered list of @{Name=..; X=..}
  # 1) wanted monitors that are not active yet: let Windows activate every connected monitor (extend), trimmed in step 2.
  [void][CCD]::Query($QDC_ACTIVE); $activeNames=@([CCD]::Paths | % { TargetName $_ })
  $missing=@($plan | ? { $n=$_.Name; -not ($activeNames | ? { $_ -like "*$n*" }) })
  if($missing.Count){ $e=[CCD]::SetDisplayConfig(0,$null,0,$null,[uint32](0x80 -bor 0x4)); Log "activating $(($missing | % Name) -join ', '): SDC_TOPOLOGY_EXTEND=$e"; Start-Sleep 3 }
  # 2) edit the ACTIVE config: keep only planned monitors, set size and position of each source.
  [void][CCD]::Query($QDC_ACTIVE); $modes=[CCD]::Modes; $paths=@()
  foreach($item in $plan){
    $p = [CCD]::Paths | ? { (TargetName $_) -like "*$($item.Name)*" } | Select -First 1
    if(-not $p){ Log "monitor '$($item.Name)' not available (unplugged, or LG input elsewhere?)"; continue }
    $i=$p.src.modeInfoIdx; $m=$modes[$i]; $m.posX=$item.X; $m.posY=0; $m.srcWidth=[uint32]$Width; $m.srcHeight=[uint32]$Height; $modes[$i]=$m
    $tgt=$p.tgt; $tgt.modeInfoIdx=$INVALID; $rr=$tgt.refreshRate; $rr.N=[uint32]$Hz; $rr.D=1; $tgt.refreshRate=$rr; $p.tgt=$tgt
    $paths += $p }
  if(-not $paths.Count){ return 1460 }
  # if the planned primary (X=0) is missing, move the first remaining source to (0,0)
  if(-not ($paths | ? { $modes[$_.src.modeInfoIdx].posX -eq 0 })){ $i=$paths[0].src.modeInfoIdx; $m=$modes[$i]; $m.posX=0; $modes[$i]=$m }
  # SDC_APPLY | SDC_USE_SUPPLIED_DISPLAY_CONFIG | SDC_SAVE_TO_DATABASE | SDC_ALLOW_CHANGES
  return [CCD]::SetDisplayConfig([uint32]$paths.Count,[CCD+PATH[]]$paths,[uint32]$modes.Count,[CCD+MODE[]]$modes,[uint32](0x80 -bor 0x20 -bor 0x200 -bor 0x400)) }

New-Item -ItemType Directory -Force (Split-Path $logFile) | Out-Null
if($Layout -eq 'status'){ $st=Status; Log $st; $st; exit 0 }
$plan = switch($Layout){
  'lgonly'   { @(@{Name=$IDD; X=0}) }
  'hdmi'     { @(@{Name=$LGMON; X=0}, @{Name=$IDD; X=$Width}) }
  'extended' { @(@{Name=$IDD; X=0}, @{Name=$LGMON; X=$Width}) } }
$r = Apply $plan; Start-Sleep 2; if($r -eq 0){ SetScaleAll }; $st=Status; Log "SetDisplayConfig=$r -> $st"
if(-not (IddActive)){ Log "FAILSAFE: Looking Glass monitor not active, re-applying lgonly"; $Layout='lgonly'; $r2 = Apply @(@{Name=$IDD; X=0}); Start-Sleep 2; Log "failsafe SetDisplayConfig=$r2 -> $(Status)" }
"result=$r"; Status
exit $r
