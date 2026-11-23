#!/usr/bin/env python
"""HOI4 spectator & fast-simulation harness — "AI window" into a running game.

Standard flow:
  1. python tools/hoi4_spectate.py launch          # start game (-debug -nolauncher)
  2. python tools/hoi4_spectate.py wait            # block until main menu ready
  3. python tools/hoi4_spectate.py console observe # inject `observe` via console
  4. python tools/hoi4_spectate.py snapshot        # copy logs+latest save to _ctx/spectate/

Zero-accuracy-loss fast test-run flow (skips main menu via -hands_off, keeps
monthly HOI4txt autosaves by default, removes VSync/75Hz/render/E-core caps):
  1. python tools/hoi4_spectate.py fast            # backup+patch settings, launch -hands_off, pin P-cores
  2. python tools/hoi4_spectate.py nogui           # optional: Ctrl+F9 to hide HUD without -debug
  3. (exit hoi4.exe when done)
  4. python tools/hoi4_spectate.py restore         # restore original graphics settings

Note: `-hands_off` skips the main menu and enters the game directly, so do not
run `wait` after `fast` (unless `--no-hands-off` without `--start-save` is used).

Requires: Windows + steam running + mod already enabled in dlc_load.json.

Usage:
  python tools/hoi4_spectate.py fast [--debug] [--start-save NAME] [--autosave MONTHLY] [--binary-save]
  python tools/hoi4_spectate.py nogui [--pid N]
  python tools/hoi4_spectate.py fast-console [--pid N] [--no-observe] [--no-smooth] [--no-nogui]
  python tools/hoi4_spectate.py tune [--pid N] [--affinity 0xFFFF]
  python tools/hoi4_spectate.py profile-apply [--autosave MONTHLY] [--binary-save]
  python tools/hoi4_spectate.py restore
  python tools/hoi4_spectate.py launch | wait | console <cmd> [--pid N] | snapshot | status
"""
import argparse, contextlib, ctypes, glob, os, re, shutil, struct, subprocess, sys, time, zlib

DOCS = os.path.expanduser('~/Documents/Paradox Interactive/Hearts of Iron IV')
LOGS = os.path.join(DOCS, 'logs')
SAVES = os.path.join(DOCS, 'save games')
EXE = r'C:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV\hoi4.exe'
OUT = '_ctx/spectate'
PROFILE_BACKUP_DIR = os.path.join(OUT, 'profile_backup')

AUTOSAVE_INTERVALS = ('NEVER', 'DAILY', 'WEEKLY', 'MONTHLY', 'HALFYEAR', 'YEARLY')

# ---------- console input & process tuning ----------

# Win64 INPUT ABI: the union sizes off MOUSEINPUT (32 B), so INPUT = 8 B
# (type + padding) + 32 B = 40 B — declaring it as 24 B truncates SendInput.
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
SCAN_CONSOLE = 0x29        # `/~ key left of 1 — HOI4 console toggle (layout-independent)
SCAN_RETURN = 0x1C         # main Enter — field-proven submit path for the console
SCAN_LCTRL = 0x1D          # left Ctrl — used with F9 for debug_nogui without -debug
SCAN_F9 = 0x43             # F9 — Ctrl+F9 toggles GUI
WM_CHAR = 0x0102           # text input — posts straight into the wndproc, no IME chain
TH32CS_SNAPPROCESS = 0x00000002

PROCESS_SET_INFORMATION = 0x0200
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
HIGH_PRIORITY_CLASS = 0x00000080
PROCESS_POWER_THROTTLING_CLASS = 4
PROCESS_POWER_THROTTLING_CURRENT_VERSION = 1
PROCESS_POWER_THROTTLING_EXECUTION_SPEED = 0x1
RELATION_PROCESSOR_CORE = 0

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', ctypes.c_long), ('dy', ctypes.c_long),
                ('mouseData', ctypes.c_ulong), ('dwFlags', ctypes.c_ulong),
                ('time', ctypes.c_ulong), ('dwExtraInfo', ctypes.c_ulonglong)]

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', ctypes.c_ushort), ('wScan', ctypes.c_ushort),
                ('dwFlags', ctypes.c_ulong), ('time', ctypes.c_ulong),
                ('dwExtraInfo', ctypes.c_ulonglong)]

class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [('uMsg', ctypes.c_ulong), ('wParamL', ctypes.c_ushort),
                ('wParamH', ctypes.c_ushort)]

class _UNION(ctypes.Union):
    _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT), ('hi', HARDWAREINPUT)]

class INPUT(ctypes.Structure):
    _fields_ = [('type', ctypes.c_ulong), ('u', _UNION)]

class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [('dwSize', ctypes.c_ulong),
                ('cntUsage', ctypes.c_ulong),
                ('th32ProcessID', ctypes.c_ulong),
                ('th32DefaultHeapID', ctypes.c_ulonglong),
                ('th32ModuleID', ctypes.c_ulong),
                ('cntThreads', ctypes.c_ulong),
                ('th32ParentProcessID', ctypes.c_ulong),
                ('pcPriClassBase', ctypes.c_long),
                ('dwFlags', ctypes.c_ulong),
                ('szExeFile', ctypes.c_wchar * 260)]

class PROCESS_POWER_THROTTLING_STATE(ctypes.Structure):
    _fields_ = [('Version', ctypes.c_ulong),
                ('ControlMask', ctypes.c_ulong),
                ('StateMask', ctypes.c_ulong)]

def _user32():
    u = ctypes.WinDLL('user32', use_last_error=True)
    u.SendInput.argtypes = (ctypes.c_uint, ctypes.c_void_p, ctypes.c_int)
    u.GetWindowThreadProcessId.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    u.IsWindowVisible.argtypes = (ctypes.c_void_p,)
    u.IsIconic.argtypes = (ctypes.c_void_p,)
    u.ShowWindow.argtypes = (ctypes.c_void_p, ctypes.c_int)
    u.SetForegroundWindow.argtypes = (ctypes.c_void_p,)
    u.GetForegroundWindow.argtypes = ()
    u.BringWindowToTop.argtypes = (ctypes.c_void_p,)
    u.AllowSetForegroundWindow.argtypes = (ctypes.c_ulong,)
    u.AttachThreadInput.argtypes = (ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int)
    u.SetFocus.argtypes = (ctypes.c_void_p,)
    u.GetFocus.argtypes = ()
    u.PostMessageW.argtypes = (ctypes.c_void_p, ctypes.c_uint,
                             ctypes.c_size_t, ctypes.c_long)
    return u

def _kernel32():
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k.CreateToolhelp32Snapshot.argtypes = (ctypes.c_ulong, ctypes.c_ulong)
    k.Process32FirstW.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    k.Process32NextW.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    k.CloseHandle.argtypes = (ctypes.c_void_p,)
    k.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
    k.OpenProcess.restype = ctypes.c_void_p
    k.SetProcessAffinityMask.argtypes = (ctypes.c_void_p, ctypes.c_size_t)
    k.SetProcessAffinityMask.restype = ctypes.c_int
    k.SetPriorityClass.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
    k.SetPriorityClass.restype = ctypes.c_int
    k.SetProcessInformation.argtypes = (ctypes.c_void_p, ctypes.c_int,
                                        ctypes.c_void_p, ctypes.c_ulong)
    k.SetProcessInformation.restype = ctypes.c_int
    k.GetLogicalProcessorInformationEx.argtypes = (ctypes.c_int, ctypes.c_void_p,
                                                     ctypes.c_void_p)
    k.GetLogicalProcessorInformationEx.restype = ctypes.c_int
    return k

def _key_input(wVk=0, wScan=0, dwFlags=0):
    e = INPUT()
    e.type = INPUT_KEYBOARD
    e.u.ki = KEYBDINPUT(wVk, wScan, dwFlags, 0, 0)
    return e

def _send_input(events):
    """SendInput; raises on short write (return < n) instead of silently dropping."""
    u = _user32()
    arr = (INPUT * len(events))(*events)
    sent = u.SendInput(len(arr), arr, ctypes.sizeof(INPUT))
    if sent != len(arr):
        raise RuntimeError(
            f'SendInput injected {sent}/{len(arr)} events '
            f'(GetLastError={ctypes.get_last_error()})')

def _press_scancode(scan):
    """physical-key press by scan code — layout-independent, reaches raw-input readers."""
    _send_input([_key_input(wScan=scan, dwFlags=KEYEVENTF_SCANCODE),
                 _key_input(wScan=scan, dwFlags=KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP)])

def _hoi4_pids():
    """pids whose exe is hoi4.exe."""
    k = _kernel32()
    snap = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap in (None, ctypes.c_void_p(-1).value):
        raise RuntimeError(f'CreateToolhelp32Snapshot failed '
                           f'(GetLastError={ctypes.get_last_error()})')
    pe = PROCESSENTRY32W()
    pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    pids = set()
    try:
        ok = k.Process32FirstW(snap, ctypes.byref(pe))
        while ok:
            if pe.szExeFile.lower() == 'hoi4.exe':
                pids.add(pe.th32ProcessID)
            ok = k.Process32NextW(snap, ctypes.byref(pe))
    finally:
        k.CloseHandle(snap)
    return pids

def _hoi4_windows(pids):
    """visible top-level hwnds owned by any pid in `pids`."""
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
    u = _user32()
    hwnds = []
    def _cb(hwnd, _lparam):
        pid = ctypes.c_ulong(0)
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and u.IsWindowVisible(hwnd):
            hwnds.append(hwnd)
        return 1
    u.EnumWindows(WNDENUMPROC(_cb), None)
    return hwnds

def game_window(pid=None):
    """unique visible hoi4.exe window; optional `pid` pins the process."""
    pids = {int(pid)} if pid else _hoi4_pids()
    hwnds = _hoi4_windows(pids)
    if not hwnds:
        raise RuntimeError('no visible hoi4.exe window found')
    if len(hwnds) > 1:
        raise RuntimeError(f'{len(hwnds)} hoi4.exe windows found — pass pid '
                           f'(candidates: {[hex(h) for h in hwnds]})')
    return hwnds[0]

@contextlib.contextmanager
def _attached_input(hwnd, u):
    """AttachThreadInput to hwnd's thread for the `with` block: GetFocus/SetFocus
    then see the game's own focus chain. Detaches on exit if it attached."""
    tid = u.GetWindowThreadProcessId(hwnd, ctypes.byref(ctypes.c_ulong(0)))
    cur = ctypes.windll.kernel32.GetCurrentThreadId()
    attached = bool(tid) and tid != cur and bool(u.AttachThreadInput(cur, tid, True))
    try:
        yield
    finally:
        if attached:
            u.AttachThreadInput(cur, tid, False)

def foreground(hwnd):
    """restore, raise and foreground the game window, then SetFocus it inside its
    own thread's focus chain (AttachThreadInput makes that legal)."""
    u = _user32()
    if u.IsIconic(hwnd):
        u.ShowWindow(hwnd, 9)  # SW_RESTORE
    pid = ctypes.c_ulong(0)
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    u.AllowSetForegroundWindow(pid.value)   # hint only — result intentionally ignored
    u.BringWindowToTop(hwnd)
    with _attached_input(hwnd, u):
        if not u.SetForegroundWindow(hwnd):
            raise RuntimeError(f'SetForegroundWindow({hex(hwnd)}) failed')
        u.SetFocus(hwnd)
    if u.GetForegroundWindow() != hwnd:
        print('warning: game window lost foreground — console toggle may miss')

def _post_chars(hwnd, text, u):
    """one WM_CHAR per UTF-16 unit — the same message a physical keypress ends as
    after TranslateMessage, but posted straight into the game window's queue:
    no IME chain, no hooks, no keyboard layout, no focus race."""
    data = text.encode('utf-16-le', 'surrogatepass')
    for i in range(0, len(data), 2):
        unit = data[i] | (data[i + 1] << 8)
        if not u.PostMessageW(hwnd, WM_CHAR, unit, 1):
            raise RuntimeError(f'PostMessageW(WM_CHAR {unit:#06x}) failed '
                               f'(GetLastError={ctypes.get_last_error()})')

def console(cmd, pid=None):
    """foreground hoi4.exe, open console (scan 0x29), post cmd as WM_CHAR,
    scan-code Enter, close console. Command text never touches the keyboard/IME
    path, so a zh-CN IME cannot swallow or rewrite it."""
    hwnd = game_window(pid)
    foreground(hwnd)
    _press_scancode(SCAN_CONSOLE)
    time.sleep(0.4)
    _post_chars(hwnd, cmd, _user32())
    time.sleep(0.1)
    _press_scancode(SCAN_RETURN)
    time.sleep(0.3)
    _press_scancode(SCAN_CONSOLE)  # toggle console closed — no Esc, so a failed open can't pop the game menu
    print(f'sent: {cmd}')

# ---------- log waiting ----------

def err_size():
    p = os.path.join(LOGS, 'error.log')
    return os.path.getsize(p) if os.path.exists(p) else 0

def wait_menu(timeout=300):
    """main menu ≈ error.log stable for 5s + system.log mentions mainscreen."""
    last, stable = -1, 0
    t0 = time.time()
    sys_log = os.path.join(LOGS, 'system.log')
    while time.time() - t0 < timeout:
        sz = err_size()
        if sz == last: stable += 1
        else: stable, last = 0, sz
        if stable >= 5:
            try:
                tail = open(sys_log, encoding='utf-8', errors='replace').read()[-2000:]
                if 'Main screen' in tail or 'no_game_date' in tail:
                    print(f'menu ready (error.log={sz}b)')
                    return True
            except OSError:
                pass
        time.sleep(1)
    print('timeout waiting for menu')
    return False

# ---------- save harvesting ----------

def newest_save():
    files = glob.glob(os.path.join(SAVES, '*.hoi4'))
    return max(files, key=os.path.getmtime) if files else None

def parse_save_head(path):
    """HOI4 save: 'HOI4txt' magic + zlib stream of Clausewitz text."""
    raw = open(path, 'rb').read()
    if raw[:7] == b'HOI4txt':
        # try decompress whole body after magic
        body = raw[7:]
        try:
            txt = zlib.decompress(body).decode('utf-8', 'replace')
        except zlib.error:
            # fallback: some saves are plain text
            txt = raw.decode('utf-8', 'replace')
    else:
        txt = raw.decode('utf-8', 'replace')
    return txt

def save_summary(path):
    txt = parse_save_head(path)
    date = re.search(r'date\s*=\s*"?([\d.]+)"?', txt)
    player = re.search(r'player\s*=\s*"?([A-Z]{3})', txt)
    wars = re.findall(r'\bwar\s*=\s*\{', txt)
    majors = re.findall(r'^\s*(GER|SOV|ENG|FRA|ITA|USA|JAP)\s*=\s*\{', txt, re.M)
    return {'date': date.group(1) if date else '?',
            'player': player.group(1) if player else 'observe',
            'wars': len(wars), 'majors_seen': len(set(majors))}

def snapshot():
    os.makedirs(OUT, exist_ok=True)
    for name in ('error.log', 'game.log', 'system.log', 'ai.log', 'executed_commands.log'):
        src = os.path.join(LOGS, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(OUT, name))
    sv = newest_save()
    if sv:
        dst = os.path.join(OUT, 'latest.hoi4')
        shutil.copy2(sv, dst)
        print('save:', sv, '→', dst)
        print('summary:', save_summary(dst))
    print('logs copied to', OUT)

def status():
    sz = err_size()
    sv = newest_save()
    info = save_summary(sv) if sv else {}
    print(f'error.log={sz}b  save={os.path.basename(sv) if sv else "none"}  {info}')

# ---------- P-core detection & process tuning ----------

def parse_pcore_mask(raw: bytes) -> int | None:
    """Parse GetLogicalProcessorInformationEx(RelationProcessorCore) buffer.

    Returns the group-0 bitmask for the highest EfficiencyClass cores (P-cores),
    or None if the CPU is homogeneous (<= 1 distinct efficiency class).
    """
    by_eff: dict[int, int] = {}
    off = 0
    total = len(raw)
    while off < total:
        if off + 8 > total:
            raise ValueError('truncated processor info record')
        rel, size = struct.unpack_from('<II', raw, off)
        if size < 8 or off + size > total:
            raise ValueError(f'invalid processor info record size={size}')
        if rel == RELATION_PROCESSOR_CORE:
            if size < 32:
                raise ValueError('truncated processor core record')
            eff = raw[off + 9]
            group_count = struct.unpack_from('<H', raw, off + 30)[0]
            if 32 + group_count * 16 > size:
                raise ValueError('truncated group mask array')
            for i in range(group_count):
                mask, grp = struct.unpack_from('<QH', raw, off + 32 + i * 16)
                if grp == 0:
                    by_eff[eff] = by_eff.get(eff, 0) | mask
        off += size
    if len(by_eff) <= 1:
        return None
    return by_eff[max(by_eff)]

def detect_pcore_mask() -> int | None:
    """Return group-0 P-core affinity mask on hybrid CPUs, else None."""
    k = _kernel32()
    n = ctypes.c_ulong(0)
    k.GetLogicalProcessorInformationEx(RELATION_PROCESSOR_CORE, None, ctypes.byref(n))
    if n.value == 0:
        return None
    buf = ctypes.create_string_buffer(n.value)
    if not k.GetLogicalProcessorInformationEx(RELATION_PROCESSOR_CORE, buf, ctypes.byref(n)):
        raise RuntimeError(
            f'GetLogicalProcessorInformationEx failed '
            f'(GetLastError={ctypes.get_last_error()})')
    return parse_pcore_mask(buf.raw[:n.value])

def resolve_pid(pid=None) -> int:
    """Resolve explicit or unique running hoi4.exe PID."""
    if pid is not None:
        return int(pid)
    pids = sorted(_hoi4_pids())
    if not pids:
        raise RuntimeError('no hoi4.exe process found')
    if len(pids) > 1:
        raise RuntimeError(f'{len(pids)} hoi4.exe processes found — pass --pid '
                           f'(candidates: {pids})')
    return pids[0]

def tune_process(pid: int, *, affinity_mask: int | None = None,
                 auto_pcores: bool = True, high_priority: bool = True,
                 disable_ecoqos: bool = True) -> dict:
    """Pin hoi4.exe to P-cores, raise priority to High, and disable EcoQoS."""
    target_mask = (affinity_mask if affinity_mask is not None
                   else (detect_pcore_mask() if auto_pcores else None))
    if target_mask is not None and target_mask <= 0:
        raise ValueError(f'affinity_mask must be positive, got {target_mask!r}')
    k = _kernel32()
    h = k.OpenProcess(PROCESS_SET_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION,
                      False, int(pid))
    if not h:
        raise RuntimeError(f'OpenProcess({pid}) failed '
                           f'(GetLastError={ctypes.get_last_error()})')
    ecoqos_ok = False
    try:
        if target_mask is not None:
            if not k.SetProcessAffinityMask(h, target_mask):
                raise RuntimeError(
                    f'SetProcessAffinityMask({pid}, {hex(target_mask)}) failed '
                    f'(GetLastError={ctypes.get_last_error()})')
        if high_priority:
            if not k.SetPriorityClass(h, HIGH_PRIORITY_CLASS):
                raise RuntimeError(
                    f'SetPriorityClass({pid}) failed '
                    f'(GetLastError={ctypes.get_last_error()})')
        if disable_ecoqos:
            state = PROCESS_POWER_THROTTLING_STATE(
                PROCESS_POWER_THROTTLING_CURRENT_VERSION,
                PROCESS_POWER_THROTTLING_EXECUTION_SPEED,
                0,
            )
            ecoqos_ok = bool(k.SetProcessInformation(
                h, PROCESS_POWER_THROTTLING_CLASS,
                ctypes.byref(state), ctypes.sizeof(state)))
    finally:
        k.CloseHandle(h)
    return {
        'pid': int(pid),
        'affinity_mask': target_mask,
        'high_priority': bool(high_priority),
        'ecoqos_disabled': ecoqos_ok if disable_ecoqos else False,
    }

# ---------- reversible fast observer profile ----------

_BLOCK_OPEN_RE = re.compile(r'^\s*("([^"]+)"|([A-Za-z0-9_.-]+))\s*=\s*\{')
_KV_RE = re.compile(r'^(\s*)("([^"]+)"|([A-Za-z0-9_.-]+))\s*=(.*)$')

def _split_inline_comment(val_text: str) -> tuple[str, str]:
    """Split trailing # comment outside double quotes, preserving leading space."""
    in_quote = False
    escaped = False
    for idx, ch in enumerate(val_text):
        if in_quote:
            if escaped:
                escaped = False
            elif ch == '\\':
                escaped = True
            elif ch == '"':
                in_quote = False
            continue
        if ch == '"':
            in_quote = True
        elif ch == '#':
            body = val_text[:idx].rstrip()
            pad = val_text[len(body):idx]
            return body, pad + val_text[idx:]
    return val_text, ''

def _scan_braces_outside_quotes(line: str) -> list[tuple[int, str]]:
    """Return [(char_index, '{' | '}')] ignoring strings and # comments."""
    events: list[tuple[int, str]] = []
    in_quote = False
    escaped = False
    for idx, ch in enumerate(line):
        if in_quote:
            if escaped:
                escaped = False
            elif ch == '\\':
                escaped = True
            elif ch == '"':
                in_quote = False
            continue
        if ch == '#':
            break
        if ch == '"':
            in_quote = True
        elif ch in ('{', '}'):
            events.append((idx, ch))
    return events

def _format_missing_block_lines(path: tuple[str, ...],
                                section_overrides: dict[tuple[str, ...], dict[str, str]],
                                indent: str,
                                *, quoted_sections: bool = False,
                                add_version: bool = False) -> list[str]:
    """Build lines for an omitted section and any nested child sections."""
    name = path[-1]
    label = f'"{name}"' if quoted_sections else name
    lines = [f'{indent}{label}={{']
    child_indent = indent + '\t'
    for k, v in section_overrides.get(path, {}).items():
        lines.append(f'{child_indent}{k}={v}')
    if add_version and path in section_overrides:
        lines.append(f'{child_indent}version=0')
    for child_path in section_overrides:
        if len(child_path) == len(path) + 1 and child_path[:-1] == path:
            lines.extend(_format_missing_block_lines(
                child_path, section_overrides, child_indent,
                quoted_sections=quoted_sections, add_version=add_version))
    lines.append(f'{indent}}}')
    return lines

def _patch_clausewitz_sections(
    text: str,
    section_overrides: dict[tuple[str, ...], dict[str, str]],
    *,
    quoted_sections: bool = False,
    add_version_on_new: bool = False,
) -> str:
    """Patch key=value entries by nested Clausewitz section path, preserving formatting."""
    eol = '\r\n' if '\r\n' in text else '\n'
    had_trailing_nl = text.endswith('\n')
    lines = text.splitlines()

    stack: list[str | None] = []
    seen_sections: set[tuple[str, ...]] = {()}
    seen_keys: dict[tuple[str, ...], set[str]] = {p: set() for p in section_overrides}
    section_indent: dict[tuple[str, ...], str] = {}
    out_lines: list[str] = []

    def _emit_missing_for(closing_path: tuple[str, ...], indent_hint: str):
        if closing_path in section_overrides:
            for k, v in section_overrides[closing_path].items():
                if k not in seen_keys[closing_path]:
                    out_lines.append(f'{indent_hint}{k}={v}')
                    seen_keys[closing_path].add(k)
        for sub_path in section_overrides:
            if (len(sub_path) == len(closing_path) + 1
                    and sub_path[:-1] == closing_path
                    and sub_path not in seen_sections):
                out_lines.extend(_format_missing_block_lines(
                    sub_path, section_overrides, indent_hint,
                    quoted_sections=quoted_sections,
                    add_version=add_version_on_new))
                seen_sections.add(sub_path)
                for k in section_overrides[sub_path]:
                    seen_keys[sub_path].add(k)

    for line in lines:
        cur_path = tuple(s for s in stack if s is not None)
        is_exact_path = all(s is not None for s in stack)

        kv = _KV_RE.match(line)
        emitted_line = line
        if kv and is_exact_path and cur_path in section_overrides:
            indent, raw_key = kv.group(1), kv.group(2)
            key = kv.group(3) if kv.group(3) is not None else kv.group(4)
            section_indent.setdefault(cur_path, indent)
            if key in section_overrides[cur_path]:
                _, comment = _split_inline_comment(kv.group(5))
                new_val = section_overrides[cur_path][key]
                emitted_line = f'{indent}{raw_key}={new_val}{comment}'
                seen_keys[cur_path].add(key)

        braces = _scan_braces_outside_quotes(emitted_line)
        bm = _BLOCK_OPEN_RE.match(emitted_line)
        open_name = (bm.group(2) if bm.group(2) is not None else bm.group(3)) if bm else None
        open_idx = bm.end() - 1 if bm else -1

        # Update stack & handle closing braces for watched sections.
        if braces and emitted_line.lstrip().startswith('}') and braces[0][1] == '}':
            if is_exact_path and stack:
                closing_path = tuple(stack)
                child_ind = section_indent.get(closing_path, '\t' * len(closing_path))
                _emit_missing_for(closing_path, child_ind)
            out_lines.append(emitted_line)
            for idx, b in braces:
                if b == '}':
                    if stack:
                        stack.pop()
                else:
                    name = open_name if idx == open_idx else None
                    stack.append(name)
                    if name is not None and all(s is not None for s in stack):
                        seen_sections.add(tuple(stack))
        else:
            # Handle rare single-line `key=val}` closing a watched section.
            split_pos = None
            sim_stack = list(stack)
            for idx, b in braces:
                if b == '{':
                    name = open_name if idx == open_idx else None
                    sim_stack.append(name)
                else:
                    if sim_stack and all(s is not None for s in sim_stack):
                        cp = tuple(sim_stack)
                        has_child_missing = any(
                            len(sp) == len(cp) + 1 and sp[:-1] == cp and sp not in seen_sections
                            for sp in section_overrides
                        )
                        has_key_missing = (
                            cp in section_overrides
                            and any(k not in seen_keys[cp] for k in section_overrides[cp])
                        )
                        if has_child_missing or has_key_missing:
                            split_pos = idx
                            break
                    if sim_stack:
                        sim_stack.pop()

            if split_pos is not None:
                prefix = emitted_line[:split_pos].rstrip()
                suffix = emitted_line[split_pos:]
                if prefix:
                    out_lines.append(prefix)
                cp = tuple(s for s in stack if s is not None)
                child_ind = section_indent.get(cp, '\t' * len(cp))
                _emit_missing_for(cp, child_ind)
                out_lines.append(suffix)
            else:
                out_lines.append(emitted_line)

            for idx, b in braces:
                if b == '{':
                    name = open_name if idx == open_idx else None
                    stack.append(name)
                    if name is not None and all(s is not None for s in stack):
                        seen_sections.add(tuple(stack))
                elif stack:
                    stack.pop()

    _emit_missing_for((), '')
    if not out_lines:
        return ''
    return eol.join(out_lines) + (eol if had_trailing_nl else '')

def patch_settings_txt(text: str, *, autosave: str = 'MONTHLY',
                       binary_save: bool = False) -> str:
    """Apply zero-accuracy-loss fast-run overrides to settings.txt content."""
    autosave_norm = autosave.upper()
    if autosave_norm not in AUTOSAVE_INTERVALS:
        raise ValueError(f'autosave must be one of {AUTOSAVE_INTERVALS}, got {autosave!r}')
    overrides: dict[tuple[str, ...], dict[str, str]] = {
        (): {
            'autosave': f'"{autosave_norm}"',
            'save_as_binary': 'yes' if binary_save else 'no',
            'pause_on_popups': 'no',
            'outliner_open': 'no',
            'minimap_visible': 'no',
            'event_sounds': 'no',
            'telemetry_enabled': 'no',
        },
        ('graphics',): {
            'size': '{ x=1280 y=720 }',
            'max_refresh_rate': '0',
            'fullScreen': 'no',
            'borderless': 'no',
            'shadows': 'no',
            'disable_3d_model_viewer': 'yes',
            'multi_sampling': '0',
            'maxanisotropy': '0',
            'vsync': 'no',
        },
        ('mapRenderingOptions',): {
            'draw_water': 'no',
            'draw_trees': 'no',
            'draw_rivers': 'no',
            'draw_postfx': 'no',
            'draw_sky': 'no',
            'draw_bloom': 'no',
            'draw_tooltips': 'no',
            'draw_hires_terrain': 'no',
            'draw_citysprawl': 'no',
            'draw_shadows': 'no',
            'draw_weather_effects': 'no',
            'draw_water_reflections': 'no',
            'draw_units': 'no',
            'draw_buildings': 'no',
            'high_gfx_shaders': 'no',
            'draw_map_full_res': 'no',
        },
    }
    return _patch_clausewitz_sections(text, overrides)

def patch_pdx_settings_txt(text: str) -> str:
    """Disable VSync and switch to windowed 1280x720 in pdx_settings.txt."""
    overrides: dict[tuple[str, ...], dict[str, str]] = {
        ('Graphics',): {},
        ('Graphics', 'display_mode'): {'value': '"windowed"'},
        ('Graphics', 'vsync'): {'enabled': 'no'},
        ('Graphics', 'windowed_resolution'): {'value': '"1280x720"'},
    }
    return _patch_clausewitz_sections(
        text, overrides, quoted_sections=True, add_version_on_new=True)

def _atomic_write_text(path: str, content: str) -> None:
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='') as fh:
        fh.write(content)
    os.replace(tmp, path)

def apply_fast_profile(*, docs_dir: str = DOCS,
                       backup_dir: str = PROFILE_BACKUP_DIR,
                       autosave: str = 'MONTHLY',
                       binary_save: bool = False) -> dict:
    """Back up normal settings (idempotently) and write fast-run settings."""
    if _hoi4_pids():
        raise RuntimeError('hoi4.exe is running — stop the game before modifying profile settings')
    autosave_norm = autosave.upper()
    if autosave_norm not in AUTOSAVE_INTERVALS:
        raise ValueError(f'autosave must be one of {AUTOSAVE_INTERVALS}, got {autosave!r}')
    os.makedirs(backup_dir, exist_ok=True)
    patchers = [
        ('settings.txt', lambda t: patch_settings_txt(
            t, autosave=autosave_norm, binary_save=binary_save)),
        ('pdx_settings.txt', patch_pdx_settings_txt),
    ]
    backed_up: list[str] = []
    patched: list[str] = []
    for fname, patcher in patchers:
        src = os.path.join(docs_dir, fname)
        if not os.path.exists(src):
            continue
        bak = os.path.join(backup_dir, fname)
        if not os.path.exists(bak):
            shutil.copy2(src, bak)
            backed_up.append(fname)
        with open(src, 'r', encoding='utf-8', newline='') as fh:
            raw = fh.read()
        _atomic_write_text(src, patcher(raw))
        patched.append(fname)
    return {
        'backed_up': backed_up,
        'patched': patched,
        'backup_dir': backup_dir,
        'autosave': autosave_norm,
        'binary_save': bool(binary_save),
    }

def restore_profile(*, docs_dir: str = DOCS,
                    backup_dir: str = PROFILE_BACKUP_DIR) -> list[str]:
    """Restore original settings.txt / pdx_settings.txt from backup."""
    if _hoi4_pids():
        raise RuntimeError('hoi4.exe is running — stop the game before restoring profile settings')
    restored: list[str] = []
    for fname in ('settings.txt', 'pdx_settings.txt'):
        bak = os.path.join(backup_dir, fname)
        if not os.path.exists(bak):
            continue
        dst = os.path.join(docs_dir, fname)
        tmp = dst + '.tmp'
        shutil.copy2(bak, tmp)
        os.replace(tmp, dst)
        os.remove(bak)
        restored.append(fname)
    return restored

# ---------- fast launch & hotkeys ----------

def _validate_cli_token(val: str, name: str) -> str:
    if not isinstance(val, str) or not val:
        raise ValueError(f'{name} must be non-empty')
    if any(c.isspace() for c in val) or '-' in val:
        raise ValueError(
            f'{name} cannot contain whitespace or hyphens '
            f'(HOI4 CLI restriction): {val!r}')
    return val

def build_fast_args(*, hands_off: bool = True, debug: bool = False,
                    autosave: str = 'MONTHLY', start_save: str | None = None,
                    start_tag: str | None = None,
                    start_speed: int = 4) -> list[str]:
    """Build HOI4 command-line arguments for a fast test run."""
    autosave_norm = autosave.upper()
    if autosave_norm not in AUTOSAVE_INTERVALS:
        raise ValueError(f'autosave must be one of {AUTOSAVE_INTERVALS}, got {autosave!r}')
    if isinstance(start_speed, bool) or not isinstance(start_speed, int) or not (0 <= start_speed <= 4):
        raise ValueError(f'start_speed must be 0..4, got {start_speed!r}')
    args = ['-nolauncher', f'-autosave={autosave_norm}']
    if start_save:
        save_name = start_save[:-5] if start_save.lower().endswith('.hoi4') else start_save
        _validate_cli_token(save_name, 'start_save')
        args.extend([f'-start_save={save_name}', f'-start_speed={start_speed}'])
    else:
        if hands_off:
            args.extend(['-hands_off', f'-start_speed={start_speed}'])
        if start_tag:
            _validate_cli_token(start_tag, 'start_tag')
            args.append(f'-start_tag={start_tag}')
            if not hands_off:
                args.append(f'-start_speed={start_speed}')
    if debug:
        args.append('-debug')
    return args

def press_hotkey(mod_scan: int, key_scan: int) -> None:
    """Press and release a modifier+key chord via physical scan codes."""
    _send_input([
        _key_input(wScan=mod_scan, dwFlags=KEYEVENTF_SCANCODE),
        _key_input(wScan=key_scan, dwFlags=KEYEVENTF_SCANCODE),
        _key_input(wScan=key_scan, dwFlags=KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP),
        _key_input(wScan=mod_scan, dwFlags=KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP),
    ])

def toggle_nogui(pid=None) -> None:
    """Foreground hoi4.exe and send Ctrl+F9 to hide/show the GUI without -debug."""
    hwnd = game_window(pid)
    foreground(hwnd)
    time.sleep(0.15)
    press_hotkey(SCAN_LCTRL, SCAN_F9)
    print('toggled gui (Ctrl+F9)')

def fast_console(pid=None, *, observe: bool = True, smooth: bool = True,
                 nogui: bool = True) -> None:
    """Inject fast-observer console commands into a -debug session."""
    cmds = []
    if observe:
        cmds.append('observe')
    if smooth:
        cmds.append('debug_smooth')
    if nogui:
        cmds.append('debug_nogui')
    for cmd in cmds:
        console(cmd, pid=pid)

def launch_fast(*, hands_off: bool = True, debug: bool = False,
                autosave: str = 'MONTHLY', binary_save: bool = False,
                start_save: str | None = None, start_tag: str | None = None,
                start_speed: int = 4, apply_profile: bool = True,
                tune: bool = True, affinity_mask: int | None = None,
                auto_pcores: bool = True, tune_timeout: float = 15.0) -> dict:
    """Apply fast profile (reversible), launch hoi4.exe, and tune P-core scheduling."""
    if _hoi4_pids():
        raise RuntimeError('hoi4.exe is already running')
    args = build_fast_args(
        hands_off=hands_off, debug=debug, autosave=autosave,
        start_save=start_save, start_tag=start_tag, start_speed=start_speed,
    )
    profile = (apply_fast_profile(autosave=autosave, binary_save=binary_save)
               if apply_profile else None)
    try:
        proc = subprocess.Popen([EXE, *args], cwd=os.path.dirname(EXE))
    except BaseException:
        if profile is not None and profile.get('backed_up'):
            try:
                restore_profile(backup_dir=profile['backup_dir'])
            except OSError:
                pass
        raise

    target_pid = None
    tune_res = None
    if tune:
        deadline = time.monotonic() + tune_timeout
        while True:
            live = _hoi4_pids()
            if proc.pid in live:
                target_pid = proc.pid
                break
            if live:
                target_pid = sorted(live)[-1]
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(0.5)
        if target_pid is not None:
            tune_res = tune_process(
                target_pid, affinity_mask=affinity_mask, auto_pcores=auto_pcores)
        else:
            print('warning: timed out waiting for hoi4.exe PID to tune; run `tune` manually')
    return {
        'args': args,
        'profile': profile,
        'pid': target_pid if target_pid is not None else proc.pid,
        'tune': tune_res,
    }

# ---------- entry ----------

def _parse_affinity(text: str) -> int:
    val = int(text, 0)
    if val <= 0:
        raise argparse.ArgumentTypeError(f'affinity must be > 0, got {text!r}')
    return val

def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog='hoi4_spectate.py',
        description='HOI4 spectator & zero-accuracy-loss fast test-run harness.',
    )
    sub = ap.add_subparsers(dest='command')

    sub.add_parser('launch', help='start normal game (-debug -nolauncher)')
    sub.add_parser('wait', help='block until main menu ready (normal launch only)')
    sub.add_parser('snapshot', help='copy logs + latest save to _ctx/spectate/')
    sub.add_parser('status', help='one-line game state probe')

    p_con = sub.add_parser('console', help='send a console command (requires -debug)')
    p_con.add_argument('--pid', type=int, default=None, help='pin hoi4.exe PID')
    p_con.add_argument('tokens', nargs='*', help='console command words')

    p_fast = sub.add_parser(
        'fast',
        help='apply fast profile, launch -hands_off (or --start-save), and tune P-cores')
    p_fast.add_argument('--debug', action='store_true',
                        help='add -debug (needed for console commands like observe/debug_smooth)')
    p_fast.add_argument('--no-hands-off', action='store_true',
                        help='do not pass -hands_off (stops at menu or uses --start-tag)')
    p_fast.add_argument('--start-save', default=None,
                        help='load save directly (without .hoi4; omits -hands_off)')
    p_fast.add_argument('--start-tag', default=None,
                        help='country tag to start as (requires valid tag without spaces/hyphens)')
    p_fast.add_argument('--start-speed', type=int, default=4, choices=range(5),
                        help='initial speed 0..4 (4 = speed 5, default 4)')
    p_fast.add_argument('--autosave', default='MONTHLY', choices=AUTOSAVE_INTERVALS,
                        help='autosave cadence (default MONTHLY)')
    p_fast.add_argument('--binary-save', action='store_true',
                        help='set save_as_binary=yes in settings.txt (HOI4bin)')
    p_fast.add_argument('--no-profile', action='store_true',
                        help='do not patch settings.txt / pdx_settings.txt')
    p_fast.add_argument('--no-tune', action='store_true',
                        help='do not adjust CPU affinity / priority / EcoQoS')
    p_fast.add_argument('--affinity', type=_parse_affinity, default=None,
                        help='explicit CPU affinity mask (e.g. 0xFFFF)')
    p_fast.add_argument('--all-cores', action='store_true',
                        help='do not pin to P-cores automatically')

    p_pa = sub.add_parser('profile-apply',
                          help='back up and apply fast graphics/observer settings')
    p_pa.add_argument('--autosave', default='MONTHLY', choices=AUTOSAVE_INTERVALS)
    p_pa.add_argument('--binary-save', action='store_true')

    sub.add_parser('restore',
                   help='restore backed-up normal settings (hoi4.exe must not be running)')

    p_tune = sub.add_parser('tune',
                            help='pin running hoi4.exe to P-cores, High priority, disable EcoQoS')
    p_tune.add_argument('--pid', type=int, default=None)
    p_tune.add_argument('--affinity', type=_parse_affinity, default=None)
    p_tune.add_argument('--all-cores', action='store_true')
    p_tune.add_argument('--no-high-priority', action='store_true')
    p_tune.add_argument('--no-ecoqos', action='store_true')

    p_ng = sub.add_parser('nogui', help='foreground hoi4.exe and toggle Ctrl+F9')
    p_ng.add_argument('--pid', type=int, default=None)

    p_fc = sub.add_parser('fast-console',
                          help='inject observe + debug_smooth + debug_nogui (requires -debug)')
    p_fc.add_argument('--pid', type=int, default=None)
    p_fc.add_argument('--no-observe', action='store_true')
    p_fc.add_argument('--no-smooth', action='store_true')
    p_fc.add_argument('--no-nogui', action='store_true')

    return ap

def main(argv=None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if not raw_argv:
        status()
        return 0
    if raw_argv[0] == 'console' and '-h' not in raw_argv[1:] and '--help' not in raw_argv[1:]:
        tokens, pid = list(raw_argv[1:]), None
        if '--pid' in tokens:
            i = tokens.index('--pid')
            if i + 1 >= len(tokens):
                print('error: --pid requires a value', file=sys.stderr)
                return 2
            pid = int(tokens[i + 1])
            del tokens[i:i + 2]
        if not tokens:
            print('error: console requires a command string', file=sys.stderr)
            return 2
        try:
            console(' '.join(tokens), pid=pid)
        except RuntimeError as e:
            print(f'console failed: {e}')
            return 1
        return 0

    args = _build_parser().parse_args(raw_argv)
    cmd = args.command
    if cmd == 'launch':
        os.system(f'powershell -Command \'Start-Process "{EXE}" -ArgumentList "-debug","-nolauncher"\'')
        print('launched')
    elif cmd == 'wait':
        return 0 if wait_menu() else 1
    elif cmd == 'snapshot':
        snapshot()
    elif cmd == 'status':
        status()
    elif cmd == 'fast':
        try:
            res = launch_fast(
                hands_off=not args.no_hands_off,
                debug=args.debug,
                autosave=args.autosave,
                binary_save=args.binary_save,
                start_save=args.start_save,
                start_tag=args.start_tag,
                start_speed=args.start_speed,
                apply_profile=not args.no_profile,
                tune=not args.no_tune,
                affinity_mask=args.affinity,
                auto_pcores=not args.all_cores,
            )
            print('fast launched:', res)
        except (RuntimeError, ValueError, OSError) as e:
            print(f'fast launch failed: {e}', file=sys.stderr)
            return 1
    elif cmd == 'profile-apply':
        try:
            res = apply_fast_profile(
                autosave=args.autosave, binary_save=args.binary_save)
            print('profile applied:', res)
        except (RuntimeError, ValueError, OSError) as e:
            print(f'profile-apply failed: {e}', file=sys.stderr)
            return 1
    elif cmd == 'restore':
        try:
            res = restore_profile()
            print('restored:', res if res else 'none (no backup present)')
        except (RuntimeError, OSError) as e:
            print(f'restore failed: {e}', file=sys.stderr)
            return 1
    elif cmd == 'tune':
        try:
            pid = resolve_pid(args.pid)
            res = tune_process(
                pid,
                affinity_mask=args.affinity,
                auto_pcores=not args.all_cores,
                high_priority=not args.no_high_priority,
                disable_ecoqos=not args.no_ecoqos,
            )
            print('tuned:', res)
        except (RuntimeError, ValueError) as e:
            print(f'tune failed: {e}', file=sys.stderr)
            return 1
    elif cmd == 'nogui':
        try:
            toggle_nogui(pid=args.pid)
        except RuntimeError as e:
            print(f'nogui failed: {e}', file=sys.stderr)
            return 1
    elif cmd == 'fast-console':
        try:
            fast_console(
                pid=args.pid,
                observe=not args.no_observe,
                smooth=not args.no_smooth,
                nogui=not args.no_nogui,
            )
        except RuntimeError as e:
            print(f'fast-console failed: {e}', file=sys.stderr)
            return 1
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
