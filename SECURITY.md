# Security Policy

## Supported versions

Security fixes are made for the latest release only.

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Report them privately through GitHub's
[private vulnerability reporting](https://github.com/omsingh02/tempmail-watcher/security/advisories/new).
Include the version, your platform, and steps or a sample email that reproduces the issue.

You can expect an acknowledgement within a week. Once a fix is released, the advisory will be published with credit to you, unless you prefer otherwise.

## Scope

tempmail-watcher processes email from public, unauthenticated inboxes, so all email content is untrusted. Issues of particular interest:

- Email content that reaches a shell, script interpreter or terminal unsanitized
- Links being opened in the browser that shouldn't be
- Exposure of the session file or provider credentials

The temporary email providers themselves (GuerrillaMail, Mail.tm) are out of scope; report problems with them to their operators.
