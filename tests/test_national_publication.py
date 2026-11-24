import copy
import datetime as dt
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from national.common import blank,finalize,ImportErrorDetail
from national.publication import publish,validate,source_receipt

NOW=dt.datetime(2026,10,27,12,tzinfo=dt.timezone.utc)


def network():
 n=blank('2026-10-27','2026-10-28','synthetic publication test')
 n.update({'candidateOnly':True,'scope':'cz-rail-ostrava','liveCoverage':{'rail':'not-connected','ostrava':'not-connected'},
  'sourceCoverage':{k:{'trips':1,'sha256':'a'*64} for k in ['pid','rail','ostrava']},
  'stops':[{'id':'A','type':0},{'id':'B','type':0}], 'routes':[['r','1',2]],
  'trips':[[f't-{k}','s',0,'B','1',[[0,3600,3600,0,0,1,1],[1,7200,7200,0,0,2,1]]] for k in ['pid','rail','ostrava']],
  'active':{'2026-10-27':['s'],'2026-10-28':['s']}})
 return finalize(n)


def inputs():
 return {'schema':1,'sourcePlanComplete':True,'synthetic':False,'sources':{
  k:[{'sha256':'b'*64,'verifiedAt':'2026-10-27T11:00:00+00:00','httpStatus':304}] for k in ['pid','rail','ostrava']}}

REPORT={'diagnosticIncompleteTimingsAllowed':False,'completeRailTimingsExported':True}


class NationalPublicationTests(unittest.TestCase):
 def setUp(self):
  self.minimums=patch('national.publication.MINIMUMS',{k:1 for k in ['pid','rail','ostrava','stops','dailyTrips']})
  self.minimums.start();self.addCleanup(self.minimums.stop)
 def test_a_complete_revision_has_versioned_byte_hashes_and_source_freshness(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);m=publish(network(),REPORT,inputs(),root/'national',root/'runtime',NOW)
   self.assertEqual('2026-10-27T11:00:00+00:00',m['checkedAt'])
   self.assertEqual(2,len(m['days']));self.assertEqual('national-preview',m['channel'])
   self.assertEqual(m,validate(root/'national',m,NOW))
   self.assertEqual(m,json.loads((root/'national/manifest.json').read_text()))
 def test_failed_validation_preserves_the_previous_pointer_and_files(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);public=root/'national';publish(network(),REPORT,inputs(),public,root/'runtime',NOW)
   old=(public/'manifest.json').read_bytes();names=set(p.name for p in public.iterdir())
   n=network();n['trips'][0][5][0][1]=3500;finalize(n)
   with patch('national.publication.validate',side_effect=ImportErrorDetail('test failure')):
    with self.assertRaisesRegex(ImportErrorDetail,'test failure'):publish(n,REPORT,inputs(),public,root/'runtime',NOW)
   self.assertEqual(old,(public/'manifest.json').read_bytes());self.assertEqual(names,set(p.name for p in public.iterdir()))
 def test_changed_daily_bytes_and_wrong_walking_hash_are_rejected(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);m=publish(network(),REPORT,inputs(),root/'national',root/'runtime',NOW)
   day=next(iter(m['days'].values()));p=root/'national'/day;old=p.read_bytes();p.write_bytes(old[:-4])
   with self.assertRaisesRegex(ImportErrorDetail,'changed'):validate(root/'national',m,NOW)
   p.write_bytes(old);(root/'national'/m['walking']['file']).write_text('{"schema":2}')
   with self.assertRaisesRegex(ImportErrorDetail,'walking'):validate(root/'national',m,NOW)
 def test_all_three_collections_must_have_recent_http_revalidation(self):
  for kind in ['rail','ostrava','pid']:
   for field,value in [('verifiedAt','2026-10-24T12:00:00Z'),('httpStatus',None)]:
    receipt=inputs();receipt['sources'][kind][0][field]=value
    with self.subTest(kind=kind,field=field),self.assertRaises(ImportErrorDetail):source_receipt(receipt,NOW)
 def test_partial_or_synthetic_receipts_and_diagnostic_networks_cannot_publish(self):
  for field,value in [('sourcePlanComplete',False),('synthetic',True)]:
   receipt=inputs();receipt[field]=value
   with self.assertRaises(ImportErrorDetail):source_receipt(receipt,NOW)
  with tempfile.TemporaryDirectory() as temp:
   with self.assertRaisesRegex(ImportErrorDetail,'diagnostic'):
    publish(network(),{**REPORT,'diagnosticIncompleteTimingsAllowed':True},inputs(),Path(temp)/'national',Path(temp)/'runtime',NOW)
 def test_production_pid_destination_is_refused(self):
  with tempfile.TemporaryDirectory() as temp:
   with self.assertRaisesRegex(ImportErrorDetail,'production PID'):
    publish(network(),REPORT,inputs(),Path(temp)/'data-v2',Path(temp)/'runtime',NOW)
 def test_the_previous_immutable_revision_remains_available(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);old=publish(network(),REPORT,inputs(),root/'national',root/'runtime',NOW)
   n=network();n['trips'][0][4]='updated';finalize(n)
   current=publish(n,REPORT,inputs(),root/'national',root/'runtime',NOW)
   self.assertNotEqual(old['sha256'],current['sha256'])
   self.assertTrue(all((root/'national'/name).exists() for name in old['days'].values()))

 def test_unchanged_content_produces_identical_immutable_gzip_bytes(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);a=publish(network(),REPORT,inputs(),root/'national',root/'runtime',NOW)
   b=publish(network(),REPORT,inputs(),root/'national',root/'runtime',NOW+dt.timedelta(minutes=1))
   self.assertEqual(a['files'],b['files'])
   self.assertNotEqual(a['importedAt'],b['importedAt'])
 def test_incorrect_logical_revision_is_not_published(self):
  with tempfile.TemporaryDirectory() as temp:
   n=network();n['trips'][0][4]='changed without a new revision'
   with self.assertRaisesRegex(ImportErrorDetail,'differs from its revision'):
    publish(n,REPORT,inputs(),Path(temp)/'national',Path(temp)/'runtime',NOW)

 def test_truncated_real_collections_cannot_replace_a_revision(self):
  self.minimums.stop()
  with tempfile.TemporaryDirectory() as temp:
   with self.assertRaisesRegex(ImportErrorDetail,'unexpectedly small'):
    publish(network(),REPORT,inputs(),Path(temp)/'national',Path(temp)/'runtime',NOW)

 def test_mismatched_source_trip_counts_are_refused(self):
  n=network();n['sourceCoverage']['rail']['trips']=2;finalize(n)
  with tempfile.TemporaryDirectory() as temp:
   with self.assertRaisesRegex(ImportErrorDetail,'source counts differ'):
    publish(n,REPORT,inputs(),Path(temp)/'national',Path(temp)/'runtime',NOW)
