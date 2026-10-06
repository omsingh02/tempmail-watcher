# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- The progress line said "1 checks".

## [1.2.0] - 2026-10-06

First public release.

### Added

- Windows support: native clipboard, toast notifications, and colors in the classic console.
- WSL support: Windows toast notifications and opening links in the Windows browser.
- `NO_COLOR` support.
- Exit code `124` when the timeout passes without an OTP or confirmation link.
- JSON `timeout` and `poll_error` events, and an `opened` field on `message_received`.
- Mail.tm sessions log in again automatically when their token expires.
- A warning when inbox checks keep failing, with backoff between retries.

### Changed

- The watcher exits only after an email that contains an OTP or confirmation link; other emails are shown and skipped.
- `--open` only opens links with strong verification signals.
- `--resume` fails when there is no session from the last 60 minutes instead of silently creating a new inbox. The window now counts from inbox creation.
- `--domain` must be one of the provider's domains, `--interval` must be at least 1 second, and `--once` can't be combined with `--watch`.
- The session cache follows OS conventions: `~/Library/Caches/tempmail` on macOS, `%LOCALAPPDATA%\tempmail` on Windows, and `$XDG_CACHE_HOME` is honored on Linux.
- JSON `copied` is `null` when the clipboard copy failed.

### Fixed

- Confirmation links were discarded when they contained the inbox address or were on domains such as dropbox.com, microsoft.com or reddit.com.
- Zip codes, barcodes, promo codes and reference numbers were reported as OTPs.
- An email could be lost when downloading it failed.
- Clipboard failures were reported as success, and `EMAIL=$(tempmail -a)` could hang on Wayland.
- Box borders were misaligned around emoji and wide characters.
- Output no longer crashes when the terminal or pipe can't encode emoji.

### Security

- On macOS, an email subject could run AppleScript through the notification.
- Email headers and links could inject terminal escape sequences or fake `--raw` output lines.
- The session file, which contains provider credentials, is now private to the user and written atomically.

## [1.1.0]

- Initial version (not publicly released).

[Unreleased]: https://github.com/omsingh02/tempmail-watcher/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/omsingh02/tempmail-watcher/releases/tag/v1.2.0
