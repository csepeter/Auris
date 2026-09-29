import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app as app_module
from core import database, security
from core import settings as app_settings


class SecurityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db_path = database.DB_PATH
        self.original_startup = app_module._startup_complete
        self.original_settings = app_settings.SETTINGS_FILE
        database.DB_PATH = os.path.join(self.tmp.name, 'reader.db')
        app_settings.SETTINGS_FILE = Path(self.tmp.name) / 'settings.json'
        app_module._startup_complete = True
        database.init_db()
        app_module.app.config['TESTING'] = True
        self.client = app_module.app.test_client()

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        app_settings.SETTINGS_FILE = self.original_settings
        app_module._startup_complete = self.original_startup
        self.tmp.cleanup()

    def test_cross_site_write_is_rejected(self):
        response = self.client.post(
            '/api/settings', data='{"llm_base_url": "http://evil.example"}',
            content_type='text/plain',
            headers={'Origin': 'http://evil.example', 'Sec-Fetch-Site': 'cross-site'},
        )
        self.assertEqual(response.status_code, 403)
        self.assertNotEqual(app_settings.get('llm_base_url'), 'http://evil.example')

    def test_foreign_origin_without_fetch_metadata_is_rejected(self):
        response = self.client.post(
            '/api/settings', json={'theme': 'sepia'},
            headers={'Origin': 'http://localhost:3000'},
        )
        self.assertEqual(response.status_code, 403)

    def test_same_origin_write_is_allowed(self):
        response = self.client.post(
            '/api/settings', json={'theme': 'sepia'},
            headers={'Origin': 'http://localhost', 'Sec-Fetch-Site': 'same-origin'},
        )
        self.assertEqual(response.status_code, 200)

    def test_foreign_host_header_is_rejected(self):
        response = self.client.get('/api/settings', headers={'Host': 'attacker.example'})
        self.assertEqual(response.status_code, 403)

    def test_api_keys_are_masked_and_mask_is_not_saved(self):
        app_settings.save({'openai_api_key': 'sk-secret', 'llm_api_key': 'local-secret'})
        data = self.client.get('/api/settings').get_json()
        self.assertEqual(data['openai_api_key'], security.SECRET_MASK)
        self.assertNotIn('sk-secret', str(data))
        response = self.client.post(
            '/api/settings', json={'openai_api_key': security.SECRET_MASK, 'theme': 'paper'}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(app_settings.get('openai_api_key'), 'sk-secret')

    def test_llm_test_uses_stored_key_for_mask(self):
        app_settings.save({'openai_api_key': 'sk-secret'})
        with patch.object(app_module.llm_characters, 'list_models', return_value=['gpt']) as lm:
            self.client.post(
                '/api/settings/llm-test',
                json={'provider': 'openai', 'api_key': security.SECRET_MASK},
            )
        self.assertEqual(lm.call_args.kwargs['api_key'], 'sk-secret')

    def test_invalid_repo_is_rejected(self):
        response = self.client.post(
            '/api/settings', json={'higgs_model_repo': '../../evil'}
        )
        self.assertEqual(response.status_code, 400)
        response = self.client.post(
            '/api/settings/model-download', json={'repo_id': 'http://x/y/z'}
        )
        self.assertEqual(response.status_code, 400)


if __name__ == '__main__':
    unittest.main()
