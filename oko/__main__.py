import argparse
import asyncio
from collections.abc import Callable
import fcntl
import json
import os
from pathlib import Path
import secrets
import socket
import urllib.request
import webbrowser

import uvicorn
from .app import create_app
from .sources import DemoSource, Source
from .telegram import LocalTelegram


def existing(root: Path, browser: bool) -> None:
    import time
    for _ in range(100):
        try:
            data = json.loads((root / 'runtime.json').read_text())
            request = urllib.request.Request(data['url'] + '/api/status',
                                            headers={'Authorization': 'Bearer ' + data['token']})
            with urllib.request.urlopen(request, timeout=.2) as response:
                if response.status == 200:
                    if browser:
                        webbrowser.open(data['url'] + '/#token=' + data['token'])
                    print('Открыт существующий экземпляр Око.')
                    return
        except (OSError, ValueError):
            time.sleep(.05)
    raise SystemExit('Экземпляр Око занят или останавливается. Повторите запуск через несколько секунд.')


def run(*, fault: Callable[[str], None] = lambda _: None, source: Source | None = None) -> None:
    parser = argparse.ArgumentParser(description='Око — локальный читатель')
    parser.add_argument('--data', type=Path, default=Path('data'))
    parser.add_argument('--telegram-project', type=Path, help='Путь к существующему telegram-mcp с .env и .venv')
    parser.add_argument('--demo', action='store_true', help='Подключить синтетический тестовый источник')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--lease', type=float, default=6, help='Срок связи вкладки, секунд')
    parser.add_argument('--grace', type=float, default=15, help='Ожидание переподключения, секунд')
    args = parser.parse_args()
    if args.lease <= 0 or args.grace <= 0:
        parser.error('Таймауты должны быть положительными')
    root = args.data.resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.umask(0o077)
    with (root / 'instance.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            existing(root, not args.no_browser)
            return
        token = secrets.token_urlsafe(32)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(('127.0.0.1', args.port))
            sock.listen(128)
            port = sock.getsockname()[1]
            url = f'http://127.0.0.1:{port}'
            server: uvicorn.Server | None = None

            def shutdown() -> None:
                if server:
                    server.should_exit = True

            app = create_app(root, source=source or (DemoSource() if args.demo else None),
                             token=token, fault=fault, shutdown=shutdown,
                             lease=args.lease, grace=args.grace, rss_enabled=not args.demo,
                             telegram_client=LocalTelegram(args.telegram_project, root) if args.telegram_project and not args.demo else None)
            config = uvicorn.Config(app, access_log=False, log_level='critical', timeout_graceful_shutdown=2)
            server = uvicorn.Server(config)

            async def serve() -> None:
                assert server is not None
                task = asyncio.create_task(server.serve(sockets=[sock]))
                while not server.started and not task.done():
                    await asyncio.sleep(.01)
                if server.started:
                    temporary = root / 'runtime.tmp'
                    temporary.write_text(json.dumps(dict(url=url, token=token, pid=os.getpid())))
                    os.replace(temporary, root / 'runtime.json')
                    print(f'Око запущено: {url} (источник: {"тестовый" if args.demo else "RSS / Telegram" if args.telegram_project else "RSS"}).', flush=True)
                    if not args.no_browser:
                        await asyncio.to_thread(webbrowser.open, url + '/#token=' + token)
                await task

            # asyncio.run waits indefinitely for cancellation-resistant tasks.
            # This loop belongs solely to Oko; closing it bounds owned work.
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.set_exception_handler(lambda _loop, _context: None)
            try:
                loop.run_until_complete(serve())
            finally:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.wait(pending, timeout=.25))
                loop.close()
                asyncio.set_event_loop(None)
        finally:
            sock.close()
            for name in ('runtime.json', 'runtime.tmp'):
                (root / name).unlink(missing_ok=True)


if __name__ == '__main__':
    run()
