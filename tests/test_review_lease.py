import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from national.common import digest,ImportErrorDetail
from national.review_lease import check_sources,renew_location_bindings,INDEX

class ReviewLeaseTests(unittest.TestCase):
 def source(self,root):
  (root/'config').mkdir();(root/'reports/station-register-20261027').mkdir(parents=True)
  plan='https://kodis-files.s3.eu-central-1.amazonaws.com/Ostrava_Svinov_abc123.pdf'
  station='https://provoz.spravazeleznic.cz/PORTAL/Show.aspx?oid=12345';index='https://provoz.spravazeleznic.cz/PORTAL/ViewArticle.aspx?oid=67890'
  documents={INDEX:('<a href="'+plan+'">Ostrava-Svinov</a>').encode(),plan:b'retained official plan',index:b'<a href="Show.aspx?oid=12345">register</a>',station:b'retained station register'}
  (root/'config/svinov-fixed-stair-connections.json').write_text(json.dumps({'planUrl':plan,'planSha256':hashlib.sha256(documents[plan]).hexdigest()}))
  (root/'reports/station-register-20261027/manifest.json').write_text(json.dumps({'sourceUrl':station,'indexUrl':index,'sourceSha256':hashlib.sha256(documents[station]).hexdigest()}))
  return documents,plan,station
 def test_only_unchanged_current_official_documents_renew_the_lease(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);documents,plan,station=self.source(root)
   proof=check_sources(root,documents.__getitem__)
   self.assertEqual(hashlib.sha256(documents[plan]).hexdigest(),proof['operatorPlanSha256'])
   self.assertTrue(proof['visualReviewAtUnchanged'])
   for changed in [plan,station]:
    altered=dict(documents);altered[changed]+=b'changed'
    with self.subTest(changed=changed),self.assertRaisesRegex(ImportErrorDetail,'changed'):
     check_sources(root,altered.__getitem__)
 def test_an_absent_or_second_current_svinov_plan_requires_review(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);documents,plan,station=self.source(root)
   for text in [b'no current plan',documents[INDEX]+b'<a href="https://kodis-files.s3.eu-central-1.amazonaws.com/Ostrava_Svinov_other.pdf">new</a>']:
    altered=dict(documents);altered[INDEX]=text
    with self.assertRaisesRegex(ImportErrorDetail,'unique current link'):check_sources(root,altered.__getitem__)
 def test_coordinate_endpoint_rebinding_changes_only_known_evidence_dates(self):
  original={'id':'call','sourceCall':{'patternSha256':'a'*64},'physicalPlatformId':'H1','platformEvidence':{'reviewId':'r','reviewedFrom':'2026-10-26','reviewedUntil':'2026-11-03','verifiedAt':'2026-10-27T12:00:00Z'}}
  current=copy.deepcopy(original);current['platformEvidence'].update({'reviewedFrom':'2026-10-27','reviewedUntil':'2026-11-04'})
  catalog={'review':{'verifiedAt':'2026-10-27T12:00:00Z'},'locations':[{'stopId':'call','stopSha256':digest(original)}]}
  result=renew_location_bindings(catalog,{'stops':[]},{'stops':[current]},{'r':original['platformEvidence']},'2026-10-27','2026-11-04',{'documentsUnchanged':True})
  self.assertEqual(digest(current),result['locations'][0]['stopSha256'])
  self.assertEqual(digest(original),result['locations'][0]['priorReviewedStopSha256'])
  self.assertEqual(catalog['review']['verifiedAt'],result['review']['verifiedAt'])
  self.assertEqual(digest(original),catalog['locations'][0]['stopSha256'])
  for field,value in [('sourceCall',{'patternSha256':'b'*64}),('physicalPlatformId','H2')]:
   changed=copy.deepcopy(current);changed[field]=value
   with self.subTest(field=field),self.assertRaisesRegex(ImportErrorDetail,'beyond evidence dates'):
    renew_location_bindings(catalog,{'stops':[]},{'stops':[changed]},{'r':original['platformEvidence']},'2026-10-27','2026-11-04',{})
