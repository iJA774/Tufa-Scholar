import unittest
import os
import tempfile
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from apps.api.main import app
from src.persistence import repository as repo
from src.agents import literature as w


class CollectionWorkflowTests(unittest.TestCase):
    def test_deleted_collection_retains_snapshot_for_graph_and_retry(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'TUFA_DATA_DIR': directory}, clear=True), TestClient(app) as client:
            project = client.post('/projects', json={'name': 'project'}).json()['id']
            collection = client.post(f'/projects/{project}/collections', json={'name': 'selected'}).json()['id']
            selected = client.post(f'/projects/{project}/papers', json={'title': 'selected', 'abstract': 'selected evidence'}).json()['id']
            client.post(f'/projects/{project}/papers', json={'title': 'outside', 'abstract': 'OUTSIDE_SECRET'})
            client.post(f'/projects/{project}/collections/{collection}/papers', json={'paper_id': selected}).raise_for_status()
            run_id = client.post(f'/projects/{project}/questions', json={'question': 'summarize', 'source': 'collection', 'collection_id': collection}).json()['run_id']
            self.assertEqual(client.delete(f'/projects/{project}/collections/{collection}').status_code, 204)
            task = repo.claim_task()
            self.assertEqual(task['paper_ids'], [selected])
            payloads = []
            def model(name, payload, schema):
                payloads.append(str(payload))
                if name == 'runtime/route':
                    self.assertNotIn('direct', payload['available_actions'])
                    return w.Route(action='paper_research', task='selected') if not payload['results'] else w.Route(action='review' if payload['review'] is None else 'report')
                if name == 'literature/research':
                    return w.Specialist(action='search', query='selected') if not payload['recent_result'] else (w.Specialist(action='read', candidate_ids=[payload['candidates'][0]['id']]) if not payload['evidence'] else w.Specialist(action='finish'))
                if name == 'literature/review': return w.Review(sufficient=True, summary='ok')
                if name.endswith('synthesize'): return w.Draft(claims=[w.Claim(text='selected evidence', citations=['a1'])])
                if name.endswith('verify_claim'): return w.Verdict(supported=True)
                raise AssertionError(name)
            with patch.object(w, 'structured', side_effect=model), patch('src.tools.search.retrieve') as external, patch.object(repo, 'task_history') as history:
                content, evidence = w.run({k: task[k] for k in ('project_id', 'query', 'source', 'paper_ids')}, history=[])
                repo.fail_task(run_id, 'test retry')
                retried = repo.retry_task(run_id)
                self.assertEqual(retried['paper_ids'], [selected])
                retry_content, retry_evidence = w.run({k: retried[k] for k in ('project_id', 'query', 'source', 'paper_ids')}, history=[])
            external.assert_not_called()
            history.assert_not_called()
            self.assertTrue(all('OUTSIDE_SECRET' not in payload for payload in payloads))
            self.assertEqual([item['paper_id'] for item in evidence], [selected])
            self.assertEqual([item['paper_id'] for item in retry_evidence], [selected])
            self.assertIn('selected evidence', content)
            self.assertIn('selected evidence', retry_content)

    def test_empty_never_calls_model_or_network(self):
        with patch.object(w, 'structured') as model, patch('src.tools.search.retrieve') as retrieve:
            content, evidence = w.run({'source': 'collection', 'paper_ids': []}, history=[])
        self.assertIn('证据不足', content)
        self.assertEqual(evidence, [])
        model.assert_not_called()
        retrieve.assert_not_called()

    def test_isolated_graph_filters_other_papers_and_history(self):
        task = {'project_id': 1, 'query': 'summarize', 'source': 'collection', 'paper_ids': [3]}
        allowed = {'id': 3, 'title': 'selected', 'abstract': 'selected evidence', 'versions': []}
        decoy = {'id': 4, 'title': 'forbidden', 'abstract': 'forbidden text', 'versions': []}
        payloads = []
        def model(name, payload, schema):
            payloads.append(str(payload))
            if name == 'runtime/route':
                self.assertEqual(payload['history'], [])
                self.assertNotIn('direct', payload['available_actions'])
                return w.Route(action='paper_research', task='selected') if not payload['results'] else w.Route(action='review' if payload['review'] is None else 'report')
            if name == 'literature/research':
                return w.Specialist(action='search', query='selected') if not payload['recent_result'] else (w.Specialist(action='read', candidate_ids=[payload['candidates'][0]['id']]) if not payload['evidence'] else w.Specialist(action='finish'))
            if name == 'literature/review': return w.Review(sufficient=True, summary='ok')
            if name.endswith('synthesize'): return w.Draft(claims=[w.Claim(text='selected evidence', citations=['a1'])])
            if name.endswith('verify_claim'): return w.Verdict(supported=True)
            raise AssertionError(name)
        with patch.object(repo, 'project_evidence', return_value=[allowed, decoy]), patch.object(repo, 'local_search', return_value=[]) as local, patch('src.tools.search.retrieve') as retrieve, patch('src.literature.retrieval.search_works') as external, patch.object(w, 'structured', side_effect=model), patch.object(w.registry, 'invoke', wraps=w.registry.invoke) as invoke:
            content, evidence = w.run({k: task[k] for k in ('project_id', 'query', 'source', 'paper_ids')}, history=[])
        retrieve.assert_not_called()
        external.assert_not_called()
        self.assertEqual([call.args[0] for call in invoke.call_args_list], ['search_papers', 'collect_evidence'])
        self.assertTrue(all(call.kwargs['context'].project_id == 1 and call.kwargs['context'].source == 'collection' and call.kwargs['context'].paper_ids == (3,) for call in invoke.call_args_list))
        self.assertEqual(local.call_count, 1)
        self.assertTrue(all(call.args[2] == [3] for call in local.call_args_list))
        self.assertTrue(all('OUTSIDE_SECRET' not in payload for payload in payloads))
        self.assertTrue(all('forbidden' not in payload for payload in payloads))
        self.assertNotIn('forbidden', content)
        self.assertEqual([e['paper_id'] for e in evidence], [3])

    def test_full_collection_is_available_to_candidate_pool(self):
        papers = [{'id': i, 'title': f'paper {i}', 'abstract': f'evidence {i}', 'versions': []} for i in range(1, 9)]
        research = {'project_id': 1, 'query': 'topic', 'source': 'collection', 'paper_ids': list(range(1, 9))}
        with patch.object(repo, 'project_evidence', return_value=papers), patch.object(repo, 'local_search', return_value=papers):
            result = w.search({'research': research, 'queries': ['topic'], 'query_results': {}, 'warnings': []})
        self.assertEqual(len(result['candidates']), 8)
        self.assertEqual(len(result['papers']), 6)
        self.assertEqual({p['id'] for p in result['candidates']}, set(research['paper_ids']))

    def test_collection_evidence_uses_repository_text_not_forged_search_result(self):
        task = {'project_id': 1, 'query': 'selected', 'source': 'collection', 'paper_ids': [3]}
        stored = {'id': 3, 'title': 'Stored title', 'abstract': 'Stored evidence', 'versions': []}
        forged = {'id': 3, 'title': 'Forged title', 'abstract': 'Forged evidence'}
        with patch.object(repo, 'project_evidence', return_value=[stored]):
            evidence = w.evidence({'research': task, 'papers': [forged]})['evidence']
        self.assertEqual([(e['title'], e['text']) for e in evidence], [('Stored title', 'Stored evidence')])

    def test_verify_rejects_unselected_paper(self):
        state = {'research': { 'source': 'collection', 'paper_ids': [3]}, 'draft': w.Draft(claims=[w.Claim(text='result', citations=['x'])]), 'evidence': [{'id': 'x', 'paper_id': 4, 'text': 'result', 'title': 'other'}]}
        with patch.object(w, 'structured') as model:
            result = w.verify(state)
        model.assert_not_called()
        self.assertEqual(result['evidence'], [])
