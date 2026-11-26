"""統一 HTTPS / SMTP 的 TLS 憑證驗證設定。

Windows 11 系統憑證庫對部分根憑證（如 Sectigo / USERTrust）採延遲按需下載，
而 PyInstaller 打包後的 Python OpenSSL 不會觸發 Windows 自動下載根憑證，
容易導致 `[SSL: CERTIFICATE_VERIFY_FAILED] unable to get issuer certificate`。
這裡同時載入系統憑證庫與 certifi 完整根憑證包，維持嚴格驗證並兼容各台 Windows 電腦。
"""

from __future__ import annotations


def build_ssl_context():
    """建立結合系統憑證庫與 certifi 根憑證包的 SSLContext。"""
    try:
        import ssl
    except Exception:  # noqa: BLE001
        return None
    ctx = ssl.create_default_context()
    try:
        import certifi

        cafile = certifi.where()
        if cafile:
            ctx.load_verify_locations(cafile=cafile)
    except Exception:  # noqa: BLE001
        pass
    return ctx
