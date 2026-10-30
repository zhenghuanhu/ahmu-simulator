"""
模拟测试端 TFTP 服务器 (改动4: 故障历史 TFTP 上传的对向接收端)

对齐 OHMS 故障历史报告获取664.py 的 TFTPServer: 测试端监听 TFTP 端口(59),
接收 AHMU 检索完成后上传的故障历史文件。采用标准 TFTP (RFC1350 + blksize)。

用法:
  python tests/simulate_tftp_server.py --port 59
"""
import argparse
import socket


class TftpServer:
    """标准 TFTP 服务器 (接收一次上传, 返回文件名与内容)"""

    def __init__(self, port: int = 59, blksize: int = 2048):
        self.port = port
        self.blksize = blksize

    def receive_once(self, timeout: float = 8.0):
        """接收一次 TFTP 上传, 返回 (filename, data_bytes) 或 (None, None)"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", self.port))
        sock.settimeout(timeout)
        try:
            # 1. 等 WRQ
            data, addr = sock.recvfrom(65535)
            if data[0:2] != b"\x00\x02":
                return None, None
            filename = data[2:data.find(b"\x00", 2)].decode("utf-8", errors="replace")
            # 2. 用新 TID 回 ACK(0)
            new_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            new_sock.settimeout(timeout)
            new_sock.sendto(b"\x00\x04\x00\x00", addr)
            # 3. 逐块收 DATA, 逐块回 ACK
            buf = bytearray()
            while True:
                pkt, addr = new_sock.recvfrom(65535)
                if pkt[0:2] != b"\x00\x03":
                    break
                block = int.from_bytes(pkt[2:4], "big")
                chunk = pkt[4:]
                buf.extend(chunk)
                new_sock.sendto(b"\x00\x04" + block.to_bytes(2, "big"), addr)
                if len(chunk) < self.blksize:
                    break
            new_sock.close()
            return filename, bytes(buf)
        except socket.timeout:
            return None, None
        finally:
            sock.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=59)
    args = ap.parse_args()

    server = TftpServer(args.port)
    print(f"[TFTPServer] 监听 127.0.0.1:{args.port}, 等待 AHMU 上传...")
    filename, data = server.receive_once()
    if filename:
        print(f"[TFTPServer] 收到文件: {filename} ({len(data)} 字节)")
        print(f"[TFTPServer] 内容: {data.decode('utf-8', errors='replace')[:200]}")
    else:
        print("[TFTPServer] 超时未收到上传")


if __name__ == "__main__":
    main()
