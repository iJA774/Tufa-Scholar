"""Fetch public web pages without proxies or ambient credentials."""
import http.client
import ipaddress
import re
import socket
import httpx
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit, quote

MAX_BYTES = 2_000_000


class WebPageError(RuntimeError):
    pass


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, port, address):
        super().__init__(host, port, timeout=10)
        self.address = address

    def connect(self):
        self.sock = socket.create_connection((self.address, self.port), self.timeout)


class _PinnedHTTPS(_PinnedHTTP):
    def connect(self):
        super().connect()
        self.sock = httpx.create_ssl_context(verify=True, trust_env=False).wrap_socket(self.sock, server_hostname=self.host)


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = {'body': [], 'main': [], 'article': [], 'fallback': []}
        self.stack = []
        self.skip = 0
        self.title = []
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in ('script', 'style', 'nav', 'footer', 'header', 'aside', 'noscript', 'svg'):
            self.skip += 1
        if tag == 'title': self.in_title = True
        if tag in self.parts: self.stack.append(tag)
        if not self.skip and tag in ('p', 'div', 'br', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self._append('\n' + ('- ' if tag == 'li' else '#' * int(tag[1]) + ' ' if tag.startswith('h') and len(tag) == 2 else ''))

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == 'title': self.in_title = False
        if tag in ('script', 'style', 'nav', 'footer', 'header', 'aside', 'noscript', 'svg') and self.skip:
            self.skip -= 1
        if tag in self.parts and tag in self.stack:
            self.stack.remove(tag)
        if not self.skip and tag in ('p', 'div', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'): self._append('\n')

    def _append(self, text):
        for name in set(self.stack): self.parts[name].append(text)
        if not self.in_title: self.parts['fallback'].append(text)

    def handle_data(self, data):
        if self.in_title: self.title.append(data)
        if not self.skip: self._append(data)

    def result(self):
        for name in ('article', 'main', 'body', 'fallback'):
            text = re.sub(r'\n\s*\n+', '\n\n', ''.join(self.parts[name])).strip()
            if text: return text
        return ''


def _target(url):
    if len(url) > 4096: raise ValueError
    parts = urlsplit(url)
    if parts.scheme.lower() not in ('http', 'https') or not parts.hostname or parts.username is not None or parts.password is not None:
        raise ValueError('网页地址不可访问')
    port = parts.port if parts.port is not None else (443 if parts.scheme.lower() == 'https' else 80)
    if port != (443 if parts.scheme.lower() == 'https' else 80):
        raise ValueError('网页地址不可访问')
    hostname = parts.hostname.encode('idna').decode('ascii')
    if '%' in hostname: raise ValueError
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        addresses = {entry[4][0] for entry in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)}
    else:
        addresses = {str(literal)}
    if not addresses or any('%' in address or not ipaddress.ip_address(address).is_global or ipaddress.ip_address(address).is_multicast or ipaddress.ip_address(address).is_reserved for address in addresses):
        raise ValueError('网页地址不可访问')
    return parts, hostname, port, sorted(addresses)[0]


def fetch_page(url):
    try:
        for _ in range(6):
            parts, hostname, port, address = _target(url)
            url = urlunsplit((parts.scheme.lower(), f'[{hostname}]' if ':' in hostname else hostname, parts.path, parts.query, ''))
            conn = (_PinnedHTTPS if parts.scheme.lower() == 'https' else _PinnedHTTP)(hostname, port, address)
            try:
                path = quote(parts.path or '/', safe="/%:@!$&'()*+,;=-._~")
                if parts.query: path += '?' + quote(parts.query, safe="/%?:@!$&'()*+,;=-._~")
                conn.request('GET', path, headers={'Host': f'[{hostname}]' if ':' in hostname else hostname, 'Accept': 'text/html,text/plain', 'Accept-Encoding': 'identity', 'User-Agent': 'TufaScholar/1.0', 'Connection': 'close'})
                response = conn.getresponse()
                if response.status in (301, 302, 303, 307, 308):
                    location = response.getheader('Location')
                    if not location: raise ValueError
                    url = urljoin(url, location)
                    continue
                if response.status != 200: raise ValueError
                content_type = response.getheader('Content-Type', '').lower()
                media_type = content_type.split(';', 1)[0].strip()
                if media_type not in ('text/html', 'text/plain') or response.getheader('Content-Encoding', 'identity').lower().strip() not in ('', 'identity'): raise ValueError
                deadline = time.monotonic() + 20
                chunks, size = [], 0
                while True:
                    if time.monotonic() > deadline: raise ValueError
                    chunk = (response.read1 if hasattr(response, 'read1') else response.read)(min(65536, MAX_BYTES + 1 - size))
                    if not chunk: break
                    chunks.append(chunk); size += len(chunk)
                    if size > MAX_BYTES: raise ValueError
                data = b''.join(chunks)
                match = re.search(r'charset=([\w.-]+)', content_type)
                try: text = data.decode(match.group(1) if match else 'utf-8', errors='replace')
                except LookupError: text = data.decode('utf-8', errors='replace')
                if media_type == 'text/html':
                    parser = _Text(); parser.feed(text)
                    title, text = ''.join(parser.title).strip(), parser.result()
                else: title, text = '', text.strip()
                return {'url': url, 'title': title, 'markdown': text, 'retrieved_at': datetime.now(timezone.utc).isoformat()}
            finally:
                conn.close()
        raise ValueError
    except Exception:
        raise WebPageError('网页读取失败或地址不可访问') from None
