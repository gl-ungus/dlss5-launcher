"""Local game discovery, safe argv construction, and live DLSS telemetry."""
from __future__ import annotations

import dataclasses
import json
import mmap
import os
from pathlib import Path
import re
import shlex
import shutil
import struct
import subprocess
import time
import uuid

import vdf
import photo_runtime

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / 'runtime' / 'launcher'
SETTINGS = STATE / 'settings.json'
SHM = Path(f'/tmp/dlssnr-{os.getuid()}/shm.bin')
CTL = ROOT / 'runtime/bin/dlssnr-shmctl'
_renderer = 'dlss'
def display_size() -> tuple[int, int]:
    """The connected display's preferred resolution, straight from DRM sysfs.

    The compositor window must match the panel: when it is smaller, games that
    take their video mode from the window render below native resolution and
    their mouse warping lands off centre.
    """
    for status in sorted(Path('/sys/class/drm').glob('*/status')):
        try:
            if status.read_text().strip() != 'connected':
                continue
            first = (status.parent / 'modes').read_text().split('\n', 1)[0]
            width, height = (int(part) for part in first.strip().split('x'))
        except (OSError, ValueError):
            continue
        if width and height:
            return width, height
    return 1920, 1080


NATIVE_WIDTH, NATIVE_HEIGHT = display_size()
DEFAULTS = dict(scale=0.67, strength=1.0, style=0, compare=False, mode='auto',
                width=NATIVE_WIDTH, height=NATIVE_HEIGHT, fullscreen=False,
                arguments='', environment='', launch=0, renderer='dlss',
                photo_prompt=photo_runtime.DEFAULT_PROMPT, photo_change=.35, photo_edge=384,
                photo_steps=1, photo_fps=30)


def read_vdf(path: Path) -> dict:
    with path.open(encoding='utf-8', errors='replace') as stream:
        return vdf.load(stream)


def load_settings() -> dict:
    try:
        result = json.loads(SETTINGS.read_text())
        if not isinstance(result, dict):
            return {}
        return result
    except (OSError, ValueError):
        return {}


def save_settings(settings: dict):
    STATE.mkdir(parents=True, exist_ok=True)
    temp = SETTINGS.with_suffix('.tmp')
    temp.write_text(json.dumps(settings, indent=2) + '\n')
    temp.replace(SETTINGS)


def steam_roots(home: Path | None = None) -> list[Path]:
    home = home or Path.home()
    roots = [home / '.local/share/Steam', home / '.steam/steam',
             home / '.var/app/com.valvesoftware.Steam/data/Steam']
    return list(dict.fromkeys(p.resolve() for p in roots if (p / 'steamapps').is_dir()))


def appinfo(path: Path, wanted: set[int]) -> dict[int, dict]:
    """Read only requested records from Steam appinfo v27/v28/v29 (39/40/41)."""
    data = path.read_bytes()
    if len(data) < 8:
        raise ValueError('Steam app cache is incomplete')
    magic = struct.unpack_from('<I', data)[0]
    if magic not in (0x07564427, 0x07564428, 0x07564429):
        raise ValueError('Unrecognized Steam app cache; use Add game to choose an executable')
    keys = None
    pos, end = 8, len(data)
    if magic == 0x07564429:
        end = struct.unpack_from('<Q', data, 8)[0]
        if end + 4 > len(data):
            raise ValueError('Steam string table is incomplete')
        count = struct.unpack_from('<I', data, end)[0]
        keys = [s.decode('utf-8', 'replace') for s in data[end + 4:].split(b'\0')[:count]]
        pos = 16
    result = {}
    header_size = 48 if magic == 0x07564427 else 68
    while pos + 8 <= end:
        appid, size = struct.unpack_from('<II', data, pos)
        if not appid:
            break
        next_pos = pos + 8 + size
        if size < header_size - 8 or next_pos > end:
            raise ValueError('Steam app cache changed while reading; refresh the library')
        if appid in wanted:
            record = vdf.binary_loads(data[pos + header_size:next_pos], key_table=keys,
                                     raise_on_remaining=False)
            result[appid] = record.get('appinfo', {})
        pos = next_pos
    return result


@dataclasses.dataclass
class LaunchOption:
    executable: str
    arguments: str = ''
    directory: str = ''
    platform: str = 'native'
    description: str = ''


@dataclasses.dataclass
class Game:
    id: str
    name: str
    directory: str
    options: list[LaunchOption]
    appid: str = ''
    steam_root: str = ''
    library: str = ''
    artwork: str = ''
    source: str = 'Custom'
    note: str = ''


def discover_games(custom: list[dict] | None = None) -> tuple[list[Game], list[str]]:
    games, notes = [], []
    for steam in steam_roots():
        libraries = [steam]
        try:
            folders = read_vdf(steam / 'steamapps/libraryfolders.vdf')['libraryfolders']
            libraries += [Path(f['path']) for f in folders.values() if isinstance(f, dict) and 'path' in f]
        except (OSError, ValueError, KeyError, SyntaxError) as exc:
            notes.append(f'Library scan: {exc}')
        manifests = []
        for library in dict.fromkeys(libraries):
            for path in (library / 'steamapps').glob('appmanifest_*.acf'):
                try:
                    manifest = read_vdf(path)['AppState']
                    if not int(manifest.get('StateFlags', '0')) & 4:
                        continue
                    manifests.append((library, manifest))
                except (OSError, ValueError, KeyError, SyntaxError):
                    continue
        try:
            info = appinfo(steam / 'appcache/appinfo.vdf', {int(m['appid']) for _, m in manifests})
        except (OSError, ValueError, SyntaxError, struct.error) as exc:
            info = {}
            notes.append(str(exc))
        for library, m in manifests:
            appid = m['appid']
            metadata = info.get(int(appid), {})
            kind = metadata.get('common', {}).get('type', '').lower()
            if kind and kind not in ('game', 'demo', 'mod'):
                continue
            if re.match(r'^(Proton|Steam Linux Runtime|Steamworks)', m['name']):
                continue
            directory = library / 'steamapps/common' / m['installdir']
            if not directory.is_dir():
                continue
            options = []
            for entry in metadata.get('config', {}).get('launch', {}).values():
                if not isinstance(entry, dict) or not entry.get('executable'):
                    continue
                oslist = entry.get('config', {}).get('oslist', 'windows').split(',')
                if 'linux' not in oslist and 'windows' not in oslist:
                    continue
                exe = entry['executable'].replace('\\', '/').lstrip('/')
                target = directory / exe
                if not target.is_file():
                    continue
                platform = 'native' if 'linux' in oslist else 'windows'
                if platform == 'native' and entry.get('config', {}).get('osarch') == '32':
                    continue
                description = entry.get('description_loc', {}).get('english') or entry.get('description') or target.name
                options.append(LaunchOption(str(target), entry.get('arguments', ''),
                    str(directory / entry.get('workingdir', '').replace('\\', '/').lstrip('/')),
                    platform, f'{description} · {"Linux" if platform == "native" else "Proton"}'))
            options.sort(key=lambda item: (item.platform != 'native', 'default' not in item.description.lower()))
            art = steam / 'appcache/librarycache' / appid / 'library_hero.jpg'
            if not art.is_file():
                art = steam / 'appcache/librarycache' / appid / 'library_header.jpg'
            note = '' if options else 'Choose this game’s executable with Configure launch.'
            games.append(Game(f'steam:{appid}', m['name'], str(directory), options, appid,
                              str(steam), str(library), str(art) if art.is_file() else '', 'Steam', note))
    for entry in custom or []:
        try:
            games.append(Game(**{**entry, 'options': [LaunchOption(**o) for o in entry['options']]}))
        except (KeyError, TypeError):
            notes.append('A saved custom game could not be loaded.')
    return sorted({g.id: g for g in games}.values(), key=lambda g: g.name.casefold()), notes


def custom_game(path: str, name: str = '', arguments: str = '', directory: str = '') -> Game:
    exe = Path(path).expanduser().resolve()
    if not exe.is_file():
        raise ValueError('Choose an existing executable or launch script.')
    return Game('custom:' + uuid.uuid4().hex, name.strip() or exe.stem,
                directory or str(exe.parent),
                [LaunchOption(str(exe), arguments, directory or str(exe.parent),
                              'windows' if exe.suffix.lower() == '.exe' else 'native', exe.name)])


def proton_runner(steam_root: Path | None = None, appid: str = '') -> Path:
    candidates = []
    steam_root = steam_root or next(iter(steam_roots()), Path.home() / '.local/share/Steam')
    configured = ''
    try:
        mapping = read_vdf(steam_root / 'config/config.vdf')['InstallConfigStore']['Software']['Valve']['Steam']['CompatToolMapping']
        configured = mapping.get(appid, mapping.get('0', {})).get('name', '')
    except (OSError, KeyError, ValueError, SyntaxError):
        pass
    for base in [steam_root / 'compatibilitytools.d', Path('/usr/share/steam/compatibilitytools.d'),
                 steam_root / 'steamapps/common']:
        if not base.is_dir():
            continue
        for folder in base.iterdir():
            runner = folder / 'proton'
            if not runner.is_file():
                continue
            matched = configured and configured.casefold() in folder.name.casefold()
            try:
                tools = read_vdf(folder / 'compatibilitytool.vdf').get('compatibilitytools', {}).get('compat_tools', {})
                matched = matched or configured in tools
            except (OSError, SyntaxError, ValueError):
                pass
            score = 0 if matched else 1 if 'cachyos' in folder.name.lower() else 2 if 'experimental' in folder.name.lower() else 3
            candidates.append((score, str(runner)))
    if not candidates:
        raise ValueError('No Proton runner found. Install Proton in Steam before launching a Windows game.')
    return Path(sorted(candidates)[0][1])


def parse_environment(text: str) -> dict[str, str]:
    result = {}
    for token in shlex.split(text):
        key, sep, value = token.partition('=')
        if not sep or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
            raise ValueError('Environment entries must look like NAME=value (quote values containing spaces).')
        result[key] = value
    return result


@dataclasses.dataclass
class LaunchPlan:
    command: list[str]
    cwd: str
    environment: dict[str, str]
    mode: str
    log: str
    name: str


def launch_plan(game: Game, preferences: dict) -> LaunchPlan:
    p = {**DEFAULTS, **preferences}
    if not game.options:
        raise ValueError('Choose the executable using Configure launch first.')
    index = int(p['launch'])
    if not 0 <= index < len(game.options):
        index = 0
    option = game.options[index]
    target = Path(option.executable)
    if not target.is_file():
        raise ValueError(f'Executable is missing: {target}')
    command = [str(target), *shlex.split(option.arguments), *shlex.split(p['arguments'])]
    env = {}
    if game.appid:
        env.update(SteamAppId=game.appid, SteamGameId=game.appid)
    if option.platform == 'windows':
        steam = Path(game.steam_root) if game.steam_root else next(iter(steam_roots()), Path.home() / '.local/share/Steam')
        prefix = Path(game.library) / 'steamapps/compatdata' / game.appid if game.appid else STATE / 'prefixes' / game.id.replace(':', '-')
        env.update(STEAM_COMPAT_DATA_PATH=str(prefix), STEAM_COMPAT_CLIENT_INSTALL_PATH=str(steam),
                   PROTON_ENABLE_NVAPI='1', SteamAppId=game.appid or '0', SteamGameId=game.appid or '0')
        command = [str(proton_runner(steam, game.appid)), 'run', *command]
    elif not os.access(target, os.X_OK):
        raise ValueError('This file is not executable. Choose the game’s executable or executable launch script.')
    # Steam's native legacy games often use a launcher script that replaces
    # LD_LIBRARY_PATH with the game directory. Preserve the Steam runtime
    # libraries underneath it so 32-bit titles such as Half-Life can resolve
    # libvorbis, SDL, and the other bundled dependencies exactly as they do
    # when started by Steam. The order below matches the LD_LIBRARY_PATH the
    # Steam client itself exports. Both the runtime's `lib` and `usr/lib`
    # halves are needed: `lib` holds the oldest compatibility libraries, such
    # as libpng12 and libgcrypt.11, which Half-Life's chromehtml.so and
    # libcef.so require. Without them that module fails to load, the engine's
    # VGUI2 factory slot stays null, and the game crashes in CBaseUI::Start
    # before it opens a window.
    if game.source == 'Steam' and option.platform == 'native' and game.steam_root:
        steam = Path(game.steam_root)
        runtime = steam / 'ubuntu12_32/steam-runtime'
        runtime_dirs = [
            steam / 'ubuntu12_32',
            steam / 'ubuntu12_64',
            runtime / 'pinned_libs_32',
            runtime / 'pinned_libs_64',
            runtime / 'lib/i386-linux-gnu',
            runtime / 'usr/lib/i386-linux-gnu',
            runtime / 'lib/x86_64-linux-gnu',
            runtime / 'usr/lib/x86_64-linux-gnu',
            runtime / 'lib',
            runtime / 'usr/lib',
            steam / 'steamrt32',
            steam / 'steamrt64',
        ]
        inherited = [str(path) for path in runtime_dirs if path.is_dir()]
        if inherited:
            prior = env.get('LD_LIBRARY_PATH', '')
            env['LD_LIBRARY_PATH'] = ':'.join(inherited + ([prior] if prior else []))
    mode = p['mode']
    fna = (Path(game.directory) / 'lib64/libFNA3D.so.0').is_file()
    if mode == 'auto':
        mode = 'direct' if option.platform == 'windows' or fna else ('gamescope' if shutil.which('gamescope') else 'direct')
    if fna and mode == 'direct':
        env['FNA3D_FORCE_DRIVER'] = 'Vulkan'
    env.update(parse_environment(p['environment']))
    if mode == 'zink':
        env.update(__GLX_VENDOR_LIBRARY_NAME='mesa', MESA_LOADER_DRIVER_OVERRIDE='zink', GALLIUM_DRIVER='zink')
    # Gamescope itself is a system binary and must use the system C++ runtime.
    # Steam's compatibility libraries belong only on the native game's side of
    # the compositor boundary; putting them in the parent environment makes
    # gamescope load an older libstdc++ and fail with GLIBCXX/CXXABI errors.
    child_library_path = env.pop('LD_LIBRARY_PATH', '') if mode in ('gamescope', 'screen') else ''
    if mode in ('gamescope', 'screen'):
        gamescope = shutil.which('gamescope')
        if not gamescope:
            raise ValueError('Gamescope is not installed. Use Direct mode for Vulkan/Proton games.')
        width, height = int(p['width']), int(p['height'])
        # Only the compositor gets DLSS, never both the game and its compositor.
        child_environment = ['env', '-u', 'VKLayer_DLSS5', 'DLSSNR_DISABLE=1']
        if child_library_path:
            child_environment.append(f'LD_LIBRARY_PATH={child_library_path}')
        command = [gamescope, '--backend', 'sdl', '-W', str(width), '-H', str(height),
                   '-w', str(width), '-h', str(height), *(['-f'] if p['fullscreen'] else []),
                   '--', *child_environment, *command]
    elif mode not in ('direct', 'zink'):
        raise ValueError('Unknown launch mode.')
    STATE.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    safe_id = re.sub(r'[^A-Za-z0-9_-]', '-', game.id)
    log = STATE / 'logs' / f'{safe_id}-{stamp}.log'
    env.update(VKLayer_DLSS5='1', DLSSNR_SHM=str(SHM), DLSSNR_TIME='1', DLSSNR_TIME_EVERY='120',
               XDG_DATA_DIRS=f'{ROOT}/runtime/share:{os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share")}')
    # Let helper logs remain separate. Layer output follows the game's session log.
    env.pop('DLSSNR_LOG', None)
    if p['renderer'] == 'photo':
        env.update(photo_runtime.environment(p['photo_edge'], p['photo_fps']))
    elif p['renderer'] != 'dlss':
        raise ValueError('Unknown renderer.')
    return LaunchPlan(command, option.directory or game.directory, env, mode, str(log), game.name)


def control(*arguments: str) -> str:
    path = photo_runtime.SHM if _renderer == 'photo' else SHM
    proc = subprocess.run([str(CTL), str(path), *map(str, arguments)], capture_output=True, text=True, timeout=5)
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or 'The DLSS runtime is not ready.')
    return proc.stdout


def apply_preferences(preferences: dict):
    p = {**DEFAULTS, **preferences}
    if p['renderer'] == 'photo':
        for key, value in [('bypass', 1), ('hdrmode', 1), ('compare', 2 if p['compare'] else 0), ('enabled', 1)]:
            control('set', key, str(value))
        return
    for key, value in [('passes', 1), ('workingscale', p['scale']), ('bypass', 0),
                       ('detail', p['strength']), ('colour', p['strength']), ('style', p['style']),
                       ('compare', 2 if p['compare'] else 0), ('applymodel', 1), ('enabled', 1)]:
        control('set', key, str(value))


class Telemetry:
    def __init__(self, path: Path | None = None):
        self.path = path
        self.schema = json.loads((ROOT / 'launcher/shm-layout.json').read_text())
        self.last_heartbeat = None
        self.heartbeat_at = 0.0
        self.last_success = None
        self.success_at = 0.0
        self.last_sample = 0.0

    def read(self) -> dict:
        now = time.monotonic()
        path = self.path or (photo_runtime.SHM if _renderer == 'photo' else SHM)
        try:
            with path.open('rb') as stream:
                if os.fstat(stream.fileno()).st_size < self.schema['size']:
                    return dict(state='off', label='DLSS 5 is off')
                with mmap.mmap(stream.fileno(), self.schema['size'], access=mmap.ACCESS_READ) as mapping:
                    fields = self.schema['fields']
                    get = lambda key: struct.unpack_from('<I', mapping, fields[key])[0]
                    if get('magic') != self.schema['magic'] or get('version') != self.schema['version']:
                        return dict(state='error', label='Runtime version mismatch')
                    data = {key: get(key) for key in fields if key not in ('helperReason', 'layerReason', 'gameName')}
                    for key in ('helperReason', 'layerReason', 'gameName'):
                        length = 192 if key.endswith('Reason') else 128
                        data[key] = mapping[fields[key]:fields[key] + length].split(b'\0', 1)[0].decode('utf8', 'replace')
        except (OSError, ValueError):
            return dict(state='off', label='DLSS 5 is off')
        changed = self.last_heartbeat is not None and data['heartbeat'] != self.last_heartbeat
        if changed:
            self.heartbeat_at = now
        self.last_heartbeat = data['heartbeat']
        live = self.heartbeat_at > 0 and now - self.heartbeat_at < 4 and not data['quit']
        success = data['seq_ok']
        delta = max(0, success - self.last_success) if self.last_success is not None else 0
        if delta:
            self.success_at = now
        rate = delta / (now - self.last_sample) if self.last_sample else 0
        self.last_success, self.last_sample = success, now
        layer_live = data['layerPid'] > 0 and Path(f'/proc/{data["layerPid"]}').exists()
        if not live:
            state, label = 'off', 'DLSS 5 is off'
        elif not data['enabled']:
            state, label = 'disabled', 'Effect disabled'
        elif data['helperState'] in (1, 2, 3):
            state, label = 'error', data['helperReason'] or 'Neural renderer needs attention'
        elif layer_live and data['modelUp'] and now - self.success_at < 2:
            state, label = 'active', 'DLSS 5 is processing frames'
        elif layer_live:
            state, label = 'waiting', 'Connected · waiting for frames'
        else:
            state, label = 'ready', 'Ready · waiting for a game'
        for key in ('helperEvalMsBits', 'layerMsBits', 'workingScaleBits', 'transferStrengthBits'):
            data[key] = struct.unpack('<f', struct.pack('<I', data[key]))[0]
        data.update(state=state, label=label, live=live, layer_live=layer_live, rate=rate,
                    successful=success, rendered=(data['layerFramesHi'] << 32) | data['layerFramesLo'])
        return data


def start_helper(preferences=None):
    global _renderer
    p = {**DEFAULTS, **(preferences or {})}
    if _renderer == 'photo':
        photo_runtime.stop()
    _renderer = p['renderer']
    if _renderer == 'photo':
        # Release an idle NGX helper before allocating the diffusion network.
        subprocess.run([str(ROOT/'dlss5'), 'stop'], capture_output=True, timeout=40)
        photo_runtime.start(p)
        return
    proc = subprocess.run([str(ROOT / 'dlss5'), 'start'], capture_output=True, text=True, timeout=45)
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or 'DLSS helper did not start.')
    monitor = Telemetry()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        status = monitor.read()
        if status.get('live'):
            if status['state'] == 'error':
                raise RuntimeError(status['label'])
            return
        time.sleep(0.15)
    raise RuntimeError('The helper started but is not responding. Open the helper log for details.')


def stop_helper():
    if _renderer == 'photo':
        photo_runtime.stop()
        return
    control('set', 'enabled', '0')
    proc = subprocess.run([str(ROOT / 'dlss5'), 'stop'], capture_output=True, text=True, timeout=40)
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or 'Could not stop the helper.')


def spawn_game(plan: LaunchPlan) -> subprocess.Popen:
    env = os.environ.copy()
    for key in ('DLSSNR_DISABLE', 'DLSSNR_LOG', 'WINEPREFIX'):
        env.pop(key, None)
    env.update(plan.environment)
    if 'STEAM_COMPAT_DATA_PATH' in plan.environment:
        Path(plan.environment['STEAM_COMPAT_DATA_PATH']).mkdir(parents=True, exist_ok=True)
    log = Path(plan.log)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('ab', buffering=0) as stream:
        stream.write((f'Game: {plan.name}\nMode: {plan.mode}\nCommand: {shlex.join(plan.command)}\n\n').encode())
        return subprocess.Popen(plan.command, cwd=plan.cwd, env=env, stdout=stream, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
