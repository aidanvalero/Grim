# Holds an old client's login until it has finished loading (11-07). Clients before 1.20 start connecting from --server
# while their resources are still loading. On a CI runner with software GL the join gets there first, and the first
# block level event kills them (1.14.4: TerrainParticle -> BlockModelShaper NPE, 1.18.1: ModelManager.modelGroups is
# null). On the PC the GPU loads in a second and the join comes after. This takes the client's connection, keeps it in
# the login phase with a login plugin request every 4 s (the client answers "not understood", which also keeps its 30 s
# read timeout from firing), and once the client's log shows the mob effect atlas, the last atlas a reload builds and
# after the block models, it waits 3 s more and hands the connection to the real server.
#   python3 -I hold_join.py <listen port> <server port> <client latest.log> [max hold seconds]
import re, socket, sys, threading, time

LISTEN, SERVER, LOG = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
MAX_HOLD = float(sys.argv[4]) if len(sys.argv) > 4 else 240
READY = re.compile(r'Created: .*mob_effect')
CHANNEL = b'rig:hold'


def varint(n):
    out = b''
    while True:
        b, n = n & 0x7F, n >> 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def read_exact(sock, n):
    data = b''
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError('client closed')
        data += chunk
    return data


def read_packet(sock):
    """one length-prefixed packet, returned as the raw bytes it came in as"""
    raw, n, shift = b'', 0, 0
    while True:
        b = read_exact(sock, 1)
        raw += b
        n |= (b[0] & 0x7F) << shift
        shift += 7
        if not b[0] & 0x80:
            return raw + read_exact(sock, n)


def loaded():
    try:
        return bool(READY.search(open(LOG, encoding='utf-8', errors='replace').read()))
    except OSError:
        return False


def pipe(src, dst):
    try:
        while True:
            b = src.recv(65536)
            if not b:
                break
            dst.sendall(b)
    except OSError:
        pass
    for s in (src, dst):
        try:
            s.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


srv = socket.socket()
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(('127.0.0.1', LISTEN))
srv.listen(1)
print(f'listening on {LISTEN} for {LOG}', flush=True)
client, _ = srv.accept()
t0 = time.time()
first = read_packet(client) + read_packet(client)       # handshake and login start, sent on as they came
requests, ready_at, why = 0, None, 'max hold'
client.settimeout(0.5)
while time.time() - t0 < MAX_HOLD:
    if ready_at is None and loaded():
        ready_at = time.time()
    if ready_at is not None and time.time() - ready_at >= 3:
        why = 'loaded'
        break
    # login plugin request, packet 0x04 in the login state from 1.13 through 1.20.1
    requests += 1
    body = varint(0x04) + varint(requests) + varint(len(CHANNEL)) + CHANNEL
    client.sendall(varint(len(body)) + body)
    until = time.time() + 4
    while time.time() < until:                           # the answers are only for us, drop them
        try:
            if not client.recv(4096):
                sys.exit(f'client left after {time.time() - t0:.1f}s')
        except socket.timeout:
            pass
client.settimeout(1.5)                                   # an answer still on its way mustn't reach the server
try:
    while client.recv(4096):
        pass
except socket.timeout:
    pass
server = socket.create_connection(('127.0.0.1', SERVER))
server.sendall(first)
client.settimeout(None)
print(f'released after {time.time() - t0:.1f}s ({why}), {requests} plugin requests', flush=True)
threading.Thread(target=pipe, args=(client, server), daemon=True).start()
pipe(server, client)
