import argparse
import asyncio
from collections.abc import Callable
import fcntl
import json
import math
import os
from pathlib import Path
import secrets
import socket
import urllib.request
import webbrowser

import uvicorn
from oko.reader.app import create_app
from oko.collection.demo import DemoSource
from oko.collection.contracts import Source
from oko.collection.telegram.source import LocalTelegram


def existing(root: Path, browser: bool, *, api_only: bool = False, persistent: bool = False) -> None:
    import time
    for _ in range(100):
        try:
            data = json.loads((root / 'runtime.json').read_text())
            request = urllib.request.Request(data.get('control_url', data['url']) + '/api/v3/status',
                                            headers={'Authorization': 'Bearer ' + data['token']})
            with urllib.request.urlopen(request, timeout=.2) as response:
                if response.status == 200:
                    expected = 'explicit' if api_only or persistent else 'client_presence'
                    status = json.load(response)
                    actual = status['lifetime']['kind']
                    if actual != expected:
                        raise SystemExit(f'Запущенный экземпляр использует {actual}; запрошен {expected}. Завершите его явно перед сменой профиля.')
                    bundled = json.loads((Path(__file__).parent / 'reader/generated/openapi.json').read_text())
                    version = status['api_version']
                    if version.split('.')[0] != bundled['info']['version'].split('.')[0]:
                        raise SystemExit(f'Запущенный экземпляр использует API {version}; требуется {bundled["info"]["version"]}. Завершите его перед обновлением.')
                    if browser:
                        webbrowser.open(data['url'] + ('/' if persistent else '/#token=' + data['token']))
                    print(f'Открыт существующий экземпляр Око ({actual}, API {version}).')
                    return
        except (OSError, ValueError):
            time.sleep(.05)
    raise SystemExit('Экземпляр Око занят или останавливается. Повторите запуск через несколько секунд.')


def run(*, fault: Callable[[str], None] = lambda _: None, source: Source | None = None) -> None:
    parser = argparse.ArgumentParser(description='Око — локальный читатель')
    parser.add_argument('--data', type=Path, default=Path('data'))
    parser.add_argument('--database-url-file', type=Path, help='Закрытый файл подключения PostgreSQL; схема устанавливается отдельной командой миграции')
    parser.add_argument('--telegram-env', type=Path, help='Путь к существующей .env с авторизацией Telegram')
    parser.add_argument('--telegram-project', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--demo', action='store_true', help='Подключить синтетический тестовый источник')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--api-only', action='store_true', help='API без веб-интерфейса, работа до явной остановки')
    parser.add_argument('--persistent', action='store_true', help='Постоянный веб-экземпляр с запоминаемым входом')
    parser.add_argument('--host', choices=('127.0.0.1', '0.0.0.0'), default='127.0.0.1')
    parser.add_argument('--public-url', help='Внешний локальный origin читателя, например http://oko')
    parser.add_argument('--codex-url', help='Адрес установленного процесса Codex на Mac')
    parser.add_argument('--codex-token-file', type=Path)
    parser.add_argument('--initially-disabled', action='store_true', help='Остановить работу при первом создании хранилища')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--lease', type=float, default=6, help='Срок клиентской сессии, секунд')
    parser.add_argument('--grace', type=float, default=15, help='Ожидание переподключения, секунд')
    args = parser.parse_args()
    if bool(args.codex_url) != bool(args.codex_token_file):
        parser.error('--codex-url and --codex-token-file must be supplied together')
    if args.persistent and args.api_only:
        parser.error('--persistent and --api-only are separate profiles')
    if not all(math.isfinite(value) and value > 0 for value in (args.lease, args.grace)):
        parser.error('Таймауты должны быть положительными')
    from oko.storage.credentials import private_database_url
    database_url = private_database_url(args.database_url_file) if args.database_url_file else None
    telegram_env = args.telegram_env or (args.telegram_project / '.env' if args.telegram_project else None)
    root = args.data.resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.umask(0o077)
    with (root / 'instance.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            existing(root, not (args.no_browser or args.api_only), api_only=args.api_only, persistent=args.persistent)
            return
        if database_url is None:
            fresh=not (root/'state.sqlite').exists()
            from oko.storage.bootstrap import prepare
            try:
                prepare(root)
            except ValueError as error:
                raise SystemExit(str(error)) from None
            if args.initially_disabled and fresh:
                from oko.storage.store import Store
                store = Store(root, fault=fault)
                try:
                    store.set_work_enabled(False)
                finally:
                    store.db.close()
        codex_client = None
        if args.codex_url:
            from oko.processing.codex_bridge.client import RemoteCodex
            codex_client = RemoteCodex(args.codex_url, args.codex_token_file.read_text().strip())
        token = secrets.token_urlsafe(32)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind((args.host, args.port))
            sock.listen(128)
            port = sock.getsockname()[1]
            control_url = f'http://127.0.0.1:{port}'
            url = args.public_url.rstrip('/') if args.public_url else control_url
            server: uvicorn.Server | None = None

            def shutdown() -> None:
                if server:
                    server.should_exit = True

            app = create_app(root, source=source or (DemoSource() if args.demo else None),
                             token=token, fault=fault, shutdown=shutdown,
                             lease=args.lease, grace=args.grace, api_only=args.api_only, persistent=args.persistent,
                             public_url=args.public_url, codex_client=codex_client, rss_enabled=not args.demo, database_url=database_url,
                             telegram_client=LocalTelegram(telegram_env, root) if telegram_env and not args.demo else None)
            config = uvicorn.Config(app, access_log=False, log_level='critical', timeout_graceful_shutdown=2, proxy_headers=False)
            server = uvicorn.Server(config)

            async def serve() -> None:
                assert server is not None
                task = asyncio.create_task(server.serve(sockets=[sock]))
                while not server.started and not task.done():
                    await asyncio.sleep(.01)
                if server.started:
                    temporary = root / 'runtime.tmp'
                    version = json.loads((Path(__file__).parent / 'reader/generated/openapi.json').read_text())['info']['version']
                    temporary.write_text(json.dumps(dict(url=url, control_url=control_url, token=token, pid=os.getpid(),
                                                         lifetime='explicit' if args.api_only or args.persistent else 'client_presence', api_version=version)))
                    os.replace(temporary, root / 'runtime.json')
                    print(f'Око запущено: {url} (источник: {"тестовый" if args.demo else "RSS / Telegram" if telegram_env else "RSS"}).', flush=True)
                    if not (args.no_browser or args.api_only):
                        await asyncio.to_thread(webbrowser.open, url + ('/' if args.persistent else '/#token=' + token))
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
