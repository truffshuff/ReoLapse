## Install (macOS, Apple Silicon)

1. Download **Reolapse-…-mac-arm64.dmg** below, open it and drag **Reolapse** to **Applications**.
2. The app isn't notarized by Apple, so the first time macOS will refuse to open it. Either:
   - open **System Settings → Privacy & Security**, scroll down and click **Open Anyway** next to Reolapse, or
   - run once in Terminal: `xattr -dr com.apple.quarantine /Applications/Reolapse.app`
3. On first launch Reolapse sets up its analysis engine (about 1.5 GB download, a few minutes). Updates reuse it.
4. In **Settings**, enter your NVR's address and login (stored in the macOS Keychain) and the archive folder on your share.

Requires macOS 13 or later on an Apple Silicon Mac. The job queue keeps running while the window is closed; quit with ⌘Q.
