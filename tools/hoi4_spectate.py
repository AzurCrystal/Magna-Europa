#!/usr/bin/env python
"""HOI4 spectator harness — "AI window" into a running game.

Flow:
  1. launch hoi4.exe -debug -nolauncher (mod must be enabled in dlc_load.json)
  2. wait for main menu (error.log stops growing / system.log markers)
  3. inject `observe` via console (ctypes SendInput, UTF-16) → AI plays alone
  4. watch logs/, harvest autosaves, parse key state

Requires: Windows + steam running + mod already enabled.

Usage:
  python tools/hoi4_spectate.py launch          # start game
  python tools/hoi4_spectate.py wait            # block until menu ready
  python tools/hoi4_spectate.py console observe # send a console command
  python tools/hoi4_spectate.py snapshot        # copy logs+latest save to _ctx/spectate/
  python tools/hoi4_spectate.py status          # one-line game state probe
"""
import ctypes, os, sys, time, glob, re, shutil, struct, zlib, json

DOCS = os.path.expanduser('~/Documents/Paradox Interactive/Hearts of Iron IV')
LOGS = os.path.join(DOCS, 'logs')
SAVES = os.path.join(DOCS, 'save games')
EXE = r'C:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV\hoi4.exe'
OUT = '_ctx/spectate'

# ---------- console input ----------

def _send_unicode(text):
    """type `text` into the focused window via SendInput UTF-16."""
    inp = (ctypes.c_ushort * len(text))()
    for i, ch in enumerate(text):
        pass  # placeholder — build INPUT structs below
    # Use keybd_event per char is too slow; SendInput with UNICODE events.
    user32 = ctypes.windll.user32
    user32.SendInput.argtypes = (ctypes.c_uint, ctypes.c_void_p, ctypes.c_int)
    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [('wVk', ctypes.c_ushort), ('wScan', ctypes.c_ushort),
                    ('dwFlags', ctypes.c_ulong), ('time', ctypes.c_ulong),
                    ('dwExtraInfo', ctypes.c_ulonglong)]
    class INPUT(ctypes.Structure):
        _fields_ = [('type', ctypes.c_ulong), ('ki', KEYBDINPUT)]
    evts = []
    for ch in text:
        code = ord(ch)
        for flag in (0, 2):  # KEYDOWN, KEYUP
            ki = KEYBDINPUT(0, code, 4 | flag, 0, 0)  # KEYEVENTF_UNICODE=4
            evts.append(INPUT(1, ki))
    arr = (INPUT * len(evts))(*evts)
    user32.SendInput(len(evts), arr, ctypes.sizeof(INPUT))

def _press_vk(vk):
    user32 = ctypes.windll.user32
    user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.03)
    user32.keybd_event(vk, 0, 2, 0)

def console(cmd):
    """open console, type cmd, Enter, close."""
    _press_vk(0xC0)  # VK_OEM_3 (`~`)
    time.sleep(0.4)
    _send_unicode(cmd)
    time.sleep(0.1)
    _press_vk(0x0D)  # Enter
    time.sleep(0.3)
    _press_vk(0x1B)  # Esc — close console
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

# ---------- entry ----------

if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'status'
    if cmd == 'launch':
        os.system(f'powershell -Command \'Start-Process "{EXE}" -ArgumentList "-debug","-nolauncher"\'')
        print('launched')
    elif cmd == 'wait':
        sys.exit(0 if wait_menu() else 1)
    elif cmd == 'console':
        console(' '.join(sys.argv[2:]))
    elif cmd == 'snapshot':
        snapshot()
    elif cmd == 'status':
        status()
