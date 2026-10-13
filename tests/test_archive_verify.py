import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from archive_verify import verify_archive

try:
    from PIL import Image
except ImportError:Image=None


@unittest.skipUnless(Image,'Image integrity tests require the bundled Pillow runtime.')
class ArchiveVerifyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)/'app';self.base.mkdir();self.output=Path(self.temp.name)/'review'
        self.pid='a'*24;self.post='b'*24
        self.folder=self.base/'clone_media'/self.pid/self.post;self.folder.mkdir(parents=True)
        self.manifest=self.base/'profile_archives'/self.pid/'posts'/f'{self.post}.json';self.manifest.parent.mkdir(parents=True)
    def image(self,name):
        path=self.folder/name;Image.new('RGB',(64,96),(20,30,40)).save(path)
        return {'file':path.relative_to(self.base).as_posix(),'kind':'photo','sourceIndex':1,'status':'saved','bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'mime':'image/jpeg'}
    def write(self,media):self.manifest.write_text(json.dumps({'profileId':self.pid,'postId':self.post,'media':media}),encoding='utf-8')
    def test_each_image_decodes_hash_matches_and_contact_index_maps_file(self):
        media=self.image('photo.jpg');self.write([media]);before=Path(self.base/media['file']).read_bytes()
        result=verify_archive(self.base,self.output,self.pid)
        self.assertEqual(result['summary']['verified'],1);row=result['files'][0]
        self.assertEqual((row['decode']['width'],row['decode']['height']),(64,96))
        self.assertTrue(row['hashMatched']);self.assertEqual(row['decode']['mime'],'image/jpeg')
        self.assertEqual(result['contactSheets'][0]['items'][0]['file'],media['file'])
        self.assertEqual(before,Path(self.base/media['file']).read_bytes())
    def test_corrupt_missing_and_hash_mismatch_are_reported(self):
        media=self.image('photo.jpg');media['sha256']='0'*64
        bad=self.folder/'corrupt.jpg';bad.write_bytes(b'not an image')
        self.write([media,{'file':bad.relative_to(self.base).as_posix(),'kind':'photo'},{'file':(self.folder/'missing.jpg').relative_to(self.base).as_posix(),'kind':'photo'}])
        result=verify_archive(self.base,self.output,self.pid)
        self.assertEqual(result['summary']['failed'],3)
        self.assertTrue(any('SHA256' in issue for row in result['files'] for issue in row['issues']))
    def test_duplicates_are_listed_and_preserved(self):
        first=self.image('one.jpg');second=self.image('two.jpg');second['sourceIndex']=2;self.write([first,second])
        result=verify_archive(self.base,self.output,self.pid)
        self.assertEqual(result['summary']['duplicateGroups'],1)
        self.assertEqual(len(result['duplicateGroups'][0]['files']),2)
        self.assertTrue(Path(self.base/first['file']).exists());self.assertTrue(Path(self.base/second['file']).exists())
    def test_output_and_profile_are_contained(self):
        with self.assertRaises(ValueError):verify_archive(self.base,self.base/'clone_media'/'review',self.pid)
        with self.assertRaises(ValueError):verify_archive(self.base,self.output,'../../outside')

    def test_shared_comment_attachment_is_verified_once_with_each_reference(self):
        asset=self.image('emoji.jpg');asset['kind']='emoji'
        shared=self.base/'clone_media'/'comment_assets'/'emoji.jpg';shared.parent.mkdir()
        Path(self.base/asset['file']).rename(shared);asset['file']=shared.relative_to(self.base).as_posix()
        self.manifest.write_text(json.dumps({'profileId':self.pid,'postId':self.post,'media':[],
            'comments':[{'id':'reply-one','text':'','attachments':[asset]},
                        {'id':'reply-two','text':'','attachments':[asset]}]}),encoding='utf-8')
        result=verify_archive(self.base,self.output,self.pid)
        self.assertEqual(result['summary']['verified'],1)
        self.assertEqual(result['summary']['duplicateGroups'],0)
        self.assertEqual({c['commentId'] for c in result['files'][0]['claims']},{'reply-one','reply-two'})
        self.assertTrue(result['files'][0]['hashMatched'])
        (shared.parent/'index.json').write_text('{}',encoding='utf-8')
        all_profiles=verify_archive(self.base,self.output)
        self.assertEqual(all_profiles['summary']['files'],1)
        self.assertEqual(all_profiles['summary']['unverified'],0)

    def test_missing_comment_attachment_is_reported(self):
        self.manifest.write_text(json.dumps({'profileId':self.pid,'postId':self.post,
            'comments':[{'id':'reply','text':'','attachments':[{'kind':'emoji',
                'file':'clone_media/comment_assets/missing.png','status':'saved'}]}]}),encoding='utf-8')
        result=verify_archive(self.base,self.output,self.pid)
        self.assertEqual(result['summary']['failed'],1)
        self.assertEqual(result['files'][0]['claims'][0]['commentId'],'reply')


if __name__=='__main__':unittest.main()
