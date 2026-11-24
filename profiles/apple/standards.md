# apple

## Verification

- The universal Verification table picks the driver: cua-driver for a macOS GUI app and T3 device tools for an iOS or Android app. A repository's `verify-<app>` skill records the observed behaviour as Verification Evidence.
- For iOS Simulator and Android Emulator, call T3 `device_list`, then `device_open` with the host and device IDs. Use the returned `agent-device` command for launches, interaction and recordings, and `device_screenshot` to inspect the screen. The repository's `verify-<app>` skill describes the app flows to drive.
- Use `xcrun simctl` and `adb` for builds, installs and logs, not for driving devices.
