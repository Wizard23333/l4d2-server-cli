"""Bounded Source A2S_INFO query with challenge and split-packet support."""
from __future__ import annotations

import bz2
import socket
import struct
import time
import zlib

from .common import CliError

SINGLE = b"\xff\xff\xff\xff"
SPLIT = b"\xfe\xff\xff\xff"
REQUEST = SINGLE + b"TSource Engine Query\x00"
MAX_RESPONSE = 1024 * 1024


class Cursor:
    def __init__(self, data: bytes):
        self.data, self.offset = data, 0

    def take(self, length: int) -> bytes:
        if self.offset + length > len(self.data):
            raise ValueError("Truncated A2S response")
        value = self.data[self.offset:self.offset + length]
        self.offset += length
        return value

    def number(self, fmt: str) -> int:
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0]

    def string(self) -> str:
        end = self.data.find(b"\x00", self.offset)
        if end < 0:
            raise ValueError("Unterminated A2S string")
        value = self.data[self.offset:end].decode("utf-8", errors="replace")
        self.offset = end + 1
        return value


def parse_info(packet: bytes) -> dict:
    if packet[:5] != SINGLE + b"I":
        raise ValueError("Not a Source A2S_INFO packet")
    cursor = Cursor(packet[5:])
    protocol = cursor.number("<B")
    name, map_name, folder, game = [cursor.string() for _ in range(4)]
    app_id = cursor.number("<H")
    players, max_players, bots = [cursor.number("<B") for _ in range(3)]
    server_type, environment = cursor.take(1).decode(errors="replace"), cursor.take(1).decode(errors="replace")
    password_required, vac = bool(cursor.number("<B")), bool(cursor.number("<B"))
    version = cursor.string()
    info = {"protocol": protocol, "name": name, "map": map_name, "folder": folder,
            "game": game, "app_id": app_id, "players": players, "bots": bots,
            "human_players": max(0, players - bots), "max_players": max_players,
            "server_type": server_type, "environment": environment,
            "password_required": password_required, "vac": vac, "version": version}
    if cursor.offset < len(cursor.data):
        flags = cursor.number("<B")
        if flags & 0x80:
            info["port"] = cursor.number("<H")
        if flags & 0x10:
            info["steam_id"] = cursor.number("<Q")
        if flags & 0x40:
            cursor.number("<H")
            cursor.string()
        if flags & 0x20:
            info["keywords"] = cursor.string()
        if flags & 0x01:
            info["game_id"] = cursor.number("<Q")
    if folder.lower() != "left4dead2" or app_id != 550:
        raise ValueError("Query endpoint is not a Left 4 Dead 2 server")
    return info


def query(host: str, port: int, timeout: float = 3) -> dict:
    deadline = time.monotonic() + timeout
    pieces: dict[int, bytes] = {}
    expected_id, expected_total, compressed = None, None, False
    unpacked_size, expected_crc = None, None
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect((host, port))
        sock.send(REQUEST)
        challenges = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CliError("A2S query timed out.", "query_timeout", 124)
            sock.settimeout(remaining)
            try:
                packet = sock.recv(65536)
            except (TimeoutError, OSError) as exc:
                raise CliError(f"A2S query unavailable: {exc}", "query_unavailable") from exc
            try:
                if packet[:5] == SINGLE + b"A":
                    if len(packet) != 9 or challenges >= 2:
                        raise ValueError("Invalid or repeated challenge")
                    challenges += 1
                    sock.send(REQUEST + packet[5:9])
                    continue
                if packet[:4] == SPLIT:
                    if len(packet) < 12:
                        raise ValueError("Short split packet")
                    request_id, total, index, _size = struct.unpack("<IBBH", packet[4:12])
                    if not 1 <= total <= 64 or index >= total:
                        raise ValueError("Invalid split packet index")
                    if expected_id is None:
                        expected_id, expected_total = request_id, total
                        compressed = bool(request_id & 0x80000000)
                    if request_id != expected_id or total != expected_total:
                        raise ValueError("Mixed split responses")
                    content = packet[12:]
                    if compressed and index == 0:
                        if len(content) < 8:
                            raise ValueError("Missing compression metadata")
                        unpacked_size, expected_crc = struct.unpack("<II", content[:8])
                        content = content[8:]
                    pieces[index] = content
                    if sum(map(len, pieces.values())) > MAX_RESPONSE:
                        raise ValueError("Query response too large")
                    if len(pieces) != total:
                        continue
                    packet = b"".join(pieces[number] for number in range(total))
                    if compressed:
                        if unpacked_size is None or unpacked_size > MAX_RESPONSE:
                            raise ValueError("Invalid compressed length")
                        decoder = bz2.BZ2Decompressor()
                        packet = decoder.decompress(packet, max_length=MAX_RESPONSE + 1)
                        if not decoder.eof or len(packet) != unpacked_size or zlib.crc32(packet) != expected_crc:
                            raise ValueError("Invalid compressed payload")
                    if not packet.startswith(SINGLE):
                        packet = SINGLE + packet
                return parse_info(packet)
            except (ValueError, struct.error, OSError, EOFError) as exc:
                raise CliError(f"Invalid A2S response: {exc}", "invalid_query_response") from exc
