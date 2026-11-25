using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace LowPriceHunterHost
{
    static class Program
    {
        [STAThread]
        static void Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            string url = args.Length > 0 ? args[0] : "http://127.0.0.1:8765/";
            string iconPath = args.Length > 1 ? args[1] : null;
            string userData = args.Length > 2 ? args[2] : null;
            string csrfToken = args.Length > 3 ? args[3] : "";
            string baseUrl = url.TrimEnd('/');

            var form = new Form();
            form.Text = "Low Price Hunter";
            form.Width = 1140;
            form.Height = 940;
            form.MinimumSize = new Size(820, 600);
            form.StartPosition = FormStartPosition.CenterScreen;
            form.BackColor = Color.FromArgb(243, 244, 246);

            Icon appIcon = SystemIcons.Application;
            if (!string.IsNullOrEmpty(iconPath) && File.Exists(iconPath))
            {
                try
                {
                    appIcon = new Icon(iconPath);
                    form.Icon = appIcon;
                }
                catch { }
            }

            var web = new WebView2();
            web.Dock = DockStyle.Fill;
            form.Controls.Add(web);

            // 系統匣常駐圖示與右鍵選單
            bool allowExit = false;
            bool shownTrayHint = false;
            bool isMonitoring = false;
            int lastToastId = -1;
            string lastToastUrl = "";
            bool polling = false;

            var trayMenu = new ContextMenuStrip();
            var itemShow = new ToolStripMenuItem("開啟 Low Price Hunter");
            var itemToggle = new ToolStripMenuItem("開始監控");
            var itemExit = new ToolStripMenuItem("關閉視窗");
            trayMenu.Items.Add(itemShow);
            trayMenu.Items.Add(itemToggle);
            trayMenu.Items.Add(new ToolStripSeparator());
            trayMenu.Items.Add(itemExit);

            var tray = new NotifyIcon();
            tray.Icon = appIcon;
            tray.Text = "Low Price Hunter";
            tray.ContextMenuStrip = trayMenu;
            tray.Visible = true;

            Action showWindow = () =>
            {
                if (!form.Visible) form.Show();
                if (form.WindowState == FormWindowState.Minimized)
                    form.WindowState = FormWindowState.Normal;
                form.Activate();
            };

            itemShow.Click += (s, e) => showWindow();
            tray.DoubleClick += (s, e) => showWindow();

            tray.BalloonTipClicked += (s, e) =>
            {
                if (!string.IsNullOrEmpty(lastToastUrl) &&
                    (lastToastUrl.StartsWith("http://", StringComparison.OrdinalIgnoreCase) ||
                     lastToastUrl.StartsWith("https://", StringComparison.OrdinalIgnoreCase)))
                {
                    try
                    {
                        Process.Start(new ProcessStartInfo(lastToastUrl) { UseShellExecute = true });
                    }
                    catch { }
                }
                else
                {
                    showWindow();
                }
            };

            itemToggle.Click += (s, e) =>
            {
                if (string.IsNullOrEmpty(csrfToken))
                {
                    showWindow();
                    return;
                }
                string endpoint = isMonitoring ? "/api/stop" : "/api/start";
                Task.Run(() =>
                {
                    try
                    {
                        using (var wc = new WebClient())
                        {
                            wc.Encoding = Encoding.UTF8;
                            wc.Headers[HttpRequestHeader.ContentType] = "application/json";
                            wc.Headers["X-CSRF-Token"] = csrfToken;
                            wc.UploadString(baseUrl + endpoint, "POST", "{}");
                        }
                    }
                    catch { }
                });
            };

            itemExit.Click += (s, e) =>
            {
                allowExit = true;
                tray.Visible = false;
                form.Close();
            };

            // 點右上角 X 時縮小至系統匣，不中斷背景監控；系統關機或更新腳本關閉時則正常結束
            form.FormClosing += (s, e) =>
            {
                if (e.CloseReason == CloseReason.UserClosing && !allowExit)
                {
                    e.Cancel = true;
                    form.Hide();
                    if (!shownTrayHint)
                    {
                        shownTrayHint = true;
                        lastToastUrl = "";
                        try
                        {
                            tray.ShowBalloonTip(
                                4000,
                                "Low Price Hunter 已縮小至系統匣",
                                "程式仍在背景持續運作；雙擊右下角圖示可重新開啟介面。",
                                ToolTipIcon.Info
                            );
                        }
                        catch { }
                    }
                }
                else
                {
                    tray.Visible = false;
                }
            };

            form.FormClosed += (s, e) =>
            {
                tray.Visible = false;
                tray.Dispose();
            };

            // 每 3 秒輪詢本機狀態與待彈出氣泡通知（純文字 key=value，無額外組件相依）
            var pollTimer = new Timer();
            pollTimer.Interval = 3000;
            pollTimer.Tick += (s, e) =>
            {
                if (polling || form.IsDisposed) return;
                polling = true;
                Task.Run(() =>
                {
                    string body = null;
                    try
                    {
                        using (var wc = new WebClient())
                        {
                            wc.Encoding = Encoding.UTF8;
                            body = wc.DownloadString(baseUrl + "/api/tray-state");
                        }
                    }
                    catch { }
                    finally
                    {
                        polling = false;
                    }

                    if (string.IsNullOrEmpty(body) || form.IsDisposed) return;
                    bool mon = false;
                    string health = "ok";
                    int toastId = 0;
                    string toastTitle = "";
                    string toastBody = "";
                    string toastUrl = "";
                    bool toastError = false;
                    string dealsToday = "0";

                    foreach (var rawLine in body.Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries))
                    {
                        int idx = rawLine.IndexOf('=');
                        if (idx <= 0) continue;
                        string k = rawLine.Substring(0, idx).Trim();
                        string v = rawLine.Substring(idx + 1).Trim();
                        if (k == "monitoring") mon = (v == "1");
                        else if (k == "health") health = v;
                        else if (k == "deals_today") dealsToday = v;
                        else if (k == "toast_id") int.TryParse(v, out toastId);
                        else if (k == "toast_title") toastTitle = v;
                        else if (k == "toast_body") toastBody = v;
                        else if (k == "toast_url") toastUrl = v;
                        else if (k == "toast_error") toastError = (v == "1");
                    }

                    try
                    {
                        form.BeginInvoke((Action)(() =>
                        {
                            if (form.IsDisposed) return;
                            isMonitoring = mon;
                            itemToggle.Text = mon ? "停止監控" : "開始監控";
                            string tip = "Low Price Hunter｜" + (mon ? "監控中" : "已停止")
                                + (health == "error" ? "（異常）" : "")
                                + "｜今日 " + dealsToday + " 筆";
                            if (tip.Length > 62) tip = tip.Substring(0, 62);
                            try { tray.Text = tip; } catch { }

                            if (lastToastId < 0)
                            {
                                lastToastId = toastId;
                            }
                            else if (toastId > lastToastId && !string.IsNullOrEmpty(toastTitle))
                            {
                                lastToastId = toastId;
                                lastToastUrl = toastUrl;
                                try
                                {
                                    tray.ShowBalloonTip(
                                        7000,
                                        toastTitle,
                                        string.IsNullOrEmpty(toastBody) ? "點擊查看貼文" : toastBody,
                                        toastError ? ToolTipIcon.Warning : ToolTipIcon.Info
                                    );
                                }
                                catch { }
                            }
                        }));
                    }
                    catch { }
                });
            };
            pollTimer.Start();

            form.Load += async (sender, e) =>
            {
                try
                {
                    CoreWebView2Environment env = null;
                    if (!string.IsNullOrEmpty(userData))
                    {
                        Directory.CreateDirectory(userData);
                        env = await CoreWebView2Environment.CreateAsync(null, userData);
                    }
                    await web.EnsureCoreWebView2Async(env);
                    web.CoreWebView2.Navigate(url);
                }
                catch (Exception ex)
                {
                    MessageBox.Show("WebView2 初始化失敗：\n" + ex.Message, "Low Price Hunter");
                    allowExit = true;
                    form.Close();
                }
            };

            Application.Run(form);
        }
    }
}
