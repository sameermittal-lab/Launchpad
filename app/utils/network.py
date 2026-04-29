"""Network utilities - detect local IP, generate QR codes."""

import io
import socket
from base64 import b64encode

import qrcode


def get_local_ip() -> str:
    """Detect the local network IP address.

    Opens a UDP socket to a public IP (no packet sent) to find which
    local interface the OS would use. Falls back to 127.0.0.1.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except OSError:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def get_startup_url(host: str, port: int) -> str:
    """Build the URL a user would type in their browser."""
    if host in ("0.0.0.0", ""):
        ip = get_local_ip()
        return f"http://{ip}:{port}"
    return f"http://{host}:{port}"


def generate_qr_data_url(url: str) -> str:
    """Generate a QR code PNG as a data URL."""
    qr = qrcode.QRCode(version=1, box_size=8, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#6366f1", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data = b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{data}"
