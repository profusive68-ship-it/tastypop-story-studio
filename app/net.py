"""원격 접속 지원: 로컬 IP·Tailscale IP·QR·PIN 잠금·맥북 잠자기 방지."""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

from . import settings

_PID_FILE = settings.CONFIG_DIR / "caffeinate.json"
_proc: subprocess.Popen | None = None


def lan_ip() -> str | None:
    """기본 라우트에 쓰이는 로컬 IPv4."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.4)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return None


def all_ipv4() -> list[str]:
    ips: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    # macOS/Linux ifconfig 보조
    try:
        out = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=5).stdout
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("inet "):
                ip = line.split()[1]
                if ip not in ips:
                    ips.append(ip)
    except Exception:
        pass
    return [i for i in ips if not i.startswith("127.")]


def tailscale_ip() -> str | None:
    """Tailscale(100.64.0.0/10) 주소 자동 감지."""
    try:
        if shutil.which("tailscale"):
            r = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5)
            ip = (r.stdout or "").strip().splitlines()
            if ip and ip[0]:
                return ip[0].strip()
    except Exception:
        pass
    for ip in all_ipv4():
        try:
            a, b = (int(x) for x in ip.split(".")[:2])
            if a == 100 and 64 <= b <= 127:
                return ip
        except Exception:
            continue
    return None


def port() -> int:
    return int(settings.load_settings()["server"].get("port", 8766))


def pin() -> str:
    return str(settings.load_settings()["server"].get("pin") or "")


def set_pin(value: str) -> str:
    value = "".join(ch for ch in str(value) if ch.isdigit())[:8]
    settings.save_settings({"server": {"pin": value}})
    return value


def urls() -> list[dict]:
    p = port()
    out = []
    ip = lan_ip()
    if ip:
        q = f"?pin={pin()}" if pin() else ""
        out.append({"kind": "wifi", "label": "같은 Wi-Fi(집·사무실)", "ip": ip,
                    "url": f"http://{ip}:{p}/{q}".replace("/?", "/?")})
    ts = tailscale_ip()
    if ts:
        q = f"?pin={pin()}" if pin() else ""
        out.append({"kind": "tailscale", "label": "Tailscale(외출 시)", "ip": ts,
                    "url": f"http://{ts}:{p}/{q}".replace("/?", "/?")})
    out.append({"kind": "local", "label": "이 맥북", "ip": "127.0.0.1",
                "url": f"http://127.0.0.1:{p}/"})
    return out


def qr_png(text: str, out_path: Path) -> Path:
    """QR 코드 생성(qrcode 패키지). 없으면 안내용 에러."""
    try:
        import qrcode  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError("QR 라이브러리(qrcode)가 없습니다. start.command 로 다시 설치해 주세요.") from e
    img = qrcode.make(text, box_size=8, border=2)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path


def caffeinate_status() -> dict:
    global _proc
    running = bool(_proc and _proc.poll() is None)
    if not running and _PID_FILE.exists():
        try:
            pid = int(json.loads(_PID_FILE.read_text()).get("pid", 0))
            os.kill(pid, 0)
            running = True
        except Exception:
            running = False
    return {"available": bool(shutil.which("caffeinate")), "running": running}


def caffeinate_on() -> dict:
    """잠자기 방지(caffeinate -dimsu): 화면만 꺼지고 서버는 계속 살아 있게."""
    global _proc
    if not shutil.which("caffeinate"):
        return {"ok": False, "message": "caffeinate 는 macOS 전용입니다."}
    st = caffeinate_status()
    if st["running"]:
        return {"ok": True, "running": True, "message": "이미 켜져 있습니다."}
    try:
        _proc = subprocess.Popen(["caffeinate", "-dimsu"], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        settings.ensure_dirs()
        _PID_FILE.write_text(json.dumps({"pid": _proc.pid}), encoding="utf-8")
        return {"ok": True, "running": True, "pid": _proc.pid, "message": "잠자기 방지 ON"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


def caffeinate_off() -> dict:
    global _proc
    if _proc and _proc.poll() is None:
        _proc.terminate()
        _proc = None
    if _PID_FILE.exists():
        try:
            pid = int(json.loads(_PID_FILE.read_text()).get("pid", 0))
            os.kill(pid, 15)
        except Exception:
            pass
        _PID_FILE.unlink(missing_ok=True)
    return {"ok": True, "running": False, "message": "잠자기 방지 OFF"}
