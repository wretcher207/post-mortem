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


def file_get(key, default=None):
    """Value from the config file only (ignores the environment). Used to tell
    a key co-located with a base_url in the config from a bare env key that was
    set for a different endpoint."""
    return _load_file().get(key) or default


def set_file_value(key, value):
    """Atomically set one config value while preserving unrelated lines."""
    global _file_values
    if not key or any(char in key for char in "=\r\n"):
        raise ValueError("config key is invalid")
    if not isinstance(value, str) or any(char in value for char in "\r\n"):
        raise ValueError("config value must be a single line")

    lines = []
    try:
        with open(CONFIG_PATH, encoding="utf-8") as file:
            lines = file.read().splitlines()
    except OSError:
        pass

    replacement = f"{key}={value}"
    found = False
    updated = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("#") and "=" in stripped:
            existing_key = stripped.split("=", 1)[0].strip()
            if existing_key == key:
                if not found:
                    updated.append(replacement)
                    found = True
                continue
        updated.append(line)
    if not found:
        updated.append(replacement)

    directory = os.path.dirname(CONFIG_PATH)
    os.makedirs(directory, exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as file:
            file.write("\n".join(updated) + "\n")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, CONFIG_PATH)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _file_values = None
