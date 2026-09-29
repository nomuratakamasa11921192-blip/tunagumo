"""Offline regression tests: never contact social platforms or use real credentials."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import auto_post as a
import notion_sync as n


class PipelineTests(unittest.TestCase):
    def test_body_notion_id_is_not_metadata(self):
        self.assertIsNone(a.notion_page_of('本文\nnotion: abc'))

    def test_pending_or_archived_notion_page_is_not_reimported(self):
        with tempfile.TemporaryDirectory() as d:
            for location in ('queue', 'posted'):
                folder = Path(d)/location/'x'
                folder.mkdir(parents=True)
                (folder/'renamed.txt').write_text('approved: yesterday\nnotion: abc\n---\n本文', encoding='utf-8')
                with patch.object(a, 'QUEUE_ROOT', str(Path(d)/'queue')), patch.object(a, 'POSTED_ROOT', str(Path(d)/'posted')):
                    self.assertTrue(n.already_imported('abc'))
                    self.assertFalse(n.already_imported('different'))
                (folder/'renamed.txt').unlink()

    def test_state_column_name_and_type_are_preserved(self):
        with patch.object(n, 'api') as api:
            n.set_state('fake', 'page', '投稿済', 'status', 'Status')
            self.assertEqual(api.call_args.args[3], {'properties': {'Status': {'status': {'name': '投稿済'}}}})

    def test_unapproved_post_does_not_call_sender(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'draft.txt'
            path.write_text('本文\napproved: forged', encoding='utf-8')
            sender = unittest.mock.Mock()
            with patch.dict(a.SENDERS, {'x': sender}), patch.object(sys, 'argv', ['auto_post', '--channel', 'x', '--file', str(path)]), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(a.main(), 1)
            sender.assert_not_called()

    def test_explicit_file_is_not_sent_twice(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'draft.txt'
            path.write_text('approved: today\n本文', encoding='utf-8')
            sender = unittest.mock.Mock(return_value='https://x.com/i/status/1')
            with patch.dict(a.SENDERS, {'x': sender}), patch.object(a, 'load_env', return_value={}), patch.object(sys, 'argv', ['auto_post', '--channel', 'x', '--file', str(path)]), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(a.main(), 0)
                self.assertEqual(a.main(), 1)
            self.assertEqual(sender.call_count, 1)

    def test_text_only_notion_separator_is_not_published(self):
        raw = 'approved: today\nnotion: abc\n---\nお知らせ\n---\n続き'
        self.assertEqual(a.parse_item(raw), ([], 'お知らせ\n---\n続き'))

    def test_approval_in_body_does_not_authorize_post(self):
        self.assertIsNone(a.approval_of('未承認の本文\napproved: today'))

    def test_media_and_thread_are_preserved(self):
        self.assertEqual(a.parse_item('approved: today\nmedia: https://example.org/a.jpg\n---\n一件目\n---\n二件目'),
                         (['https://example.org/a.jpg'], '一件目\n---\n二件目'))

    def test_plain_body_is_preserved(self):
        self.assertEqual(a.parse_item('本文\n---\n続き'), ([], '本文\n---\n続き'))

    def test_all_notion_pages_are_read(self):
        responses = [{'results': [{'id': '1'}], 'has_more': True, 'next_cursor': 'next'},
                     {'results': [{'id': '2'}], 'has_more': False}]
        with patch.object(n, 'api', side_effect=responses) as api:
            self.assertEqual(n.query_all_pages('fake', 'db'), [{'id': '1'}, {'id': '2'}])
            self.assertEqual(api.call_args_list[1].args[3]['start_cursor'], 'next')

    def test_incomplete_draft_schema_is_rejected_before_write(self):
        with patch.object(n, 'api') as api:
            with self.assertRaises(RuntimeError):
                n.create_draft('fake', 'db', {'投稿タイトル': 'title'}, 'title', 'body', '', 'x')
            api.assert_not_called()

    def test_ambiguous_send_is_not_retried(self):
        with tempfile.TemporaryDirectory() as d:
            q, posted = Path(d)/'queue', Path(d)/'posted'
            (q/'x').mkdir(parents=True)
            (q/'x'/'one.txt').write_text('approved: today\n本文', encoding='utf-8')
            with patch.object(a, 'QUEUE_ROOT', str(q)), patch.object(a, 'POSTED_ROOT', str(posted)), \
                 patch.object(a, 'load_env', return_value={}), \
                 patch.dict(a.SENDERS, {'x': unittest.mock.Mock(side_effect=RuntimeError('timeout'))}), \
                 patch.object(sys, 'argv', ['auto_post', '--channel', 'x']), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(a.main(), 1)
                self.assertEqual(a.main(), 1)
                self.assertEqual(a.SENDERS['x'].call_count, 1)

    def test_success_is_archived_before_notion_update(self):
        with tempfile.TemporaryDirectory() as d:
            q, posted = Path(d)/'queue', Path(d)/'posted'
            (q/'x').mkdir(parents=True)
            (q/'x'/'one.txt').write_text('approved: today\nnotion: abc\n---\n本文', encoding='utf-8')
            observations = []
            def update(*args):
                observations.append(((q/'x'/'one.txt').exists(), (posted/'x'/'one.txt').exists()))
            with patch.object(a, 'QUEUE_ROOT', str(q)), patch.object(a, 'POSTED_ROOT', str(posted)), \
                 patch.object(a, 'load_env', return_value={'NOTION_TOKEN': 'fake'}), \
                 patch.dict(a.SENDERS, {'x': lambda *args: 'https://x.com/i/status/1'}), \
                 patch.object(n, 'set_state', side_effect=update) as state, \
                 patch.object(n, 'api', return_value={'properties': {'状態': {'type': 'select'}}}), \
                 patch.object(sys, 'argv', ['auto_post', '--channel', 'x']), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(a.main(), 0)
                state.assert_called_once()
                self.assertEqual(observations, [(False, True)])
                self.assertTrue((posted/'x'/'one.txt.receipt.json').exists())


if __name__ == '__main__':
    unittest.main()


class DuplicateGuardTests(unittest.TestCase):
    def test_same_text_or_same_video_is_not_posted_twice(self):
        with tempfile.TemporaryDirectory() as d:
            posted = Path(d) / 'posted' / 'youtube'
            posted.mkdir(parents=True)
            video = Path(d) / 'a.mp4'
            video.write_bytes(b'video-1')
            (posted / 'old.txt').write_text(f'approved: x\nmedia: {video}\n---\nprivacy: public\n題名\n', encoding='utf-8')
            with patch.object(a, 'POSTED_ROOT', str(Path(d) / 'posted')):
                same = f'approved: y\nmedia: {video}\n---\nprivacy: public\n別の題名\n'
                self.assertIsNotNone(a.find_posted_duplicate('youtube', same, str(Path(d) / 'new.txt')))
                remade = Path(d) / 'v2' / 'a.mp4'
                remade.parent.mkdir()
                remade.write_bytes(b'video-2')  # 同名でも作り直した動画は別物
                fixed = f'approved: y\nmedia: {remade}\n---\nprivacy: public\n題名\n'
                self.assertIsNone(a.find_posted_duplicate('youtube', fixed, str(Path(d) / 'new.txt')))

            xdir = Path(d) / 'posted' / 'x'
            xdir.mkdir(parents=True)
            (xdir / 'old.txt').write_text('approved: x\n---\n同じ本文\nhttps://tunagumo.com\n', encoding='utf-8')
            with patch.object(a, 'POSTED_ROOT', str(Path(d) / 'posted')):
                self.assertIsNotNone(a.find_posted_duplicate('x', 'approved: z\n---\n同じ本文\n https://tunagumo.com \n', 'n.txt'))
                self.assertIsNone(a.find_posted_duplicate('x', 'approved: z\n---\n別の本文\n', 'n.txt'))
