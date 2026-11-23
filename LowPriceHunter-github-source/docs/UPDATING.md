# GitHub Releases 與自動更新

## 1. 目前版本

程式版本定義在 `ps5hunter/__init__.py`：

```python
__version__ = "1.1.0"
```

發佈新版本時，先修改這個數字，再重新打包。

## 2. 建立 GitHub repo

1. 在 GitHub 建立 repo，例如 `yourname/LowPriceHunter`。
2. 把 `LowPriceHunter-repo` 內容推上去。
3. 在 App 的「設定 → 版本與更新」填入 `yourname/LowPriceHunter`。

## 3. 打包 release 資產

在 Windows 建置：

```powershell
pyinstaller --noconfirm --clean --onedir --windowed `
  --name LowPriceHunter --icon assets\logo.ico `
  --add-data "ps5hunter\ui\index.html;ps5hunter\ui" `
  --add-data "assets;assets" `
  --add-data "native_host;native_host" `
  --collect-all playwright start_ui.py
```

把 `dist\LowPriceHunter\*` 壓成 zip，命名為：

```
LowPriceHunter-update.zip
```

`data\` 不要放進更新 zip；App 下載後只覆蓋程式，使用者資料會保留。

## 4. 建立 release

1. GitHub repo → Releases → Draft a new release
2. Tag：`v1.2.0`（要比目前版本大）
3. 上傳 `LowPriceHunter-update.zip`
4. 填寫更新說明（會顯示在 App 的下載提示）
5. 發佈（不要勾選 pre-release）

## 5. App 檢查邏輯

- 使用 GitHub API：`/repos/<owner>/<repo>/releases/latest`
- 每天在設定的時間檢查一次（預設 09:00）
- 比對 `tag_name` 與目前版本
- 有新版本時：
  - 封面顯示提示
  - 「查看更新」開 release 頁
  - 「下載更新檔」把 zip 存到桌面
- 「略過此版本」會記住版本，不再提示同一版

## 6. 更新步驟（目前）

1. 按「下載更新檔」
2. 關閉 App
3. 解壓縮 zip
4. 覆蓋舊的 `LowPriceHunter.exe` 與 `_internal\`
5. 保留原本的 `data\`，重新開啟

## 7. 之後可加的自動安裝

若要做「一鍵自動覆蓋」：

- release zip 固定包含 `LowPriceHunter.exe` 與 `_internal\`
- App 下載後解壓到 `data\updates\<version>`
- 產生 PowerShell 腳本：等待 App 結束 → 備份舊檔 → robocopy 新檔（排除 `data\`、`logs\`）→ 重新啟動
- 需要处理檔案鎖定、防毒誤判、失敗回滾與程式簽章

目前先提供檢查＋提示＋下載，避免自動覆蓋造成無法啟動。
