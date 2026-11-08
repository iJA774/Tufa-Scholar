import unittest
from unittest.mock import patch
from src.integrations import web_page
PinnedHTTPS = web_page._PinnedHTTPS
from src.tools import ToolContext, ToolRegistry


class Response:
    def __init__(self, status=200, body=b'', headers=None):
        self.status, self.body, self.headers = status, body, headers or {}
    def getheader(self, name, default=None): return self.headers.get(name, default)
    def read(self, size):
        chunk, self.body = self.body[:size], self.body[size:]
        return chunk


class Connection:
    responses = []
    requests = 0
    def __init__(self, host, port, address): self.host, self.address = host, address
    def request(self, method, path, headers): Connection.requests += 1
    def getresponse(self): return self.responses.pop(0)
    def close(self): pass


class WebPageTests(unittest.TestCase):
    def setUp(self):
        self.registry = ToolRegistry()
        self.context = ToolContext(project_id=1, source='web')
        self.dns = patch('src.integrations.web_page.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('8.8.8.8', 443))])
        self.transport = patch('src.integrations.web_page._PinnedHTTPS', Connection)
        self.http_transport = patch('src.integrations.web_page._PinnedHTTP', Connection)
        self.dns.start(); self.transport.start(); self.http_transport.start()
        self.addCleanup(self.dns.stop); self.addCleanup(self.transport.stop); self.addCleanup(self.http_transport.stop)

    def test_https_uses_original_hostname_and_trusted_context(self):
        from unittest.mock import Mock
        sock, context = Mock(), Mock()
        with patch('src.integrations.web_page.socket.create_connection', return_value=sock), patch('src.integrations.web_page.httpx.create_ssl_context', return_value=context) as factory:
            connection = PinnedHTTPS('example.com', 443, '8.8.8.8')
            connection.connect()
        factory.assert_called_once_with(verify=True, trust_env=False)
        context.wrap_socket.assert_called_once_with(sock, server_hostname='example.com')

    def test_ipv6_and_missing_body(self):
        Connection.responses = [Response(body=b'<title>Title</title><p>body text</p>', headers={'Content-Type': 'text/html'})]
        result = web_page.fetch_page('https://[2606:4700:4700::1111]/x#fragment')
        self.assertEqual(result['url'], 'https://[2606:4700:4700::1111]/x')
        self.assertIn('body text', result['markdown'])
        self.assertNotIn('Title', result['markdown'])

    def test_empty_search_snippet_remains_candidate(self):
        from src.integrations.web_search import _normalise
        result = _normalise([{'url': 'https://example.com/a'}], 'fixture', 5)
        self.assertEqual(result[0]['content'], '')
        self.assertEqual(result[0]['title'], 'example.com')

    def test_html_pagination_and_evidence_offsets(self):
        body = '<html><title>Example</title><body><nav>ignore</nav><article><h1>Title</h1><p>' + 'a' * 1600 + ' keyword important end</p></article><script>secret</script></body></html>'
        Connection.responses = [Response(body=body.encode(), headers={'Content-Type': 'text/html; charset=utf-8'}) for _ in range(2)]
        first = self.registry.invoke('read_web_page', {'url': 'https://example.com/a'}, context=self.context)
        second = self.registry.invoke('read_web_page', {'url': 'https://example.com/a', 'offset': first['next_offset']}, context=self.context)
        self.assertEqual(len(first['markdown']), 1000)
        self.assertIsNone(second['next_offset'])
        self.assertNotIn('ignore', first['markdown']); self.assertNotIn('secret', second['markdown'])
        candidate = {'url': second['url'], 'title': second['title'], 'content': second['markdown'], 'content_offset': second['offset'], 'source_level': 'web_content', 'provider': 'web_page', 'retrieved_at': second['retrieved_at']}
        e = self.registry.invoke('collect_evidence', {'query': 'keyword', 'papers': [candidate]}, context=self.context)[0]
        self.assertIn('keyword', e['text']); self.assertEqual(e['char_start'], 1000)
        self.assertEqual(e['char_end'] - e['char_start'], len(e['text']))

    def test_reject_nonpublic_addresses_ports_and_encodings(self):
        for address in ('224.0.0.1', '240.0.0.1', 'fe80::1%4'):
            with patch('src.integrations.web_page.socket.getaddrinfo', return_value=[(2, 1, 6, '', (address, 443))]):
                with self.assertRaises(web_page.WebPageError): web_page.fetch_page('https://example.com')
        for url in ('https://example.com:0', 'https://example.com:8080', 'file:///tmp/a'):
            with self.assertRaises(web_page.WebPageError): web_page.fetch_page(url)
        for headers in ({'Content-Type': 'text/htmlx'}, {'Content-Type': 'text/html', 'Content-Encoding': 'gzip'}):
            Connection.responses = [Response(body=b'bad', headers=headers)]
            with self.assertRaises(web_page.WebPageError): web_page.fetch_page('https://example.com')
        Connection.responses = [Response(body=b'x' * (web_page.MAX_BYTES + 1), headers={'Content-Type': 'text/plain'})]
        with self.assertRaises(web_page.WebPageError): web_page.fetch_page('https://example.com')

    def test_reject_redirect_private_credentials_errors_and_scope(self):
        Connection.requests = 0
        Connection.responses = [Response(302, headers={'Location': 'http://127.0.0.1/private'})]
        with self.assertRaisesRegex(web_page.WebPageError, '网页读取失败'):
            self.registry.invoke('read_web_page', {'url': 'https://example.com'}, context=self.context)
        self.assertEqual(Connection.requests, 1)
        with self.assertRaises(web_page.WebPageError): web_page.fetch_page('https://user:secret@example.com')
        with patch('src.integrations.web_page.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 443))]):
            with self.assertRaises(web_page.WebPageError): web_page.fetch_page('https://example.com')
        Connection.responses = [Response(503)]
        with self.assertRaises(web_page.WebPageError) as exc: web_page.fetch_page('https://example.com/secret')
        self.assertNotIn('secret', str(exc.exception))
        with self.assertRaises(ValueError): self.registry.invoke('read_web_page', {'url': 'https://example.com'}, context=ToolContext(1, 'local'))
