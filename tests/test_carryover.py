import unittest,tempfile,json,gzip,sys,copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from gtfs_carryover import carry_previous_day

class CarryoverTest(unittest.TestCase):
    def test_retains_real_overnight_times_and_maps_new_platform_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            old={'sha256':'old','active':{'2026-10-07':['wed']},'stops':[{'id':'A'},{'id':'B'}],'routes':[['r','7',0]],'paths':[],
                 'trips':[['night','wed',0,'B','',[[0,86460,86460,0,0,1,7],[1,87000,87000,0,0,2,7]]],['day','wed',0,'B','',[[0,60,60,0,0,1,1],[1,600,600,0,0,2,1]]]]}
            (folder/'manifest.json').write_text(json.dumps({'days':{'2026-10-07':'old.gz'}}))
            with gzip.open(folder/'old.gz','wt') as f:json.dump(old,f)
            current={'sha256':'new','start':'2026-10-08','active':{'2026-10-08':['thu']},'stops':[{'id':'B'},{'id':'A'}],'routes':[['r','7',0]],'paths':[],'trips':[]}
            self.assertEqual(carry_previous_day(current,folder),1)
            ride=current['trips'][0];self.assertEqual(ride[0],'night');self.assertEqual(ride[5][0][:3],[1,86460,86460]);self.assertNotIn(ride[1],current['active']['2026-10-08']);self.assertIn(ride[1],current['active']['2026-10-07'])
            with gzip.open(folder/'today.gz','wt') as f:json.dump(current,f)
            (folder/'manifest.json').write_text(json.dumps({'days':{'2026-10-08':'today.gz'}}))
            next_feed=copy.deepcopy(current);next_feed['trips']=[];next_feed['active'].pop('2026-10-07')
            self.assertEqual(carry_previous_day(next_feed,folder),1);self.assertEqual(next_feed['trips'][0][1],ride[1])

    def test_missing_previous_feed_does_not_invent_yesterday(self):
        with tempfile.TemporaryDirectory() as tmp:self.assertEqual(carry_previous_day({'start':'2026-10-08'},tmp),0)

if __name__=='__main__':unittest.main()
