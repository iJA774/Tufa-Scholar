import unittest
from unittest.mock import patch
import httpx
from pydantic import BaseModel
from src.integrations.model import structured


class Output(BaseModel):
    answer: str


class ModelTests(unittest.TestCase):
    def test_missing_config_never_calls_http(self):
        with patch('src.integrations.model.settings.model_config', return_value={}), patch('src.integrations.model.httpx.post') as post:
            with self.assertRaisesRegex(RuntimeError, '设置'):
                structured('literature/direct', {}, Output)
            post.assert_not_called()

    def test_http_error_does_not_expose_key_or_body(self):
        request = httpx.Request('POST', 'https://example.invalid')
        response = httpx.Response(401, request=request, text='secret-key')
        config = {'base_url': 'https://example.invalid/v1', 'model': 'test', 'api_key': 'secret-key'}
        with patch('src.integrations.model.settings.model_config', return_value=config), patch('src.integrations.model.httpx.post', return_value=response):
            with self.assertRaises(RuntimeError) as error:
                structured('literature/direct', {}, Output)
        self.assertNotIn('secret-key', str(error.exception))
        self.assertIn('401', str(error.exception))
