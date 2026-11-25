# swift

## Toolchain

- SwiftFormat and SwiftLint are Gates. Use the Profile's configuration templates.
- Periphery is Advisory and never a local Gate. Public repositories use the official reusable workflow in the Profile template. It needs no account or secret.
- For local Periphery use, ranokay signs in to Periphery Pro with `periphery auth login`; use the vendored `periphery-scan-setup` skill to install and configure Periphery in a project, then `periphery-scan` to find and remove unused code, and file findings as issues. Do not use the archived open-source 3.x release.
- When the beta ends, use the official stable workflow as a required check for public repositories; ranokay decides whether private repositories retain Periphery.
