// File .exe nho: bam dup -> mo tool trong cua so ung dung (app_window.py).
// Tim Python o .venv canh file .exe (hoac thu muc cha), chay an khong cua so den.
// Build: launcher_exe\build_exe.bat
using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

static class Launcher
{
    [STAThread]
    static int Main()
    {
        string dir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
        string title = Path.GetFileNameWithoutExtension(Application.ExecutablePath);
        string script = Path.Combine(dir, "app_window.py");
        if (!File.Exists(script))
        {
            MessageBox.Show("Thiếu file app_window.py cạnh file này.\nHãy đặt file .exe trong thư mục tool (cạnh start.bat).",
                title, MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        string py = null;
        foreach (string d in new[] { dir, Path.GetDirectoryName(dir) })
        {
            if (string.IsNullOrEmpty(d)) continue;
            string p = Path.Combine(d, ".venv", "Scripts", "pythonw.exe");
            if (File.Exists(p)) { py = p; break; }
        }
        if (py == null)
        {
            MessageBox.Show("Máy chưa cài tool. Hãy chạy install.bat trước.",
                title, MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        var psi = new ProcessStartInfo(py, "\"" + script + "\"");
        psi.WorkingDirectory = dir;
        psi.UseShellExecute = false;
        psi.EnvironmentVariables["PYTHONUTF8"] = "1";
        psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
        Process.Start(psi);
        return 0;
    }
}
