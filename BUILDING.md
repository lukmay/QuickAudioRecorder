# Native Windows artifacts

The release matrix builds separately on Windows x64 and Windows ARM64 using
native CPython 3.12. An ARM64 filename is not evidence of an ARM64 binary;
`scripts/check_pe_arch.py` checks PE machine headers.

## Native build

Use a disposable virtual environment on the target architecture, with Visual
Studio C++ tools for that architecture and a Windows SDK installed. Do not run
ARM64 Python under x64 emulation or rename x64 wheels to ARM64.

```powershell
python -m venv .venv
.venv/Scripts/Activate.ps1
python -m pip install --only-binary=:all: -r requirements-build.txt
# Substitute arm64 on a native ARM64 runner:
./scripts/build_proctap.ps1 -Architecture x64
python -m pytest
python -m PyInstaller --noconsole --onefile --noupx --additional-hooks-dir packaging/hooks --name QuickAudioRecorder main.py
python scripts/check_pe_arch.py x64 dist/QuickAudioRecorder.exe
```

Frozen releases ignore inherited `SD_ENABLE_ASIO` in their own process so
sounddevice selects the bundled non-ASIO DLL; source installations are unchanged.

Both release configurations build and include proc-tap. A dependency build,
import, test or packaging failure must fail the job; do not silently ship without
application capture. Workflow-dispatch builds retain an artifact for manual
validation without creating a release. Dispatching workflows is a separate
user-approved action.

## proc-tap provenance

`proc-tap==1.1.1` is MIT licensed. Its published source distribution omits the
C++ source needed on Windows, and its universal wheel contains no Windows native
extension. The build script therefore downloads the complete upstream source:

- Repository: https://github.com/m96-chan/ProcTap
- Commit: `b7979443b554edc341f8a190a475714edf062b29` (v1.1.1)
- Archive SHA-256: `8c1738d5c5ca4926b649a363d8f4e0ac0c1f2786c9f0d12ac65d0247d8b5d056`
- Build: setuptools, MSVC C++20, Windows SDK; links `ole32`, `uuid`, `propsys`.

The script verifies the archive, builds a platform wheel, imports its extension
and checks its machine type. Never use the generic universal wheel as a Windows
native substitute. Version pins fix resolution inputs, not bit-for-bit output:
retain the wheel hashes and compiler/SDK versions with each validated build.

proc-tap can silently fall back to system-wide loopback internally. The recorder
checks `is_process_specific()` and rejects this fallback before recording. A
successful native import alone is not proof of isolated application capture.
The underlying Windows API requires a sufficiently recent Windows build
(Microsoft documents build 20348+); proc-tap 1.1.1 itself checks 20438+. Actual
availability is checked by the native backend.

## Verification gates

For **each** architecture, record:

1. Python, proc-tap extension and executable PE machine types.
2. Clean dependency installation, tests and PyInstaller build logs.
3. Frozen launch and multiprocessing worker/native import startup.
4. Microphone and loopback capture; initial timing and continuous audio.
5. Application capture with target and unrelated applications playing distinct
   tones: only target audio may appear. Test mixed and separate outputs, stop,
   device/process loss and partial-file preservation.

CI runners generally have no usable audio hardware. A green build does not
replace native hardware testing. Cross-compilation is useful build evidence,
not native ARM64 runtime evidence.

### Local evidence so far

- Complete pinned ARM64 dependency wheel set resolved successfully for CPython 3.12.
- proc-tap source built and loaded on x64 CPython 3.12.
- Unmodified proc-tap C++ source cross-compiled with MSVC 14.51 and ARM64 Python
  3.12.10 headers/import library; resulting extension is PE ARM64 (`0xAA64`).
  It has **not** been loaded or run on ARM64 hardware.
- Pinned x64 Python 3.12 PyInstaller build completed, including the custom hook.
- The first full run had one real-device VB-Cable fallback pitch failure.
  Explicit 200 ms WASAPI fallback buffering fixed the reproduced conversion
  discontinuity without changing test tolerance: 108 tests then passed.
- Latest silent-only run: 111 passed, explicitly excluding `tests/test_cable.py`.
  No further audible checks will run without user approval. Earlier sounddevice
  NumPy 2.5 deprecation warnings and SoundCard's interpreter-shutdown
  `CoUninitialize` exception remain unresolved dependency diagnostics.
- Frozen x64 worker captured a 997 Hz tone with no error. A two-process isolation
  probe measured unrelated/target spectral amplitude ratio 0.000194. These checks
  preceded the final buffering-only change; the final executable was rebuilt,
  but audible runtime checks have not been repeated.
- Native ARM64 executable build/launch and hardware/application smoke checks
  remain required. Neither architecture's final runtime support is yet certified.

## Redistribution

The application's MIT license does not relicense bundled dependencies. Preserve
actual dependency and bundled-library notices, and review source/relinking
obligations before publishing. In particular PyQt6's PyPI distribution is GPLv3
(commercial licensing is separate), Qt has its own module licenses, and lameenc
and its statically linked LAME require LGPL review. SoundFile, NumPy and SciPy
also bundle libraries with separate notices. Avoid claiming a license file alone
satisfies every redistribution obligation. This branch's new release recipe
still requires a dependency-license/source-material review before publication.
