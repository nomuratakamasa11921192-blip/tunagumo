"""No credentials, media uploads or posts: exercise the publication boundary."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import auto_post as a
import publish_x_video as v


class VideoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'video.mp4'
        self.path.write_bytes(b'fake-video')

    def test_video_is_attached_to_first_post_only(self):
        responses = [{'data': {'username': 'tunagumo_com'}},
                     {'data': {'id': '11'}}, {'data': {'id': '12'}}]
        with patch.object(v, 'validate_video', return_value=self.path), \
             patch.object(v, 'upload', return_value='123') as upload, \
             patch.object(v, 'request', side_effect=responses) as request:
            self.assertEqual(v.publish('fake', self.path, ['one', 'two']), 'https://x.com/i/status/11')
            upload.assert_called_once()
            self.assertEqual(request.call_args_list[1].args[3],
                             {'text': 'one', 'media': {'media_ids': ['123']}})
            self.assertEqual(request.call_args_list[2].args[3],
                             {'text': 'two', 'reply': {'in_reply_to_tweet_id': '11'}})

    def test_account_mismatch_blocks_upload(self):
        with patch.object(v, 'validate_video', return_value=self.path), \
             patch.object(v, 'upload') as upload, \
             patch.object(v, 'request', return_value={'data': {'username': 'other'}}):
            with self.assertRaises(RuntimeError):
                v.publish('fake', self.path, ['one'])
            upload.assert_not_called()

    def test_missing_token_does_not_contact_x(self):
        with patch.object(v, 'validate_video', return_value=self.path), patch.object(v, 'request') as request:
            with self.assertRaises(RuntimeError):
                v.publish(None, self.path, ['one'])
            request.assert_not_called()

    def test_upload_failure_does_not_post_text(self):
        with patch.object(v, 'validate_video', return_value=self.path), \
             patch.object(v, 'upload', side_effect=RuntimeError('failed')), \
             patch.object(v, 'request', return_value={'data': {'username': 'tunagumo_com'}}) as request:
            with self.assertRaises(RuntimeError):
                v.publish('fake', self.path, ['one'])
            self.assertEqual(request.call_count, 1)

    def test_chunks_use_dedicated_endpoints_and_wait_for_processing(self):
        responses = [{'data': {'id': '123'}}, {}, {}, {},
                     {'data': {'id': '123', 'processing_info': {'state': 'pending', 'check_after_secs': 1}}},
                     {'data': {'id': '123', 'processing_info': {'state': 'succeeded'}}}]
        with patch.object(v, 'CHUNK_BYTES', 4), patch.object(v, 'request', side_effect=responses) as request, \
             patch.object(v.time, 'sleep') as sleep:
            self.assertEqual(v.upload('fake', self.path), '123')
            self.assertEqual(request.call_count, 6)
            self.assertEqual(request.call_args_list[0].args[2], '/media/upload/initialize')
            for index in range(3):
                args = request.call_args_list[index + 1].args
                self.assertEqual(args[2], '/media/upload/123/append')
                self.assertIn(f'\r\n{index}\r\n'.encode(), args[3])
            self.assertEqual(request.call_args_list[4].args[2], '/media/upload/123/finalize')
            self.assertIn('command=STATUS&media_id=123', request.call_args_list[5].args[2])
            sleep.assert_called_once_with(1)

    def test_failed_and_unknown_processing_are_rejected(self):
        for state in ('failed', 'unexpected'):
            with self.subTest(state=state), patch.object(v, 'request', side_effect=[
                    {'data': {'id': '123'}}, {},
                    {'data': {'id': '123', 'processing_info': {'state': state}}}]):
                with self.assertRaises(RuntimeError):
                    v.upload('fake', self.path)

    def test_unreasonable_wait_is_rejected(self):
        with patch.object(v, 'request', side_effect=[{'data': {'id': '123'}}, {},
                {'data': {'id': '123', 'processing_info': {'state': 'pending', 'check_after_secs': 9000}}}]), \
             patch.object(v.time, 'sleep') as sleep:
            with self.assertRaises(RuntimeError):
                v.upload('fake', self.path)
            sleep.assert_not_called()

    def test_bad_media_id_is_rejected(self):
        with patch.object(v, 'request', return_value={'data': {'id': '../tweets'}}) as request:
            with self.assertRaises(RuntimeError):
                v.upload('fake', self.path)
            self.assertEqual(request.call_count, 1)

    def test_invalid_codec_or_duration_is_rejected(self):
        for duration, codec in [('150', 'h264'), ('nan', 'h264'), ('20', 'hevc')]:
            probe = {'format': {'duration': duration}, 'streams': [{'codec_type': 'video', 'codec_name': codec}]}
            with patch.object(v.shutil, 'which', return_value='ffprobe'), \
                 patch.object(v.subprocess, 'run', return_value=Mock(stdout=json.dumps(probe))):
                with self.assertRaises(RuntimeError):
                    v.validate_video(self.path)

    def test_dry_run_validates_video_without_upload(self):
        with patch.object(v, 'validate_video', return_value=self.path), patch.object(v, 'publish') as publish:
            a.send_x({}, [str(self.path)], 'text', True)
            publish.assert_not_called()

    def test_no_text_only_fallback_on_video_failure(self):
        with patch.object(v, 'validate_video', return_value=self.path), \
             patch.object(v, 'publish', side_effect=RuntimeError('no token')), \
             patch('publish_x.post_tweet') as tweet:
            with self.assertRaises(RuntimeError):
                a.send_x({}, [str(self.path)], 'text', False)
            tweet.assert_not_called()

    def test_instagram_rejects_local_file_even_in_dry_run(self):
        with self.assertRaises(RuntimeError):
            a.send_instagram({}, [str(self.path)], 'text', True)

    def test_line_does_not_treat_video_as_image(self):
        with self.assertRaisesRegex(RuntimeError, '動画添付'):
            a.send_line({}, ['https://example.test/video.mp4?x=1'], 'text', True)


if __name__ == '__main__':
    unittest.main()
