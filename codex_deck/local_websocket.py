"""Bounded RFC 6455 framing for the native TUI on a private Unix socket.

Codex's unix:// transport uses WebSocket, while app-server stdio uses JSONL.
This module implements only the server side needed by that local relay.
"""
import base64
import hashlib
import struct


MAX_MESSAGE = 16_000_000


def encode_frame(payload, opcode=1):
    size = len(payload)
    header = bytes([0x80 | opcode])
    if size < 126:
        return header + bytes([size]) + payload
    if size < 65536:
        return header + bytes([126]) + struct.pack("!H", size) + payload
    return header + bytes([127]) + struct.pack("!Q", size) + payload


def accept_handshake(connection):
    data = bytearray()
    connection.settimeout(10)
    while b"\r\n\r\n" not in data:
        part = connection.recv(4096)
        if not part:
            raise ConnectionError("Codex 连接在握手时关闭")
        data.extend(part)
        if len(data) > 16384:
            raise ValueError("WebSocket 握手过大")
    headers, remainder = bytes(data).split(b"\r\n\r\n", 1)
    lines = headers.decode("ascii").split("\r\n")
    fields = {}
    for line in lines[1:]:
        key, value = line.split(":", 1)
        fields[key.strip().lower()] = value.strip()
    if (not lines[0].startswith("GET ") or fields.get("upgrade", "").lower() != "websocket"
        or fields.get("sec-websocket-version") != "13" or "origin" in fields):
        raise ValueError("无效的本机 Codex WebSocket 握手")
    key = fields.get("sec-websocket-key", "")
    if len(base64.b64decode(key, validate=True)) != 16:
        raise ValueError("无效的 WebSocket 密钥")
    digest = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
    connection.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                        "Connection: Upgrade\r\nSec-WebSocket-Accept: " + digest + "\r\n\r\n").encode())
    connection.setblocking(False)
    return remainder


class Decoder:
    def __init__(self):
        self.buffer = bytearray()
        self.fragments = bytearray()
        self.fragmented = False
        self.closed = False

    def feed(self, data):
        self.buffer.extend(data)
        messages, replies = [], bytearray()
        if len(self.buffer) > MAX_MESSAGE + 14:
            raise ValueError("WebSocket 缓冲区过大")
        while len(self.buffer) >= 2:
            first, second = self.buffer[:2]
            final, opcode = bool(first & 128), first & 15
            if first & 112 or not second & 128:
                raise ValueError("无效的 WebSocket 客户端帧")
            size, offset = second & 127, 2
            if size in (126, 127):
                count = 2 if size == 126 else 8
                if len(self.buffer) < 2 + count:
                    break
                size = int.from_bytes(self.buffer[2:2 + count], "big")
                offset += count
            if size > MAX_MESSAGE:
                raise ValueError("Codex 消息超过大小限制")
            if opcode >= 8 and (size > 125 or not final):
                raise ValueError("无效的控制帧")
            if len(self.buffer) < offset + 4 + size:
                break
            mask = self.buffer[offset:offset + 4]
            payload = bytes(value ^ mask[index % 4] for index, value in enumerate(self.buffer[offset + 4:offset + 4 + size]))
            del self.buffer[:offset + 4 + size]
            if opcode == 8:
                self.closed = True
                replies.extend(encode_frame(payload, 8))
                break
            if opcode == 9:
                replies.extend(encode_frame(payload, 10))
                continue
            if opcode == 10:
                continue
            if opcode == 1:
                if self.fragmented:
                    raise ValueError("WebSocket 分片顺序错误")
                self.fragmented = not final
                self.fragments.extend(payload)
            elif opcode == 0 and self.fragmented:
                self.fragments.extend(payload)
                self.fragmented = not final
            else:
                raise ValueError("仅接受文本 JSON 消息")
            if len(self.fragments) > MAX_MESSAGE:
                raise ValueError("WebSocket 分片消息过大")
            if final:
                messages.append(bytes(self.fragments).decode("utf-8"))
                self.fragments.clear()
        return messages, replies
