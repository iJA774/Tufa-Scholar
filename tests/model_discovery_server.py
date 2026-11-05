"""Start an isolated API and a deterministic OpenAI-compatible server for UI acceptance."""
import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import tempfile
from threading import Thread
import time

import uvicorn


class Provider(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/slow/'):
            time.sleep(3)
        if self.path.endswith('/models'):
            if self.path.startswith('/empty/'):
                data, status = {'data': []}, 200
            elif self.path.startswith('/error/'):
                data, status = {'error': 'provider failure'}, 500
            elif self.headers.get('Authorization') != 'Bearer demo-key':
                data, status = {'error': 'invalid key'}, 401
            else:
                data, status = {'data': [{'id': 'provider/model-alpha'}, {'id': 'model-beta'}]}, 200
        else:
            data, status = {}, 404
        payload = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--serve', action='store_true', required=True)
    parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        os.environ['TUFA_DATA_DIR'] = directory
        for name in ('OPENAI_API_KEY', 'OPENAI_BASE_URL', 'OPENAI_MODEL'):
            os.environ.pop(name, None)
        provider = ThreadingHTTPServer(('127.0.0.1', 18181), Provider)
        Thread(target=provider.serve_forever, daemon=True).start()
        print('API: http://127.0.0.1:18180; provider: http://127.0.0.1:18181/v1; key: demo-key', flush=True)
        print('Alternative paths: /empty/v1, /error/v1, /slow/v1', flush=True)
        try:
            uvicorn.run('apps.api.main:app', host='127.0.0.1', port=18180, log_level='warning')
        finally:
            provider.shutdown()
            provider.server_close()


if __name__ == '__main__':
    main()
