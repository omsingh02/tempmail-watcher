<div align="center">

# tempmail-watcher

**A disposable inbox in one command. The verification code lands in your clipboard.**

[![PyPI](https://img.shields.io/pypi/v/tempmail-watcher)](https://pypi.org/project/tempmail-watcher/)
[![Python](https://img.shields.io/pypi/pyversions/tempmail-watcher)](https://pypi.org/project/tempmail-watcher/)
[![CI](https://github.com/omsingh02/tempmail-watcher/actions/workflows/ci.yml/badge.svg)](https://github.com/omsingh02/tempmail-watcher/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](https://github.com/omsingh02/tempmail-watcher/blob/main/LICENSE)

[Install](#install) • [Usage](#usage) • [Scripting](#scripting) • [How it works](#how-it-works) • [FAQ](#faq)

<img src="https://raw.githubusercontent.com/omsingh02/tempmail-watcher/main/.github/assets/demo.gif" alt="tempmail creating an inbox, receiving a verification email and copying the OTP to the clipboard" width="740">

<sub>Sped up. Rendered from the tool's real output by <a href="https://github.com/omsingh02/tempmail-watcher/blob/main/.github/assets/demo.py">demo.py</a>.</sub>

</div>

Paste the address into a signup form, and by the time you switch back, the code or confirmation link is already in your clipboard. Useful for testing signup flows, QA, and keeping your real address out of throwaway registrations.

- **Zero setup.** No account, no API key, no dependencies. It's a single Python file.
- **Finds the right thing.** Codes like `482913`, `482-913` or `ABC-789`, and ranked confirmation links. Zip codes, promo codes, unsubscribe and tracking links are ignored.
- **Clipboard first.** The address is copied right away, then the code or link the moment it arrives, with a desktop notification.
- **Scriptable.** `--raw` and `--json` output and meaningful exit codes for test pipelines.
- **Runs everywhere.** Linux (Wayland and X11), macOS, Windows and WSL, all tested in CI.
- **Two providers.** [GuerrillaMail](https://www.guerrillamail.com) by default, [Mail.tm](https://mail.tm) as an alternative.

## Install

```sh
# Recommended: an isolated install with pipx
pipx install tempmail-watcher

# Or with uv
uv tool install tempmail-watcher

# Or with pip
python -m pip install tempmail-watcher
```

Requires Python 3.10+. On Linux, clipboard support needs [`wl-clipboard`](https://github.com/bugaevc/wl-clipboard) (Wayland) or `xclip`/`xsel` (X11).

<details>
<summary>No install: run the single file</summary>

```sh
curl -fsSLO https://raw.githubusercontent.com/omsingh02/tempmail-watcher/main/tempmail.py
python3 tempmail.py          # Windows: py tempmail.py
```

</details>

## Usage

```sh
tempmail                         # new inbox, wait, copy the code or link, exit
tempmail -o                      # also open the confirmation link in your browser
tempmail -a                      # just print and copy a fresh address
tempmail -w                      # keep watching (multi-step signups)
tempmail -r                      # resume the last inbox (within 60 minutes)
tempmail -p mailtm               # use Mail.tm instead of GuerrillaMail
tempmail -u mybot99 -d grr.la    # choose the username and domain
```

Emails without a code or link, like welcome mails and newsletters, are shown and skipped. Run `tempmail --help` for everything else.

<details>
<summary>All options</summary>

| Option | Description | Default |
|---|---|---|
| `-p`, `--provider` | `guerrilla` or `mailtm` | `guerrilla` |
| `-u`, `--user` | Username / prefix for the address | random |
| `-d`, `--domain` | Domain to use; must be one of the provider's (`--list-domains`) | provider default |
| `-i`, `--interval` | Seconds between inbox checks (minimum 1) | `2.5` |
| `-t`, `--timeout` | Give up after this many seconds; `0` waits forever | `300` |
| `-c`, `--copy` | What to copy: `smart` (code, else link), `otp`, `link`, `email`, `none` | `smart` |
| `-o`, `--open` | Open high-confidence confirmation links in the browser | |
| `-w`, `--watch` | Keep watching for more emails | |
| `--once` | Exit after the first email with a code or link (the default) | |
| `-a`, `--address` | Print and copy a new address, then exit | |
| `-r`, `--resume` | Resume the previous inbox from the last 60 minutes | |
| `--raw` | Line-based output for scripts | |
| `-j`, `--json` | JSON event stream for scripts | |
| `--no-notify` | No desktop notifications | |
| `--no-copy-address` | Don't copy the new address | |
| `--list-domains` | List the provider's domains and exit | |

Set `NO_COLOR=1` to turn off colors.

</details>

## Scripting

Create an address, run your signup test with it, then wait for the code:

```sh
email=$(tempmail -a --no-copy-address)             # create an address
./my-signup-test.sh "$email"                       # sign up with it
otp=$(tempmail -r --raw | sed -n 's/^OTP: //p')    # wait for the code
```

`--raw` prints `EMAIL:`, then `OTP:`, `LINK:`, `FROM:` and `SUBJECT:` lines per email. `--json` prints one event per line. If the timeout passes without a code or link, the exit code is `124`.

<details>
<summary>JSON events and exit codes</summary>

| Event | Fields |
|---|---|
| `inbox_created` | `email`, `provider`, `resumed` |
| `message_received` | `from`, `subject`, `date`, `otps`, `links`, `copied`, `copied_type`, `opened` |
| `poll_error` | `error` (after repeated failures; it keeps retrying) |
| `timeout` | `seconds` |

| Exit code | Meaning |
|---|---|
| `0` | Code or link received (or `-a` / `--list-domains` succeeded) |
| `1` | Error: inbox creation failed, invalid domain, or nothing to `--resume` |
| `2` | Invalid arguments |
| `124` | Timed out without a code or link |

</details>

## How it works

1. **Creates an inbox** on GuerrillaMail or Mail.tm and copies the address.
2. **Polls the inbox** every 2.5 seconds, for up to 5 minutes by default.
3. **Reads each email**, extracts codes and ranks the links, then copies the best match, notifies you and exits.

Clipboard and notifications use what each platform provides: `wl-copy`/`xclip`/`xsel` and `notify-send` on Linux, `pbcopy` and Notification Center on macOS, the Windows clipboard and toast notifications on Windows and WSL. If a tool is missing, that step is skipped and everything is still printed.

## Privacy and security

> **⚠️ Temporary inboxes are not private.** Anyone who knows a GuerrillaMail address can read its inbox, so don't use these addresses for accounts or data you care about.

- Email content is treated as untrusted: control characters are stripped before printing, and `--open` only opens links that look like real verification links.
- To make `-r` work, the provider session is saved in your user cache directory, readable only by you. Delete it to forget the inbox.
- Please respect the terms of the sites you sign up for and the providers' fair-use limits.

Found a vulnerability? See the [security policy](https://github.com/omsingh02/tempmail-watcher/blob/main/SECURITY.md).

## FAQ

<details>
<summary><b>Why not just use a temp-mail website?</b></summary>

You never leave the terminal: the address is copied for you, the code is copied when it arrives, and the same command works in scripts and CI.

</details>

<details>
<summary><b>A site rejects the email domain</b></summary>

Try another domain with `-d` (see `tempmail --list-domains`), or switch provider with `-p mailtm`.

</details>

<details>
<summary><b>Nothing is copied on Linux</b></summary>

Install `wl-clipboard` on Wayland or `xclip`/`xsel` on X11. The address and codes are still printed either way.

</details>

<details>
<summary><b>Where is the session stored?</b></summary>

In `~/.cache/tempmail` on Linux (or `$XDG_CACHE_HOME`), `~/Library/Caches/tempmail` on macOS and `%LOCALAPPDATA%\tempmail` on Windows. Delete it to forget the last inbox.

</details>

## Contributing

Bug reports and pull requests are welcome. See [CONTRIBUTING.md](https://github.com/omsingh02/tempmail-watcher/blob/main/CONTRIBUTING.md) to get started.

## License

[MIT](https://github.com/omsingh02/tempmail-watcher/blob/main/LICENSE) © Om Singh. Not affiliated with GuerrillaMail or Mail.tm.
