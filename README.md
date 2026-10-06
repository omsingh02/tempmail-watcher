# tempmail-watcher

[![CI](https://github.com/omsingh02/tempmail-watcher/actions/workflows/ci.yml/badge.svg)](https://github.com/omsingh02/tempmail-watcher/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/downloads/)
[![Platforms](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey)](#platform-support)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

A zero-dependency command-line tool that creates a disposable email inbox, waits for a verification email, and copies the OTP code or confirmation link straight to your clipboard.

Handy for testing signup flows, QA work, and keeping your real address out of throwaway registrations.

```console
$ tempmail --raw
EMAIL: kq7x2m@sharklasers.com
OTP: 482913
FROM: noreply@example.com
SUBJECT: Your verification code
```

## Features

- **No dependencies.** Python standard library only. It's a single file, so you can also just download and run it.
- **Two providers.** [GuerrillaMail](https://www.guerrillamail.com) (default, no signup) and [Mail.tm](https://mail.tm).
- **Finds OTP codes** like `123456`, `123-456`, `123 456` and `ABC-789`, while ignoring zip codes, years, promo codes and reference numbers.
- **Finds confirmation links**, ranks them, and skips unsubscribe, social and tracking links.
- **Copies automatically.** The new address goes to your clipboard immediately; the code or link follows when the email arrives.
- **Desktop notifications** and optional auto-open of the confirmation link in your browser.
- **Scriptable.** `--raw` and `--json` output with meaningful exit codes.
- **Resume** the previous inbox within 60 minutes.
- **Cross-platform.** Linux (Wayland and X11), macOS, Windows, and WSL.

## Installation

Requires Python 3.10 or newer.

With [pipx](https://pipx.pypa.io) (recommended):

```sh
pipx install git+https://github.com/omsingh02/tempmail-watcher.git
```

With [uv](https://docs.astral.sh/uv/):

```sh
uv tool install git+https://github.com/omsingh02/tempmail-watcher.git
```

Or grab the single file and run it directly:

```sh
curl -fsSLO https://raw.githubusercontent.com/omsingh02/tempmail-watcher/main/tempmail.py
python3 tempmail.py          # Windows: py tempmail.py
```

On Linux, clipboard support needs [`wl-clipboard`](https://github.com/bugaevc/wl-clipboard) (Wayland) or `xclip`/`xsel` (X11). macOS and Windows work out of the box.

## Usage

```sh
tempmail                         # New inbox, copy address, wait for an OTP/link, copy it, exit
tempmail -o                      # Also open the confirmation link in your browser
tempmail -a                      # Just create an address, print it, copy it, exit
tempmail -u mybot99 -d grr.la    # Custom username and domain
tempmail -p mailtm               # Use Mail.tm instead of GuerrillaMail
tempmail -w                      # Keep watching for more emails (multi-step signups)
tempmail -r                      # Resume the previous inbox (within 60 minutes)
tempmail --copy link             # Copy the link even when the email also has a code
tempmail --list-domains          # Show available domains for the provider
```

Emails without an OTP or confirmation link (welcome emails, newsletters) are shown and skipped, and the watcher keeps waiting.

### Options

| Flag | Short | Description | Default |
|---|---|---|---|
| `--provider` | `-p` | Provider backend: `guerrilla` or `mailtm` | `guerrilla` |
| `--user` | `-u` | Custom username / prefix | random |
| `--domain` | `-d` | Domain to use; must be one of the provider's (see `--list-domains`) | provider default |
| `--interval` | `-i` | Polling interval in seconds (minimum 1) | `2.5` |
| `--timeout` | `-t` | Stop after this many seconds; `0` waits forever | `300` |
| `--copy` | `-c` | What to copy on arrival: `smart` (OTP, else link), `otp`, `link`, `email`, `none` | `smart` |
| `--open` | `-o` | Open high-confidence confirmation links in the default browser | off |
| `--watch` | `-w` | Keep watching for multiple emails | off |
| `--once` | | Exit after the first email with an OTP or link (the default; conflicts with `-w`) | on |
| `--address` | `-a` | Print and copy a new address, then exit | off |
| `--resume` | `-r` | Resume the previous inbox; fails if there is none from the last 60 minutes | off |
| `--raw` | | Line-based output for scripts | off |
| `--json` | `-j` | JSON event stream for scripts | off |
| `--no-notify` | | Disable desktop notifications | off |
| `--no-copy-address` | | Don't copy the new address to the clipboard | off |
| `--list-domains` | | List the provider's domains and exit | |
| `--version` | `-v` | Print the version and exit | |

Set `NO_COLOR=1` to turn off colors.

## Scripting

`--raw` prints `EMAIL: <address>` first, then for each email: `OTP:` and `LINK:` (when found), `FROM:` and `SUBJECT:`. A timeout is reported on stderr.

`--json` prints one JSON object per line:

| Event | Fields |
|---|---|
| `inbox_created` | `email`, `provider`, `resumed` |
| `message_received` | `from`, `subject`, `date`, `otps`, `links`, `copied`, `copied_type`, `opened` |
| `poll_error` | `error` (after repeated failures; retries continue) |
| `timeout` | `seconds` |

Example: get an address, trigger a signup, then wait for the code.

```sh
email=$(tempmail -a --no-copy-address)
./my-signup-test.sh "$email"
otp=$(tempmail -r --json | jq -r 'select(.event == "message_received") | .otps[0] // empty' | head -n 1)
```

### Exit codes

| Code | Meaning |
|---|---|
| `0` | OTP or confirmation link received (or `-a` / `--list-domains` succeeded) |
| `1` | Error: inbox creation failed, invalid domain, or nothing to `--resume` |
| `2` | Invalid command-line arguments |
| `124` | Timeout reached without an OTP or confirmation link |

## Platform support

| | Linux | macOS | Windows | WSL |
|---|---|---|---|---|
| Clipboard | `wl-copy`, `xclip` or `xsel` | `pbcopy` | built in | `clip.exe` (or `wl-copy` under WSLg) |
| Notifications | `notify-send` | Notification Center | toast notification | Windows toast notification |
| Open links | default browser | default browser | default browser | default browser, `wslview` or `explorer.exe` |
| Session cache | `$XDG_CACHE_HOME/tempmail` (`~/.cache/tempmail`) | `~/Library/Caches/tempmail` | `%LOCALAPPDATA%\tempmail` | same as Linux |

Missing clipboard or notification tools are skipped; the address and codes are still printed.

## Privacy and security

- **Temporary inboxes are not private.** Anyone who knows or guesses a GuerrillaMail address can read its inbox. Don't use these addresses for accounts or data you care about.
- **Email content is treated as untrusted.** Control characters are stripped before anything is printed, and `--open` only opens links that look like real verification links. Sender addresses can be forged, though, so review what gets opened.
- **The session file** stores the provider session (and the Mail.tm password) so `-r` can resume. It is readable only by you; delete it to forget the inbox.
- Please respect the terms of the services you sign up for and the providers' fair-use limits.

To report a vulnerability, see [SECURITY.md](SECURITY.md).

## Development

```sh
git clone https://github.com/omsingh02/tempmail-watcher.git
cd tempmail-watcher
python -m pip install pytest
python -m pytest              # tests mock the network, clipboard and browser
pipx run ruff check .         # lint
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

[MIT](LICENSE) © Om Singh. Not affiliated with GuerrillaMail or Mail.tm.
