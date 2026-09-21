"""Opt-in hardware integration check: opens a Vulkan test window, never a user's game."""
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QListWidgetItem
import backend
import app as ui

sandbox = tempfile.TemporaryDirectory()
backend.SETTINGS = Path(sandbox.name) / 'settings.json'
application = QApplication([])
application.setStyle('Fusion')
application.setStyleSheet(ui.STYLE)
application.setQuitOnLastWindowClosed(False)
window = ui.Launcher()
window.show()
results = []
phase = 0
started = time.monotonic()
phase_at = started
process = None


def record(name, **details):
    item = dict(check=name, **details)
    results.append(item)
    print(json.dumps(item), flush=True)


def finish(error=None):
    if error:
        record('FAIL', error=error)
    if process and process.poll() is None:
        # Only terminate the test's own process group, never another application.
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    try:
        backend.stop_helper()
    except Exception as exc:
        record('cleanup', error=str(exc))
    (backend.ROOT / 'verification/gui-live-results.json').write_text(json.dumps(results, indent=2) + '\n')
    application.exit(1 if error else 0)


def tick():
    global phase, phase_at, process
    now = time.monotonic()
    if now - started > 65:
        finish('Timed out during phase ' + str(phase))
        return
    try:
        if phase == 0 and window.games:
            game = backend.Game('test:gui', 'Vulkan test', '/tmp',
                                [backend.LaunchOption('/usr/bin/vkcube', '--c 30000 --width 960 --height 540', '/tmp')])
            window.games.append(game)
            item = QListWidgetItem(game.name)
            item.setData(Qt.ItemDataRole.UserRole, game.id)
            window.library.addItem(item)
            window.library.setCurrentItem(item)
            window.mode.setCurrentIndex(window.mode.findData('direct'))
            window.launch_button.click()
            phase = 1
        elif phase == 1:
            if window.error.isVisible():
                raise RuntimeError(window.error.text())
            if window.latest.get('state') == 'active':
                process = window.game_process
                record('launch_button_processed_frames', success=window.latest['successful'], output=[window.latest['layerWidth'], window.latest['layerHeight']])
                window.grab().save(str(backend.ROOT / 'verification/launcher-live.png'))
                window.effect.click()
                phase, phase_at = 2, now
        elif phase == 2 and window.latest.get('state') == 'disabled':
            assert process.poll() is None, 'Effect toggle closed the game'
            record('effect_off_keeps_game_open')
            window.effect.click()
            window.strength.setValue(65)
            window.compare.setChecked(True)
            phase, phase_at = 3, now
        elif phase == 3 and now - phase_at > 2 and window.latest.get('state') == 'active':
            settings = backend.control('settings')
            assert 'detail=0.65\n' in settings, settings
            assert 'compare=2\n' in settings, settings
            record('live_strength_and_comparison_applied')
            window.quality.setCurrentIndex(0)
            phase, phase_at = 4, now
        elif phase == 4 and now - phase_at > 2 and window.latest.get('state') == 'active':
            assert abs(window.latest['workingScaleBits'] - .5) < .01
            record('quality_changed_live', model_size=[window.latest['width'], window.latest['height']])
            window.stop_button.click()
            phase, phase_at = 5, now
        elif phase == 5 and not window.busy and window.latest.get('state') == 'off':
            assert process.poll() is None, 'Stop DLSS closed the game'
            record('stop_releases_helper_keeps_game_open')
            assert window.launch_button.text() == 'Resume DLSS 5'
            window.launch_button.click()
            phase, phase_at = 6, now
        elif phase == 6 and not window.busy and window.latest.get('state') == 'active':
            record('resume_processed_frames_again')
            finish()
    except Exception as exc:
        finish(str(exc))


timer = QTimer()
timer.setInterval(300)
timer.timeout.connect(tick)
timer.start()
raise SystemExit(application.exec())
