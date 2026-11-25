using System.Diagnostics;
using System.Net.Http;
using System.Runtime.InteropServices;
using System.Text.Json;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using Microsoft.Win32;

namespace PipkinWindows;

internal static class Program
{
    [STAThread]
    private static void Main()
    {
        ApplicationConfiguration.Initialize();
        Application.Run(new MainWindow());
    }
}

internal sealed class MainWindow : Form
{
    private const int Port = 8765;
    private static readonly Uri AppOrigin = new($"http://127.0.0.1:{Port}/");
    private static readonly HttpClient Http = new() { Timeout = TimeSpan.FromSeconds(2) };

    private readonly WebView2 _webView;
    private readonly Label _statusLabel;
    private Process? _serverProcess;
    private bool _closing;
    private bool _isBusy;

    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int attrValue, int attrSize);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool FlashWindowEx(ref FLASHWINFO pwfi);

    [StructLayout(LayoutKind.Sequential)]
    private struct FLASHWINFO
    {
        public uint cbSize;
        public IntPtr hwnd;
        public uint dwFlags;
        public uint uCount;
        public uint dwTimeout;
    }

    private const int DwmwaUseImmersiveDarkMode = 20;
    private const uint FlashwAll = 3;
    private const uint FlashwTimerNoFg = 12;

    public MainWindow()
    {
        Text = "Pipkin";
        Width = 1120;
        Height = 780;
        MinimumSize = new Size(760, 520);
        StartPosition = FormStartPosition.CenterScreen;
        try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }

        bool dark = IsWindowsDarkMode();
        BackColor = dark ? Color.FromArgb(16, 24, 21) : Color.FromArgb(225, 230, 223);
        ForeColor = dark ? Color.FromArgb(230, 236, 233) : Color.FromArgb(24, 32, 28);

        _statusLabel = new Label
        {
            Text = "Starting Pipkin…",
            Dock = DockStyle.Fill,
            TextAlign = ContentAlignment.MiddleCenter,
            Font = new Font("Segoe UI", 11f, FontStyle.Regular),
            ForeColor = ForeColor,
            BackColor = BackColor,
        };

        _webView = new WebView2
        {
            Dock = DockStyle.Fill,
            Visible = false,
            DefaultBackgroundColor = BackColor,
        };

        Controls.Add(_webView);
        Controls.Add(_statusLabel);

        HandleCreated += (_, _) => ApplyTitleBarTheme(IsWindowsDarkMode());
        Load += async (_, _) => await InitializeAsync();
        FormClosing += OnFormClosing;
    }

    private void OnFormClosing(object? sender, FormClosingEventArgs e)
    {
        if (_isBusy && e.CloseReason == CloseReason.UserClosing)
        {
            var choice = MessageBox.Show(
                this,
                "Pipkin is still working on a reply or Agent run.\n\nClose anyway and stop the current run?",
                "Pipkin",
                MessageBoxButtons.YesNo,
                MessageBoxIcon.Question,
                MessageBoxDefaultButton.Button2);
            if (choice != DialogResult.Yes)
            {
                e.Cancel = true;
                return;
            }
        }
        ShutdownServer();
    }

    private async Task InitializeAsync()
    {
        try
        {
            if (!await IsServerHealthyAsync())
            {
                if (!StartPythonServer(out string? error))
                {
                    ShowStartupError(error ?? "Could not start the local Pipkin server.");
                    return;
                }

                bool ready = await WaitForServerAsync(TimeSpan.FromSeconds(15));
                if (!ready)
                {
                    ShowStartupError(
                        "Pipkin's local server did not respond in time.\n\n" +
                        "Make sure Python 3.9+ is installed and try launching Pipkin again.");
                    return;
                }
            }

            string userDataDir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "Pipkin",
                "WebView2");
            Directory.CreateDirectory(userDataDir);

            var env = await CoreWebView2Environment.CreateAsync(userDataFolder: userDataDir);
            await _webView.EnsureCoreWebView2Async(env);

            var core = _webView.CoreWebView2;
            core.Settings.IsStatusBarEnabled = false;
            core.Settings.AreDefaultContextMenusEnabled = true;
            core.Settings.IsZoomControlEnabled = true;

            core.NavigationStarting += OnNavigationStarting;
            core.NewWindowRequested += OnNewWindowRequested;
            core.WebMessageReceived += OnWebMessageReceived;

            core.Navigate(AppOrigin.AbsoluteUri);
            _webView.Visible = true;
            _statusLabel.Visible = false;
        }
        catch (WebView2RuntimeNotFoundException)
        {
            ShowStartupError(
                "Microsoft Edge WebView2 Runtime is required to run Pipkin.\n\n" +
                "It is built into Windows 11 and can be installed from Microsoft on Windows 10.");
        }
        catch (Exception ex)
        {
            ShowStartupError($"Pipkin could not start:\n\n{ex.Message}");
        }
    }

    private void OnNavigationStarting(object? sender, CoreWebView2NavigationStartingEventArgs e)
    {
        if (!Uri.TryCreate(e.Uri, UriKind.Absolute, out Uri? target))
        {
            e.Cancel = true;
            return;
        }
        if (IsLocalAppUri(target))
        {
            return;
        }
        e.Cancel = true;
        OpenExternalUrl(target);
    }

    private void OnNewWindowRequested(object? sender, CoreWebView2NewWindowRequestedEventArgs e)
    {
        e.Handled = true;
        if (Uri.TryCreate(e.Uri, UriKind.Absolute, out Uri? target))
        {
            OpenExternalUrl(target);
        }
    }

    private void OnWebMessageReceived(object? sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        try
        {
            using var doc = JsonDocument.Parse(e.WebMessageAsJson);
            var root = doc.RootElement;
            string? type = root.TryGetProperty("type", out var typeEl) ? typeEl.GetString() : null;
            if (type == "theme")
            {
                string? scheme = root.TryGetProperty("scheme", out var schemeEl) ? schemeEl.GetString() : null;
                if (scheme == "dark") ApplyTitleBarTheme(true);
                else if (scheme == "light") ApplyTitleBarTheme(false);
                else if (scheme == "system" || scheme == "auto") ApplyTitleBarTheme(IsWindowsDarkMode());
            }
            else if (type == "busy")
            {
                _isBusy = root.TryGetProperty("value", out var valEl) && valEl.ValueKind == JsonValueKind.True;
                Text = _isBusy ? "Pipkin — Working…" : "Pipkin";
            }
            else if (type == "attention" || type == "finished")
            {
                FlashTaskbarIfInactive(type == "attention" ? 5u : 2u);
            }
        }
        catch
        {
            // Ignore unrecognized messages from the page.
        }
    }

    private void FlashTaskbarIfInactive(uint count)
    {
        if (!IsHandleCreated || Form.ActiveForm == this) return;
        var info = new FLASHWINFO
        {
            cbSize = (uint)Marshal.SizeOf<FLASHWINFO>(),
            hwnd = Handle,
            dwFlags = FlashwAll | FlashwTimerNoFg,
            uCount = count,
            dwTimeout = 0,
        };
        _ = FlashWindowEx(ref info);
    }

    private static bool IsLocalAppUri(Uri uri)
    {
        return uri.Scheme == Uri.UriSchemeHttp
            && string.Equals(uri.Host, "127.0.0.1", StringComparison.OrdinalIgnoreCase)
            && uri.Port == Port;
    }

    private static void OpenExternalUrl(Uri uri)
    {
        // Only allow http/https links to open in the user's default Windows browser.
        if (uri.Scheme != Uri.UriSchemeHttp && uri.Scheme != Uri.UriSchemeHttps)
        {
            return;
        }
        try
        {
            Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true });
        }
        catch
        {
            // Ignore OS shell launch failures.
        }
    }

    private bool StartPythonServer(out string? error)
    {
        error = null;
        string? serverScript = FindServerScript();
        if (serverScript == null)
        {
            error = "Could not find server.py next to Pipkin.exe.\n\nRe-extract the full Pipkin folder and try again.";
            return false;
        }

        string? workingDir = Path.GetDirectoryName(serverScript);
        (string exe, string args)[] candidates =
        {
            ("python", $"\"{serverScript}\" --serve --port {Port}"),
            ("py", $"-3 \"{serverScript}\" --serve --port {Port}"),
            ("python3", $"\"{serverScript}\" --serve --port {Port}"),
        };

        foreach (var (exe, args) in candidates)
        {
            try
            {
                var startInfo = new ProcessStartInfo
                {
                    FileName = exe,
                    Arguments = args,
                    WorkingDirectory = workingDir ?? AppContext.BaseDirectory,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = false,
                    RedirectStandardError = false,
                };
                startInfo.Environment["PYTHONDONTWRITEBYTECODE"] = "1";
                var proc = Process.Start(startInfo);
                if (proc != null)
                {
                    _serverProcess = proc;
                    return true;
                }
            }
            catch
            {
                // Try the next Python launcher candidate.
            }
        }

        error = "Python 3.9+ was not found on PATH.\n\nInstall Python from python.org (check 'Add Python to PATH') and launch Pipkin again.";
        return false;
    }

    private static string? FindServerScript()
    {
        string baseDir = AppContext.BaseDirectory;
        string direct = Path.Combine(baseDir, "server.py");
        if (File.Exists(direct)) return direct;

        // Also support running from windows/bin/Release/... during local development.
        DirectoryInfo? dir = new(baseDir);
        for (int i = 0; i < 5 && dir != null; i++)
        {
            string candidate = Path.Combine(dir.FullName, "server.py");
            if (File.Exists(candidate)) return candidate;
            dir = dir.Parent;
        }
        return null;
    }

    private static async Task<bool> IsServerHealthyAsync()
    {
        try
        {
            using var response = await Http.GetAsync(new Uri(AppOrigin, "health"));
            return response.IsSuccessStatusCode;
        }
        catch
        {
            return false;
        }
    }

    private async Task<bool> WaitForServerAsync(TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline && !_closing)
        {
            if (_serverProcess is { HasExited: true })
            {
                return false;
            }
            if (await IsServerHealthyAsync())
            {
                return true;
            }
            await Task.Delay(200);
        }
        return false;
    }

    private void ShutdownServer()
    {
        _closing = true;
        if (_serverProcess == null) return;
        try
        {
            if (!_serverProcess.HasExited)
            {
                _serverProcess.Kill(entireProcessTree: true);
                _serverProcess.WaitForExit(1500);
            }
        }
        catch
        {
            // Best-effort cleanup on exit.
        }
        finally
        {
            _serverProcess.Dispose();
            _serverProcess = null;
        }
    }

    private void ShowStartupError(string message)
    {
        _statusLabel.Text = message;
        MessageBox.Show(this, message, "Pipkin", MessageBoxButtons.OK, MessageBoxIcon.Warning);
    }

    private void ApplyTitleBarTheme(bool dark)
    {
        if (!IsHandleCreated) return;
        int useDark = dark ? 1 : 0;
        _ = DwmSetWindowAttribute(Handle, DwmwaUseImmersiveDarkMode, ref useDark, Marshal.SizeOf<int>());
    }

    private static bool IsWindowsDarkMode()
    {
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(
                @"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize");
            object? value = key?.GetValue("AppsUseLightTheme");
            return value is int i && i == 0;
        }
        catch
        {
            return true;
        }
    }
}
