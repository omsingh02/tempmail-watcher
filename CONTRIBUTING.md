# Contributing

Thanks for your interest in improving tempmail-watcher! Bug reports, fixes, new providers and docs improvements are all welcome.

## Getting started

```sh
git clone https://github.com/omsingh02/tempmail-watcher.git
cd tempmail-watcher
python -m pip install pytest
python -m pytest
```

You can run the tool straight from the checkout with `python tempmail.py`, or install it in editable mode with `pipx install -e .`.

## Guidelines

- **Stay dependency-free.** The tool uses only the Python standard library, and `tempmail.py` must keep working as a single downloaded file.
- **Support Python 3.10+** on Linux, macOS, Windows and WSL. If you touch clipboard, notification, browser or path handling, think about each platform. CI runs the tests on all three operating systems.
- **Treat email content as untrusted.** Anything from an email (sender, subject, body, links) must never reach a shell, a script, or the terminal without sanitizing.
- **Add tests** for bug fixes and new behavior. Unit tests must not use the network, the real clipboard, or a browser; mock them like the existing tests do.
- **Platform checks** against the real clipboard and notification systems live in `tests/test_integration.py`. They only run with `TEMPMAIL_INTEGRATION=1` (they overwrite your clipboard), and CI runs them on Linux X11 and Wayland, macOS, Windows and WSL.
- **Lint** with `pipx run ruff check .` before opening a pull request.
- **Update [CHANGELOG.md](CHANGELOG.md)** under `Unreleased` for user-facing changes.

## Pull requests

1. Fork the repository and create a branch from `main`.
2. Make your change with tests.
3. Make sure `python -m pytest` and `ruff check .` pass.
4. Open a pull request describing what changed and why.

## Reporting bugs

Use the [bug report form](https://github.com/omsingh02/tempmail-watcher/issues/new/choose). Please include your OS, Python version, `tempmail --version`, and the provider. Don't paste codes or links you still need.

Security issues should be reported privately; see [SECURITY.md](SECURITY.md).

## Code of conduct

This project follows the [Code of Conduct](CODE_OF_CONDUCT.md).
