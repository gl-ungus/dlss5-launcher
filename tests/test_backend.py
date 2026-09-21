import dataclasses
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))
import backend as b


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.exe = self.root / 'A game with spaces'
        self.exe.write_text('#!/bin/sh\nexit 0\n')
        self.exe.chmod(0o755)
        self.game = b.Game('custom:test', 'Example', str(self.root),
                           [b.LaunchOption(str(self.exe), '--name "two words"', str(self.root))])

    def tearDown(self):
        self.tmp.cleanup()

    def test_native_arguments_are_argv_not_shell(self):
        p = b.launch_plan(self.game, dict(mode='direct', arguments='"$(touch BAD)" ";"'))
        self.assertEqual(p.command, [str(self.exe), '--name', 'two words', '$(touch BAD)', ';'])
        self.assertEqual(p.cwd, str(self.root))
        self.assertEqual(p.environment['VKLayer_DLSS5'], '1')
        self.assertNotIn('XDG_CONFIG_HOME', p.environment)

    def test_photo_uses_separate_asynchronous_transport(self):
        p = b.launch_plan(self.game, dict(mode='direct', renderer='photo', photo_edge=384))
        self.assertEqual(p.environment['DLSSNR_ASYNC'], '1')
        self.assertEqual(p.environment['DLSSNR_PHOTO_SIZE'], '384')
        self.assertEqual(p.environment['DLSSNR_PHOTO_FPS'], '30')
        self.assertNotEqual(p.environment['DLSSNR_SHM'], str(b.SHM))
        self.assertIn('/photo/share:', p.environment['XDG_DATA_DIRS'])
        self.assertNotIn('/runtime/share:', p.environment['XDG_DATA_DIRS'])
        regular = b.launch_plan(self.game, dict(mode='direct'))
        self.assertNotIn('DLSSNR_ASYNC', regular.environment)

    def test_photo_presents_model_output_without_amplification(self):
        calls = []
        with patch('backend.control', side_effect=lambda *args: calls.append(args) or ''):
            b.apply_preferences(dict(renderer='photo', strength=10))
        self.assertIn(('set', 'bypass', '1'), calls)
        self.assertIn(('set', 'hdrmode', '1'), calls)
        self.assertFalse(any(c[1] in ('detail', 'colour') for c in calls))

    def test_invalid_photo_resolution_is_rejected(self):
        with self.assertRaises(ValueError):
            b.launch_plan(self.game, dict(mode='direct', renderer='photo', photo_edge=4096))

    def test_photo_fps_cap_and_uncapped(self):
        for fps in (0, 15, 60):
            p=b.launch_plan(self.game, dict(mode='direct',renderer='photo',photo_fps=fps))
            self.assertEqual(p.environment['DLSSNR_PHOTO_FPS'],str(fps))
        with self.assertRaises(ValueError):
            b.launch_plan(self.game,dict(mode='direct',renderer='photo',photo_fps=-1))

    def test_gamescope_injects_only_compositor(self):
        with patch('backend.shutil.which', return_value='/usr/bin/gamescope'):
            p = b.launch_plan(self.game, dict(mode='gamescope'))
        separator = p.command.index('--')
        self.assertEqual(p.command[separator + 1:separator + 5], ['env', '-u', 'VKLayer_DLSS5', 'DLSSNR_DISABLE=1'])
        self.assertIn(str(self.exe), p.command)

    def test_screen_mode_is_fullscreen_gamescope(self):
        with patch('backend.shutil.which', return_value='/usr/bin/gamescope'):
            p = b.launch_plan(self.game, dict(mode='screen', fullscreen=True))
        self.assertEqual(p.mode, 'screen')
        self.assertIn('-f', p.command)
        self.assertIn('DLSSNR_DISABLE=1', p.command)

    def test_max_slop_reaches_ten_times_model_strength(self):
        calls = []
        with patch('backend.control', side_effect=lambda *args: calls.append(args) or ''):
            b.apply_preferences({'scale': 1.0, 'strength': 10.0, 'style': 0, 'compare': False})
        self.assertIn(('set', 'detail', '10.0'), calls)
        self.assertIn(('set', 'colour', '10.0'), calls)

    def test_auto_uses_native_fna_without_modifying_it(self):
        lib = self.root / 'lib64/libFNA3D.so.0'
        lib.parent.mkdir()
        lib.touch()
        before = self.exe.read_bytes()
        p = b.launch_plan(self.game, {})
        self.assertEqual(p.mode, 'direct')
        self.assertEqual(p.environment['FNA3D_FORCE_DRIVER'], 'Vulkan')
        self.assertEqual(self.exe.read_bytes(), before)

    def test_windows_reuses_steam_prefix(self):
        self.game.appid = '123'
        self.game.library = str(self.root)
        self.game.steam_root = str(self.root)
        self.game.options[0].platform = 'windows'
        with patch('backend.proton_runner', return_value=Path('/fake/proton')):
            p = b.launch_plan(self.game, dict(mode='direct'))
        self.assertEqual(p.command[:2], ['/fake/proton', 'run'])
        self.assertEqual(p.environment['STEAM_COMPAT_DATA_PATH'], str(self.root / 'steamapps/compatdata/123'))
        self.assertEqual(p.environment['SteamAppId'], '123')
        self.assertNotIn('WINEPREFIX', p.environment)

    def test_native_steam_launch_preserves_runtime_libraries(self):
        self.game.source = 'Steam'
        self.game.steam_root = str(self.root)
        (self.root / 'ubuntu12_32/steam-runtime/usr/lib/i386-linux-gnu').mkdir(parents=True)
        p = b.launch_plan(self.game, dict(mode='direct'))
        self.assertIn('ubuntu12_32/steam-runtime/usr/lib/i386-linux-gnu', p.environment['LD_LIBRARY_PATH'])

    def test_gamescope_keeps_steam_libraries_inside_child(self):
        self.game.source = 'Steam'
        self.game.steam_root = str(self.root)
        (self.root / 'ubuntu12_32').mkdir()
        with patch('backend.shutil.which', return_value='/usr/bin/gamescope'):
            p = b.launch_plan(self.game, dict(mode='gamescope'))
        self.assertNotIn('LD_LIBRARY_PATH', p.environment)
        self.assertTrue(any(arg.startswith('LD_LIBRARY_PATH=') for arg in p.command))

    def test_invalid_environment_and_missing_files_are_errors(self):
        for value in ['NAME', '1INVALID=x', 'hello world']:
            with self.assertRaises(ValueError):
                b.parse_environment(value)
        self.assertEqual(b.parse_environment('A="two words" B=$HOME'), {'A': 'two words', 'B': '$HOME'})
        self.exe.unlink()
        with self.assertRaises(ValueError):
            b.launch_plan(self.game, {})

    def test_missing_gamescope_does_not_silently_launch_unprocessed(self):
        with patch('backend.shutil.which', return_value=None):
            with self.assertRaisesRegex(ValueError, 'Gamescope is not installed'):
                b.launch_plan(self.game, dict(mode='gamescope'))

    def test_corrupt_settings_and_atomic_save(self):
        path = self.root / 'state/settings.json'
        with patch('backend.STATE', path.parent), patch('backend.SETTINGS', path):
            self.assertEqual(b.load_settings(), {})
            b.save_settings({'profiles': {'a': {'strength': .7}}})
            self.assertEqual(b.load_settings()['profiles']['a']['strength'], .7)
            self.assertFalse(path.with_suffix('.tmp').exists())
            path.write_text('not json')
            self.assertEqual(b.load_settings(), {})

    def test_appinfo_key_table_record(self):
        # Modern Steam cache stores the key names by index, then records after a 68-byte header.
        payload = b'\x00' + struct.pack('<I', 0) + b'\x01' + struct.pack('<I', 1) + b'game\0\x08\x08'
        size = 60 + len(payload)
        record = struct.pack('<II', 123, size) + bytes(60) + payload
        table_offset = 16 + len(record) + 4
        content = struct.pack('<IIQ', 0x07564429, 1, table_offset) + record + bytes(4)
        content += struct.pack('<I', 2) + b'appinfo\0type\0'
        path = self.root / 'appinfo.vdf'
        path.write_bytes(content)
        self.assertEqual(b.appinfo(path, {123})[123]['type'], 'game')
        self.assertEqual(b.appinfo(path, {999}), {})
        path.write_bytes(content[:30])
        with self.assertRaises(ValueError):
            b.appinfo(path, {123})

    def test_telemetry_never_calls_stale_model_active(self):
        schema = json.loads((b.ROOT / 'launcher/shm-layout.json').read_text())
        data = bytearray(schema['size'])
        def put(name, value):
            struct.pack_into('<I', data, schema['fields'][name], value)
        for key, value in dict(magic=schema['magic'], version=schema['version'], enabled=1,
                               helperState=4, modelUp=1, heartbeat=100, seq_ok=40,
                               layerPid=os.getpid()).items():
            put(key, value)
        path = self.root / 'shm'
        path.write_bytes(data)
        monitor = b.Telemetry(path)
        with patch('backend.time.monotonic', return_value=100):
            self.assertEqual(monitor.read()['state'], 'off')
        put('heartbeat', 101)
        path.write_bytes(data)
        with patch('backend.time.monotonic', return_value=101):
            self.assertEqual(monitor.read()['state'], 'waiting')
        put('heartbeat', 102)
        put('seq_ok', 41)
        path.write_bytes(data)
        with patch('backend.time.monotonic', return_value=102):
            self.assertEqual(monitor.read()['state'], 'active')
        with patch('backend.time.monotonic', return_value=110):
            self.assertEqual(monitor.read()['state'], 'off')


if __name__ == '__main__':
    unittest.main()
