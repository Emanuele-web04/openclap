# ClapTrigger

Always-on macOS helper that listens for a double clap in the background, opens Codex through `codex://`, and optionally plays a local audio file with native playback.

## Architecture

- `daemon`: owns the microphone, detector, action queue, config reloads, and private Unix control socket.
- `menubar`: lightweight status/control UI that talks to the daemon over the local socket.
- `launchd`: keeps both components alive at login through LaunchAgents.

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
python main.py set-sensitivity sensitive
python main.py set-sensitivity strict
```

Remove the LaunchAgents:

```bash
python main.py uninstall
```

## Config

The persistent config lives at:

```text
~/Library/Application Support/ClapTrigger/config.json
```

Important keys:

- `service.armed`: enable or disable clap detection.
- `service.input_device_name`: preferred microphone name.
- `service.sensitivity_preset`: one of `balanced`, `sensitive`, or `strict`.
- `detector.calibration_profile`: saved auto-tuned profile from the guided calibration wizard.
- `detector.event_window_seconds`: overlapped analysis window used for event-style clap detection.
- `detector.refractory_seconds`: duplicate suppression window after one confirmed clap.
- `actions.codex_url`: defaults to `codex://`.
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
- Trigger actions run on a worker thread, so opening Codex or starting audio playback does not block microphone processing.
- The menu bar app requires `rumps`; it is installed through `requirements.txt`.
- The default media path is local/native playback. If no local audio file is configured, the daemon can fall back to a URL.
