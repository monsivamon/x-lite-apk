# 🐦 X LITE APK ✨

Builds patched Twitter/X APKs using [Piko](https://github.com/crimera/piko/tree/x-lite) patches from the `x-lite` branch.

**⏰ Runs automatically every day at 16:00 JST.**

📦 [Latest Build Download](https://github.com/monsivamon/x-lite-apk/releases/latest)

## 🚀 Features

- 📱 Publishes up to **2 APK variants** per release (arm64 bundle, lowest Android requirement first).
- 🛟 **Per-variant fallback**: a failed download or patch on one variant does not block the other.
- ✅ If a patch fails, the build continues with `--continue-on-error` instead of stopping.
- 📊 Per-patch success/failure is written to `patches-applied.txt`, split into one section per variant.
- 🧹 The generated `.mpp` is limited to **NewX (X-Lite) patches** only.
- 🚫 Skips `alpha` / `beta` X versions; only stable `prod` / `release` builds are used.

## 📦 Release Assets

Every release includes:

| Asset | Description |
|---|---|
| `piko-lite-v{version}-{commit}-android-{minVer}.apk` | Patched APK for a specific minimum Android version (e.g. `android-12L+.apk`, `android-9.0+.apk`). One per successfully built variant. |
| `patches-{commit}.mpp` | Piko patch bundle used for the build (NewX only). |
| `patches-applied.txt` | Per-variant sections listing applied patches with `Success` / `Failure`; new patches marked with `**NEW**`. |

### Which APK should I install?

| File suffix | Target |
|---|---|
| `-android-9.0+.apk` | Broad compatibility (Android 9 and newer), single screen DPI |
| `-android-12L+.apk` | Universal screen DPI (120-640dpi), Android 12L and newer |

Pick the one whose **Minimum Version** matches your device. If both work, either is fine.

## ⚠️ Disclaimer

All patches included in this build are under development.  
Stable operation is not guaranteed.  
Unexpected issues may occur. Please use at your own risk.

## 🙏 Credits

- 🛠️ [morphe](https://github.com/MorpheApp) - patcher
- 🛠️ [crimera](https://github.com/crimera/piko) - Piko patches
- 💡 [j-hc](https://github.com/j-hc) - Project is inspired by j-hc's revanced builder template.