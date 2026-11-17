from pathlib import Path
import re
import secrets


HOST = "127.0.0.1"
DEFAULT_PORT = 8765
SESSION_TOKEN_PATH = Path(__file__).resolve().parents[2] / "local" / "runtime" / "session.token"


def load_session_token(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(secrets.token_urlsafe(32) + "\n")
    except FileExistsError:
        pass
    token = path.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise ValueError("Session token file is invalid; remove it to generate a new session token.")
    return token
