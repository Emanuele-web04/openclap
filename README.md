# OpenClap

Always-on macOS helper that listens for a double clap in the background, opens a user-selected Mac app, and now ships with a native SwiftUI macOS shell for settings, diagnostics, and menu bar control.

## Free macOS app beta

The project now supports a real downloadable macOS app flow:

- GitHub Releases can ship `OpenClap-<version>.dmg`
- the DMG contains a single native `OpenClap.app`
- the app embeds the Python helper runtime used for clap detection
- users drag the app into `Applications`, open it once, grant microphone access, and the app installs its own background LaunchAgents
- Python does not need to be installed on the end-user Mac

The first release track is intentionally simple:

- free download
- unsigned beta
- notarization can be added later without changing the basic packaging flow

## Architecture

- `daemon`: owns the microphone, detector, action queue, config reloads, and private Unix control socket.
- `native macOS shell`: SwiftUI app window plus native menu bar companion for settings, diagnostics, and quick controls.
- `launchd`: keeps the helper daemon and native app alive at login through LaunchAgents.
- `embedded helper runtime`: the app bundle ships with a PyInstaller-built Python helper inside `Contents/Resources/Helper`.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
```

macOS will ask for microphone permissions the first time the daemon starts.

## CLI

List microphones:

```bash
python main.py list-devices
```

Run the daemon in the foreground:

```bash
python main.py daemon
```

Run the legacy internal menu bar app:

```bash
python main.py menubar
```

Print the current app version:

```bash
python main.py version
```

Install LaunchAgents for auto-start at login:

```bash
python main.py install
```

Preview LaunchAgent output without changing the system:

```bash
python main.py install --dry-run
```

Run diagnostics:

```bash
python main.py doctor
```

Fire the configured actions manually through the daemon:

```bash
python main.py test-trigger
```

Run the guided clap calibration:

```bash
python main.py calibrate
```

Change detector sensitivity:

```bash
python main.py set-sensitivity balanced
python main.py set-sensitivity responsive
python main.py set-sensitivity sensitive
python main.py set-sensitivity strict
```

Require a wake word after the double clap:

```bash
python main.py set-wake-phrase "jarvis"
python main.py set-wake-keyword-path /absolute/path/to/wake-up.ppn
python main.py set-voice-enabled true
```

Remove the LaunchAgents:

```bash
python main.py uninstall
```

## Build a standalone app

Install build dependencies in your environment:

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt pyinstaller
```

The native shell also requires Xcode command line tools or a full Xcode install because `swift build` is part of the bundle pipeline.

Build the `.app` bundle:

```bash
PYTHON_BIN=.venv/bin/python ./scripts/build_app.sh
```

Package the `.dmg`:

```bash
./scripts/build_dmg.sh
```

Artifacts land in:

```text
dist/OpenClap.app
dist/OpenClap-0.1.0.dmg
```

## First launch behavior for the bundled app

- `OpenClap.app` is now the native SwiftUI shell the user sees and keeps in the Dock / menu bar
- the helper runtime is embedded inside the app bundle and used only for daemon commands and background detection
- LaunchAgents can point the background UI startup at the native app executable while the daemon keeps using the helper runtime

## GitHub Releases

The repo includes `.github/workflows/release.yml` for macOS releases.

Recommended release flow:

```bash
git tag v0.1.0
git push origin v0.1.0
```

That workflow:

- installs dependencies plus `pyinstaller`
- builds `OpenClap.app`
- builds `OpenClap-0.1.0.dmg`
- attaches the DMG to the GitHub Release

## Config

The persistent config lives at:

```text
~/Library/Application Support/OpenClap/config.json
```

Important keys:

- `app.launch_at_login`: whether LaunchAgents should keep the app + daemon alive at login.
- `app.diagnostics_enabled`: whether recent detection history should be kept for the diagnostics UI.
- `detector.backend`: `native` or `pector`.
- `detector.pector_binary_path`: optional external `pector_c` binary path.
- `service.armed`: enable or disable clap detection.
- `service.armed_on_launch`: reset the daemon to armed whenever it starts at login.
- `service.input_device_name`: preferred microphone name.
- `service.sensitivity_preset`: one of `balanced`, `responsive`, `sensitive`, or `strict`.
- `voice.enabled`: when `true`, a double clap only opens a short confirmation window.
- `voice.wake_phrase`: the wake phrase label expected after the double clap. Default: `jarvis`.
- `voice.keyword_path`: optional Porcupine `.ppn` file for custom phrases when using the Porcupine backend.
- `voice.confirmation_window_seconds`: how long the daemon waits for the wake word after the clap.
- `detector.calibration_profile`: saved auto-tuned profile from the guided calibration wizard.
- `detector.event_window_seconds`: overlapped analysis window used for event-style clap detection.
- `detector.refractory_seconds`: duplicate suppression window after one confirmed clap.
- `actions.target_app_path`: absolute path to the selected `.app` bundle.
- `actions.target_app_name`: cached display name for the selected app.
- `actions.local_audio_file`: optional file played with `afplay`.
- `actions.fallback_media_url`: optional URL opened only when no local audio file is available.

## Tests

```bash
source .venv/bin/activate
python -m unittest discover -s tests
```

## Notes

- The daemon uses a fixed `16 kHz` mono stream, an overlapped event window, and a clap-specific spectral score to reduce false positives from sharp non-clap noises.
- The app now supports an optional `pector` backend for stronger percussive onset detection. `pector` is an external GPL-3.0 dependency and is intentionally not bundled into the distributable app.
- Voice confirmation can gate the final trigger as `clap clap` followed by a wake word. The default phrase is `jarvis`.
- Calibration now learns a clap range: `2 soft claps + 2 normal claps + 2 loud claps`, then stores min/median/max energy stats so runtime detection handles both quieter and louder claps better.
- Recent near-misses now carry confidence plus rejection reasons like `noise`, `music-like pattern`, `timing mismatch`, `low confidence`, or `cooldown`, so the native diagnostics UI can explain why a clap was ignored.
- Trigger actions run on a worker thread, so opening the selected app or starting audio playback does not block microphone processing.
- The legacy Python menu bar app still exists as an internal fallback; the public product surface is the native SwiftUI app.
- Users choose the target app from the menu bar with a native macOS application picker.
- The default media path is local/native playback. If no local audio file is configured, the daemon can fall back to a URL.
- Packaging is built around a generated `.icns` icon, a Swift-built native shell, an embedded PyInstaller helper, and a drag-and-drop DMG.

## Optional pector backend

To install the external `pector` detector locally and switch the daemon to it:

```bash
source .venv/bin/activate
python main.py install-pector
```

To switch back to the internal detector:

```bash
source .venv/bin/activate
python main.py set-detector-backend native
```
