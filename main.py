import logging
import socket
import sys

from housebot.bot import create_application

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)

_LOCK_PORT = 47247
_lock_socket: socket.socket | None = None


def _acquire_lock() -> None:
    global _lock_socket
    _lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _lock_socket.bind(("127.0.0.1", _LOCK_PORT))
    except OSError:
        print(
            "ERROR: another bot instance is already running (port 47247 busy).\n"
            "Stop the other process first, then retry.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    _acquire_lock()
    app = create_application()
    print("Bot started. Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)
