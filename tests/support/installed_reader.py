"""Server fixture copied outside checkout; production imports come from wheel."""
import json
import os
import oko
from pathlib import Path
import socket
import sys
import uvicorn
from tests.support.app import create_app

assert Path(oko.__file__).resolve().is_relative_to(Path(os.environ['PYTHONPATH'].split(os.pathsep)[0]).resolve())
app = create_app(Path(sys.argv[1]), token=sys.argv[2], lease=60, grace=60)
sock = socket.socket()
sock.bind(('127.0.0.1',0))
print(json.dumps({'origin':f'http://127.0.0.1:{sock.getsockname()[1]}'}),flush=True)
uvicorn.Server(uvicorn.Config(app,log_level='error')).run(sockets=[sock])
