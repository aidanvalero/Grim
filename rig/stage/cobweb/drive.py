# Repro driver for issue #2867 (jumping under a cobweb + tapping crouch) on the real vanilla 26.3 client.
# Key presses go through KeyDriver.java, which sets the client's own jump/crouch key flags once per tick
# over JDWP (works with the PC locked, tick-exact). Phase markers go into the server log via RCON `say`,
# and Grim's flags are counted per phase from that log.
#
#   python -I drive.py setup            build the test spot (web 2 blocks over the floor)
#   python -I drive.py probe            check a scripted jump actually moves the player
#   python -I drive.py trial NAME...    run named patterns (see PATTERNS) and count flags
import os, re, socket, struct, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRATCH = os.path.dirname(HERE)
SERVER_LOG = os.path.join(SCRATCH, os.environ.get('REPRO_SERVER', 'repro263'), 'console.log')
NAMES = os.environ.get('NAMES')   # e.g. names-1.21.11.properties for an obfuscated client
JAVA = os.path.join(SCRATCH, 'jdk25', 'jdk-25.0.4.1+1', 'bin', 'java.exe')
PLAYER = 'AidanRepro'
SPOT = '6.5 -60 6.5'         # cobweb at (6, -58, 6): a standing jump puts the head in it, a crouched one doesn't
SPOT = os.environ.get('SPOT_XYZ', SPOT)   # e.g. the low-ceiling spot
if os.environ.get('CORNER'):
    # the reporter's exact offsets (x 59.30000001192093, z 499.69999998807907): pressed into the corner
    # formed by a wall to the west (x=5) and one to the south (z=7) of the web column
    SPOT = '6.30000001192093 -60 6.69999998807907'
OPEN_SPOT = '9.5 -60 9.5'    # no web, for the probe
CYCLE = 90                   # ticks per jump: the web holds the head up for ~3.5 s
# YOFF=160 lifts every spot from y=-60 to y=100: 1.8 clients only know heights 0-255, so below 0 they
# stand in unloaded space and never move. Applied to tp/fill/setblock/execute-if-block commands.
YOFF = int(os.environ.get('YOFF', '0'))

def shift(cmd):
    if not YOFF: return cmd
    p = cmd.split()
    def up(i): p[i] = f'{float(p[i]) + YOFF:g}' if '.' in p[i] else str(int(p[i]) + YOFF)
    if p[0] == 'tp' and len(p) >= 5: up(3)
    elif p[0] == 'setblock': up(2)
    elif p[0] == 'fill': up(2); up(5)
    elif p[:3] == ['execute', 'if', 'block']: up(4)
    return ' '.join(p)

class Rcon:
    def __init__(self):
        self.s = socket.create_connection(('127.0.0.1', 25598), timeout=10)
        self.s.sendall(self._pkt(1, 3, 'repro')); self.s.recv(4096)
    @staticmethod
    def _pkt(i, t, s):
        b = s.encode() + b'\x00\x00'
        return struct.pack('<iii', len(b) + 8, i, t) + b
    def __call__(self, cmd):
        cmd = shift(cmd)
        self.s.sendall(self._pkt(2, 2, cmd))
        return self.s.recv(65536)[12:-2].decode('utf-8', 'replace')

class Schedule:
    def __init__(self): self.t, self.lines = 0, []
    def at(self, t, action, arg=''): self.lines.append((t, f'{t} {action} {arg}'.strip()))
    def rcon(self, cmd): self.at(self.t, 'rcon', shift(cmd))
    def wait(self, n): self.t += n
    def tap(self, key, hold, gap=0):
        self.at(self.t, 'down', key); self.at(self.t + hold, 'up', key); self.t += hold + gap
    def jump_cycle(self, crouch_after=None, taps=2, hold=2, gap=3, crouch_before=False):
        start = self.t
        if crouch_before:
            for _ in range(taps): self.tap('sneak', hold, gap)
        self.tap('jump', 3)
        if crouch_after is not None:
            self.wait(crouch_after)
            for _ in range(taps): self.tap('sneak', hold, gap)
        self.t = max(start + CYCLE, self.t + 10)
    def plain(self, n):
        for _ in range(n): self.jump_cycle()
    def held_jump_taps(self, offset, hold, cycles=12):
        # the reporter's log: jump held the whole time (they re-jump the tick after landing, every 10 ticks)
        # and crouch tapped `offset` ticks into each jump for `hold` ticks
        start = self.t
        self.at(start, 'down', 'jump')
        for k in range(cycles):
            self.at(start + offset + 10 * k, 'down', 'sneak')
            self.at(start + offset + hold + 10 * k, 'up', 'sneak')
        self.at(start + 10 * cycles, 'up', 'jump')
        self.t = start + 10 * cycles + 80
    def release_then_jump(self, hold, gap):
        # on the ground: hold crouch, let go, jump `gap` ticks later (the reporter's log has the jump right after a release)
        start = self.t
        self.tap('sneak', hold, gap)
        self.tap('jump', 3)
        self.t = max(start + CYCLE, self.t + 10)
    def write(self, path):
        self.at(self.t + 20, 'end')
        with open(path, 'w') as f:
            f.write('\n'.join(line for _, line in sorted(self.lines, key=lambda x: x[0])) + '\n')

PATTERNS = {
    'baseline':    lambda s: s.plain(4),
    'report':      lambda s: [s.jump_cycle(crouch_after=4) for _ in range(3)],
    'ground-taps': lambda s: [s.jump_cycle(crouch_before=True) for _ in range(3)],
    'hang-taps':   lambda s: [s.jump_cycle(crouch_after=20) for _ in range(3)],
    'late-taps':   lambda s: [s.jump_cycle(crouch_after=50) for _ in range(3)],
    'fast-taps':   lambda s: [s.jump_cycle(crouch_after=2, taps=4, hold=1, gap=1) for _ in range(3)],
}
for _gap in range(4):
    PATTERNS[f'release-jump-{_gap}'] = lambda s, g=_gap: [s.release_then_jump(hold=4, gap=g) for _ in range(3)]
for _o, _h in [(3, 2), (2, 2), (4, 2), (3, 1), (3, 3), (1, 2), (5, 2)]:
    PATTERNS[f'held-o{_o}h{_h}'] = lambda s, o=_o, h=_h: s.held_jump_taps(o, h)
PATTERNS['held-no-crouch'] = lambda s: s.held_jump_taps(0, 0)
def _after(s, wait, hold=4, gap=2):
    # wait `wait` ticks after the teleport, hold crouch `hold` ticks, release, jump `gap` ticks later.
    # Standing still, the client only sends a movement packet every 20 ticks, so `wait` sets where that
    # heartbeat lands relative to the crouch window.
    start = s.t
    s.wait(wait); s.tap('sneak', hold, gap); s.tap('jump', 3)
    s.t = max(start + 120, s.t + 10)
for _w in range(10, 29):
    PATTERNS[f'wait{_w}'] = lambda s, w=_w: _after(s, w)

def _anchor(s, d):
    # Jump once so the landing sets a known heartbeat (the client's next movement packet comes 20 ticks
    # after its last one), then hold crouch 4 ticks starting `d` ticks after that jump, let go, jump again
    start = s.t
    s.tap('jump', 3)
    s.t = start + d
    s.tap('sneak', 4, 1)
    s.tap('jump', 3)
    s.t = max(start + 80, s.t + 10)
for _d in range(12, 31):
    PATTERNS[f'anchor{_d}'] = lambda s, d=_d: _anchor(s, d)

def _control(s):
    # positive control: two unpredictable upward shoves, 3 s apart; Grim must flag both
    for _ in range(2):
        s.at(s.t, 'kick', '0.8'); s.wait(60)
PATTERNS['positive-control'] = _control
NO_FOLLOWUP = {'baseline', 'positive-control'} | {f'wait{w}' for w in range(10, 29)} | {f'anchor{d}' for d in range(12, 31)} | {f'release-jump-{g}' for g in range(4)} | {n for n in PATTERNS if n.startswith('held-')}

def run_driver(schedule_path):
    extra = [os.path.join(HERE, NAMES)] if NAMES else []
    return subprocess.Popen([JAVA, '-cp', HERE, 'KeyDriver', schedule_path] + extra, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)

# The console writes "»" as one cp1252 byte, which a utf-8 read turns into U+FFFD, so match any separator
FLAG = re.compile(r'Grim \S+ ' + PLAYER + r' failed (\w+) \(x(\d+)\) ?(\S*)')
MARK = re.compile(r'\[Rcon\] (PHASE \S+)')

ANSI = re.compile(r'\x1b\[[0-9;]*m')

def report(text):
    # Paper's console colours the alert text; without stripping, the pattern never matches (counted 0 on 1.21.11)
    text = ANSI.sub('', text)
    phase, out = 'before-first-marker', {}
    for line in text.splitlines():
        if m := MARK.search(line): phase = m.group(1); out.setdefault(phase, []); continue
        if m := FLAG.search(line): out.setdefault(phase, []).append((m.group(1), m.group(3)))
    for phase, flags in out.items():
        kinds = {}
        for check, _ in flags: kinds[check] = kinds.get(check, 0) + 1
        offs = [float(o) for c, o in flags if c == 'Simulation' and re.match(r'^\d*\.\d+$', o or '')]
        print(f'  {phase:<36} flags: {len(flags):>3}  {kinds or ""}' + (f'  biggest Simulation offset: {max(offs):.4f}' if offs else ''))

def main():
    rc = Rcon()
    cmd = sys.argv[1]
    if cmd == 'setup':
        for c in ['forceload add 0 0 15 15', 'difficulty peaceful'] + (['fill 0 -61 0 15 -61 15 minecraft:grass_block'] if YOFF else []) + [
                  'fill 4 -60 4 11 -54 11 minecraft:air', 'setblock 6 -58 6 minecraft:cobweb',
                  f'gamemode survival {PLAYER}', f'effect clear {PLAYER}',
                  'execute if block 6 -61 6 minecraft:grass_block', 'execute if block 6 -58 6 minecraft:cobweb',
                  'execute if block 6 -59 6 minecraft:air', 'execute if block 9 -58 9 minecraft:air']:
            print(f'> {c}: {rc(c)}')
        return
    sched_path = os.path.join(HERE, 'schedule.txt')
    s = Schedule()
    if cmd == 'trace-pattern':
        # server-side heights during a pattern, tagged like the reporter's log: J jump, s web-slowed, . free air, L landed
        rc(f'tp {PLAYER} {SPOT} 0 0'); time.sleep(2)
        s.wait(10); PATTERNS[sys.argv[2]](s)
        s.write(sched_path)
        p = run_driver(sched_path)
        ys = []
        while p.poll() is None:
            if m := re.search(r'(-?\d+\.\d+)d', rc(f'data get entity {PLAYER} Pos[1]')):
                y = float(m.group(1))
                if not ys or y != ys[-1]: ys.append(y)
        tags = ''
        for a, b in zip(ys, ys[1:]):
            d = b - a
            tags += 'J' if d > 0.3 else ('L' if b == -60.0 + YOFF else ('s' if abs(d) < 0.02 else '.'))
        print(p.stdout.read().strip())
        print('height changes:', len(ys) - 1)
        print(' '.join(tags[i:i + 20] for i in range(0, len(tags), 20)))
        return
    if cmd in ('probe', 'trace'):
        rc(f'tp {PLAYER} {OPEN_SPOT if cmd == "probe" else SPOT} 0 0'); time.sleep(2)
        s.wait(20); s.tap('jump', 3); s.wait(30 if cmd == 'probe' else 90)
        s.write(sched_path)
        p = run_driver(sched_path)
        ys, t0 = [], time.time()
        while p.poll() is None and time.time() - t0 < 20:
            if m := re.search(r'(-?\d+\.\d+)d', rc(f'data get entity {PLAYER} Pos[1]')): ys.append((time.time() - t0, float(m.group(1))))
        print(p.stdout.read().strip())
        heights = [y for _, y in ys]
        print(f'{cmd}: {len(ys)} height samples, lowest {min(heights)}, highest {max(heights)}')
        if cmd == 'probe':
            print('jump registered' if max(heights) > -59.9 + YOFF else 'JUMP DID NOT REGISTER')
        else:
            off_ground = [t for t, y in ys if y > -59.999 + YOFF]
            print(f'off the ground for {off_ground[-1] - off_ground[0]:.2f}s' if off_ground else 'never left the ground')
        return
    if cmd == 'trial':
        for name in sys.argv[2:]:
            s.rcon(f'tp {PLAYER} {SPOT} 0 0'); s.wait(40)
            s.rcon(f'say PHASE {name}'); s.wait(2)
            PATTERNS[name](s)
            if name not in NO_FOLLOWUP:
                s.rcon(f'say PHASE {name}-then-plain-jumps'); s.wait(2)
                s.plain(4)
        s.write(sched_path)
        pos = os.path.getsize(SERVER_LOG)
        print(f'{time.strftime("%H:%M:%S")} running {" ".join(sys.argv[2:])}: {s.t} ticks (~{s.t / 20:.0f}s)', flush=True)
        p = run_driver(sched_path)
        print(p.communicate()[0].strip())
        time.sleep(2)
        with open(SERVER_LOG, encoding='utf-8', errors='replace') as f:
            f.seek(pos); text = f.read()
        print('flags per phase:')
        report(text)

if __name__ == '__main__':
    main()
