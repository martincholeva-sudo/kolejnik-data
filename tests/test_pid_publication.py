import json
import unittest
from unittest.mock import patch
from data_validation import validate_public
from national.common import ImportErrorDetail
from national.remote_publication import publish_refs
from test_data_validation import revision, NOW
import test_remote_publication as remote
from test_remote_publication import LEGACY, run


class PidPublicationTests(unittest.TestCase):
    clean = remote.RemotePublicationTests.clean
    configure = remote.RemotePublicationTests.configure
    heads = remote.RemotePublicationTests.heads
    read = remote.RemotePublicationTests.read
    edit_remote = remote.RemotePublicationTests.edit_remote

    def setUp(self):
        remote.RemotePublicationTests.setUp(self)
        self.public = self.repo/'prepared/data-v2'
        self.public.mkdir()
        self.manifest = revision(self.public)
        def small(folder, manifest=None, now=None):
            return validate_public(folder, manifest=manifest, now=now, min_stops=1, min_trips=1)
        mock = patch('national.remote_publication.validate_public', side_effect=small)
        mock.start()
        self.addCleanup(mock.stop)

    def publish(self, **kwargs):
        return publish_refs(self.repo, self.public, channel='spojeni/data-v2', now=NOW, **kwargs)

    def test_both_branches_receive_data_and_keep_their_application(self):
        result = self.publish()
        self.assertTrue(result['atomic'])
        self.assertEqual(self.read('main', 'spojeni/data-v2/manifest.json'), self.read(LEGACY, 'spojeni/data-v2/manifest.json'))
        self.assertEqual(b'main application', self.read('main', 'index.html'))
        self.assertEqual(b'installed APK compatibility site', self.read(LEGACY, 'index.html'))

    def test_truncated_data_and_unexpected_files_never_change_remote_heads(self):
        previous = self.heads()
        (self.public/'secret.env').write_text('not data')
        with self.assertRaisesRegex(ImportErrorDetail, 'Unexpected'):
            self.publish()
        (self.public/'secret.env').unlink()
        (self.public/next(iter(self.manifest['days'].values()))).unlink()
        with self.assertRaises(FileNotFoundError):
            self.publish()
        self.assertEqual(previous, self.heads())

    def test_walking_graph_can_change_without_rewriting_daily_files(self):
        self.publish()
        name = next(iter(self.manifest['days'].values()))
        previous = self.read('main', 'spojeni/data-v2/'+name)
        walking = b'{"schema":1,"edges":[[1,2,30]]}'
        (self.public/'walking-links.json').write_bytes(walking)
        self.publish()
        for branch in ['main', LEGACY]:
            self.assertEqual(walking, self.read(branch, 'spojeni/data-v2/walking-links.json'))
            self.assertEqual(previous, self.read(branch, 'spojeni/data-v2/'+name))

    def test_concurrent_application_change_is_retained(self):
        def race(attempt, commits):
            if attempt == 0:
                self.edit_remote('main', 'index.html', b'new application')
        result = self.publish(before_push=race)
        self.assertEqual(2, result['attempts'])
        self.assertEqual(b'new application', self.read('main', 'index.html'))

    def test_unknown_channel_is_rejected(self):
        with self.assertRaises(ImportErrorDetail):
            publish_refs(self.repo, self.public, channel='android', now=NOW)
