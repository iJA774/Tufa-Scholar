import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from apps.api.main import app
from src.literature.evidence import collect_evidence
from src.literature.retrieval import retrieve_collection
from src.persistence import repository


class CollectionWorkflowTests(unittest.TestCase):
    def test_deleted_collection_keeps_submission_snapshot_for_retry(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'TUFA_DATA_DIR': directory}, clear=True), TestClient(app) as client:
            project = client.post('/projects', json={'name': 'project'}).json()['id']
            collection = client.post(f'/projects/{project}/collections', json={'name': 'selected'}).json()['id']
            selected = client.post(f'/projects/{project}/papers', json={'title': 'selected', 'abstract': 'selected evidence'}).json()['id']
            outside = client.post(f'/projects/{project}/papers', json={'title': 'outside', 'abstract': 'OUTSIDE_SECRET'}).json()['id']
            collection_path = f'/projects/{project}/collections/{collection}/papers'
            self.assertEqual(client.post(collection_path, json={'paper_id': selected}).status_code, 201)
            task = client.post(f'/projects/{project}/questions', json={
                'question': 'summarize', 'source': 'collection', 'collection_id': collection,
            }).json()
            self.assertEqual(client.delete(f'/projects/{project}/collections/{collection}').status_code, 204)

            claimed = repository.claim_task()
            self.assertEqual(claimed['paper_ids'], [selected])
            repository.fail_task(task['run_id'], 'test failure')
            retry = client.post(f"/runs/{task['run_id']}/retry").json()
            self.assertEqual(retry['paper_ids'], [selected])
            self.assertNotIn(outside, retry['paper_ids'])

    def test_collection_search_and_evidence_use_only_project_snapshot(self):
        selected = {'id': 3, 'title': 'selected', 'abstract': 'selected evidence', 'versions': []}
        outside = {'id': 4, 'title': 'outside', 'abstract': 'OUTSIDE_SECRET', 'versions': []}
        with patch('src.persistence.repository.project_evidence', return_value=[selected, outside]), \
             patch('src.persistence.repository.local_search', return_value=[outside, selected]) as local:
            result = retrieve_collection(1, 'selected', [3])
        local.assert_called_once_with(1, 'selected', [3])
        self.assertEqual([item['id'] for item in result['results']], [3])
        self.assertNotIn('OUTSIDE_SECRET', str(result))

        forged = {'id': 3, 'title': 'forged', 'abstract': 'FORGED_SECRET'}
        with patch('src.persistence.repository.project_evidence', return_value=[selected, outside]):
            evidence = collect_evidence(1, 'collection', [3], 'selected', [forged, outside])
        self.assertEqual([(item['paper_id'], item['title'], item['text']) for item in evidence], [(3, 'selected', 'selected evidence')])

    def test_empty_snapshot_has_no_candidates_and_never_expands_to_external_search(self):
        with patch('src.persistence.repository.project_evidence') as papers, \
             patch('src.persistence.repository.local_search') as local:
            result = retrieve_collection(1, 'query', [])
        self.assertEqual(result['results'], [])
        papers.assert_not_called()
        local.assert_not_called()


if __name__ == '__main__':
    unittest.main()
