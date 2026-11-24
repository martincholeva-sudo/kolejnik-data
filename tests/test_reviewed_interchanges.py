import copy
import unittest
from national_fixture import DAY,network_fixture
from national.common import blank,finalize,digest,ImportErrorDetail
from national.bundle import compose

class ReviewedInterchangeTests(unittest.TestCase):
    def test_reviewed_same_platform_aliases_retain_the_two_minute_change_minimum(self):
        n=blank(DAY,DAY,'synthetic')
        for identity in ['one','two']:
            n['stops'].append({'id':identity,'name':'Hub','type':0,'group':'hub','lat':0,'lon':0,'physicalPlatformId':'synthetic:quay:N1','physicalPlatformVerified':True,'platformEvidence':{'url':'https://example.test/plan.pdf','verifiedAt':'2026-10-27T11:00:00Z','validFrom':DAY,'reviewedUntil':DAY}})
        finalize(n);result=compose([('city',n)],DAY,DAY)
        self.assertEqual(sorted(result['paths']),[[0,1,0,False],[1,0,0,False]])
        self.assertEqual(sorted(result['transfers']),[[0,1,2,120,'',''],[1,0,2,120,'','']])
        n['stops'][1]['physicalPlatformId']='synthetic:quay:N2';result=compose([('city',n)],DAY,DAY)
        self.assertEqual(result['paths'],[])
    def test_changed_expired_or_naive_review_cannot_reuse_old_walking_links(self):
        n=network_fixture();link={'from':n['stops'][0]['id'],'to':n['stops'][1]['id'],'seconds':60,'estimated':True,'verified':True,'evidence':'https://example.test/paths','verifiedAt':'2026-10-27T11:00:00Z','reviewedFrom':DAY,'reviewedUntil':DAY,'fromStopSha256':digest(n['stops'][0]),'toStopSha256':digest(n['stops'][1])}
        good=compose([('one',n)],DAY,DAY,{'schema':1,'links':[link]});self.assertEqual(good['paths'][-1][2:],[60,True])
        for key,value in [('fromStopSha256','0'*64),('reviewedUntil','2026-10-25'),('reviewedFrom','2026-10-28'),('verifiedAt','2026-10-27T11:00:00'),('estimated','yes')]:
            changed={**link,key:value};before=copy.deepcopy(n)
            with self.assertRaises(ImportErrorDetail):compose([('one',n)],DAY,DAY,{'schema':1,'links':[changed]})
            self.assertEqual(n,before)
    def test_a_platform_claim_without_source_evidence_creates_no_aliases(self):
        n=blank(DAY,DAY,'synthetic');n['stops']=[{'id':'one','name':'Hub','type':0,'group':'hub','physicalPlatformId':'quay','physicalPlatformVerified':True}];finalize(n)
        with self.assertRaisesRegex(ImportErrorDetail,'evidence'):compose([('city',n)],DAY,DAY)

    def test_expired_platform_metadata_cannot_be_reused_for_a_new_horizon(self):
        n=blank(DAY,DAY,'synthetic')
        n['stops']=[{'id':'one','name':'Hub','type':0,'group':'hub','physicalPlatformId':'quay','physicalPlatformVerified':True,'platformEvidence':{'url':'https://example.test/plan.pdf','verifiedAt':'2026-10-27T11:00:00Z','validFrom':DAY,'reviewedUntil':DAY}}]
        finalize(n)
        with self.assertRaisesRegex(ImportErrorDetail,'expired'):compose([('city',n)],DAY,'2026-10-28')

    def test_source_geometry_attribution_survives_composition(self):
        n=network_fixture()
        catalog={'schema':1,'attribution':'© OpenStreetMap contributors, ODbL 1.0','links':[{'from':n['stops'][0]['id'],'to':n['stops'][1]['id'],'seconds':90,'estimated':True,'verified':True,'evidence':'https://example.test/paths','verifiedAt':'2026-10-27T11:00:00Z'}]}
        result=compose([('one',n)],DAY,DAY,catalog)
        self.assertIn(catalog['attribution'],result['attribution'])
        self.assertEqual(result['interchangeAttribution'],catalog['attribution'])
