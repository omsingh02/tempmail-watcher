#!/usr/bin/env python3
"""
Renders .github/assets/demo.gif: a time-compressed run of `tempmail` built from the tool's
real banner and message-card output, so the demo always matches the actual UI.

Needs chromium, ImageMagick (`magick`) and gifsicle. Run from the repository root:
    python .github/assets/demo.py
"""
import contextlib
import html
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, ".github", "assets", "demo.gif")
sys.path.insert(0, ROOT)
os.environ.pop("NO_COLOR", None)


class _FakeTTY(io.StringIO):
    def isatty(self):
        return True


# tempmail decides on colors at import time, so import it while stdout looks like a terminal
_real_stdout, sys.stdout = sys.stdout, _FakeTTY()
try:
    import tempmail
finally:
    sys.stdout = _real_stdout

from tempmail import UI, EmailMessage  # noqa: E402

WIDTH, HEIGHT = 740, 630
FG, BG = "#c9d1d9", "#0d1117"
COLORS = {30: "#0d1117", 31: "#ff7b72", 32: "#3fb950", 33: "#d29922", 34: "#58a6ff",
          35: "#d2a8ff", 36: "#39c5cf", 37: "#f0f6fc"}
BACKGROUNDS = {42: "#3fb950", 44: "#388bfd", 46: "#39c5cf"}
SGR = re.compile(r"\x1b\[([0-9;]*)m")

EMAIL = "kq7x2m@sharklasers.com"
PROMPT = f"{UI.GREEN}${UI.RESET} "


def capture(fn, *args, **kwargs) -> list:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*args, **kwargs)
    return buf.getvalue().replace("\a", "").split("\n")


def demo_message() -> EmailMessage:
    msg = EmailMessage(
        msg_id="2", sender="Acme <noreply@acme.dev>", subject="Your Acme verification code",
        date="2026-10-06 14:32:07", body_text="Your verification code is 482913. It expires in 10 minutes.",
        body_html='<p>Your verification code is <b>482913</b>.</p>'
                  '<a href="https://acme.dev/verify?token=9f82d1c7a4e5">Verify email address</a>')
    msg.analyze()
    return msg


def spinner_line(frame: int, seconds: int, checks: int) -> str:
    # Same format as the watch loop in tempmail.main()
    spin = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"][frame % 10]
    noun = "check" if checks == 1 else "checks"
    return f"  {UI.CYAN}{spin}{UI.RESET} Checking inbox... {UI.DIM}[00:{seconds:02d} elapsed, {checks} {noun}]{UI.RESET}"


def ansi_to_html(line: str) -> str:
    """Convert one line of SGR-colored text to HTML, giving wide characters exactly two cells."""
    out, state, pos = [], {}, 0

    def emit(text):
        if not text:
            return
        style = [f"color:{state.get('fg', FG)}"]
        if "bg" in state:
            style.append(f"background:{state['bg']}")
        if state.get("bold"):
            style.append("font-weight:700")
        if state.get("dim"):
            style.append("opacity:.55")
        if state.get("underline"):
            style.append("text-decoration:underline")
        cells, i = [], 0
        while i < len(text):
            ch = text[i]
            if i + 1 < len(text) and text[i + 1] == "\ufe0f":
                ch, i = ch + "\ufe0f", i + 1
            if tempmail.display_width(ch) == 2:
                # The cell is sized in the monospace font; only the glyph uses the emoji font
                cells.append(f'<span class="wide"><span>{html.escape(ch)}</span></span>')
            else:
                cells.append(html.escape(ch))
            i += 1
        out.append(f'<span style="{";".join(style)}">{"".join(cells)}</span>')

    for m in SGR.finditer(line):
        emit(line[pos:m.start()])
        pos = m.end()
        for code in [int(c) for c in (m.group(1) or "0").split(";") if c]:
            if code == 0:
                state = {}
            elif code == 1:
                state["bold"] = True
            elif code == 2:
                state["dim"] = True
            elif code == 4:
                state["underline"] = True
            elif code in COLORS:
                state["fg"] = COLORS[code]
            elif code in BACKGROUNDS:
                state["bg"] = BACKGROUNDS[code]
    emit(line[pos:])
    return "".join(out)


def page(lines: list, cursor: bool) -> str:
    body = "\n".join(ansi_to_html(line) for line in lines)
    if cursor:
        body += '<span class="cursor"> </span>'
    return f"""<!doctype html><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:{WIDTH}px;height:{HEIGHT}px;overflow:hidden;background:{BG}}}
.bar{{height:38px;display:flex;align-items:center;gap:8px;padding:0 14px;background:#161b22;border-bottom:1px solid #30363d}}
.dot{{width:12px;height:12px;border-radius:50%}}
.title{{flex:1;text-align:center;margin-right:52px;color:#7d8590;font:13px "Inter",sans-serif}}
pre{{padding:14px 18px;color:{FG};font:15px/1.3 "JetBrains Mono","JetBrainsMono Nerd Font","DejaVu Sans Mono",monospace}}
.wide{{display:inline-block;width:2ch;text-align:center;line-height:1}}
.wide span{{font-family:"Noto Color Emoji","JetBrains Mono",monospace;font-size:.95em}}
.cursor{{background:{FG}}}
</style><div class="bar"><span class="dot" style="background:#ff5f57"></span><span class="dot" style="background:#febc2e"></span><span class="dot" style="background:#28c840"></span><span class="title">tempmail</span></div><pre>{body}</pre>"""


def frames() -> list:
    """(lines, cursor, delay in 1/100 s) for each frame of the demo."""
    timeline = []
    timeline.append(([PROMPT], True, 70))
    for i in range(1, len("tempmail") + 1):
        timeline.append(([PROMPT + "tempmail"[:i]], True, 9))
    timeline.append(([PROMPT + "tempmail"], True, 45))

    banner = [PROMPT + "tempmail"] + capture(tempmail.print_banner, EMAIL, "GuerrillaMail", 2.5, 300, True, "smart")
    banner = banner[:-1]  # drop the empty string after the final newline
    for frame, (seconds, checks) in enumerate([(0, 1), (2, 2), (5, 3), (7, 4)]):
        timeline.append((banner + [spinner_line(frame, seconds, checks)], False, 80 if frame else 120))

    card = capture(tempmail.print_message_card, demo_message(), "482913")
    done = f"{UI.GREEN}{UI.BOLD}✨ Done! OTP ready in clipboard. Exiting.{UI.RESET}"
    final = banner + card[:-1] + [done, "", PROMPT]
    timeline.append((final, True, 450))
    return timeline


def main():
    for tool in ("chromium", "magick", "gifsicle"):
        if not shutil.which(tool):
            sys.exit(f"{tool} is required")
    with tempfile.TemporaryDirectory() as tmp:
        args = []
        for n, (lines, cursor, delay) in enumerate(frames()):
            src, png = os.path.join(tmp, f"{n:03d}.html"), os.path.join(tmp, f"{n:03d}.png")
            with open(src, "w", encoding="utf-8") as f:
                f.write(page(lines, cursor))
            subprocess.run(["chromium", "--headless", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                            "--force-device-scale-factor=1", f"--window-size={WIDTH},{HEIGHT}",
                            f"--screenshot={png}", f"file://{src}"],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            args += ["-delay", str(delay), png]
        raw = os.path.join(tmp, "raw.gif")
        subprocess.run(["magick", *args, "-loop", "0", "-layers", "Optimize", raw], check=True)
        subprocess.run(["gifsicle", "-O3", "--colors", "256", raw, "-o", OUT], check=True)
    print(f"wrote {OUT} ({os.path.getsize(OUT) // 1024} KB)")


if __name__ == "__main__":
    main()
