using System;
using System.Drawing;
using System.IO;
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

            var form = new Form();
            form.Text = "Low Price Hunter";
            form.Width = 1120;
            form.Height = 940;
            form.MinimumSize = new Size(820, 600);
            form.StartPosition = FormStartPosition.CenterScreen;
            form.BackColor = Color.FromArgb(243, 244, 246);

            if (!string.IsNullOrEmpty(iconPath) && File.Exists(iconPath))
            {
                try { form.Icon = new Icon(iconPath); } catch { }
            }

            var web = new WebView2();
            web.Dock = DockStyle.Fill;
            form.Controls.Add(web);

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
                    form.Close();
                }
            };

            Application.Run(form);
        }
    }
}
