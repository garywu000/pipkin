"""GitHub routing, grant isolation and reviewed commits with an in-memory provider."""
import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

import agent
import github_connector as github
import server


class RoutingTests(unittest.TestCase):
    def test_natural_requests(self):
        for text in (
            'Push these to my repository on GitHub',
            'Revise the README in this repository',
            'Could you review the open pull requests?',
            'What are the open issues on GitHub?',
            'How many open issues are in this repository?',
            'Explain the latest pull request',
            'List branches', 'Show the latest commits', 'List open issues',
            '@github list files',
            "Upload logo.png from my workspace to assets/logo.png on branch dev with commit message 'Add logo'.",
            'Attach Pipkin-2.4.1.zip from my workspace to release v2.4.1',
        ):
            with self.subTest(text=text):
                self.assertTrue(github.requested(text, True))

    def test_ordinary_and_local_requests(self):
        for text in (
            'How do I push to GitHub?', 'Show me how to commit files on GitHub',
            'What is a pull request?', 'What are the benefits of GitHub?',
            'Write an essay about GitHub', 'Translate: push this to GitHub',
            'Never push to GitHub', 'Edit the README in my local repository',
            'Read README.md', 'Review my homework', 'Review GitHub repository management best practices', 'List branches in my local repo', '/site write a GitHub page',
        ):
            with self.subTest(text=text):
                self.assertFalse(github.requested(text, True))

    def test_disconnected_explicit_request_still_routes(self):
        self.assertTrue(github.requested('Push these to my repository on GitHub'))
        self.assertTrue(github.requested('@github list files'))
        self.assertFalse(github.requested('Revise the README in this repository'))

    def test_destination(self):
        grant = {'repo': 'octo/project'}
        github.validate_destination('Review https://github.com/OCTO/PROJECT/pull/2', grant)
        github.validate_destination('Edit README in repository octo/project', grant)
        for text in ('Read https://github.com/other/project', 'Update repository other/project'):
            with self.assertRaisesRegex(ValueError, 'different GitHub repository'):
                github.validate_destination(text, grant)

    def test_route_endpoint_uses_current_grant_and_no_network(self):
        handler = mock.Mock()
        handler.read_json.return_value = {'text': 'Revise the README in this repository'}
        with mock.patch.object(server.GITHUB, 'route', return_value={'github': True, 'generation': 7}):
            server.ChatHandler.github_connection(handler, 'route')
        handler.send_json.assert_called_once_with(200, {'github': True, 'generation': 7})

    def test_cloud_models_are_rejected(self):
        for model, metadata in [('qwen:cloud', {'size': 20}), ('alias', {'size': 20, 'remote_host': 'example.com'}),
                                ('cloud', {'size': 0})]:
            with mock.patch.object(server, 'current', return_value={'model': model}), \
                    mock.patch.object(server, 'installed_models', return_value=[{'name': model, **metadata}]):
                with self.assertRaisesRegex(ValueError, 'non-cloud'):
                    server.require_local_github_model()
        with mock.patch.object(server, 'current', return_value={'model': 'qwen:8b'}), \
                mock.patch.object(server, 'installed_models', return_value=[{'name': 'qwen:8b', 'size': 5000}]):
            server.require_local_github_model()


class Provider:
    def __init__(self):
        self.account_id = 1
        self.repo_id = 2
        self.push = True
        self.default = 'main'
        self.protected = False
        self.text = 'original\n'
        self.sha = 'a' * 40
        self.calls = []
        self.fail_write = False
        self.token = 'fake-token-original'

    def api(self, token):
        def request(path, *, method='GET', body=None):
            self.calls.append((token, method, path, body))
            if path == '/user':
                return {'id': self.account_id, 'login': 'octo'}
            if path == '/repos/octo/project':
                return {'id': self.repo_id, 'full_name': 'octo/project', 'default_branch': self.default,
                        'permissions': {'push': self.push}, 'private': True}
            if path == '/repos/octo/project/branches/dev':
                return {'name': 'dev', 'protected': self.protected}
            if method == 'PUT' and path == '/repos/octo/project/contents/README.md':
                if self.fail_write:
                    raise ValueError('Uncertain provider outcome')
                self.text = base64.b64decode(body['content']).decode()
                self.sha = 'b' * 40
                return {'commit': {'sha': 'c' * 40}}
            if path.startswith('/repos/octo/project/contents/?ref='):
                return [{'path': 'README.md', 'name': 'README.md', 'type': 'file', 'sha': self.sha}]
            if path.startswith('/repos/octo/project/contents/README.md?ref='):
                return {'type': 'file', 'encoding': 'base64', 'size': len(self.text.encode()),
                        'content': base64.b64encode(self.text.encode()).decode(), 'sha': self.sha}
            raise AssertionError(f'Unexpected endpoint: {method} {path}')
        return mock.Mock(request=request)

    @property
    def writes(self):
        return [call for call in self.calls if call[1] == 'PUT']


class ConnectorTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.journal = Path(temp.name) / 'journal.sqlite3'
        self.provider = Provider()
        self.connection = github.Connection(lambda: self.provider.token, self.provider.api)
        self.connection.connect('octo/project', True)
        self.args = {'path': 'README.md', 'branch': 'dev', 'content': 'revised\n', 'message': 'Revise README'}

    def turn(self, writes=True):
        return self.connection.turn(writes=writes, journal=self.journal)

    def test_route_generation_rejects_reconnect(self):
        route = self.connection.route('Revise the README in this repository')
        self.assertTrue(route['github'])
        self.connection.connect('octo/project', True)
        with self.assertRaisesRegex(ValueError, 'connection changed'):
            self.connection.turn(generation=route['generation'])
        self.assertFalse(self.provider.writes)

    def test_failed_reconnect_clears_previous_grant(self):
        self.provider.repo_id = 3
        with mock.patch.object(self.connection, '_token_source', side_effect=ValueError('Sign in again')):
            with self.assertRaisesRegex(ValueError, 'Sign in again'):
                self.connection.connect('octo/project')
        self.assertIsNone(self.connection.status())
        self.assertFalse(self.connection.route('Revise the README in this repository')['github'])

    def test_bounded_branch_commit_and_pull_file_reads(self):
        turn = self.turn(False)
        for name, args, response, path in (
            ('github_branches', {}, [{'name': 'dev', 'protected': False}], '/branches?per_page=30'),
            ('github_commits', {'branch': 'dev'}, [{'sha': 'a' * 40, 'commit': {'message': 'Example'}}], '/commits?per_page=15&sha=dev'),
            ('github_pr_files', {'number': 2}, [{'filename': 'README.md', 'patch': '+example'}], '/pulls/2/files?per_page=15'),
        ):
            with self.subTest(name=name), mock.patch.object(turn, '_get', return_value=response) as get:
                self.assertIn('entries', turn.execute(name, args))
                get.assert_called_once_with(path)
        self.assertFalse(self.provider.writes)

    def test_read_directory_as_file_is_handled(self):
        with mock.patch.object(github.Turn, '_content', return_value=[]):
            with self.assertRaisesRegex(ValueError, 'not a supported UTF-8 file'):
                self.turn(False).execute('github_read', {'path': 'docs'})

    def test_read_uses_selected_repo_and_never_returns_token(self):
        turn = self.turn(False)
        result = turn.execute('github_read', {'path': 'README.md'})
        self.assertIn('original', result)
        self.assertIn('https://github.com/octo/project/blob/main/README.md', result)
        self.assertNotIn(self.provider.token, result)
        self.assertNotIn('github_commit_file', {t['function']['name'] for t in turn.tools})
        with self.assertRaisesRegex(ValueError, 'not authorized'):
            turn.execute('github_commit_file', self.args, approve=lambda *_: True)
        self.assertFalse(self.provider.writes)

    def test_approved_payload_exact_and_fresh_credential(self):
        turn = self.turn()
        def approve(kind, title, detail):
            self.assertEqual(kind, 'github')
            self.assertIn('README.md', title)
            self.assertIn('octo/project', detail)
            self.assertIn('revised\\n', detail)
            self.assertIn('-original', detail)
            self.assertFalse(self.provider.writes)
            self.provider.token = 'fake-token-rotated'
            return True
        result = turn.execute('github_commit_file', self.args, approve=approve)
        self.assertIn('/commit/' + 'c' * 40, result)
        self.assertEqual(len(self.provider.writes), 1)
        token, _, path, body = self.provider.writes[0]
        self.assertEqual(token, 'fake-token-rotated')
        self.assertEqual(path, '/repos/octo/project/contents/README.md')
        self.assertEqual(body, {'branch': 'dev', 'message': 'Revise README', 'sha': 'a' * 40,
                                'content': base64.b64encode(b'revised\n').decode()})
        with self.assertRaisesRegex(ValueError, 'already attempted'):
            turn.execute('github_commit_file', self.args, approve=approve)

    def test_decline_cannot_reprompt_in_same_turn(self):
        turn = self.turn()
        self.assertIn('declined', turn.execute('github_commit_file', self.args, approve=lambda *_: False))
        with self.assertRaisesRegex(ValueError, 'already attempted'):
            turn.execute('github_commit_file', self.args, approve=lambda *_: True)
        self.assertFalse(self.provider.writes)

    def test_account_change_during_approval(self):
        def approve(*_):
            self.provider.account_id = 3
            return True
        with self.assertRaisesRegex(ValueError, 'access or default branch changed'):
            self.turn().execute('github_commit_file', self.args, approve=approve)
        self.assertFalse(self.provider.writes)

    def test_changed_file_since_approval(self):
        def approve(*_):
            self.provider.sha = 'd' * 40
            return True
        with self.assertRaisesRegex(ValueError, 'File changed since approval'):
            self.turn().execute('github_commit_file', self.args, approve=approve)
        self.assertFalse(self.provider.writes)

    def test_default_and_protected_branches_denied(self):
        approve = mock.Mock(return_value=True)
        with self.assertRaisesRegex(ValueError, 'non-default'):
            self.turn().execute('github_commit_file', {**self.args, 'branch': 'main'}, approve=approve)
        self.provider.protected = True
        with self.assertRaisesRegex(ValueError, 'unprotected'):
            self.turn().execute('github_commit_file', self.args, approve=approve)
        approve.assert_not_called()

    def test_disconnect_revokes_old_turn(self):
        turn = self.turn()
        self.connection.disconnect()
        with self.assertRaisesRegex(ValueError, 'access changed'):
            turn.execute('github_read', {'path': 'README.md'})

    def test_invalid_destinations_and_forged_tools(self):
        turn = self.turn()
        for path in ('../README.md', '/README.md', '.git/config', 'docs/../README.md', '.github/workflows/build.yml'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                turn.execute('github_commit_file', {**self.args, 'path': path}, approve=lambda *_: True)
        for name in ('run_command', 'web_fetch', 'terminal_run', 'write_file', 'schedule_task'):
            with self.assertRaisesRegex(ValueError, 'not authorized'):
                turn.execute(name, {}, approve=lambda *_: True)
        self.assertFalse(self.provider.writes)

    def test_uncertain_write_blocks_changed_payload_across_turns(self):
        self.provider.fail_write = True
        with self.assertRaisesRegex(ValueError, 'Uncertain'):
            self.turn().execute('github_commit_file', self.args, approve=lambda *_: True)
        self.provider.fail_write = False
        self.provider.sha = 'd' * 40
        with self.assertRaisesRegex(ValueError, 'previous commit attempt'):
            self.turn().execute('github_commit_file', {**self.args, 'content': 'different'}, approve=lambda *_: True)
        self.assertEqual(len(self.provider.writes), 1)

    def test_agent_auto_mode_still_requests_review(self):
        turn = self.turn()
        replies = iter([{'tool_calls': [{'function': {'name': 'github_commit_file', 'arguments': self.args}}]},
                        {'content': 'The user declined.'}])
        approval = mock.Mock(return_value=False)
        def model(messages, tools, think):
            self.assertTrue(all(t['function']['name'].startswith('github_') for t in tools))
            return next(replies)
        worker = agent.Agent(model, lambda *_a, **_kw: None, lambda: None, approval,
                             mode='auto', github=turn, web=True, memory=True)
        worker.run(github.GUIDANCE, [], 'Revise the README in this repository on branch dev')
        approval.assert_called_once()
        self.assertFalse(self.provider.writes)


if __name__ == '__main__':
    unittest.main()
