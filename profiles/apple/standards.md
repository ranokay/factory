# apple

## Brand exports

- `mise run assets` writes the Icon Composer `AppIcon.icon`, the compatibility `AppIcon.icns`, the `Brand.xcassets` PDFs and the menu-bar template. Run it instead of hand-editing an export.
- `ictool` and the macOS Swift toolchain are system tools that mise cannot pin; when one is missing the task names it and `--check` verifies the committed exports by lock instead of skipping them.

## Verification

- The universal Verification table picks the driver: cua-driver for a macOS GUI app and T3 device tools for an iOS app. A repository's `verify-<app>` skill records the observed behaviour as Verification Evidence.
- For iOS Simulator, call T3 `device_list`, then `device_open` with the host and device IDs. Use the returned `agent-device` command for launches, interaction and recordings, and `device_screenshot` to inspect the screen. The repository's `verify-<app>` skill describes the app flows to drive.
- Use `xcrun simctl` for builds, installs and logs, not for driving devices.
