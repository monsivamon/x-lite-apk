# 🐦 X LITE APK ✨

Builds patched Twitter/X APKs using [Piko](https://github.com/crimera/piko/tree/x-lite) patches from the `x-lite` branch.

**⏰ Runs automatically every day at 16:00 JST.**

📦 [Latest Build Download](https://github.com/monsivamon/x-lite-apk/releases/latest)

## 🚀 Features

- ✅ If a patch fails, the build continues with `--continue-on-error` instead of stopping.
- 📊 The success/failure status of each patch is written to `patches-applied.txt`.
- 📱 Requires an `arm64-v8a` bundle; versions without one are skipped and the previous compatible version is used.
- 🚫 Skips `alpha` / `beta` X versions; only stable `prod` / `release` builds are used.

## 📦 Release Assets

Every release includes:

| Asset | Description |
|---|---|
| `piko-lite-v{version}-{commit}.apk` | Patched APK ready to install |
| `patches-{commit}.mpp` | Piko patch bundle used for the build |
| `patches-applied.txt` | List of applied patches with per-patch status (`Success` / `Failure`); new patches marked with `**NEW**` |

## ⚠️ Disclaimer

All patches included in this build are under development.  
Stable operation is not guaranteed.  
Unexpected issues may occur. Please use at your own risk.

## 🙏 Credits

- 🛠️ [morphe](https://github.com/MorpheApp) - patcher
- 🛠️ [crimera](https://github.com/crimera/piko) - Piko patches
- 💡 [j-hc](https://github.com/j-hc) - Project is inspired by j-hc's revanced builder template.