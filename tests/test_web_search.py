import ssl
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from ddgs.exceptions import DDGSException, TimeoutException, RatelimitException

from src.integrations.web_search import WebSearchError, _loopback_https_proxy_configured, search_web, search_web_fallback


class WebSearchTests(unittest.TestCase):
    def config(self, engine, **kwargs):
        return {"engine": engine, "api_key": "secret-key", "base_url": "http://localhost:8080", "wikipedia_lang": "zh", **kwargs}

    def response(self, data, status=200):
        return httpx.Response(status, json=data, request=httpx.Request("GET", "https://service.test"))

    def test_provider_protocols_and_normalisation(self):
        row = {"title": "A title", "url": "HTTPS://Example.com:443/p#fragment", "content": "<b>Text</b> &amp; info"}
        cases = [
            ("tavily", {"results": [row, row]}, "POST", "https://api.tavily.com/search", "Authorization", "Bearer secret-key", "web_snippet"),
            ("infoquest", {"search_result": {"results": [{"content": {"results": {"organic": [row]}}}]}}, "POST", "https://search.infoquest.bytepluses.com", "Authorization", "Bearer secret-key", "web_snippet"),
            ("brave_search", {"web": {"results": [row]}}, "GET", "https://api.search.brave.com/res/v1/web/search", "X-Subscription-Token", "secret-key", "web_snippet"),
            ("serper", {"organic": [row]}, "POST", "https://google.serper.dev/search", "X-API-KEY", "secret-key", "web_snippet"),
            ("searx", {"results": [row]}, "GET", "http://localhost:8080/search", None, None, "web_snippet"),
        ]
        for engine, payload, method, endpoint, header, secret, level in cases:
            with self.subTest(engine=engine), patch("src.integrations.web_search.web_search_config", return_value=self.config(engine)), patch("src.integrations.web_search.httpx.request", return_value=self.response(payload)) as request:
                result = search_web("test", 5)
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0]["url"], "https://example.com/p")
                self.assertEqual(result[0]["content"], "Text & info")
                self.assertEqual(result[0]["provider"], engine)
                self.assertEqual(result[0]["source_level"], level)
                self.assertTrue(result[0]["retrieved_at"])
                args, kwargs = request.call_args
                self.assertEqual(args, (method, endpoint))
                if engine == "tavily":
                    self.assertEqual(kwargs["timeout"].connect, 5)
                    self.assertEqual(kwargs["timeout"].read, 20)
                else:
                    self.assertEqual(kwargs["timeout"], 20)
                self.assertFalse(kwargs["follow_redirects"])
                if header:
                    self.assertEqual(kwargs["headers"][header], secret)
                    self.assertNotIn("secret-key", str(kwargs.get("params", "")))
                    self.assertNotIn("secret-key", str(kwargs.get("json", "")))

    def test_tavily_raw_content_is_distinguished_and_bounded(self):
        response = self.response({"results": [{"title": "T", "url": "https://example.com", "content": "snippet", "raw_content": "x" * 5000}]})
        with patch("src.integrations.web_search.web_search_config", return_value=self.config("tavily")), patch("src.integrations.web_search.httpx.request", return_value=response) as request:
            result = search_web("q")[0]
            self.assertEqual(result["source_level"], "web_content")
            self.assertEqual(len(result["content"]), 4000)
            self.assertTrue(request.call_args.kwargs["json"]["include_raw_content"])

    def test_tavily_falls_back_directly_once_after_loopback_proxy_connect_failure(self):
        request = httpx.Request("POST", "https://api.tavily.com/search")
        row = {"title": "Title", "url": "https://example.org", "raw_content": "Full result"}
        response = self.response({"results": [row]})
        for failure in (
            httpx.ProxyError("local proxy secret", request=request),
            httpx.ConnectError("local proxy secret", request=request),
            httpx.ConnectTimeout("local proxy secret", request=request),
        ):
            with self.subTest(failure=type(failure).__name__), \
                    patch("src.integrations.web_search.web_search_config", return_value=self.config("tavily")), \
                    patch("src.integrations.web_search._loopback_https_proxy_configured", return_value=True), \
                    patch("src.integrations.web_search.httpx.request", side_effect=[failure, response]) as request_mock:
                with self.assertLogs("src.integrations.web_search", level="WARNING") as logs:
                    result = search_web("private query")
                self.assertEqual((len(result), result[0]["source_level"], result[0]["content"]), (1, "web_content", "Full result"))
                self.assertEqual(request_mock.call_count, 2)
                proxied, direct = (call.kwargs for call in request_mock.call_args_list)
                self.assertEqual(request_mock.call_args_list[0].args, ("POST", "https://api.tavily.com/search"))
                self.assertEqual(request_mock.call_args_list[1].args, ("POST", "https://api.tavily.com/search"))
                self.assertEqual(proxied["headers"]["Authorization"], "Bearer secret-key")
                self.assertEqual(direct["headers"]["Authorization"], "Bearer secret-key")
                self.assertNotIn("trust_env", proxied)
                self.assertNotIn("verify", proxied)
                self.assertEqual(proxied["json"], direct["json"])
                self.assertEqual(direct["json"], {"query": "private query", "max_results": 5, "include_raw_content": True})
                self.assertTrue(direct["timeout"].connect <= 5)
                self.assertEqual(direct["timeout"].read, 20)
                self.assertFalse(direct["trust_env"])
                self.assertTrue(direct["verify"])
                self.assertFalse(direct["follow_redirects"])
                self.assertIn("proxy_recovery=direct", str(logs.output))
                for private in ("secret-key", "private query", "api.tavily.com", "local proxy secret"):
                    self.assertNotIn(private, str(logs.output))

    def test_tavily_does_not_retry_read_or_http_errors(self):
        request = httpx.Request("POST", "https://api.tavily.com/search")
        failures = [
            httpx.ReadTimeout("after send", request=request),
            self.response({"error": "secret-key"}, status=503),
        ]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), \
                    patch("src.integrations.web_search.web_search_config", return_value=self.config("tavily")), \
                    patch("src.integrations.web_search._loopback_https_proxy_configured", return_value=True), \
                    patch("src.integrations.web_search.httpx.request", side_effect=failure) as request_mock:
                with self.assertRaises(WebSearchError):
                    search_web("private query")
                request_mock.assert_called_once()

    def test_tavily_does_not_bypass_proxy_on_certificate_verification_failure(self):
        request = httpx.Request("POST", "https://api.tavily.com/search")
        failure = httpx.ConnectError("TLS verification failed", request=request)
        failure.__cause__ = ssl.SSLCertVerificationError("untrusted certificate")
        with patch("src.integrations.web_search.web_search_config", return_value=self.config("tavily")), \
                patch("src.integrations.web_search._loopback_https_proxy_configured", return_value=True), \
                patch("src.integrations.web_search.httpx.request", side_effect=failure) as request_mock:
            with self.assertRaises(WebSearchError):
                search_web("q")
        request_mock.assert_called_once()

    def test_loopback_proxy_detection_and_provider_scope(self):
        for proxies, bypass, expected in (
            ({"https": "http://127.0.0.1:7890"}, False, True),
            ({"https": "http://[::1]:7890"}, False, True),
            ({"https": "http://localhost:7890"}, False, True),
            ({"all": "http://127.0.0.1:7890"}, False, True),
            ({"https": "http://proxy.example:7890"}, False, False),
            ({"all": "http://127.0.0.1:7890"}, True, False),
            ({}, False, False),
        ):
            with self.subTest(proxies=proxies, bypass=bypass), \
                    patch("src.integrations.web_search.urllib.request.getproxies", return_value=proxies), \
                    patch("src.integrations.web_search.urllib.request.proxy_bypass", return_value=bypass):
                self.assertEqual(_loopback_https_proxy_configured("https://api.tavily.com/search"), expected)

        error = httpx.ConnectError("direct failure", request=httpx.Request("POST", "https://service.test"))
        for engine, proxies, bypass in (
            ("tavily", {}, False),
            ("tavily", {"https": "http://proxy.example:7890"}, False),
            ("tavily", {"https": "http://127.0.0.1:7890"}, True),
            ("infoquest", {"https": "http://127.0.0.1:7890"}, False),
        ):
            with self.subTest(engine=engine, proxies=proxies, bypass=bypass), \
                    patch("src.integrations.web_search.web_search_config", return_value=self.config(engine)), \
                    patch("src.integrations.web_search.urllib.request.getproxies", return_value=proxies), \
                    patch("src.integrations.web_search.urllib.request.proxy_bypass", return_value=bypass), \
                    patch("src.integrations.web_search.httpx.request", side_effect=error) as request_mock:
                with self.assertRaises(WebSearchError):
                    search_web("q")
                request_mock.assert_called_once()

    def test_tavily_direct_fallback_preserves_explicit_ca_bundle(self):
        marker = object()
        response = self.response({"results": []})
        with patch("src.integrations.web_search.web_search_config", return_value=self.config("tavily")), \
                patch("src.integrations.web_search._loopback_https_proxy_configured", return_value=True), \
                patch("src.integrations.web_search.os.environ", {"SSL_CERT_FILE": "C:/certs/custom.pem"}), \
                patch("src.integrations.web_search.ssl.create_default_context", return_value=marker) as create_context, \
                patch("src.integrations.web_search.httpx.request", side_effect=[
                    httpx.ConnectError("proxy failure", request=httpx.Request("POST", "https://api.tavily.com/search")),
                    response,
                ]) as request_mock:
            self.assertEqual(search_web("q"), [])
        create_context.assert_called_once_with(cafile="C:/certs/custom.pem")
        self.assertIs(request_mock.call_args.kwargs["verify"], marker)

    def test_duckduckgo_real_parser_handles_results_and_empty(self):
        html = '<html><div class="body"><h2>Title</h2><a href="https://example.org">body</a></div></html>'
        for body, expected in [(html, 1), ('<html><body>No results</body></html>', 0)]:
            with self.subTest(expected=expected), patch('src.integrations.web_search.web_search_config', return_value=self.config('duckduckgo')), patch('ddgs.http_client2.HttpClient2.request', return_value=SimpleNamespace(status_code=200, text=body)) as request:
                with self.assertLogs('src.integrations.web_search', level='INFO') as logs:
                    result = search_web('private-query')
                self.assertEqual(len(result), expected)
                if expected: self.assertEqual((result[0]['title'], result[0]['url'], result[0]['content']), ('Title', 'https://example.org/', 'body'))
                else: self.assertIn('outcome=empty', str(logs.output))
                self.assertEqual(request.call_count, 1)

    def test_duckduckgo_challenge_and_http_errors_are_not_empty(self):
        for status, body, category in [(202, 'secret anomaly-modal private-query', 'challenge'), (200, '<div class="anomaly-modal">secret</div>', 'challenge'), (429, 'secret', 'rate_limit'), (503, 'secret', 'http_status')]:
            with self.subTest(status=status), patch('src.integrations.web_search.web_search_config', return_value=self.config('duckduckgo')), patch('ddgs.http_client2.HttpClient2.request', return_value=SimpleNamespace(status_code=status, text=body)):
                with self.assertLogs('src.integrations.web_search', level='WARNING') as logs, self.assertRaises(WebSearchError) as raised: search_web('private-query')
                self.assertEqual((raised.exception.category, raised.exception.status), (category, status))
                text = str(logs.output)
                self.assertIn('category=' + category, text); self.assertIn('status=' + str(status), text)
                self.assertNotIn('secret', text); self.assertNotIn('private-query', text); self.assertNotIn('anomaly-modal', text)

    def test_wikipedia_and_arxiv(self):
        search = self.response({"query": {"search": [{"title": "科学 Research", "pageid": 17, "snippet": "<span>词条</span>"}]}})
        extracts = self.response({"query": {"pages": {"17": {"extract": "百科全文摘要"}}}})
        with patch("src.integrations.web_search.web_search_config", return_value=self.config("wikipedia")), patch("src.integrations.web_search.httpx.request", side_effect=[search, extracts]) as request:
            result = search_web("science")[0]
            self.assertEqual(result["content"], "百科全文摘要")
            self.assertEqual(result["source_level"], "web_content")
            self.assertTrue(result["url"].startswith("https://zh.wikipedia.org/wiki/"))
            self.assertEqual(request.call_args_list[0].kwargs["params"]["list"], "search")
            self.assertEqual(request.call_args_list[1].kwargs["params"]["pageids"], "17")
            self.assertEqual(request.call_args_list[1].kwargs["params"]["exlimit"], "max")
        with patch("src.integrations.web_search.web_search_config", return_value=self.config("wikipedia")), patch("src.integrations.web_search.httpx.request", side_effect=[search, self.response({"query": {"pages": {"17": {}}}})]):
            result = search_web("science")[0]
            self.assertEqual(result["content"], "词条")
            self.assertEqual(result["source_level"], "web_snippet")
        feed = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>https://arxiv.org/abs/1234.5678</id><title>Paper</title><summary>Abstract text</summary><published>2024-01-02T00:00:00Z</published></entry></feed>'''
        response = httpx.Response(200, content=feed, request=httpx.Request("GET", "https://export.arxiv.org"))
        with patch("src.integrations.web_search.web_search_config", return_value=self.config("arxiv")), patch("src.integrations.web_search.httpx.request", return_value=response) as request:
            result = search_web("science")[0]
            self.assertEqual(result["source_level"], "abstract")
            self.assertEqual(result["content"], "Abstract text")
            self.assertEqual(result["published_at"], "2024-01-02T00:00:00Z")
            self.assertEqual(request.call_args.kwargs["params"]["search_query"], "all:science")

    def test_empty_malformed_timeout_and_redaction(self):
        with patch("src.integrations.web_search.web_search_config", return_value=self.config("tavily")), patch("src.integrations.web_search._loopback_https_proxy_configured", return_value=False), patch("src.integrations.web_search.httpx.request") as request:
            request.return_value = self.response({"results": []})
            self.assertEqual(search_web("q"), [])
            for payload in ({"error": "secret-key"}, {"results": "wrong"}, {"results": [{"title": "bad", "url": "javascript:alert(1)"}]}):
                request.return_value = self.response(payload)
                with self.assertRaises(RuntimeError) as exc:
                    search_web("q")
                self.assertNotIn("secret-key", str(exc.exception))
            request.side_effect = httpx.ConnectTimeout("secret-key and URL")
            with self.assertRaises(RuntimeError) as exc:
                search_web("q")
            self.assertNotIn("secret-key", str(exc.exception))
        with patch("src.integrations.web_search.web_search_config", return_value=self.config("duckduckgo")), patch("src.integrations.web_search._Duckduckgo", side_effect=RuntimeError("secret-key")):
            with self.assertRaises(RuntimeError) as exc:
                search_web("q")
            self.assertNotIn("secret-key", str(exc.exception))

    def test_search_logs_outcomes_and_redacts_sensitive_details(self):
        row = {'title': 'title', 'url': 'https://example.com', 'content': 'body'}
        with patch('src.integrations.web_search.web_search_config', return_value=self.config('tavily')), patch('src.integrations.web_search.httpx.request', side_effect=[self.response({'results': [row]}), self.response({'results': []})]):
            with self.assertLogs('src.integrations.web_search', level='INFO') as logs:
                self.assertEqual(len(search_web('private-query')), 1)
                self.assertEqual(search_web('private-query'), [])
        self.assertIn('outcome=success result_count=1', logs.output[0])
        self.assertIn('outcome=empty result_count=0', logs.output[1])
        self.assertTrue(all('elapsed_ms=' in line and 'engine=tavily' in line for line in logs.output))
        self.assertNotIn('private-query', str(logs.output))
        self.assertNotIn('secret-key', str(logs.output))
        self.assertNotIn('https://', str(logs.output))

    def test_search_failure_categories_are_safe(self):
        request = httpx.Request('GET', 'https://secret-url.invalid/private-query')
        cases = [
            (self.response({}, 503), 'http_status', 'status=503'),
            (httpx.ConnectTimeout('secret-key private-query', request=request), 'timeout', None),
            (httpx.ConnectError('secret-key private-query', request=request), 'network', None),
            (self.response({'error': 'secret-key'}), 'invalid_response', None),
        ]
        for response, category, status in cases:
            with self.subTest(category=category), patch('src.integrations.web_search.web_search_config', return_value=self.config('tavily')), patch('src.integrations.web_search._loopback_https_proxy_configured', return_value=False), patch('src.integrations.web_search.httpx.request') as request_mock:
                if isinstance(response, Exception): request_mock.side_effect = response
                else: request_mock.return_value = response
                with self.assertLogs('src.integrations.web_search', level='WARNING') as logs, self.assertRaises(RuntimeError): search_web('private-query')
                text = ' '.join(logs.output)
                self.assertIn('category=' + category, text)
                if status: self.assertIn(status, text)
                for value in ('secret-key', 'private-query', 'secret-url', 'https://'): self.assertNotIn(value, text)
        for exc, category in [(TimeoutException('secret-key'), 'timeout'), (RatelimitException('secret-key'), 'rate_limit'), (DDGSException('secret-key'), 'provider_error')]:
            with self.subTest(category=category), patch('src.integrations.web_search.web_search_config', return_value=self.config('duckduckgo')), patch('src.integrations.web_search._Duckduckgo', side_effect=exc):
                with self.assertLogs('src.integrations.web_search', level='WARNING') as logs, self.assertRaises(RuntimeError): search_web('private-query')
                self.assertIn('category=' + category, str(logs.output))
                self.assertNotIn('secret-key', str(logs.output))
        for config, category in [(self.config('serper', api_key=''), 'missing_key'), (self.config('private-secret-engine'), 'configuration')]:
            with patch('src.integrations.web_search.web_search_config', return_value=config):
                with self.assertLogs('src.integrations.web_search', level='WARNING') as logs, self.assertRaises(RuntimeError): search_web('private-query')
                self.assertIn('category=' + category, str(logs.output))
                self.assertNotIn('private-secret-engine', str(logs.output))

    def test_typed_failure_contract_and_wrapped_causes(self):
        for status, category in [(401, 'auth'), (403, 'auth'), (429, 'rate_limit'), (503, 'http_status')]:
            with self.subTest(status=status), patch('src.integrations.web_search.web_search_config', return_value=self.config('tavily')), patch('src.integrations.web_search.httpx.request', return_value=self.response({}, status)):
                with self.assertRaises(WebSearchError) as raised:
                    search_web('private-query')
                self.assertEqual((raised.exception.category, raised.exception.status), (category, status))
                self.assertNotIn('private-query', str(raised.exception))
        for cause, category in [(httpx.ConnectTimeout('secret'), 'timeout'), (httpx.ConnectError('secret'), 'network')]:
            wrapped = DDGSException('secret')
            wrapped.__cause__ = cause
            with self.subTest(category=category), patch('src.integrations.web_search.web_search_config', return_value=self.config('duckduckgo')), patch('src.integrations.web_search._Duckduckgo', side_effect=wrapped):
                with self.assertRaises(WebSearchError) as raised:
                    search_web('private-query')
                self.assertEqual((raised.exception.category, raised.exception.status), (category, None))
                self.assertNotIn('secret', str(raised.exception))
        with patch('src.integrations.web_search.web_search_config', return_value=self.config('serper', api_key='')):
            with self.assertRaises(WebSearchError) as raised:
                search_web('q')
            self.assertEqual((raised.exception.category, raised.exception.status), ('missing_key', None))

    def test_fallback_success_empty_and_failure(self):
        with patch('src.integrations.web_search.DDGS') as client:
            client.return_value.text.return_value = [{'title': 'Title', 'href': 'https://example.org/a#frag', 'body': '<b>Text</b>'}]
            result = search_web_fallback('private query', 2)
            self.assertEqual((result[0]['url'], result[0]['content'], result[0]['provider']), ('https://example.org/a', 'Text', 'ddgs_fallback'))
            self.assertTrue(result[0]['retrieved_at'])
            client.return_value.text.assert_called_once_with('private query', max_results=2, backend='bing,yahoo')
            client.return_value.text.side_effect = DDGSException('No results found.')
            self.assertEqual(search_web_fallback('q'), [])
            client.return_value.text.side_effect = DDGSException('private query secret')
            with self.assertLogs('src.integrations.web_search', level='WARNING') as logs, self.assertRaises(WebSearchError) as raised:
                search_web_fallback('private query')
            self.assertEqual(raised.exception.category, 'provider_error')
            self.assertNotIn('secret', str(logs.output) + str(raised.exception))

    def test_fallback_missing_backend_never_uses_auto(self):
        with patch.dict('src.integrations.web_search.ENGINES', {'text': {}}, clear=True), patch('src.integrations.web_search.DDGS') as client:
            with self.assertRaises(WebSearchError) as raised:
                search_web_fallback('q')
            self.assertEqual(raised.exception.category, 'provider_error')
            client.assert_not_called()

    def test_no_key_invalid_config_and_redirect(self):
        with patch("src.integrations.web_search.web_search_config", return_value=self.config("serper", api_key="")), patch("src.integrations.web_search.httpx.request") as request:
            with self.assertRaises(RuntimeError):
                search_web("q")
            request.assert_not_called()
        with patch("src.integrations.web_search.web_search_config", return_value=self.config("searx", base_url="https://user:secret@example.com")), patch("src.integrations.web_search.httpx.request") as request:
            with self.assertRaises(RuntimeError) as exc:
                search_web("q")
            self.assertNotIn("secret", str(exc.exception))
            request.assert_not_called()
        with patch("src.integrations.web_search.web_search_config", return_value=self.config("tavily")), patch("src.integrations.web_search.httpx.request", return_value=self.response({}, 302)):
            with self.assertRaises(RuntimeError):
                search_web("q")


if __name__ == "__main__":
    unittest.main()
