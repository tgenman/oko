"""Private loopback control using the reader's verified TLS identity."""
import http.client
import json
from pathlib import Path
import socket
import ssl
from typing import Any
from urllib.parse import urlsplit


def status(metadata: dict[str, Any], *, timeout: float = 3, ca_file: Path | None = None) -> dict[str, Any]:
    endpoint = urlsplit(metadata.get('control_url', metadata['url']))
    port = endpoint.port or (443 if endpoint.scheme == 'https' else 80)
    if endpoint.scheme == 'https':
        context = ssl.create_default_context(cafile=str(ca_file) if ca_file else None)

        class LoopbackHTTPS(http.client.HTTPSConnection):
            def connect(self) -> None:
                raw = socket.create_connection(('127.0.0.1', port), self.timeout)
                try:
                    self.sock = context.wrap_socket(raw, server_hostname=endpoint.hostname)
                except BaseException:
                    raw.close()
                    raise

        connection: http.client.HTTPConnection = LoopbackHTTPS(endpoint.hostname or '', port, timeout=timeout)
    else:
        if endpoint.scheme != 'http' or endpoint.hostname not in ('localhost', '127.0.0.1'):
            raise ValueError('Control endpoint must use loopback HTTP or verified HTTPS')
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=timeout)
    try:
        # Keep the service bearer restricted to the local Host boundary. TLS SNI
        # and certificate verification still use the configured public hostname.
        connection.request('GET', '/api/v3/status', headers={
            'Host': f'127.0.0.1:{port}', 'Authorization': 'Bearer ' + metadata['token']})
        response = connection.getresponse()
        if response.status != 200:
            raise OSError(f'Control returned HTTP {response.status}')
        value = json.loads(response.read())
        if not isinstance(value, dict): raise ValueError('Invalid control status')
        return value
    finally:
        connection.close()


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, default=Path('/data'))
    args = parser.parse_args()
    value = status(json.loads((args.data / 'runtime.json').read_text()))
    if value['runtime_state'] not in ('running', 'disabled'):
        raise SystemExit('Reader is not healthy')


if __name__ == '__main__':
    main()
