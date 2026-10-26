"""UDP helper for the forged-source test E22 (tests/egress/layer3.sh).

udp.py serve <addr> <port> <log>           answer every datagram with "pong", log its payload
udp.py send <dst> <port> <sport> <text>    send from a fixed source port, print the reply
udp.py forge <src> <sport> <dst> <port> <text>
                                           raw IPv4/UDP packet with another container's source
"""

import socket
import struct
import sys


def serve(addr: str, port: str, log: str) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind((addr, int(port)))
    while True:
        data, peer = s.recvfrom(512)
        with open(log, "a") as f:
            f.write(data.decode(errors="replace") + "\n")
        s.sendto(b"pong", peer)


def send(dst: str, port: str, sport: str, text: str) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", int(sport)))  # noqa: S104 - a sending socket; only fixes the source port
    s.settimeout(5)
    s.sendto(text.encode(), (dst, int(port)))
    print(s.recvfrom(512)[0].decode())


def forge(src: str, sport: str, dst: str, port: str, text: str) -> None:
    data = text.encode()
    # UDP checksum 0 means "none" for IPv4; the kernel fills in the IP checksum and packet id
    udp = struct.pack("!HHHH", int(sport), int(port), 8 + len(data), 0) + data
    ip = struct.pack(
        "!BBHHHBBH4s4s",
        0x45, 0, 20 + len(udp), 0, 0, 64, socket.IPPROTO_UDP, 0,
        socket.inet_aton(src), socket.inet_aton(dst),
    )  # fmt: skip
    s = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_RAW)
    s.sendto(ip + udp, (dst, 0))


if __name__ == "__main__":
    command, args = sys.argv[1], sys.argv[2:]
    if command == "serve":
        serve(*args)
    elif command == "send":
        send(*args)
    elif command == "forge":
        forge(*args)
    else:
        sys.exit(__doc__)
