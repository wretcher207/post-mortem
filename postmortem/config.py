"""User config. Environment variables win; the config file fills the gaps.

The file is ~/.config/postmortem/config, plain KEY=VALUE lines (same names
as the env vars), # comments allowed:

    ANTHROPIC_API_KEY=sk-ant-...
    ANTHROPIC_BASE_URL=https://api.minimax.io/anthropic
    POSTMORTEM_MODEL=MiniMax-M3
    REAPER_DAEMON_ROOT=/path/to/reaper-daemon
"""

import os

CONFIG_PATH = os.path.expanduser("~/.config/postmortem/config")

_file_values = None


def _load_file():
    global _file_values
    if _file_values is None:
        _file_values = {}
        try:
            with open(CONFIG_PATH) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    _file_values[key.strip()] = value.strip()
        except OSError:
            pass
    return _file_values


def get(key, default=None):
    return os.environ.get(key) or _load_file().get(key) or default
