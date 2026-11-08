# Records a client's whole session at the wire for headless replay (11-07, #2158). Sits between one client and the
# server like hold_join.py, forwards every byte untouched, and splits both directions into packets: length-prefixed
# frames, zlib-decompressed once the server's Set Compression (login packet 0x03, the only thing that changes framing)
# has gone by. Offline mode, so nothing is encrypted. One JSON line per packet: ms since the client connected, c2s or
# s2c, and the packet (VarInt id + payload) base64'd. The replay works out the protocol state from the packets itself.
#   python -I fuzz/record_relay.py <listen port> <server port> <out .jsonl>
import base64, json, socket, sys, threading, time, zlib

LISTEN, SERVER, OUT = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
lock = threading.Lock()
state = {'threshold': None}        # compression threshold once the server turned it on


def read_varint(buf, i):
    n = shift = 0
    while True:
        if i >= len(buf):
            return None, i
        b = buf[i]
        i += 1
        n |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return n, i


class Splitter:
    def __init__(self, direction, out, t0):
        self.direction, self.out, self.t0, self.buf = direction, out, t0, b''

    def feed(self, data):
        self.buf += data
        while True:
            length, i = read_varint(self.buf, 0)
            if length is None or len(self.buf) < i + length:
                return
            frame, self.buf = self.buf[i:i + length], self.buf[i + length:]
            if state['threshold'] is None:
                packet = frame
            else:
                data_len, j = read_varint(frame, 0)
                packet = frame[j:] if data_len == 0 else zlib.decompress(frame[j:])
            pid, k = read_varint(packet, 0)
            if self.direction == 's2c' and state['threshold'] is None and pid == 0x03:
                state['threshold'] = read_varint(packet, k)[0]       # Set Compression: framing changes after it
            with lock:
                self.out.write(json.dumps({'t': round((time.time() - self.t0) * 1000, 1), 'd': self.direction,
                                           'p': base64.b64encode(packet).decode()}) + '\n')


def pump(src, dst, splitter):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
            splitter.feed(data)
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
print(f'recording {LISTEN} -> {SERVER} into {OUT}', flush=True)
client, _ = srv.accept()
server = socket.create_connection(('127.0.0.1', SERVER))
t0 = time.time()
with open(OUT, 'w') as out:
    out.write(json.dumps({'recording': 1, 'server_port': SERVER, 'started': time.strftime('%Y-%m-%d %H:%M:%S')}) + '\n')
    up = threading.Thread(target=pump, args=(client, server, Splitter('c2s', out, t0)), daemon=True)
    up.start()
    pump(server, client, Splitter('s2c', out, t0))
    up.join(timeout=2)
print(f'session over after {time.time() - t0:.1f}s, compression threshold {state["threshold"]}', flush=True)
