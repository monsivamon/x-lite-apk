# X LITE APK

Builds patched Twitter/X APKs using [Piko](https://github.com/crimera/piko) patches from the `x-lite` branch.

**Runs automatically every day at 16:00 JST.**

[Latest Build Download](https://github.com/monsivamon/x-lite-apk/releases/latest)

## Differences from upstream

- If a patch fails, the build continues with `--continue-on-error` instead of stopping.
- The success/failure status of each patch is shown on the GitHub Release page.

# Credits
- [morphe](https://github.com/MorpheApp) - patcher
- [j-hc](https://github.com/j-hc) - Project is inspired by j-hc's revanced builder template.