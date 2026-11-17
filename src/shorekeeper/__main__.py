import argparse
import socket

import uvicorn

from shorekeeper.app import create_app
from shorekeeper.config import DEFAULT_PORT, HOST, SESSION_TOKEN_PATH, load_session_token


def main() -> None:
    parser = argparse.ArgumentParser(description="Shorekeeper M01A local process API")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    # Reserve the actual listener before startup, so port-conflict checks cannot race.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            listener.bind((HOST, args.port))
            listener.listen(128)
        except OSError:
            parser.exit(1, f"Cannot listen on {HOST}:{args.port}; port is in use or unavailable. Try --port.\n")

        token_path = SESSION_TOKEN_PATH
        try:
            token = load_session_token(token_path)
        except (OSError, UnicodeError, ValueError):
            parser.exit(1, f"Cannot load session token at {token_path}; check or remove the file and retry.\n")
        print(f"Local session token file: {token_path}", flush=True)
        config = uvicorn.Config(
            create_app(token), host=HOST, port=args.port,
            access_log=False, proxy_headers=False,
        )
        uvicorn.Server(config).run(sockets=[listener])


if __name__ == "__main__":
    main()
