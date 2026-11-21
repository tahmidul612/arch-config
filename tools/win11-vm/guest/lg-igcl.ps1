# LG HDR 4K input switching from the win11 VM through Intel IGCL raw I2C on the iGPU's HDMI output.
# The LG needs its own command (VCP 0xF4 sent from DDC source address 0x50); Windows' dxva2 API can only send from 0x51.
# Usage: lg-igcl.ps1 probe | dp | hdmi        Writes need admin (run elevated or as SYSTEM).
param([ValidateSet('probe','dp','hdmi')][string]$Action='probe')
$ErrorActionPreference='Stop'
Add-Type @"
using System; using System.Runtime.InteropServices;
public static class Igcl {
  [StructLayout(LayoutKind.Sequential)] public struct InitArgs { public uint Size; public byte Version; public uint AppVersion; public uint Flags; public uint SupportedVersion;
    public uint U1; public ushort U2; public ushort U3; [MarshalAs(UnmanagedType.ByValArray, SizeConst=8)] public byte[] U4; }
  [StructLayout(LayoutKind.Sequential)] public struct I2CArgs { public uint Size; public byte Version; public uint DataSize; public uint Address; public int OpType; public uint Offset; public uint Flags; public ulong RAD;
    [MarshalAs(UnmanagedType.ByValArray, SizeConst=128)] public byte[] Data; }
  [DllImport("ControlLib.dll")] public static extern int ctlInit(ref InitArgs a, out IntPtr h);
  [DllImport("ControlLib.dll")] public static extern int ctlClose(IntPtr h);
  [DllImport("ControlLib.dll")] public static extern int ctlEnumerateDevices(IntPtr h, ref uint n, [In,Out] IntPtr[] d);
  [DllImport("ControlLib.dll")] public static extern int ctlEnumerateDisplayOutputs(IntPtr d, ref uint n, [In,Out] IntPtr[] o);
  [DllImport("ControlLib.dll")] public static extern int ctlI2CAccess(IntPtr o, ref I2CArgs a);
  public static I2CArgs Make(int op, uint offset, byte[] data, uint size) {
    var a = new I2CArgs(); a.Size=(uint)Marshal.SizeOf(typeof(I2CArgs)); a.OpType=op; a.Address=0x6E; a.Offset=offset; a.DataSize=size; a.Data=new byte[128];
    if (data != null) Array.Copy(data, a.Data, data.Length); return a; }
}
"@
function Packet([byte]$src, [byte[]]$body){ $chk = 0x6E -bxor $src; foreach($b in $body){ $chk = $chk -bxor $b }; ,([byte[]]($body + [byte]$chk)) }
$init = New-Object Igcl+InitArgs; $init.Size=[Runtime.InteropServices.Marshal]::SizeOf($init); $init.AppVersion=(1 -shl 16) -bor 1; $init.U4=New-Object byte[] 8
$api=[IntPtr]::Zero; $r=[Igcl]::ctlInit([ref]$init,[ref]$api); if($r -ne 0){ "error=ctlInit 0x{0:x}" -f $r; exit 2 }
try {
  $n=[uint32]0; [void][Igcl]::ctlEnumerateDevices($api,[ref]$n,$null); $devs=New-Object IntPtr[] $n; [void][Igcl]::ctlEnumerateDevices($api,[ref]$n,$devs)
  $found=$null
  foreach($d in $devs){
    $m=[uint32]0; [void][Igcl]::ctlEnumerateDisplayOutputs($d,[ref]$m,$null); if($m -eq 0){continue}
    $outs=New-Object IntPtr[] $m; [void][Igcl]::ctlEnumerateDisplayOutputs($d,[ref]$m,$outs)
    for($i=0;$i -lt $outs.Count;$i++){
      # standard DDC/CI "get VCP 0x60" request, then read the 11-byte reply; only the output wired to the LG answers
      $w=[Igcl]::Make(2,0x51,(Packet 0x51 @(0x82,0x01,0x60)),4); $rw=[Igcl]::ctlI2CAccess($outs[$i],[ref]$w)
      Start-Sleep -Milliseconds 60
      $rd=[Igcl]::Make(1,0x51,$null,11); $rr=[Igcl]::ctlI2CAccess($outs[$i],[ref]$rd)
      $hex=($rd.Data[0..10] | % { '{0:x2}' -f $_ }) -join ' '
      $k=[Array]::IndexOf($rd.Data,[byte]0x02)
      $cur = if($rw -eq 0 -and $rr -eq 0 -and $k -ge 0 -and $k -le 6 -and $rd.Data[$k+2] -eq 0x60){ $rd.Data[$k+7] } else { $null }
      if($Action -eq 'probe'){ "output[$i] write=0x{0:x} read=0x{1:x} data=[{2}] input={3}" -f $rw,$rr,$hex,$(if($null -ne $cur){'0x{0:x2}' -f $cur}else{'-'}) }
      if($null -ne $cur -and -not $found){ $found=@{h=$outs[$i]; cur=$cur; idx=$i} }
    }
  }
  if($Action -eq 'probe'){ if($found){ "input=0x{0:x2} output={1}" -f $found.cur,$found.idx } else { 'error=no Intel output answered DDC (LG not on HDMI, or HDMI output disabled)' }; exit 0 }
  if(-not $found){ 'error=no Intel output answered DDC (LG not on HDMI, or HDMI output disabled)'; exit 3 }
  $val = if($Action -eq 'dp'){0xD0}else{0x90}
  $w=[Igcl]::Make(2,0x50,(Packet 0x50 @(0x84,0x03,0xF4,0x00,$val)),6); $rw=[Igcl]::ctlI2CAccess($found.h,[ref]$w)
  "input=0x{0:x2} set=lg-f4-0x{1:x2} result=0x{2:x}" -f $found.cur,$val,$rw
  if($rw -ne 0){ exit 4 }
} finally { [void][Igcl]::ctlClose($api) }
