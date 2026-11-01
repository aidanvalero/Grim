# Fails a CI shard whose results could carry a client hitch. Every scored scenario (warm-up trips excluded) that
# tripped a Timer check must have been voided and rerun by fuzz.play (FUZZ_RETRY_TIMER); on the PC every scored run
# with Timer next to a real flag in October's suites was a false flag (pr2312-final: 6 runs, client 3, 0/48 on rerun).
#   python3 ci_gate.py <out dir>
import json, os, re, sys

out = sys.argv[1]
ANSI = re.compile(r'\x1b\[[0-9;]*m')
MARK = re.compile(r'\[Rcon\] FUZZ c(\d+) s(\d+) (start|end)')
FLAG = re.compile(r'AidanFuzz(\d+) failed (\w+)')
TIMER = {'Timer', 'TimerLimit', 'NegativeTimer'}

voided = set()
if os.path.exists(os.path.join(out, 'voided.jsonl')):
    for line in open(os.path.join(out, 'voided.jsonl')):
        v = json.loads(line)
        voided.add((str(v['client']), str(v['marker'])))
open_, seen, scored, timer, kept = {}, {}, 0, 0, []
for line in open(os.path.join(out, 'console.log'), encoding='utf-8', errors='replace'):
    line = ANSI.sub('', line)
    if (m := MARK.search(line)):
        c, s = m.group(1), m.group(2)
        if int(s) == int(c) * 1_000_000 - 1:   # fuzz.py's unscored warm-up trip
            continue
        if m.group(3) == 'start':
            open_[c], seen[c] = s, set()
        elif c in open_:
            flags = seen.pop(c)
            open_.pop(c)
            scored += 1
            if flags & TIMER:
                timer += 1
                if (c, s) not in voided:
                    kept.append(f'client {c} marker {s}: {sorted(flags)}')
    elif (f := FLAG.search(line)) and f.group(1) in open_:
        seen[f.group(1)].add(f.group(2))
print(f'{scored} scored scenarios, {timer} tripped Timer ({timer / max(1, scored):.1%}), all but {len(kept)} voided and rerun')
for k in kept:
    print(f'  kept a hitched run: {k}')
sys.exit(1 if kept else 0)
