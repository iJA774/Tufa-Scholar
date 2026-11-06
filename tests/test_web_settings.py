import logging
import os
import tempfile
import unittest
from unittest.mock import patch

from src import settings
from src.logging_setup import SafeFormatter, refresh_secrets
from src.storage import initialize_storage


class WebSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'TUFA_DATA_DIR': self.tmp.name}, clear=True)
        self.env.start()
        initialize_storage()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_defaults_and_provider_configuration(self):
        public = settings.public_settings()['web_search']
        self.assertEqual((public['engine'], public['engine_source']), ('duckduckgo', 'default'))
        self.assertEqual((public['wikipedia_lang'], public['wikipedia_lang_source']), ('zh', 'default'))
        self.assertEqual({p['id'] for p in public['providers']},
                         {'tavily', 'infoquest', 'duckduckgo', 'brave_search', 'serper', 'arxiv', 'searx', 'wikipedia'})
        self.assertEqual(settings.web_search_config(),
                         {'engine': 'duckduckgo', 'api_key': None, 'base_url': None, 'wikipedia_lang': 'zh'})

    def test_keys_are_private_and_blank_preserves_clear_removes(self):
        keys = {provider + '_api_key': provider + '-private' for provider in ('tavily', 'infoquest', 'brave_search', 'serper')}
        public = settings.update({'web_search_engine': 'tavily', **keys})['web_search']
        for secret in keys.values():
            self.assertNotIn(secret, repr(public))
        self.assertEqual(settings.web_search_config()['api_key'], keys['tavily_api_key'])
        settings.update({'tavily_api_key': ''})
        self.assertEqual(settings.web_search_config()['api_key'], keys['tavily_api_key'])
        settings.update({'clear_tavily_api_key': True, 'tavily_api_key': 'replacement'})
        self.assertIsNone(settings.web_search_config()['api_key'])

    def test_environment_precedence_and_clear_only_local_key(self):
        settings.update({'web_search_engine': 'tavily', 'tavily_api_key': 'local-secret',
                         'searx_base_url': 'http://localhost:8080', 'wikipedia_lang': 'fr'})
        with patch.dict(os.environ, {'SEARCH_API': 'tavily', 'TAVILY_API_KEY': 'env-secret',
                                     'SEARX_HOST': 'https://search.example', 'WIKIPEDIA_LANG': 'en'}):
            public = settings.update({'clear_tavily_api_key': True})['web_search']
            self.assertEqual(public['engine_source'], 'environment')
            self.assertEqual(public['searx_base_url_source'], 'environment')
            self.assertEqual(public['wikipedia_lang_source'], 'environment')
            self.assertEqual(next(p for p in public['providers'] if p['id'] == 'tavily')['key_source'], 'environment')
            self.assertEqual(settings.web_search_config()['api_key'], 'env-secret')
        self.assertIsNone(settings.web_search_config()['api_key'])
        settings.update({'web_search_engine': 'searx'})
        self.assertEqual(settings.web_search_config()['base_url'], 'http://localhost:8080')

    def test_invalid_values_rejected_without_partial_write_or_echo(self):
        for field, value in [('web_search_engine', 'not-an-engine'),
                             ('searx_base_url', 'https://user:password@example.com'),
                             ('searx_base_url', 'https://example.com/?secret=bad'),
                             ('wikipedia_lang', '../secret')]:
            with self.subTest(field=field, value=value):
                with self.assertRaises(ValueError) as error:
                    settings.update({field: value, 'serper_api_key': 'private-secret'})
                self.assertNotIn(value, str(error.exception))
                self.assertFalse(next(p for p in settings.public_settings()['web_search']['providers'] if p['id'] == 'serper')['key_configured'])

    def test_all_search_keys_redacted_from_logs(self):
        settings.update({'tavily_api_key': 'saved-tavily', 'infoquest_api_key': 'saved-infoquest',
                         'brave_search_api_key': 'saved-brave', 'serper_api_key': 'saved-serper'})
        with patch.dict(os.environ, {'TAVILY_API_KEY': 'environment-tavily', 'INFOQUEST_API_KEY': 'environment-infoquest',
                                     'BRAVE_SEARCH_API_KEY': 'environment-brave', 'SERPER_API_KEY': 'environment-serper'}):
            refresh_secrets()
            message = ' '.join(['saved-tavily', 'saved-infoquest', 'saved-brave', 'saved-serper',
                                'environment-tavily', 'environment-infoquest', 'environment-brave', 'environment-serper'])
            record = logging.LogRecord('test', logging.INFO, __file__, 1, message, (), None)
            rendered = SafeFormatter('%(message)s').format(record)
            for secret in message.split():
                self.assertNotIn(secret, rendered)


if __name__ == '__main__':
    unittest.main()
