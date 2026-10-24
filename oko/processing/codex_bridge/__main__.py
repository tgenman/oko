"""Run only from a fixed release with a private external configuration."""
import argparse
import errno
import os
from pathlib import Path
import socket
import stat
import sys
from collections.abc import Callable
from oko.processing.codex import CodexClient, LocalCodex
import uvicorn
from .protocol import BridgeConfig
from .service import create_app


def serve(config: BridgeConfig, *, adapter_factory: Callable[[], CodexClient] = LocalCodex) -> None:
    server: uvicorn.Server | None = None
    failed = False
    def fatal() -> None:
        nonlocal failed
        failed = True
        if server is not None:
            server.should_exit = True
    # A failed supervisor cannot prove descendant cleanup. Under systemd,
    # exit unsuccessfully so KillMode=control-group and RuntimeDirectory clean
    # the entire owned generation before the service restarts.
    on_fatal = fatal if sys.platform == 'linux' and os.environ.get('INVOCATION_ID') else None
    if config.socket_path is None:
        app = create_app(config, adapter_factory=adapter_factory, on_fatal=on_fatal)
        server = uvicorn.Server(uvicorn.Config(app, host=config.host, port=config.port,
                               access_log=False, log_level='warning'))
        server.run()
        if failed:
            raise SystemExit(70)
        return
    path = config.socket_path
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    parent = path.parent.stat()
    if parent.st_uid != os.getuid() or parent.st_mode & 0o027:
        raise ValueError('helper socket directory must be owned and private (0750)')
    if path.exists() or path.is_symlink():
        existing = path.lstat()
        if not stat.S_ISSOCK(existing.st_mode) or existing.st_uid != os.getuid():
            raise ValueError('refusing to replace an unowned/non-socket helper path')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(.2)
            try:
                probe.connect(str(path))
            except OSError as error:
                if error.errno != errno.ECONNREFUSED:
                    raise
            else:
                raise ValueError('another helper is listening on this socket')
        path.unlink()
    inode = None
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        try:
            listener.bind(str(path))
            inode = path.stat().st_ino
            path.chmod(0o660)
            listener.listen(128)
            app = create_app(config, adapter_factory=adapter_factory, on_fatal=on_fatal)
            server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level='warning'))
            server.run(sockets=[listener])
        finally:
            if inode is not None and path.exists() and path.lstat().st_ino == inode:
                path.unlink()
    if failed:
        raise SystemExit(70)


def main() -> None:
    parser = argparse.ArgumentParser(description='Oko private Codex host helper')
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    if args.config.stat().st_mode & 0o077:
        parser.error('config must be private (0600)')
    config = BridgeConfig.model_validate_json(args.config.read_text())
    serve(config)


if __name__ == '__main__':
    main()
