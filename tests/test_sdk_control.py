"""SDK socket guards reject changed DNS before credentials leave; telemetry uses an actual HTTP peer."""

import asyncio
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from quality.config import Settings


@pytest.mark.parametrize('sdk', ['s3', 'mqtt', 'opc', 'telemetry'])
def test_protocol_sdk_rebinding_is_rejected_before_opening_socket(sdk, monkeypatch, tmp_path):
    from quality.sdk_control import pin_mqtt, pin_opc, telemetry_session
    from quality.storage import Storage
    from paho.mqtt.client import Client, CallbackAPIVersion
    from asyncua import Client as OpcClient

    def dns(ip):
        return lambda host, port, *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, int(port)))]
    monkeypatch.setattr(socket, 'getaddrinfo', dns('127.0.0.1'))
    settings = Settings(_env_file=None, mode='integration', data_dir=tmp_path,
                        approved_hosts=['approved.internal'], storage_backend='s3',
                        s3_endpoint='http://approved.internal:8123', s3_access_key='fixture-access',
                        s3_secret_key='fixture-secret', otlp_endpoint='http://approved.internal:8123/traces')
    if sdk == 's3':
        client = Storage(settings).client
        def invoke():
            return client.head_bucket(Bucket='software-test')
    elif sdk == 'mqtt':
        client = Client(CallbackAPIVersion.VERSION2)
        pin_mqtt(client, settings, 'mqtt://approved.internal:8123')
        def invoke():
            return client.connect('approved.internal', 8123)
    elif sdk == 'opc':
        client = OpcClient('opc.tcp://approved.internal:8123')
        pin_opc(client, settings, 'opc.tcp://approved.internal:8123')
        async def invoke_async():
            with monkeypatch.context() as scoped:
                scoped.setattr(socket, 'socket', forbidden_socket)
                await client.uaclient.connect_socket('approved.internal', 8123)
        def invoke():
            return asyncio.run(invoke_async())
    else:
        client = telemetry_session(settings)
        def invoke():
            return client.post(settings.otlp_endpoint, data=b'fixture', timeout=1)
    monkeypatch.setattr(socket, 'getaddrinfo', dns('10.0.0.77'))
    def forbidden_socket(*args, **kwargs):
        pytest.fail('No socket or credentials may be sent after an unapproved DNS change')
    if sdk != 'opc':
        monkeypatch.setattr(socket, 'socket', forbidden_socket)
    with pytest.raises(Exception, match='DNS changed'):
        invoke()


def test_actual_telemetry_export_retains_spans_and_never_follows_redirects(tmp_path, monkeypatch):
    from quality.observability import setup
    from quality.sdk_control import telemetry_session
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    received = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            received.append((self.path, raw))
            self.send_response(307 if self.path == '/redirect' else 200)
            if self.path == '/redirect':
                self.send_header('Location', '/must-not-follow')
            self.send_header('Content-Length', '0')
            self.end_headers()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings = Settings(_env_file=None, data_dir=tmp_path, otlp_endpoint=f'http://127.0.0.1:{server.server_port}/v1/traces')
    # An inherited proxy must not reroute evidence or telemetry credentials.
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('ALL_PROXY', 'http://127.0.0.1:1')
    provider, tracer, _ = setup(settings)
    try:
        with tracer.start_as_current_span('sdk-controlled-software-fixture'):
            pass
        assert provider.force_flush(timeout_millis=5000)
        payload = ExportTraceServiceRequest.FromString(received[0][1])
        assert payload.resource_spans[0].scope_spans[0].spans[0].name == 'sdk-controlled-software-fixture'
        with telemetry_session(settings) as session:
            response = session.post(settings.otlp_endpoint.replace('/v1/traces', '/redirect'), data=b'fixture', timeout=2)
            assert response.status_code == 307
        assert [path for path, _ in received] == ['/v1/traces', '/redirect']
    finally:
        provider.shutdown()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
