from __future__ import annotations

import urllib.request

from traittutor.services.config.runtime_settings import load_system_settings

port = int(load_system_settings().get("backend_port") or 8001)
urllib.request.urlopen(f"http://localhost:{port}/", timeout=5).close()
