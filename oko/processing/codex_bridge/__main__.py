"""Run only from a fixed release with a private external configuration."""
import argparse
from pathlib import Path
import uvicorn
from .protocol import BridgeConfig
from .service import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description='Oko private macOS Codex helper')
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    if args.config.stat().st_mode & 0o077:
        parser.error('config must be private (0600)')
    config = BridgeConfig.model_validate_json(args.config.read_text())
    uvicorn.run(create_app(config), host=config.host, port=config.port,
                access_log=False, log_level='warning')


if __name__ == '__main__':
    main()
