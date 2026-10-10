# Met la fenêtre du jeu au premier plan (le jeu bride ses FPS quand il n'a pas le focus).
Add-Type @"
using System; using System.Runtime.InteropServices;
public class W {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] public static extern void keybd_event(byte k, byte s, uint f, UIntPtr e);
}
"@
$p = Get-Process "League of Legends" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $p) { exit 1 }
[W]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero)   # ALT (contourne le verrou de premier plan Windows)
[W]::ShowWindow($p.MainWindowHandle, 9) | Out-Null
$ok = [W]::SetForegroundWindow($p.MainWindowHandle)
[W]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)
if ($ok) { "ok" } else { "fail" }
