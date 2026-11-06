import unittest
from unittest.mock import patch

import httpx
from ddgs.exceptions import DDGSException

from src.integrations.web_search import search_web


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

    def test_duckduckgo_selects_exact_backend(self):
        with patch("src.integrations.web_search.web_search_config", return_value=self.config("duckduckgo")), patch("src.integrations.web_search.DDGS") as client:
            client.return_value.text.return_value = [{"title": "Title", "href": "https://example.org", "body": "body"}]
            self.assertEqual(search_web("q")[0]["content"], "body")
            client.assert_called_once_with(timeout=20)
            client.return_value.text.assert_called_once_with("q", max_results=5, backend="duckduckgo")
            client.return_value.text.side_effect = DDGSException("No results found.")
            self.assertEqual(search_web("q"), [])

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
        with patch("src.integrations.web_search.web_search_config", return_value=self.config("tavily")), patch("src.integrations.web_search.httpx.request") as request:
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
        with patch("src.integrations.web_search.web_search_config", return_value=self.config("duckduckgo")), patch("src.integrations.web_search.DDGS", side_effect=RuntimeError("secret-key")):
            with self.assertRaises(RuntimeError) as exc:
                search_web("q")
            self.assertNotIn("secret-key", str(exc.exception))

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
