# Contributing

Thanks for taking the time to look at this. A few notes before you send a PR, these help me review faster and keep the tool from drifting away from what it's meant to be.

## Before you start

**For bug fixes and small improvements:** just send the PR.

**For new features or larger changes:** please open an issue first so we can talk about it. The tool is intentionally minimalist, and not every feature is a good fit. Better to find out before you write the code than after.

A few things that are likely out of scope:
- Heavy dependencies for niche use cases
- Anything that adds significant runtime overhead
- Features that meaningfully expand the UI beyond the current tray + settings dialog

If in doubt, open an issue and ask.

## Pull request checklist

- One topic per PR. If your branch fixes a bug and adds a feature and refactors a module, please split it.
- Keep the diff focused. Reformatting unrelated files makes review much harder.
- Test it manually on Windows. Describe what you tested in the PR description (which mode, which hotkey, which output settings).
- If you changed the UI, include a screenshot or short GIF.
- If you added a dependency, mention why it's necessary.

## Using AI tools

Codex, Claude Code, Copilot, Cursor aree all fine. But please:
1. Mention it in the PR description.
2. Confirm you actually ran the code and tested it yourself.
3. Read the diff before submitting. If you can't explain what a part of your PR does, I probably shouldn't merge it.

AI-generated PRs are not rejected on principle, but they tend to over-engineer and add code that isn't needed. Trim before you submit.

## Development setup

```bash
pip install PyQt6 soundcard sounddevice soundfile numpy lameenc keyboard psutil pywin32
python main.py
```

For application-audio tracks and native release artifacts, follow [BUILDING.md](BUILDING.md).
It includes the verified proc-tap source build, pinned x64/ARM64 dependencies and
required architecture/runtime checks.

Run the tests before sending a PR:

```bash
pip install pytest
python -m pytest
```

For the standalone build:

Use the native packaging recipe in [BUILDING.md](BUILDING.md), including its
PyInstaller hooks. A successful cross-build is not native ARM64 runtime verification.

## Review timeline

This is a side project, so reviews can take a few days to a week. If a PR has been sitting for two weeks without a response, feel free to ping it.

## Reporting bugs

Open an issue with:
- Your Windows version
- What you did
- What you expected
- What happened instead
- Any error output (from the console if you ran it from source)

Thanks for contributing.
