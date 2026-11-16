# Tao CoverMusic.exe (mo tool trong cua so ung dung) + loi tat ngoai Desktop
# "Cover Music by Lucas Studio". Ban cu ten "Cover Audio Tool" / CoverAudioTool.exe
# duoc don di (chi khi loi tat cu tro vao dung thu muc tool nay).
# Goi tu install.bat va patch\apply_update.ps1. Loi o day KHONG lam hong cai dat:
# van dung start.bat duoc.
param(
    [Parameter(Mandatory = $true)][string]$Dir,
    [string]$Name = 'CoverMusic',
    [string]$Shortcut = 'Cover Music by Lucas Studio'
)
$ErrorActionPreference = 'Stop'
# Doi ten tu ban cu: xoa CoverAudioTool.exe + loi tat "Cover Audio Tool" tro vao thu muc nay.
try {
    $oldExe = Join-Path $Dir 'CoverAudioTool.exe'
    if ($Name -ne 'CoverAudioTool') {
        $oldLnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Cover Audio Tool.lnk'
        if (Test-Path -LiteralPath $oldLnk) {
            $target = (New-Object -ComObject WScript.Shell).CreateShortcut($oldLnk).TargetPath
            $sameDir = $false
            try { $sameDir = ([IO.Path]::GetFileName($target) -ieq 'CoverAudioTool.exe') -and (Test-Path -LiteralPath $oldExe) } catch {}
            if ($sameDir) { Remove-Item -LiteralPath $oldLnk -Force; Write-Host "  - Da xoa loi tat cu 'Cover Audio Tool'" }
        }
        if (Test-Path -LiteralPath $oldExe) { Remove-Item -LiteralPath $oldExe -Force -ErrorAction SilentlyContinue }
    }
} catch {
    Write-Host "  [CANH BAO] Khong don duoc loi tat / exe ten cu ($_)."
}
try {
    $csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
    if (-not (Test-Path -LiteralPath $csc)) { $csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe' }
    $exe = Join-Path $Dir "$Name.exe"
    $src = Join-Path $Dir 'launcher_exe\Launcher.cs'
    $ico = Join-Path $Dir 'launcher_exe\app.ico'
    & $csc /nologo /target:winexe /optimize+ /codepage:65001 "/win32icon:$ico" /reference:System.Windows.Forms.dll "/out:$exe" $src | Out-Null
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $exe)) { throw "csc loi ($LASTEXITCODE)" }
    Write-Host "  - Da tao $Name.exe"
} catch {
    Write-Host "  [CANH BAO] Khong tao duoc $Name.exe ($_). Van mo tool bang start.bat duoc."
    exit 0
}
try {
    # IShellLinkW: giu dung duong dan co dau tieng Viet (WScript.Shell lam hong dau).
    Add-Type -TypeDefinition @"
using System; using System.Runtime.InteropServices; using System.Text;
[ComImport, Guid("00021401-0000-0000-C000-000000000046")] public class MaShellLink {}
[ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("000214F9-0000-0000-C000-000000000046")]
public interface IMaShellLinkW {
  void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder f, int c, IntPtr p, int fl);
  void GetIDList(out IntPtr p); void SetIDList(IntPtr p);
  void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder n, int c);
  void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string n);
  void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder d, int c);
  void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string d);
  void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder a, int c);
  void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string a);
  void GetHotkey(out short h); void SetHotkey(short h);
  void GetShowCmd(out int s); void SetShowCmd(int s);
  void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder p, int c, out int i);
  void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string p, int i);
  void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string p, int r);
  void Resolve(IntPtr h, int f);
  void SetPath([MarshalAs(UnmanagedType.LPWStr)] string f);
}
public static class MaLnk {
  public static void Make(string lnk, string target) {
    var l = (IMaShellLinkW)new MaShellLink();
    l.SetPath(target); l.SetWorkingDirectory(System.IO.Path.GetDirectoryName(target)); l.SetIconLocation(target, 0);
    ((System.Runtime.InteropServices.ComTypes.IPersistFile)l).Save(lnk, true);
  }
}
"@
    $lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) "$Shortcut.lnk"
    [MaLnk]::Make($lnk, $exe)
    Write-Host "  - Da tao loi tat '$Shortcut' ngoai Desktop"
} catch {
    Write-Host "  [CANH BAO] Khong tao duoc loi tat Desktop ($_). Bam $Name.exe trong thu muc tool."
}
exit 0
