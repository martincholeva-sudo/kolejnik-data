import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from national.common import finalize, ImportErrorDetail
from national.publication import publish
from national.remote_publication import publish_refs
from test_national_publication import network, inputs, REPORT, NOW

ROOT=Path(__file__).resolve().parents[1]
LEGACY='work/rail-counter-times-20261027'


def run(folder,*args):
    p=subprocess.run(['git','-C',str(folder),*args],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if p.returncode:
        if os.name=='nt' and b'NtCreateDirectoryObject' in p.stderr:
            raise unittest.SkipTest('Git for Windows local transport is unavailable inside this AppContainer; exercised on Linux CI')
        raise AssertionError(p.stderr.decode('utf-8','replace'))
    return p.stdout


class RemotePublicationTests(unittest.TestCase):
    def setUp(self):
        runtime=ROOT/'runtime';runtime.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='git-publication-test-',dir=runtime)
        self.base=Path(self.temp.name).resolve()
        self.addCleanup(self.clean)
        self.assertTrue(self.base.is_relative_to(runtime.resolve()))
        self.remote=self.base/'origin.git';self.remote.mkdir()
        run(self.remote,'init','--bare','--initial-branch=main')
        self.repo=self.base/'producer';self.repo.mkdir()
        run(self.repo,'init','--initial-branch=main');self.configure(self.repo)
        run(self.repo,'remote','add','origin',str(self.remote))
        (self.repo/'index.html').write_text('main application',encoding='utf-8')
        run(self.repo,'add','index.html');run(self.repo,'commit','-m','main code')
        run(self.repo,'push','origin','HEAD:refs/heads/main')
        run(self.repo,'switch','-c',LEGACY)
        (self.repo/'index.html').write_text('installed APK compatibility site',encoding='utf-8')
        run(self.repo,'commit','-am','compatibility code');run(self.repo,'push','origin','HEAD:refs/heads/'+LEGACY)
        run(self.repo,'switch','main')
        self.public=self.repo/'prepared/data-national'
        self.minimums=patch('national.publication.MINIMUMS',{k:1 for k in ['pid','rail','ostrava','stops','dailyTrips']})
        self.minimums.start();self.addCleanup(self.minimums.stop)
        publish(network(),REPORT,inputs(),self.public,self.repo/'runtime',NOW)

    def clean(self):
        target=Path(self.temp.name).resolve()
        self.assertTrue(target.is_relative_to((ROOT/'runtime').resolve()))
        self.assertNotEqual(target,(ROOT/'runtime').resolve())
        self.temp.cleanup()

    def configure(self,repo):
        run(repo,'config','user.name','publication test')
        run(repo,'config','user.email','publication-test@example.invalid')
        run(repo,'config','core.autocrlf','false')

    def heads(self):
        return {b:run(self.remote,'rev-parse','refs/heads/'+b).decode().strip() for b in ['main',LEGACY]}

    def read(self,branch,path):
        return run(self.remote,'show',branch+':'+path)

    def edit_remote(self,branch,path,content):
        writer=self.base/('writer-'+str(len(list(self.base.glob('writer-*')))))
        run(self.base,'clone',str(self.remote),str(writer));self.configure(writer)
        run(writer,'switch','--detach','origin/'+branch)
        p=writer/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(content)
        run(writer,'add','--',path);run(writer,'commit','-m','independent edit')
        run(writer,'push','origin','HEAD:refs/heads/'+branch)
        return run(writer,'rev-parse','HEAD').decode().strip()

    def test_identical_data_reach_both_sites_without_changing_code_or_user_index(self):
        (self.repo/'user-note.txt').write_text('unrelated staged work',encoding='utf-8')
        run(self.repo,'add','user-note.txt')
        result=publish_refs(self.repo,self.public,now=NOW)
        self.assertTrue(result['success']);self.assertTrue(result['atomic'])
        self.assertEqual(b'main application',self.read('main','index.html'))
        self.assertEqual(b'installed APK compatibility site',self.read(LEGACY,'index.html'))
        self.assertEqual(self.read('main','spojeni/data-national/manifest.json'),self.read(LEGACY,'spojeni/data-national/manifest.json'))
        self.assertEqual('user-note.txt',run(self.repo,'diff','--cached','--name-only').decode().strip())
        self.assertNotIn(b'user-note.txt',run(self.remote,'ls-tree','--name-only','main'))

    def test_changed_source_bytes_leave_both_remote_pointers_unchanged(self):
        previous=self.heads();m=json.loads((self.public/'manifest.json').read_text())
        (self.public/next(iter(m['days'].values()))).write_bytes(b'truncated')
        with self.assertRaises(ImportErrorDetail):publish_refs(self.repo,self.public,now=NOW)
        self.assertEqual(previous,self.heads())

    def test_concurrent_main_change_rejects_whole_push_then_is_preserved_on_retry(self):
        original=self.heads();observed=[]
        def race(attempt,commits):
            if attempt==0:
                changed=self.edit_remote('main','index.html',b'newer independent main code')
                observed.append(changed)
            elif attempt==1:
                self.assertEqual({'main':observed[0],LEGACY:original[LEGACY]},self.heads())
                self.assertNotIn(b'spojeni',run(self.remote,'ls-tree','--name-only','main'))
        result=publish_refs(self.repo,self.public,now=NOW,before_push=race)
        self.assertEqual(2,result['attempts'])
        self.assertEqual(b'newer independent main code',self.read('main','index.html'))
        self.assertEqual(self.read('main','spojeni/data-national/manifest.json'),self.read(LEGACY,'spojeni/data-national/manifest.json'))

    def test_newer_remote_verification_is_not_replaced_with_an_older_receipt(self):
        newer=inputs()
        for rows in newer['sources'].values():rows[0]['verifiedAt']='2026-10-27T11:30:00+00:00'
        publish(network(),REPORT,newer,self.public,self.repo/'runtime',NOW)
        publish_refs(self.repo,self.public,now=NOW);previous=self.heads()
        publish(network(),REPORT,inputs(),self.public,self.repo/'runtime',NOW)
        with self.assertRaisesRegex(ImportErrorDetail,'newer remote'):publish_refs(self.repo,self.public,now=NOW)
        self.assertEqual(previous,self.heads())

    def test_retained_immutable_collision_on_one_site_blocks_both_sites(self):
        publish_refs(self.repo,self.public,now=NOW)
        old=json.loads((self.public/'manifest.json').read_text());name=next(iter(old['days'].values()))
        self.edit_remote(LEGACY,'spojeni/data-national/'+name,b'changed old immutable bytes')
        previous=self.heads();n=network();n['trips'][0][4]='new revision';finalize(n)
        publish(n,REPORT,inputs(),self.public,self.repo/'runtime',NOW)
        with self.assertRaisesRegex(ImportErrorDetail,'Immutable remote'):publish_refs(self.repo,self.public,now=NOW)
        self.assertEqual(previous,self.heads())

    def test_unchanged_publication_adds_no_commits(self):
        publish_refs(self.repo,self.public,now=NOW);previous=self.heads()
        publish_refs(self.repo,self.public,now=NOW)
        self.assertEqual(previous,self.heads())

    def test_a_server_without_atomic_support_keeps_both_sites_unchanged(self):
        previous=self.heads();run(self.remote,'config','receive.advertiseAtomic','false')
        with self.assertRaisesRegex(ImportErrorDetail,'atomic'):
            publish_refs(self.repo,self.public,now=NOW,attempts=1)
        self.assertEqual(previous,self.heads())
