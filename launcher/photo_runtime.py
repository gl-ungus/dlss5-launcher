"""Lifecycle and configuration for the separately labelled fake-AI renderer."""
from pathlib import Path
import json
import os
import signal
import subprocess
import time

from photo_worker import DEFAULT_PROMPT

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'runtime/photo'
SHM = Path(f'/tmp/dlss-photo-{os.getuid()}/shm.bin')
STATUS = STATE / 'status.json'
LOG = STATE / 'worker.log'
PYTHON = ROOT / 'runtime/photo-venv/bin/python'
WORKER = ROOT / 'launcher/photo_worker.py'
_process = None


def status():
    try:
        return json.loads(STATUS.read_text())
    except (OSError, ValueError):
        return {}


def environment(edge=384, fps=30):
    edge = int(edge)
    if edge not in (256, 384, 512):
        raise ValueError('Choose one of the photo-model processing resolutions.')
    fps = int(fps)
    if fps not in (0, 10, 15, 20, 30, 45, 60):
        raise ValueError('Choose one of the AI FPS limits.')
    return dict(DLSSNR_SHM=str(SHM), DLSSNR_ASYNC='1', DLSSNR_PHOTO_SIZE=str(edge),
                DLSSNR_PHOTO_FPS=str(fps),
                DLSSNR_DMABUF='0',
                XDG_DATA_DIRS=f'{STATE}/share:/usr/local/share:/usr/share')


def start(preferences):
    global _process
    stop()
    if not PYTHON.exists() or not (ROOT/'runtime/libVkLayer_photo.so').exists():
        raise RuntimeError('Fake AI runtime is not installed. Run launcher/setup_photo.sh first.')
    STATE.mkdir(parents=True, exist_ok=True)
    SHM.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    config = dict(prompt=preferences.get('photo_prompt', DEFAULT_PROMPT),
                  change=float(preferences.get('photo_change', .35)),
                  steps=int(preferences.get('photo_steps', 1)),
                  fps=int(preferences.get('photo_fps', 30)),
                  edge=int(preferences.get('photo_edge', 384)),
                  width=int(preferences.get('width', 1920)), height=int(preferences.get('height', 1080)))
    path = STATE/'config.json'
    path.write_text(json.dumps(config))
    STATUS.unlink(missing_ok=True)
    env = os.environ.copy()
    for key in ('VKLayer_DLSS5', 'LD_LIBRARY_PATH', 'LD_PRELOAD', 'WINEPREFIX'):
        env.pop(key, None)
    with LOG.open('wb') as stream:
        _process = subprocess.Popen([str(PYTHON), str(WORKER), '--shm', str(SHM),
            '--config', str(path), '--status', str(STATUS)], env=env,
            stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
    deadline = time.monotonic()+90
    while time.monotonic()<deadline:
        data = status()
        if data.get('pid') == _process.pid and data.get('state') in ('ready', 'active'):
            return
        if _process.poll() is not None or data.get('state') == 'error':
            stop()
            raise RuntimeError(f'{data.get("label", "Photo worker exited")}. See {LOG}')
        time.sleep(.1)
    stop()
    raise RuntimeError(f'Photo model startup timed out. See {LOG}')


def stop():
    global _process
    pid = _process.pid if _process and _process.poll() is None else status().get('pid')
    if pid:
        try:
            # Never signal a reused PID belonging to some other application.
            argv = Path(f'/proc/{int(pid)}/cmdline').read_bytes().split(b'\0')
            if os.fsencode(WORKER) in argv:
                os.kill(pid, signal.SIGTERM)
                if _process and _process.pid == pid:
                    try:
                        _process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        _process.kill(); _process.wait(timeout=5)
        except (ProcessLookupError, FileNotFoundError):
            pass
    _process = None
