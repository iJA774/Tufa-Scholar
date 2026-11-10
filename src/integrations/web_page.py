"""Fetch public web pages without proxies or ambient credentials."""
import http.client
import ipaddress
import logging
import re
import socket
import ssl
import subprocess
import sys
import httpx
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit, quote

MAX_BYTES = 2_000_000
logger = logging.getLogger(__name__)


def _extract_pdf(data):
    import io
    import pypdf.filters as filters
    from pypdf import PdfReader

    for name in ('FLATE_MAX_BUFFER_SIZE', 'JBIG2_MAX_OUTPUT_LENGTH', 'LZW_MAX_OUTPUT_LENGTH',
                 'MAX_ARRAY_BASED_STREAM_OUTPUT_LENGTH', 'MAX_DECLARED_STREAM_LENGTH',
                 'RUN_LENGTH_MAX_OUTPUT_LENGTH', 'ZLIB_MAX_OUTPUT_LENGTH'):
        if hasattr(filters, name):
            setattr(filters, name, min(getattr(filters, name), 8_000_000))
    if not data.startswith(b'%PDF-'):
        raise ValueError('invalid PDF')
    reader = PdfReader(io.BytesIO(data), strict=True)
    if reader.is_encrypted or not reader.pages:
        raise ValueError('unreadable PDF')
    parts, found_text, remaining = [], False, 200_000
    for i, page in enumerate(reader.pages):
        if i >= 50 or remaining <= 0:
            parts.append('[PDF 内容已截断]')
            break
        text = (page.extract_text() or '').strip()
        if text:
            found_text = True
            parts.append(f'## 第 {i + 1} 页\n\n' + text[:remaining])
            truncated = len(text) > remaining
            remaining -= len(text[:remaining])
            if truncated:
                parts.append('[PDF 内容已截断]')
                break
    if not found_text:
        raise ValueError('no extractable text')
    return '\n\n'.join(parts)


def _pdf_text(data):
    if not data.startswith(b'%PDF-'):
        raise ValueError('invalid PDF')
    result = subprocess.run([sys.executable, '-I', '-B', __file__, '--extract-pdf'], input=data,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20, check=True,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return result.stdout.decode('utf-8')


class WebPageError(RuntimeError):
    def __init__(self, message='网页读取失败或地址不可访问', *, category='reader_error', status=None, reason=None):
        super().__init__(message)
        self.category = category
        self.status = status
        self.reason = reason or category


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, port, addresses):
        super().__init__(host, port, timeout=10)
        self.addresses = addresses

    def connect(self):
        deadline = time.monotonic() + 10
        last_failure = None
        saw_timeout = False
        for index, address in enumerate(self.addresses):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                saw_timeout = True
                break
            # Share the existing ten-second connect budget so a silent first
            # address still leaves time to try another resolver-preferred IP.
            timeout = remaining / (len(self.addresses) - index)
            sock = None
            try:
                sock = socket.create_connection((address, self.port), timeout)
                sock.settimeout(10)
                self.sock = sock
                return
            except TimeoutError:
                if sock is not None:
                    sock.close()
                saw_timeout = True
                last_failure = WebPageError(category='timeout', reason='connect_timeout')
            except OSError:
                if sock is not None:
                    sock.close()
                last_failure = WebPageError(category='connect_error', reason='connect_failed')
        if saw_timeout:
            raise WebPageError(category='timeout', reason='connect_timeout') from None
        if last_failure is not None:
            raise last_failure
        raise WebPageError(category='connect_error', reason='connect_failed')


class _PinnedHTTPS(_PinnedHTTP):
    def connect(self):
        super().connect()
        try:
            self.sock = httpx.create_ssl_context(verify=True, trust_env=False).wrap_socket(self.sock, server_hostname=self.host)
        except TimeoutError:
            raise WebPageError(category='timeout', reason='tls_handshake_timeout') from None
        except ConnectionResetError:
            raise WebPageError(category='tls_error', reason='tls_handshake_reset') from None
        except ssl.SSLCertVerificationError:
            raise WebPageError(category='tls_error', reason='certificate_verification') from None
        except (ssl.SSLError, OSError):
            raise WebPageError(category='tls_error', reason='tls_handshake_failed') from None


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
    try:
        if len(url) > 4096:
            raise WebPageError(category='invalid_address')
        parts = urlsplit(url)
        if parts.scheme.lower() not in ('http', 'https') or not parts.hostname or parts.username is not None or parts.password is not None:
            raise WebPageError(category='invalid_address')
        port = parts.port if parts.port is not None else (443 if parts.scheme.lower() == 'https' else 80)
        if port != (443 if parts.scheme.lower() == 'https' else 80):
            raise WebPageError(category='invalid_address')
        hostname = parts.hostname.encode('idna').decode('ascii')
        if '%' in hostname:
            raise WebPageError(category='invalid_address')
        try:
            literal = ipaddress.ip_address(hostname)
        except ValueError:
            try:
                resolved = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
            except OSError:
                raise WebPageError(category='dns_error') from None
            # Keep the resolver's address preference: only move on when a pinned
            # TCP connection fails, and never send a second HTTP request.
            raw_addresses = (entry[4][0] for entry in resolved)
        else:
            raw_addresses = (str(literal),)
        addresses = []
        for address in raw_addresses:
            try:
                parsed = ipaddress.ip_address(address)
            except ValueError:
                raise WebPageError(category='invalid_address') from None
            if '%' in address or not parsed.is_global or parsed.is_multicast or parsed.is_reserved:
                raise WebPageError(category='invalid_address', reason='non_public_address')
            normalized = str(parsed)
            if normalized not in addresses:
                addresses.append(normalized)
        if not addresses:
            raise WebPageError(category='dns_error')
        return parts, hostname, port, tuple(addresses)
    except WebPageError:
        raise
    except (UnicodeError, ValueError):
        raise WebPageError(category='invalid_address') from None


def fetch_page(url):
    started = time.monotonic()
    try:
        for _ in range(6):
            parts, hostname, port, addresses = _target(url)
            url = urlunsplit((parts.scheme.lower(), f'[{hostname}]' if ':' in hostname else hostname, parts.path, parts.query, ''))
            connection_type = _PinnedHTTPS if parts.scheme.lower() == 'https' else _PinnedHTTP
            conn = connection_type(hostname, port, addresses)
            try:
                path = quote(parts.path or '/', safe="/%:@!$&'()*+,;=-._~")
                if parts.query: path += '?' + quote(parts.query, safe="/%?:@!$&'()*+,;=-._~")
                try:
                    conn.request('GET', path, headers={'Host': f'[{hostname}]' if ':' in hostname else hostname, 'Accept': 'text/html,text/plain,application/pdf', 'Accept-Encoding': 'identity', 'User-Agent': 'TufaScholar/1.0', 'Connection': 'close'})
                except WebPageError:
                    raise
                except TimeoutError:
                    raise WebPageError(category='timeout', reason='request_timeout') from None
                except (OSError, http.client.HTTPException):
                    raise WebPageError(category='read_error', reason='request_failed') from None
                try:
                    response = conn.getresponse()
                except TimeoutError:
                    raise WebPageError(category='timeout', reason='response_timeout') from None
                except (OSError, http.client.HTTPException):
                    raise WebPageError(category='read_error', reason='response_failed') from None
                if response.status in (301, 302, 303, 307, 308):
                    location = response.getheader('Location')
                    if not location: raise WebPageError(category='redirect_error', reason='missing_location')
                    url = urljoin(url, location)
                    continue
                if response.status != 200:
                    raise WebPageError(category='http_status', status=response.status)
                content_type = response.getheader('Content-Type', '').lower()
                media_type = content_type.split(';', 1)[0].strip()
                if media_type not in ('text/html', 'text/plain', 'application/pdf') or response.getheader('Content-Encoding', 'identity').lower().strip() not in ('', 'identity'):
                    raise WebPageError(category='unsupported_content')
                deadline = time.monotonic() + 20
                chunks, size = [], 0
                while True:
                    if time.monotonic() > deadline: raise WebPageError(category='timeout', reason='body_read_timeout')
                    try:
                        chunk = (response.read1 if hasattr(response, 'read1') else response.read)(min(65536, MAX_BYTES + 1 - size))
                    except TimeoutError:
                        raise WebPageError(category='timeout', reason='body_read_timeout') from None
                    except (OSError, http.client.HTTPException):
                        raise WebPageError(category='read_error', reason='body_read_failed') from None
                    if not chunk: break
                    chunks.append(chunk); size += len(chunk)
                    if size > MAX_BYTES: raise WebPageError(category='size_limit')
                data = b''.join(chunks)
                if media_type == 'application/pdf':
                    try:
                        title, text = '', _pdf_text(data)
                    except subprocess.TimeoutExpired:
                        raise WebPageError(category='timeout', reason='pdf_parse_timeout') from None
                    except ValueError:
                        raise WebPageError(category='invalid_content', reason='invalid_pdf') from None
                    except Exception:
                        raise WebPageError(category='parse_error', reason='pdf_parse_failed') from None
                else:
                    match = re.search(r'charset=([\w.-]+)', content_type)
                    try: text = data.decode(match.group(1) if match else 'utf-8', errors='replace')
                    except LookupError: text = data.decode('utf-8', errors='replace')
                    if media_type == 'text/html':
                        try:
                            parser = _Text(); parser.feed(text)
                            title, text = ''.join(parser.title).strip(), parser.result()
                        except Exception:
                            raise WebPageError(category='parse_error', reason='html_parse_failed') from None
                    else: title, text = '', text.strip()
                outcome, reason = ('success', 'ok') if text.strip() else ('empty', 'no_extractable_text')
                logger.info('web_page completed outcome=%s reason=%s status=200 elapsed_ms=%d', outcome, reason, int((time.monotonic() - started) * 1000))
                return {'url': url, 'title': title, 'markdown': text, 'retrieved_at': datetime.now(timezone.utc).isoformat()}
            finally:
                conn.close()
        raise WebPageError(category='redirect_error', reason='redirect_limit')
    except WebPageError as failure:
        logger.warning('web_page failed outcome=failed category=%s reason=%s status=%s elapsed_ms=%d', failure.category, failure.reason, failure.status if failure.status is not None else '-', int((time.monotonic() - started) * 1000))
        raise failure from None
    except (TimeoutError, socket.timeout):
        logger.warning('web_page failed outcome=failed category=timeout reason=io_timeout status=- elapsed_ms=%d', int((time.monotonic() - started) * 1000))
        raise WebPageError(category='timeout', reason='io_timeout') from None
    except Exception:
        logger.warning('web_page failed outcome=failed category=reader_error reason=unexpected_error status=- elapsed_ms=%d', int((time.monotonic() - started) * 1000))
        raise WebPageError(category='reader_error', reason='unexpected_error') from None


if __name__ == '__main__' and sys.argv[1:] == ['--extract-pdf']:
    sys.stdout.buffer.write(_extract_pdf(sys.stdin.buffer.read()).encode('utf-8'))
