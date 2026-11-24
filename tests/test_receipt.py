import json
from pathlib import Path
import tempfile
import unittest
from save_receipt import save

class ReceiptTests(unittest.TestCase):
    def receipt(self):
        return {'channel':'data-v2','revision':'a'*64,'sourceCheckedAt':'2026-11-24T10:00:00Z',
            'start':'2026-11-24','end':'2026-12-07','deliveryVerifiedAt':'2026-11-24T10:05:00Z',
            'webAndMobileVerified':True,'updaterCommit':'b'*40,'run':'123'}
    def test_successful_delivery_is_auditable(self):
        with tempfile.TemporaryDirectory() as folder:
            save(json.dumps(self.receipt()),Path(folder))
            self.assertEqual(self.receipt(),json.loads((Path(folder)/'data-v2.json').read_text()))
    def test_unverified_or_extra_private_fields_are_not_written(self):
        for d in [{**self.receipt(),'token':'not-public'},{**self.receipt(),'webAndMobileVerified':False},{**self.receipt(),'channel':'../elsewhere'}]:
            with tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(ValueError):save(json.dumps(d),Path(folder))
                self.assertEqual([],list(Path(folder).iterdir()))
