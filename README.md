# ClapTrigger

Always-on macOS helper that listens for a double clap in the background, opens a user-selected Mac app, and optionally plays a local audio file with native playback.

## Free macOS app beta

The project now supports a real downloadable macOS app flow:

- GitHub Releases can ship `ClapTrigger-<version>.dmg`
- the DMG contains a single `ClapTrigger.app`
- users drag the app into `Applications`, open it once, grant microphone access, and the app installs its own background LaunchAgents
- Python does not need to be installed on the end-user Mac

The first release track is intentionally simple:

- free download
- unsigned beta
- notarization can be added later without changing the basic packaging flow

## Architecture

- `daemon`: owns the microphone, detector, action queue, config reloads, and private Unix control socket.
- `menubar`: lightweight status/control UI that talks to the daemon over the local socket.
- `launchd`: keeps both components alive at login through LaunchAgents.
- `PyInstaller bundle`: turns the project into one `ClapTrigger.app` that can launch normally, or relaunch itself as `daemon` / `menubar` under `launchd`.

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

Run the menu bar app:

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

Build the `.app` bundle:

```bash
./scripts/build_app.sh
```

Package the `.dmg`:

```bash
./scripts/build_dmg.sh
```

Artifacts land in:

```text
dist/ClapTrigger.app
dist/ClapTrigger-0.1.0.dmg
```

## First launch behavior for the bundled app

- if `ClapTrigger.app` is not inside `Applications`, the app shows a prompt and does not install the background helper yet
- once the app lives in `Applications`, opening it installs or refreshes the LaunchAgents and then hands control to the menu bar instance started by `launchd`
- the same bundled executable is reused for normal app launch, `daemon`, and `menubar` mode

## GitHub Releases

The repo includes `.github/workflows/release.yml` for macOS releases.

Recommended release flow:

```bash
git tag v0.1.0
git push origin v0.1.0
```

That workflow:

- installs dependencies plus `pyinstaller`
- builds `ClapTrigger.app`
- builds `ClapTrigger-0.1.0.dmg`
- attaches the DMG to the GitHub Release

## Config

The persistent config lives at:

```text
~/Library/Application Support/ClapTrigger/config.json
```

Important keys:

- `service.armed`: enable or disable clap detection.
- `service.input_device_name`: preferred microphone name.
- `service.sensitivity_preset`: one of `balanced`, `responsive`, `sensitive`, or `strict`.
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
- Calibration now learns a clap range: `2 soft claps + 2 normal claps + 2 loud claps`, then stores min/median/max energy stats so runtime detection handles both quieter and louder claps better.
- Trigger actions run on a worker thread, so opening the selected app or starting audio playback does not block microphone processing.
- The menu bar app requires `rumps`; it is installed through `requirements.txt`.
- Users choose the target app from the menu bar with a native macOS application picker.
- The default media path is local/native playback. If no local audio file is configured, the daemon can fall back to a URL.
- Packaging is built around PyInstaller, a generated `.icns` icon, and a drag-and-drop DMG so the project can ship as a real free macOS beta before notarization.
