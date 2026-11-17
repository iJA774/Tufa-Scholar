import tempfile
import unittest
from unittest.mock import patch
from pydantic import ValidationError
from src.agents.preprocesser import Classification, classify_markdown
from src.infrastructure import settings
from src.persistence.storage import initialize_storage
from src.persistence.document_classifications import get_classification, get_classification_by_digest, upsert_classification


class PreprocesserTests(unittest.TestCase):
    def test_storage_and_settings(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'TUFA_DATA_DIR': directory}, clear=True):
            initialize_storage()
            settings.update({'openai_model': 'general', 'preprocesser_model': 'local'})
            self.assertEqual(settings.preprocesser_config()['model'], 'local')
            with patch.dict('os.environ', {'TUFA_PREPROCESSER_MODEL': 'environment'}):
                self.assertEqual(settings.public_settings()['preprocesser'], {'model': 'environment', 'model_source': 'environment'})
            settings.update({'preprocesser_model': ''})
            self.assertEqual(settings.public_settings()['preprocesser'], {'model': 'general', 'model_source': 'default'})
            upsert_classification('a.md', '123', {'domains': [' AI ', 'AI'], 'tags': ['tag'], 'description': '摘要'})
            self.assertEqual(get_classification('a.md')['domains'], ['AI'])
            with self.assertRaises(ValueError):
                upsert_classification('a.md', '456', {'domains': [], 'tags': [], 'description': '字' * 21})
            self.assertEqual(get_classification('a.md')['digest'], '123')
            upsert_classification('a.md', '456', {'domains': ['新'], 'tags': [], 'description': '新'})
            self.assertEqual(get_classification('a.md')['domains'], ['新'])

    def test_digest_lookup_and_repeatable_migration(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'TUFA_DATA_DIR': directory}, clear=True):
            initialize_storage()
            self.assertIsNone(get_classification_by_digest('missing'))
            first = {'domains': ['领域'], 'tags': ['标签'], 'description': '首份'}
            second = {'domains': ['其他'], 'tags': ['另一'], 'description': '另一份'}
            upsert_classification('z.md', 'same', second)
            upsert_classification('a.md', 'same', first)
            initialize_storage()
            self.assertEqual(get_classification_by_digest('same'), get_classification('a.md'))
            self.assertEqual(get_classification_by_digest('same')['domains'], ['领域'])
            upsert_classification('a.md', 'changed', first)
            self.assertEqual(get_classification_by_digest('same'), get_classification('z.md'))

    def test_classifier_limits_and_request(self):
        def fake(prompt, payload, schema, **kwargs):
            self.assertEqual(len(payload['markdown']), 30000)
            self.assertEqual(kwargs['model_options']['reasoning_effort'], 'none')
            return schema(domains=[' A ', 'A'], tags=['标签'], description='短')
        with patch('src.agents.preprocesser.structured', fake):
            self.assertEqual(classify_markdown('x' * 40000, 'model').domains, ['A'])
        with self.assertRaises(ValidationError):
            Classification(domains=['A'], tags=['B'], description='字' * 21)
