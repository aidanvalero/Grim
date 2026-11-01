# Two Grim jars entry by entry. Every class must be byte-identical. Text resources may differ only in CRLF vs LF (a
# Windows checkout with core.autocrlf packs CRLF, Linux packs LF), and the build metadata (version string in
# META-INF/MANIFEST.MF and plugin.yml, timestamp/branch/git name in grimac.properties) may differ. Anything else fails.
# 11-01: CI-built stock eb4c657 and PR #2789 vs the PC's jars, 6024/6024 classes identical, 112 text files CRLF only.
#   python3 jar_diff.py <a.jar> <b.jar>
import sys, zipfile

METADATA = {'META-INF/MANIFEST.MF', 'plugin.yml', 'grimac.properties'}
a, b = zipfile.ZipFile(sys.argv[1]), zipfile.ZipFile(sys.argv[2])
na = {n for n in a.namelist() if not n.endswith('/')}
nb = {n for n in b.namelist() if not n.endswith('/')}
classes = sum(n.endswith('.class') for n in na)
bad, crlf, meta = sorted(na ^ nb), 0, 0
for n in sorted(na & nb):
    x, y = a.read(n), b.read(n)
    if x == y:
        continue
    if n in METADATA:
        meta += 1
    elif not n.endswith('.class') and x.replace(b'\r\n', b'\n') == y.replace(b'\r\n', b'\n'):
        crlf += 1
    else:
        bad.append(n)
print(f'{classes} classes: {sum(n.endswith(".class") for n in bad)} differ; {crlf} text files differ only in line endings; '
      f'{meta} build-metadata files differ; {len(bad)} real differences')
for n in bad[:20]:
    print('  differs:', n)
sys.exit(1 if bad else 0)
