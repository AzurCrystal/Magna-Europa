"""Offline tests for hoi4_spectate console injection: INPUT ABI sizes, the
keystroke + WM_CHAR sequence console() constructs, and the foreground/attach
contract. No game, no windows, no SendInput — every Win32 call is mocked.
Safe on non-Windows hosts (no WINFUNCTYPE used at import)."""

import contextlib
import ctypes
import io
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock


TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import hoi4_spectate as hs  # noqa: E402


class InputAbiTests(unittest.TestCase):
    """Win64 INPUT union: type(4) + pad(4) + union(32, sized by MOUSEINPUT) = 40."""

    def test_input_is_40_bytes(self):
        self.assertEqual(ctypes.sizeof(hs.MOUSEINPUT), 32)
        self.assertEqual(ctypes.sizeof(hs.KEYBDINPUT), 24)
        self.assertEqual(ctypes.sizeof(hs._UNION), 32)
        self.assertEqual(ctypes.sizeof(hs.INPUT), 40)
        self.assertEqual(hs.INPUT.u.offset, 8)

    def test_key_input_builds_keyboard_event(self):
        e = hs._key_input(wScan=0x29, dwFlags=hs.KEYEVENTF_SCANCODE)
        self.assertEqual(e.type, hs.INPUT_KEYBOARD)
        self.assertEqual(e.u.ki.wVk, 0)
        self.assertEqual(e.u.ki.wScan, 0x29)
        self.assertEqual(e.u.ki.dwFlags, hs.KEYEVENTF_SCANCODE)

    def test_processentry_layout(self):
        pe = hs.PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(pe)
        self.assertEqual(hs.PROCESSENTRY32W.szExeFile.offset, 44)
        self.assertGreaterEqual(pe.dwSize, 44 + 520)


class SendInputCheckTests(unittest.TestCase):
    """SendInput return value is validated — a short write must raise."""

    def _fake_user32(self, sent):
        u = mock.MagicMock()
        u.SendInput.return_value = sent
        return u

    def test_raises_on_short_write(self):
        with mock.patch.object(ctypes, 'WinDLL', return_value=self._fake_user32(1)):
            with self.assertRaisesRegex(RuntimeError, r'SendInput'):
                hs._send_input([hs._key_input(wVk=0x41),
                                hs._key_input(wVk=0x41, dwFlags=hs.KEYEVENTF_KEYUP)])

    def test_passes_count_and_struct_size(self):
        u = self._fake_user32(2)
        with mock.patch.object(ctypes, 'WinDLL', return_value=u):
            hs._send_input([hs._key_input(wVk=0x41)] * 2)
        n, _arr, cb = u.SendInput.call_args[0]
        self.assertEqual(n, 2)
        self.assertEqual(cb, 40)


class PostCharsTests(unittest.TestCase):
    """_post_chars posts one WM_CHAR per UTF-16 code unit straight to hwnd —
    the same message TranslateMessage yields for a physical keypress, without
    touching the keyboard/IME path at all."""

    def _u(self, ok=1):
        u = mock.MagicMock()
        u.PostMessageW.return_value = ok
        return u

    def test_ascii_posts_ordinals(self):
        u = self._u()
        hs._post_chars(0x1234, 'observe', u)
        self.assertEqual([c.args for c in u.PostMessageW.call_args_list],
                         [(0x1234, hs.WM_CHAR, ord(c), 1) for c in 'observe'])

    def test_utf16_units_for_bmp_and_astral(self):
        u = self._u()
        hs._post_chars(0x1234, 'aé\U0001D11E', u)
        self.assertEqual([c.args[2] for c in u.PostMessageW.call_args_list],
                         [0x61, 0xE9, 0xD834, 0xDD1E])

    def test_post_failure_raises(self):
        u = self._u(ok=0)
        with self.assertRaisesRegex(RuntimeError, 'WM_CHAR'):
            hs._post_chars(0x1234, 'ab', u)


class ForegroundTests(unittest.TestCase):
    """foreground() contract: restore → AllowSetForegroundWindow → BringWindowToTop
    → attach input → SetForegroundWindow → SetFocus → detach → verify."""

    HWND = 0x1234

    def _u(self, tid=555, pid=4242, sfw=1, gfw=HWND, iconic=0):
        u = mock.MagicMock()
        u.IsIconic.return_value = iconic
        def _gwtpi(h, p):
            p._obj.value = pid
            return tid
        u.GetWindowThreadProcessId.side_effect = _gwtpi
        u.SetForegroundWindow.return_value = sfw
        u.GetForegroundWindow.return_value = gfw
        u.AttachThreadInput.return_value = 1
        return u

    def _run(self, u, tid_cur=99, out=None):
        w = mock.MagicMock()
        w.kernel32.GetCurrentThreadId.return_value = tid_cur
        with mock.patch.object(hs, '_user32', return_value=u), \
             mock.patch.object(ctypes, 'windll', w, create=True), \
             contextlib.redirect_stdout(out or io.StringIO()):
            hs.foreground(self.HWND)

    def test_full_sequence_focus_while_attached(self):
        u = self._u()
        self._run(u)
        u.AllowSetForegroundWindow.assert_called_once_with(4242)
        u.BringWindowToTop.assert_called_once_with(self.HWND)
        u.SetFocus.assert_called_once_with(self.HWND)
        self.assertEqual(u.AttachThreadInput.call_args_list,
                         [mock.call(99, 555, True), mock.call(99, 555, False)])
        names = [c[0] for c in u.mock_calls]
        attach = names.index('AttachThreadInput')
        detach = len(names) - 1 - names[::-1].index('AttachThreadInput')
        self.assertLess(attach, names.index('SetForegroundWindow'))
        self.assertLess(names.index('SetForegroundWindow'), names.index('SetFocus'))
        self.assertLess(names.index('SetFocus'), detach)

    def test_iconic_window_restored(self):
        u = self._u(iconic=1)
        self._run(u)
        u.ShowWindow.assert_called_once_with(self.HWND, 9)

    def test_sfw_failure_raises_and_still_detaches(self):
        u = self._u(sfw=0)
        with self.assertRaisesRegex(RuntimeError, 'SetForegroundWindow'):
            self._run(u)
        self.assertEqual(u.AttachThreadInput.call_args_list,
                         [mock.call(99, 555, True), mock.call(99, 555, False)])
        u.SetFocus.assert_not_called()

    def test_same_thread_skips_attach(self):
        u = self._u()
        self._run(u, tid_cur=555)
        u.AttachThreadInput.assert_not_called()
        u.SetFocus.assert_called_once_with(self.HWND)

    def test_foreground_lost_warns(self):
        u = self._u(gfw=0x9999)
        out = io.StringIO()
        self._run(u, out=out)
        self.assertIn('foreground', out.getvalue())


class ConsoleSequenceTests(unittest.TestCase):
    """console() contract: scan-0x29 toggles frame posted WM_CHAR text and a
    scan-code 0x1C Enter. No VK input, no clipboard, no UNICODE events."""

    def _run_console(self, cmd='observe', pid=None):
        log = []
        u = mock.MagicMock()
        u.PostMessageW.side_effect = \
            lambda h, m, w, l: log.append(('post', h, m, w)) or 1
        def fake_send(events):
            log.extend(('key', e) for e in events)
        with mock.patch.object(hs, 'game_window', return_value=0x1234) as gw, \
             mock.patch.object(hs, 'foreground') as fg, \
             mock.patch.object(hs, '_user32', return_value=u), \
             mock.patch.object(hs, '_send_input', side_effect=fake_send), \
             mock.patch.object(hs.time, 'sleep'):
            hs.console(cmd, pid=pid)
        return log, u, gw, fg

    def test_window_foregrounded_then_chars_posted(self):
        log, u, gw, fg = self._run_console(pid=42)
        gw.assert_called_once_with(42)
        fg.assert_called_once_with(0x1234)
        posts = [x for x in log if x[0] == 'post']
        self.assertEqual([p[3] for p in posts], [ord(c) for c in 'observe'])
        self.assertTrue(all(p[1] == 0x1234 and p[2] == hs.WM_CHAR for p in posts))

    def test_scancode_pairs_frame_the_posts(self):
        log, _, _, _ = self._run_console()
        key_idx = [i for i, x in enumerate(log) if x[0] == 'key']
        post_idx = [i for i, x in enumerate(log) if x[0] == 'post']
        self.assertEqual(len(key_idx), 6)       # open pair + Enter pair + close pair
        self.assertEqual(len(post_idx), len('observe'))
        self.assertEqual(key_idx[:2], [0, 1])
        self.assertEqual(key_idx[-2:], [len(log) - 2, len(log) - 1])
        self.assertEqual(min(post_idx), 2)
        self.assertLess(max(post_idx), key_idx[2])

    def test_all_keys_are_scancodes_no_vk_no_esc(self):
        log, _, _, _ = self._run_console()
        keys = [x[1] for x in log if x[0] == 'key']
        self.assertEqual([e.u.ki.wScan for e in keys],
                         [hs.SCAN_CONSOLE, hs.SCAN_CONSOLE,
                          hs.SCAN_RETURN, hs.SCAN_RETURN,
                          hs.SCAN_CONSOLE, hs.SCAN_CONSOLE])
        self.assertTrue(all(e.u.ki.dwFlags & hs.KEYEVENTF_SCANCODE for e in keys))
        self.assertTrue(all(e.u.ki.wVk == 0 for e in keys))
        ups = [bool(e.u.ki.dwFlags & hs.KEYEVENTF_KEYUP) for e in keys]
        self.assertEqual(ups, [False, True] * 3)
        self.assertFalse(any(e.u.ki.dwFlags & 0x0004 for e in keys))  # no KEYEVENTF_UNICODE

    def test_no_clipboard_path_remains(self):
        self.assertFalse(hasattr(hs, '_set_clipboard'))
        _, u, _, _ = self._run_console()
        u.OpenClipboard.assert_not_called()
        u.SetClipboardData.assert_not_called()


def _make_core_record(eff: int, mask: int, grp: int = 0) -> bytes:
    """Build a synthetic SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX core record (48 B)."""
    hdr = struct.pack('<II', hs.RELATION_PROCESSOR_CORE, 48)
    body = bytearray(40)
    body[1] = eff & 0xFF
    struct.pack_into('<H', body, 22, 1)           # GroupCount at offset 30 (8 + 22)
    struct.pack_into('<QH', body, 24, mask, grp)  # GroupMask[0] at offset 32 (8 + 24)
    return hdr + bytes(body)


def _make_non_core_record(rel: int = 2, size: int = 24) -> bytes:
    return struct.pack('<II', rel, size) + b'\x00' * (size - 8)


class ProcessAbiTests(unittest.TestCase):
    """PROCESS_POWER_THROTTLING_STATE must match Win32 ABI (12 bytes, offsets 0/4/8)."""

    def test_power_throttling_state_layout(self):
        cls = hs.PROCESS_POWER_THROTTLING_STATE
        self.assertEqual(ctypes.sizeof(cls), 12)
        self.assertEqual(cls.Version.offset, 0)
        self.assertEqual(cls.ControlMask.offset, 4)
        self.assertEqual(cls.StateMask.offset, 8)


class PCoreMaskTests(unittest.TestCase):
    """Hybrid CPU topology detection from RelationProcessorCore buffers."""

    def test_14700kf_hybrid_layout_returns_pcore_mask(self):
        records = []
        # 8 SMT P-cores (threads 0..15, EfficiencyClass=1)
        for p in range(8):
            records.append(_make_core_record(eff=1, mask=0b11 << (2 * p)))
        # 12 non-SMT E-cores (threads 16..27, EfficiencyClass=0)
        for e in range(12):
            records.append(_make_core_record(eff=0, mask=1 << (16 + e)))
        self.assertEqual(hs.parse_pcore_mask(b''.join(records)), 0xFFFF)

    def test_homogeneous_cpu_returns_none(self):
        raw = b''.join(_make_core_record(eff=0, mask=1 << i) for i in range(4))
        self.assertIsNone(hs.parse_pcore_mask(raw))
        self.assertIsNone(hs.parse_pcore_mask(b''))

    def test_skips_non_core_records(self):
        raw = (
            _make_non_core_record(rel=2, size=24)
            + _make_core_record(eff=1, mask=0b11)
            + _make_non_core_record(rel=3, size=16)
            + _make_core_record(eff=0, mask=0b100)
        )
        self.assertEqual(hs.parse_pcore_mask(raw), 0b11)

    def test_malformed_or_truncated_buffers_raise(self):
        with self.assertRaises(ValueError):
            hs.parse_pcore_mask(b'\x00\x00\x00')
        with self.assertRaises(ValueError):
            hs.parse_pcore_mask(struct.pack('<II', 0, 4))
        with self.assertRaises(ValueError):
            hs.parse_pcore_mask(struct.pack('<II', 0, 16) + b'\x00' * 8)

    def test_detect_pcore_mask_two_call_pattern(self):
        raw = _make_core_record(eff=1, mask=0x0F) + _make_core_record(eff=0, mask=0xF0)
        k = mock.MagicMock()

        def fake_glpie(rel, buf, n_ptr):
            if buf is None:
                n_ptr._obj.value = len(raw)
                return 0
            ctypes.memmove(buf, raw, len(raw))
            n_ptr._obj.value = len(raw)
            return 1

        k.GetLogicalProcessorInformationEx.side_effect = fake_glpie
        with mock.patch.object(hs, '_kernel32', return_value=k):
            self.assertEqual(hs.detect_pcore_mask(), 0x0F)


class TuneProcessTests(unittest.TestCase):
    """tune_process pins affinity, raises priority, disables EcoQoS, and closes handle."""

    def _kernel(self, *, open_h=0xBEEF, aff_ok=1, prio_ok=1, info_ok=1):
        k = mock.MagicMock()
        k.OpenProcess.return_value = open_h
        k.SetProcessAffinityMask.return_value = aff_ok
        k.SetPriorityClass.return_value = prio_ok
        k.SetProcessInformation.return_value = info_ok
        return k

    def test_tune_auto_pcores_priority_and_ecoqos(self):
        k = self._kernel()
        with mock.patch.object(hs, '_kernel32', return_value=k), \
             mock.patch.object(hs, 'detect_pcore_mask', return_value=0xFFFF):
            res = hs.tune_process(4242)
        k.OpenProcess.assert_called_once_with(
            hs.PROCESS_SET_INFORMATION | hs.PROCESS_QUERY_LIMITED_INFORMATION,
            False, 4242)
        k.SetProcessAffinityMask.assert_called_once_with(0xBEEF, 0xFFFF)
        k.SetPriorityClass.assert_called_once_with(0xBEEF, hs.HIGH_PRIORITY_CLASS)
        args = k.SetProcessInformation.call_args[0]
        self.assertEqual(args[0], 0xBEEF)
        self.assertEqual(args[1], hs.PROCESS_POWER_THROTTLING_CLASS)
        self.assertEqual(args[3], 12)
        k.CloseHandle.assert_called_once_with(0xBEEF)
        self.assertEqual(res, {
            'pid': 4242,
            'affinity_mask': 0xFFFF,
            'high_priority': True,
            'ecoqos_disabled': True,
        })

    def test_explicit_affinity_and_no_auto_pcores(self):
        k = self._kernel()
        with mock.patch.object(hs, '_kernel32', return_value=k), \
             mock.patch.object(hs, 'detect_pcore_mask', side_effect=AssertionError):
            res = hs.tune_process(77, affinity_mask=0xFF)
        k.SetProcessAffinityMask.assert_called_once_with(0xBEEF, 0xFF)
        self.assertEqual(res['affinity_mask'], 0xFF)

        k2 = self._kernel()
        with mock.patch.object(hs, '_kernel32', return_value=k2), \
             mock.patch.object(hs, 'detect_pcore_mask', side_effect=AssertionError):
            res2 = hs.tune_process(77, auto_pcores=False)
        k2.SetProcessAffinityMask.assert_not_called()
        self.assertIsNone(res2['affinity_mask'])

    def test_close_handle_called_on_error_and_open_failure_raises(self):
        k_fail_open = self._kernel(open_h=0)
        with mock.patch.object(hs, '_kernel32', return_value=k_fail_open):
            with self.assertRaisesRegex(RuntimeError, 'OpenProcess'):
                hs.tune_process(99, auto_pcores=False)
        k_fail_open.CloseHandle.assert_not_called()

        k_fail_prio = self._kernel(prio_ok=0)
        with mock.patch.object(hs, '_kernel32', return_value=k_fail_prio), \
             mock.patch.object(hs, 'detect_pcore_mask', return_value=0xFF):
            with self.assertRaisesRegex(RuntimeError, 'SetPriorityClass'):
                hs.tune_process(99)
        k_fail_prio.CloseHandle.assert_called_once_with(0xBEEF)


SAMPLE_SETTINGS = (
    'force_pow2_textures=no\r\n'
    'language="l_simp_chinese"\r\n'
    'graphics={\r\n'
    '\tsize={ x=2560 y=1440 }\r\n'
    '\tmin_gui={ x=1280 y=720 }\r\n'
    '\trefreshRate=175\r\n'
    '\tmax_refresh_rate=75\r\n'
    '\tfullScreen=no\r\n'
    '\tborderless=yes\r\n'
    '\tshadows=no\r\n'
    '\tdisable_3d_model_viewer=no\r\n'
    '\tmulti_sampling=0\r\n'
    '\tmaxanisotropy=4\r\n'
    '\tvsync=yes\r\n'
    '}\r\n'
    'minimap_visible=no\r\n'
    'mapRenderingOptions={\r\n'
    '\tdraw_terrain=yes\r\n'
    '\tdraw_water=yes\r\n'
    '\tdraw_borders=yes\r\n'
    '\tdraw_trees=yes\r\n'
    '\tdraw_rivers=yes\r\n'
    '\tdraw_postfx=no\r\n'
    '\tdraw_sky=yes\r\n'
    '\tdraw_bloom=yes\r\n'
    '\tdraw_tooltips=yes\r\n'
    '\tdraw_hires_terrain=no\r\n'
    '\tdraw_citysprawl=yes\r\n'
    '\tdraw_shadows=yes\r\n'
    '\tdraw_weather_effects=yes\r\n'
    '\tdraw_water_reflections=yes\r\n'
    '\tdraw_units=yes\r\n'
    '\tdraw_buildings=yes\r\n'
    '\thigh_gfx_shaders=yes\r\n'
    '\tdraw_map_full_res=yes\r\n'
    '\ttexture_quality=0\r\n'
    '}\r\n'
    'lastplayer="AzurCrystal"\r\n'
    'autosave="MONTHLY"\r\n'
    'outliner_open=yes\r\n'
    'save_as_binary=no\r\n'
    'pause_on_popups=no\r\n'
    'telemetry_enabled=yes\r\n'
    'event_sounds=yes\r\n'
    'background={\r\n'
    '\tgfx/loadingscreens/load_1.dds\tgfx/loadingscreens/load_goe.dds}\r\n'
    'weight={\r\n'
    '\t0\t0}\r\n'
    'cvaa_settings={\r\n'
    '\tcvaa_tts_enabled=no\r\n'
    '\tevent_sounds=yes\r\n'
    '}\r\n'
)

SAMPLE_PDX_SETTINGS = (
    '"Graphics"={\r\n'
    '\t"display_mode"={\r\n'
    '\t\tvalue="borderless_fullscreen"\r\n'
    '\t\tversion=0\r\n'
    '\t}\r\n'
    '\t"vsync"={\r\n'
    '\t\tenabled=yes\r\n'
    '\t\tversion=0\r\n'
    '\t}\r\n'
    '\t"fullscreen_resolution"={\r\n'
    '\t\tvalue="2560x1440"\r\n'
    '\t\tversion=0\r\n'
    '\t}\r\n'
    '\t"renderer"={\r\n'
    '\t\tvalue="dx11"\r\n'
    '\t\tversion=0\r\n'
    '\t}\r\n'
    '\t"windowed_resolution"={\r\n'
    '\t\tvalue="2560x1440"\r\n'
    '\t\tversion=0\r\n'
    '\t}\r\n'
    '}\r\n'
    '"System"={\r\n'
    '\t"language"={\r\n'
    '\t\tvalue="l_simp_chinese"\r\n'
    '\t\tversion=0\r\n'
    '\t}\r\n'
    '}'
)


class ProfilePatchTests(unittest.TestCase):
    """Settings patchers and transactional apply/restore stay reversible and safe."""

    def test_patch_settings_txt_preserves_crlf_and_unrelated_entries(self):
        patched = hs.patch_settings_txt(SAMPLE_SETTINGS)
        self.assertIn('\r\n', patched)
        self.assertNotIn('\r\r\n', patched)
        self.assertTrue(patched.endswith('\r\n'))
        # Graphics overrides applied
        for expected in (
            '\tsize={ x=1280 y=720 }\r\n',
            '\tmax_refresh_rate=0\r\n',
            '\tfullScreen=no\r\n',
            '\tborderless=no\r\n',
            '\tdisable_3d_model_viewer=yes\r\n',
            '\tmaxanisotropy=0\r\n',
            '\tvsync=no\r\n',
        ):
            self.assertIn(expected, patched)
        # Visual-only mapRenderingOptions turned off, terrain/borders/texture preserved
        self.assertIn('\tdraw_terrain=yes\r\n', patched)
        self.assertIn('\tdraw_borders=yes\r\n', patched)
        self.assertIn('\ttexture_quality=0\r\n', patched)
        for off_key in ('draw_water', 'draw_trees', 'draw_rivers', 'draw_units',
                        'draw_weather_effects', 'high_gfx_shaders', 'draw_map_full_res'):
            self.assertIn(f'\t{off_key}=no\r\n', patched)
        # Top-level observer/autosave settings
        self.assertIn('autosave="MONTHLY"\r\n', patched)
        self.assertIn('save_as_binary=no\r\n', patched)
        self.assertIn('outliner_open=no\r\n', patched)
        self.assertIn('event_sounds=no\r\n', patched)
        self.assertIn('telemetry_enabled=no\r\n', patched)
        # Preserved personal/nested blocks (and nested event_sounds untouched)
        self.assertIn('language="l_simp_chinese"\r\n', patched)
        self.assertIn('lastplayer="AzurCrystal"\r\n', patched)
        self.assertIn('load_goe.dds}\r\n', patched)
        self.assertIn('cvaa_settings={\r\n\tcvaa_tts_enabled=no\r\n\tevent_sounds=yes\r\n}\r\n', patched)

    def test_patch_settings_txt_binary_save_and_invalid_autosave(self):
        patched = hs.patch_settings_txt(SAMPLE_SETTINGS, autosave='halfyear', binary_save=True)
        self.assertIn('autosave="HALFYEAR"\r\n', patched)
        self.assertIn('save_as_binary=yes\r\n', patched)
        with self.assertRaises(ValueError):
            hs.patch_settings_txt(SAMPLE_SETTINGS, autosave='HOURLY')

    def test_patch_pdx_settings_txt_preserves_system_and_renderer(self):
        patched = hs.patch_pdx_settings_txt(SAMPLE_PDX_SETTINGS)
        self.assertFalse(patched.endswith('\n'))
        self.assertIn('\t"display_mode"={\r\n\t\tvalue="windowed"\r\n\t\tversion=0\r\n\t}\r\n', patched)
        self.assertIn('\t"vsync"={\r\n\t\tenabled=no\r\n\t\tversion=0\r\n\t}\r\n', patched)
        self.assertIn('\t"windowed_resolution"={\r\n\t\tvalue="1280x720"\r\n\t\tversion=0\r\n\t}\r\n', patched)
        self.assertIn('"fullscreen_resolution"={\r\n\t\tvalue="2560x1440"', patched)
        self.assertIn('"renderer"={\r\n\t\tvalue="dx11"', patched)
        self.assertIn('"System"={\r\n\t"language"={\r\n\t\tvalue="l_simp_chinese"', patched)

    def test_apply_and_restore_profile_idempotent_and_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs = os.path.join(tmp, 'docs')
            bak = os.path.join(tmp, 'bak')
            os.makedirs(docs)
            s_path = os.path.join(docs, 'settings.txt')
            p_path = os.path.join(docs, 'pdx_settings.txt')
            with open(s_path, 'w', encoding='utf-8', newline='') as fh:
                fh.write(SAMPLE_SETTINGS)
            with open(p_path, 'w', encoding='utf-8', newline='') as fh:
                fh.write(SAMPLE_PDX_SETTINGS)

            with mock.patch.object(hs, '_hoi4_pids', return_value=set()):
                r1 = hs.apply_fast_profile(docs_dir=docs, backup_dir=bak)
                self.assertEqual(r1['backed_up'], ['settings.txt', 'pdx_settings.txt'])
                # Second apply must NOT overwrite backup with already-patched files.
                r2 = hs.apply_fast_profile(docs_dir=docs, backup_dir=bak, binary_save=True)
                self.assertEqual(r2['backed_up'], [])

                restored = hs.restore_profile(docs_dir=docs, backup_dir=bak)
                self.assertEqual(restored, ['settings.txt', 'pdx_settings.txt'])

            with open(s_path, encoding='utf-8', newline='') as fh:
                self.assertEqual(fh.read(), SAMPLE_SETTINGS)
            with open(p_path, encoding='utf-8', newline='') as fh:
                self.assertEqual(fh.read(), SAMPLE_PDX_SETTINGS)
            self.assertFalse(os.path.exists(os.path.join(bak, 'settings.txt')))
            self.assertFalse(os.path.exists(os.path.join(bak, 'pdx_settings.txt')))

    def test_apply_and_restore_refuse_while_hoi4_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(hs, '_hoi4_pids', return_value={1234}):
                with self.assertRaisesRegex(RuntimeError, 'hoi4.exe is running'):
                    hs.apply_fast_profile(docs_dir=tmp, backup_dir=tmp)
                with self.assertRaisesRegex(RuntimeError, 'hoi4.exe is running'):
                    hs.restore_profile(docs_dir=tmp, backup_dir=tmp)


class FastArgsTests(unittest.TestCase):
    """HOI4 launch argument construction and CLI token validation."""

    def test_default_fast_args(self):
        self.assertEqual(
            hs.build_fast_args(),
            ['-nolauncher', '-autosave=MONTHLY', '-hands_off', '-start_speed=4'],
        )

    def test_variants_and_start_save_conflict_guard(self):
        self.assertEqual(
            hs.build_fast_args(hands_off=False, debug=True, start_tag='GER', autosave='yearly'),
            ['-nolauncher', '-autosave=YEARLY', '-start_tag=GER', '-start_speed=4', '-debug'],
        )
        # start_save strips .hoi4 and intentionally omits -hands_off
        self.assertEqual(
            hs.build_fast_args(hands_off=True, start_save='baseline_1939.hoi4', start_speed=3),
            ['-nolauncher', '-autosave=MONTHLY', '-start_save=baseline_1939', '-start_speed=3'],
        )

    def test_invalid_tokens_and_speeds_raise(self):
        with self.assertRaises(ValueError):
            hs.build_fast_args(start_save='bad save')
        with self.assertRaises(ValueError):
            hs.build_fast_args(start_save='bad-save')
        with self.assertRaises(ValueError):
            hs.build_fast_args(start_tag='G-R')
        with self.assertRaises(ValueError):
            hs.build_fast_args(start_speed=5)


class HotkeyAndFastConsoleTests(unittest.TestCase):
    """toggle_nogui sends Ctrl+F9 scancode chord; fast_console runs commands in order."""

    def test_toggle_nogui_sends_lctrl_f9_chord(self):
        sent_events = []
        with mock.patch.object(hs, 'game_window', return_value=0x1234) as gw, \
             mock.patch.object(hs, 'foreground') as fg, \
             mock.patch.object(hs, '_send_input', side_effect=sent_events.extend), \
             mock.patch.object(hs.time, 'sleep'), \
             contextlib.redirect_stdout(io.StringIO()):
            hs.toggle_nogui(pid=42)
        gw.assert_called_once_with(42)
        fg.assert_called_once_with(0x1234)
        self.assertEqual(
            [(e.u.ki.wScan, bool(e.u.ki.dwFlags & hs.KEYEVENTF_KEYUP)) for e in sent_events],
            [(hs.SCAN_LCTRL, False), (hs.SCAN_F9, False),
             (hs.SCAN_F9, True), (hs.SCAN_LCTRL, True)],
        )
        self.assertTrue(all(e.u.ki.dwFlags & hs.KEYEVENTF_SCANCODE for e in sent_events))

    def test_fast_console_order_and_flags(self):
        with mock.patch.object(hs, 'console') as con:
            hs.fast_console(pid=77)
        self.assertEqual(
            [c.args for c in con.call_args_list],
            [('observe',), ('debug_smooth',), ('debug_nogui',)],
        )
        self.assertTrue(all(c.kwargs == {'pid': 77} for c in con.call_args_list))

        with mock.patch.object(hs, 'console') as con:
            hs.fast_console(pid=77, observe=False, smooth=True, nogui=False)
        con.assert_called_once_with('debug_smooth', pid=77)


if __name__ == '__main__':
    unittest.main()
