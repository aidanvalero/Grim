# Per-process CPU (cores) and RSS for the rig's processes over a 20 s window, from /proc
import os, time


def snap():
    out = {}
    for p in os.listdir('/proc'):
        if not p.isdigit():
            continue
        try:
            cmd = open(f'/proc/{p}/cmdline', 'rb').read().replace(b'\0', b' ').decode(errors='replace')
            st = open(f'/proc/{p}/stat').read().rsplit(')', 1)[1].split()
            rss = int(open(f'/proc/{p}/statm').read().split()[1]) * 4096
        except OSError:
            continue
        if 'java' in cmd or 'Xvfb' in cmd:
            out[p] = (int(st[11]) + int(st[12]), rss, cmd)
    return out


def kind(cmd):
    if 'paper.jar' in cmd:
        return 'server'
    if 'AidanFuzz' in cmd:
        return 'client' + cmd.split('AidanFuzz')[1][:1]
    if 'KeyDriver' in cmd:
        return 'keydriver'
    if 'Xvfb' in cmd:
        return 'xvfb'
    return 'other'


a = snap()
time.sleep(20)
b = snap()
total = 0.0
for p, (t, rss, cmd) in b.items():
    if p in a:
        cores = (t - a[p][0]) / os.sysconf('SC_CLK_TCK') / 20
        total += cores
        print(f'{kind(cmd):10} {cores:5.2f} cores  rss {rss / 2**20:6.0f} MB')
print(f'total      {total:5.2f} cores of {os.cpu_count()} visible')
