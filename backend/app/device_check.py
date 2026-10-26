"""
设备可用性检查里那些「不该拖慢主流程」的小工具。

单独成文件的原因:device.py 会 import 路由层要用的东西,
而这里只有纯粹的网络探测 —— 分开后测试与复用都更简单。
"""

from __future__ import annotations

import socket


def port_open(host: str, port: int, timeout: float = 1.5) -> bool:
    """TCP 端口能否连上。用于判断 iproxy 转发是否已就绪。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()
