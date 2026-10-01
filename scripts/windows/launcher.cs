using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows.Forms;

internal static class AurisLauncher
{
    private static readonly object LogLock = new object();

    // Windows argv quoting, including paths ending in a backslash.
    private static string Quote(string value)
    {
        var text = new StringBuilder("\"");
        int slashes = 0;
        foreach (char ch in value)
        {
            if (ch == '\\') { slashes++; continue; }
            if (ch == '"') text.Append('\\', slashes * 2 + 1);
            else text.Append('\\', slashes);
            text.Append(ch);
            slashes = 0;
        }
        text.Append('\\', slashes * 2);
        return text.Append('"').ToString();
    }

    [STAThread]
    private static int Main(string[] args)
    {
        string install = AppDomain.CurrentDomain.BaseDirectory;
        string data = Environment.GetEnvironmentVariable("AURIS_DATA_DIR");
        if (String.IsNullOrWhiteSpace(data))
            data = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Auris");
        for (int i = 0; i + 1 < args.Length; i++)
            if (args[i] == "--data-dir") data = args[i + 1];
        string logs = Path.Combine(Path.GetFullPath(data), "logs");
        try
        {
            Directory.CreateDirectory(logs);
            string log = Path.Combine(logs, "launcher.log");
            if (File.Exists(log) && new FileInfo(log).Length > 5000000)
            {
                if (File.Exists(log + ".old")) File.Delete(log + ".old");
                File.Move(log, log + ".old");
            }
            string python = Path.Combine(install, "runtime", "python.exe");
            string entry = Path.Combine(install, "app", "reader", "desktop.py");
            if (!File.Exists(python) || !File.Exists(entry))
                throw new FileNotFoundException("Az Auris fájljai hiányoznak. Futtasd újra a telepítőt.");
            var arguments = new StringBuilder("-s -X utf8 -u " + Quote(entry));
            foreach (string arg in args) arguments.Append(" " + Quote(arg));
            int result;
            do
            {
                var info = new ProcessStartInfo(python, arguments.ToString());
                info.WorkingDirectory = Path.GetDirectoryName(entry);
                info.UseShellExecute = false;
                info.CreateNoWindow = true;
                info.RedirectStandardOutput = true;
                info.RedirectStandardError = true;
                info.StandardOutputEncoding = Encoding.UTF8;
                info.StandardErrorEncoding = Encoding.UTF8;
                info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
                info.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1";
                using (var process = Process.Start(info))
                {
                    DataReceivedEventHandler capture = delegate(object sender, DataReceivedEventArgs e)
                    {
                        if (e.Data == null) return;
                        lock (LogLock) File.AppendAllText(log, e.Data + Environment.NewLine, Encoding.UTF8);
                    };
                    process.OutputDataReceived += capture;
                    process.ErrorDataReceived += capture;
                    process.BeginOutputReadLine();
                    process.BeginErrorReadLine();
                    process.WaitForExit();
                    result = process.ExitCode;
                }
            } while (result == 75);
            return result;
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message + "\n\nNaplók: " + logs, "Auris – indítási hiba",
                            MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
