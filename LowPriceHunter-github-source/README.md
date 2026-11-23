# Low Price Hunter

掃描你已加入的 Facebook 買賣社團，找到符合「關鍵字＋價格區間」的貼文就寄 Email 通知。
介面是 **WebView2 原生視窗**（不是瀏覽器分頁），支援多社團、多物品、即時或每日摘要。

> 本專案只包含程式碼與範例設定，**不含任何帳號、密碼、Cookie 或瀏覽器 session**。

---

## 功能

- **原生桌面視窗**：C# WinForms + WebView2，沒有網址欄與分頁
- **五步設定**：登入 Facebook → 選社團 → 物品規則 → Email → 掃描週期
- **多物品規則**：每個物品可有自己的關鍵字、排除詞、最低價、最高價
- **多社團套用**：用矩陣勾選每個社團要套用哪些物品
- **自動排序**：強制使用 Facebook「新貼文」時間排序，不會只看到熱門舊文
- **只看新貼文**：可設定只通知 N 分鐘內發布的貼文（預設 10 分鐘）
- **游標去重**：每社團記住最後看過的貼文，由新到舊追到游標就停，避免掃描延遲或高頻洗版漏掉新貼文
- **跨社團去重**：同一貼文出現在多個社團只寄一封，通知會列出所有來源
- **預設排除已售出**：已售出、已成交、已保留、sold、已下架 預設跳過（可自行修改）
- **高風險詞提示**：預付、先匯款、貨到付款、代購、急售 會在通知中標記，但不攔截
- **必須包含**：除關鍵字外，可要求貼文含產品類型字（例如 128G／256G／保固），避免只看到平台或分類字就誤判
- **價格區間**：支援 `5000-6000`、`1萬2`、`12k`、`可議`、`含運`；貼文區間與你的範圍重疊就通知（邊界包含）
- **可議不影響價格**：`可議`／`誠可議` 會忽略，價格仍用前面的數字；只有單獨出現、沒有數字時才算價格不明
- **價格靠近產品字**：價格必須出現在產品字附近，避免「平台…配件 $800」這種距離太遠的誤判
- **排除多項商品清單**：一次列 3 個以上價格且沒有「合售／整組」等字，視為清單跳過
- **規則測試器**：貼一段貼文，即時顯示每條規則命中／未命中的原因
- **測試器抓真實貼文**：貼 Facebook 貼文或社團網址，自動抓取內容測試
- **去重**：同一篇貼文只通知一次；第一次執行只建立基準線
- **通知方式**：即時寄出，或每日摘要一次寄
- **機器人信箱**：收件者只需填自己的 Email，不用碰應用程式密碼
- **通知渠道**：Email、Telegram、Discord 可複選；Telegram Token 與 Discord Webhook 以 DPAPI 分開加密
- **多個收件人**：Email 可用逗號、分號或換行分隔，會逐一寄送，收件者不會看到彼此地址
- **封面控制**：主畫面只留開始監控、停止監控、一鍵輸出日誌，其他設定收在「設定」
- **中英文介面**：設定可切換 中文／English
- **日誌與診斷包**：`logs/` 每日輪替；一鍵輸出 zip 到桌面（Email 會遮罩）
- **設定匯入／匯出**：可把社團、規則、Email、掃描設定存成 JSON，換電腦時完整還原
- **每日摘要分組**：摘要會按物品分組列出
- **登出 Facebook**：切換監控帳號時，一鍵停止監控並清除本機登入 session
- **開機自動啟動**（可選）與**啟動後自動監控**（可選）
- **可攜版**：`software/` 內含打包好的 exe，解壓即可用

---

## 系統需求

| 項目 | 需求 |
|---|---|
| 作業系統 | Windows 10 / 11（64 位元） |
| WebView2 Runtime | Windows 11 內建；Windows 10 需安裝 |
| .NET Framework | 4.8（Windows 10/11 通常內建） |
| 原始碼執行 | Python 3.10+、Google Chrome（Playwright 使用） |

---

## 一鍵安裝（推薦）

解壓縮後，直接雙擊根目錄的：

```
一鍵安裝 Low Price Hunter.bat
```

安裝器會：

- 複製程式到 `%LOCALAPPDATA%\Programs\LowPriceHunter`
- 建立桌面與開始選單捷徑（使用老鷹圖示）
- 加入 Windows「新增/移除程式」
- 升級時保留 `data/`（登入 session、設定、資料庫）

要卸載：雙擊 `一鍵卸載 Low Price Hunter.bat`，或從開始選單執行。

> 安裝腳本沒有數位簽章，Windows SmartScreen 可能顯示「未知的發行者」；
> 選擇「更多資訊 → 仍要執行」即可。也可以跳過安裝，直接跑 `software/LowPriceHunter/LowPriceHunter.exe`。

## 快速開始（可攜版）

1. 解壓縮這個壓縮檔。
2. 進入 `software/LowPriceHunter/`。
3. 雙擊 `LowPriceHunter.exe` 後，在「Email 通知」步驟填機器人 Gmail 與應用程式密碼，按「儲存機器人信箱」。
   （也可以先建立 `data/bot_email.json`，見下方說明。）
4. 雙擊 `LowPriceHunter.exe`。
5. 在視窗中依序完成：
   - 按「開啟瀏覽器登入」登入 Facebook
   - 按「抓取我已加入的社團」或手動貼網址
   - 設定要監控的物品與價格
   - 填接收通知的 Email
   - 按「開始監控」

> 第一次執行會在 `data/` 自動建立設定檔與資料庫；不會把舊貼文一次寄給你。

> GitHub 原始碼 repo 不含 `software/`（執行檔較大，已列入 `.gitignore`）。
> 可攜版請使用本壓縮檔內的 `software/`，或執行 `build.ps1` 自行產生，或放到 GitHub Releases。

---

## 機器人 Email 設定

本程式預設用一個「機器人信箱」寄信，這樣使用者只需要填收件地址。

最快的設定方式：在 App 的「Email 通知」步驟直接填機器人 Gmail 與應用程式密碼，按「儲存機器人信箱」。
下方的「開啟 Google 應用程式密碼頁面」會幫你開啟 Google 頁面（仍需自己登入與建立密碼）。

也可以手動建立 `data/bot_email.json`（第一次啟動會自動用 DPAPI 加密成 `bot_email.dpapi`，並刪除明文）。

```json
{
  "user": "your-bot@gmail.com",
  "app_password": "xxxx xxxx xxxx xxxx",
  "from_name": "Low Price Hunter Bot"
}
```

取得 Gmail 應用程式密碼：

1. 開啟 Google 帳號的「兩步驟驗證」
2. 到 <https://myaccount.google.com/apppasswords> 建立應用程式密碼
3. 貼進上面的 `app_password`（空白可以保留）

也可以改用 `.env`（放在 exe 旁）：

```
GMAIL_USER=your-bot@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
MAIL_TO=your-receiving@gmail.com
```

> Gmail 一般帳號每日寄送上限約 500 封。憑證只存在本機，請勿外流。

---

## 從原始碼執行

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe start_ui.py
```

第一次啟動會開啟原生視窗；若 WebView2 視窗失敗，會自動退回預設瀏覽器開啟 `http://127.0.0.1:8765/`。

---

## 打包成 exe

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

`build.ps1` 會：

1. 建立 `.venv` 並安裝依賴
2. 用 Windows 內建的 `csc.exe` 編譯 `native_host/LowPriceHunterHost.exe`
3. 用 PyInstaller 產生 `dist/LowPriceHunter/`

---

## 設定檔

`data/app_config.json`：

```json
{
  "groups": [
    { "name": "iPhone 買賣社團", "url": "https://www.facebook.com/groups/123456789/", "rules": ["iPhone 15"] }
  ],
  "rules": [
    {
      "name": "iPhone 15",
      "keywords": ["iPhone 15", "iPhone 16"],
      "exclude": ["殼", "貼", "充電線", "徵求", "收購", "已售", "【收】", "預算"],
      "require": ["128G", "256G", "512G", "Pro", "保固"],
      "price_near_keyword": true,
      "near_distance": 40,
      "reject_multi_item": true,
      "bundle_words": ["合售", "整組", "全套", "打包", "一起帶", "含運"],
      "min_price": 8000,
      "max_price": 25000
    }
  ],
  "email": { "to": ["you@gmail.com", "friend@gmail.com"], "mode": "bot" },
  "notify_mode": "instant",
  "digest_hour": 21,
  "max_post_age_minutes": 10,
  "interval_seconds": 300,
  "max_posts_per_group": 10,
  "autostart": false,
  "auto_monitor": false,
  "profile_dir": "data/ui_profile"
}
```

- `rules` 省略時，該社團套用全部規則；`rules: []` 代表不監控該社團。
- `notify_mode`：`instant`（即時）或 `digest`（每日摘要）。
- `email.to`：單一字串或以逗號／換行分隔，也可用 JSON 陣列；最多 20 個。
- `autostart`：登入 Windows 後自動在背景啟動。
- `auto_monitor`：程式啟動後自動開始監控。
- `max_post_age_minutes`：只通知幾分鐘內發布的貼文；`0` = 不限。抓不到時間的貼文會跳過。
- `price_near_keyword`：價格必須靠近產品字（`require` 或 `keywords`），預設 `true`。
- `near_distance`：價格與產品字的最大距離（字數），預設 `60`。
- `reject_multi_item`：貼文有 3 個以上價格且沒有 `bundle_words` 時視為商品清單，跳過。
- `bundle_words`：允許清單的合售字，例如「合售、整組、全套」。

> Python 套件資料夾沿用 `ps5hunter`（歷史因素），但監控規則與功能都已通用化，可監控任何產品。

---

## 資料保護

- **機器人憑證**：`data/bot_email.dpapi` 以 Windows DPAPI（目前使用者）加密；換 Windows 使用者或拿到別台電腦都無法解密。
- **渠道憑證**：`data/channels.dpapi` 儲存 Telegram Bot Token 與 Discord Webhook，同樣用 DPAPI 加密。
- **Facebook 登入**：`data/ui_profile` 由 Chromium 管理，Cookie 本身由 Windows DPAPI 金鑰加密，不是明文。
- **一般設定**：社團、規則、收件 Email 仍是明文，但不含密碼。
- **DPAPI 的限制**：同一個 Windows 使用者權限下執行的惡意程式仍可解密。建議搭配：
  - **BitLocker**（Windows Pro）或 **VeraCrypt** 加密整顆磁碟／容器
  - 用專門的 Windows 使用者執行本程式
- **換電腦**：DPAPI 不能跨機器。請在新電腦的 UI 重新輸入機器人 Gmail 與應用程式密碼；或使用一次性的 `bot_email.json`，第一次啟動會自動加密並刪除明文。

## 專案結構

```
.
├─ ps5hunter/               Python 套件
│  ├─ webui.py              本機 HTTP UI + 監控引擎
│  ├─ scraper.py            Playwright 讀取 Facebook
│  ├─ parser.py             價格與關鍵字解析
│  ├─ generic.py            通用物品規則
│  ├─ notifier.py           Gmail 寄信
│  ├─ db.py                 SQLite 去重
│  └─ ui/index.html         前端介面
├─ native_host/             C# WebView2 視窗
├─ installer/               一鍵安裝／卸載腳本
├─ assets/                  圖示
├─ software/LowPriceHunter/ 打包好的可攜版（不含資料）
├─ start_ui.py              啟動入口
├─ build.ps1                打包腳本
└─ requirements.txt
```

---

## 運作原理

1. 用 Playwright 開啟 `https://www.facebook.com/groups/<id>/?sorting_setting=CHRONOLOGICAL`
   （等於自動切到「新貼文」）
2. 讀取貼文卡片，展開「查看更多」
3. 取得永久連結：相片 `set=gm.<post_id>`、Marketplace `commerce/listing/<id>`，或點「回應」取得 `/permalink/<id>/`
4. 以關鍵字、排除詞、價格區間判斷
5. 去重後寄 Email（或存入每日摘要）

---

## 自動更新（GitHub Releases）

App 內建版本檢查：

- 設定 →「版本與更新」填入 GitHub repo，例如 `yourname/LowPriceHunter`
- 勾選「每天自動檢查更新」，設定檢查時間（預設 09:00）
- 有新版本時，封面會出現提示，可「查看更新」或「下載更新檔」
- 下載的 zip 解壓縮後覆蓋程式即可，`data\` 會保留

Release 建議：

- tag：`v1.1.0`、`v1.2.0`（語意版本）
- asset：`LowPriceHunter-update.zip`（優先）或 `LowPriceHunter-portable.zip`
- 版本號需大於目前程式版本才會提示

詳細發布步驟見 `docs/UPDATING.md`。

## 常開部署

### Windows 舊筆電／迷你主機（建議）

- 插電，並把睡眠設為「永不」：設定 → 系統 → 電源。
- 在 App 勾選「登入 Windows 後自動在背景啟動」與「啟動後自動開始監控」。
- 建議使用專門的 Windows 使用者，並開 BitLocker。
- 螢幕可以關，但不要讓電腦休眠。

### NAS／Docker

目前版本綁定 **Windows DPAPI** 與 **WebView2**，不支援 Linux NAS 原生執行。可行替代：

- Windows 迷你主機（最簡單）
- NAS 上的 Windows 虛擬機

若未來要做 Linux 伺服器版，需要把憑證改為 passphrase／環境變數，並用瀏覽器開 UI。

## 免責聲明

- Facebook 沒有提供一般社團成員的公開 API；本工具以瀏覽器自動化讀取你**已加入**的社團。
- 自動化可能違反 Facebook 服務條款，帳號有被限制的風險；請自行評估，建議低頻率、使用專門帳號。
- 本專案不含任何 Facebook 或 Gmail 憑證；請勿把 `data/`、`.env`、`bot_email.json` 上傳到公開儲存庫。
- `assets/logo.*` 為 AI 生成的專案圖示，可自由替換；若要商業使用請自行確認相關條款。
- 本軟體僅供個人研究與學習，開發者不對使用造成的損失負責。

---

## License

MIT License，詳見 [LICENSE](LICENSE)。
