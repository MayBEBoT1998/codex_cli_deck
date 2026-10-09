"""Bounded RFC 6455 framing for both sides of the private local relay."""
import base64
import hashlib
import os
import struct


MAX_MESSAGE = 16_000_000


def encode_frame(payload, opcode=1, masked=False):
    size = len(payload)
    header = bytes([0x80 | opcode])
    mask_flag = 128 if masked else 0
    if size < 126:
        header += bytes([mask_flag | size])
    elif size < 65536:
        header += bytes([mask_flag | 126]) + struct.pack("!H", size)
    else:
        header += bytes([mask_flag | 127]) + struct.pack("!Q", size)
    if masked:
        mask = os.urandom(4)
        return header + mask + bytes(value ^ mask[i % 4] for i, value in enumerate(payload))
    return header + payload


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


def connect_handshake(connection):
    """Perform the client half of a WebSocket upgrade to native app-server."""
    key = base64.b64encode(os.urandom(16)).decode()
    connection.settimeout(10)
    connection.sendall(("GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
                        "Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n"
                        f"Sec-WebSocket-Key: {key}\r\n\r\n").encode())
    data = bytearray()
    while b"\r\n\r\n" not in data:
        part = connection.recv(4096)
        if not part:
            raise ConnectionError("Codex 后端在握手时断开")
        data.extend(part)
        if len(data) > 16384:
            raise ValueError("Codex 后端握手过大")
    headers, remainder = bytes(data).split(b"\r\n\r\n", 1)
    lines = headers.decode("ascii").split("\r\n")
    fields = {}
    for line in lines[1:]:
        name, value = line.split(":", 1)
        fields[name.strip().lower()] = value.strip()
    expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
    if (lines[0].split()[1:2] != ["101"] or fields.get("sec-websocket-accept") != expected
        or fields.get("upgrade", "").lower() != "websocket"):
        raise ValueError("Codex 后端 WebSocket 握手失败")
    connection.setblocking(False)
    return remainder


class Decoder:
    def __init__(self, expect_masked=True):
        self.buffer = bytearray()
        self.fragments = bytearray()
        self.fragmented = False
        self.closed = False
        self.expect_masked = expect_masked

    def feed(self, data):
        self.buffer.extend(data)
        messages, replies = [], bytearray()
        if len(self.buffer) > MAX_MESSAGE + 14:
            raise ValueError("WebSocket 缓冲区过大")
        while len(self.buffer) >= 2:
            first, second = self.buffer[:2]
            final, opcode = bool(first & 128), first & 15
            masked = bool(second & 128)
            if first & 112 or masked != self.expect_masked:
                raise ValueError("无效的 WebSocket 帧")
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
            mask_length = 4 if masked else 0
            if len(self.buffer) < offset + mask_length + size:
                break
            payload = bytes(self.buffer[offset + mask_length:offset + mask_length + size])
            if masked:
                mask = self.buffer[offset:offset + 4]
                payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
            del self.buffer[:offset + mask_length + size]
            if opcode == 8:
                self.closed = True
                replies.extend(encode_frame(payload, 8, masked=not self.expect_masked))
                break
            if opcode == 9:
                replies.extend(encode_frame(payload, 10, masked=not self.expect_masked))
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
