"""Desktop entry point for the packaged Reel Harvest app."""

from __future__ import annotations

import sys
import socket
import threading
import webbrowser


def choose_port(start: int = 5000, attempts: int = 25) -> int:
    for port in range(start, start + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
            try:
                server_socket.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("Could not find a free local port for Reel Harvest.")


if "--gallery-dl-worker" in sys.argv:
    # A frozen EXE cannot invoke `python -m gallery_dl`; launch the bundled
    # gallery-dl module in a child process instead.
    sys.argv.remove("--gallery-dl-worker")
    import gallery_dl

    gallery_dl.main()
else:
    import app

    port = choose_port()
    threading.Timer(0.8, lambda: webbrowser.open(f"http://127.0.0.1:{port}/")).start()
    app.app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
