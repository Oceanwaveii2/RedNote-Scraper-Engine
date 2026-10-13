"""Execute browser scripts against minimal DOM fixtures without a browser/site."""
import json
import shutil
import subprocess
import unittest

from browser_capture import SCROLL_SCRIPT,NOTE_SCRIPT
from profile_capture import PROFILE_SNAPSHOT_SCRIPT,PROFILE_ADVANCE_SCRIPT


@unittest.skipUnless(shutil.which('node'),'Capture script execution requires Node.')
class CaptureScriptTests(unittest.TestCase):
    def execute(self,script,setup):
        code=setup+'\nconsole.log(JSON.stringify(eval('+json.dumps(script)+')));'
        result=subprocess.run(['node','-e',code],capture_output=True,encoding='utf-8',timeout=10)
        if result.returncode:self.fail(result.stderr)
        return json.loads(result.stdout)
    def profile_setup(self,text,visible=True):
        return '''const marker={innerText:%s,getClientRects:()=>%s};
const scroll={scrollTop:1500,clientHeight:500,scrollHeight:2000};
global.getComputedStyle=()=>({visibility:'visible'});
global.window={__INITIAL_STATE__:{}};
global.location={hostname:'www.rednote.com',pathname:'/user/profile/aaaaaaaaaaaaaaaaaaaaaaaa',origin:'https://www.rednote.com'};
global.document={scrollingElement:scroll,documentElement:scroll,body:{},querySelectorAll:s=>s.includes('.end-container')?[marker]:[]};
'''%(json.dumps(text),'[{}]' if visible else '[]')
    def test_only_visible_literal_english_feed_endings_establish_terminal(self):
        for script in (PROFILE_SNAPSHOT_SCRIPT,PROFILE_ADVANCE_SCRIPT):
            for text in ('THE END',"You've reached the end",'No more notes.'):
                self.assertEqual(self.execute(script,self.profile_setup(text))['endMarker'],text)
            for text in ('THE END of the caption','No more notes about this topic','All notes by this author'):
                self.assertEqual(self.execute(script,self.profile_setup(text))['endMarker'],'')
            self.assertEqual(self.execute(script,self.profile_setup('THE END',False))['endMarker'],'')
    def test_feed_boundary_reads_only_matching_settled_notes_tab(self):
        state={'user':{'activeTab':{'_value':{'index':0}},'noteQueries':{'_value':[{'userId':'a'*24,'hasMore':False}]},'notes':{'_value':[[{'id':'b'*24},{'id':'b'*24}]]},'isFetchingNotes':{'_value':[False]},'firstFetchNote':{'_value':False}}}
        def run():
            setup=self.profile_setup('')+'\nwindow.__INITIAL_STATE__='+json.dumps(state)+';'
            return self.execute(PROFILE_SNAPSHOT_SCRIPT,setup)['feedBoundary']
        self.assertEqual(run()['sourceNoteCount'],1)
        state['user']['isFetchingNotes']['_value'][0]=True
        self.assertIsNone(run())
        state['user']['isFetchingNotes']['_value'][0]=False
        state['user']['activeTab']['_value']['index']=1
        self.assertIsNone(run())
        state['user']['activeTab']['_value']['index']=0
        state['user']['noteQueries']['_value'][0]['userId']='c'*24
        self.assertIsNone(run())
    def comment_setup(self,label,disabled=False,loading=False):
        return '''let clicks=0;
const button={innerText:%s,className:'show-more',disabled:%s,getClientRects:()=>[{}],closest:()=>%s,contains:()=>false,click:()=>{clicks++;}};
const scroll={scrollTop:0,clientHeight:500,scrollHeight:2000};
const root={getClientRects:()=>[{}],querySelectorAll:s=>s.includes('.show-more')?[button]:[]};
global.getComputedStyle=()=>({visibility:'visible',overflowY:'auto'});
global.document={scrollingElement:scroll,documentElement:scroll,body:{},querySelectorAll:()=>[root]};
'''%(json.dumps(label),'true' if disabled else 'false','({})' if loading else 'null')
    def test_reply_expansion_never_toggles_collapse_or_disabled_loading_controls(self):
        for label in ('Show less','Collapse','收起','折叠'):
            result=self.execute(SCROLL_SCRIPT,self.comment_setup(label))
            self.assertEqual(result['clicked'],0)
        self.assertEqual(self.execute(SCROLL_SCRIPT,self.comment_setup('Show 4 more replies',disabled=True))['clicked'],0)
        self.assertEqual(self.execute(SCROLL_SCRIPT,self.comment_setup('Show 4 more replies',loading=True))['clicked'],0)
        result=self.execute(SCROLL_SCRIPT,self.comment_setup('Show 4 more replies'))
        self.assertEqual(result['clicked'],1);self.assertFalse(result['scrolled'])
    def test_note_snapshot_selects_exposed_h264_mp4_before_higher_resolution_hevc(self):
        setup='''
const root={getClientRects:()=>[{}],querySelectorAll:()=>[],querySelector:()=>null};
global.getComputedStyle=()=>({visibility:'visible'});
global.document={querySelectorAll:s=>s==='.note-container,.note-detail-mask,.note-detail'?[root]:[],querySelector:()=>null};
global.location={hostname:'www.rednote.com',pathname:'/explore/bbbbbbbbbbbbbbbbbbbbbbbb',origin:'https://www.rednote.com'};
global.window={__INITIAL_STATE__:{note:{noteDetailMap:{bbbbbbbbbbbbbbbbbbbbbbbb:{note:{type:'video',user:{userId:'aaaaaaaaaaaaaaaaaaaaaaaa'},interactInfo:{commentCount:0},video:{media:{stream:{h265:[{masterUrl:'https://video.xhscdn.com/hevc.mp4',width:3840,height:2160}],h264:[{masterUrl:'https://video.xhscdn.com/avc.mp4',width:1920,height:1080}]}}}}}}}}};
'''
        result=self.execute(NOTE_SCRIPT,setup)
        self.assertEqual(len(result['videos']),1)
        self.assertEqual(result['videos'][0]['url'],'https://video.xhscdn.com/avc.mp4')
        self.assertEqual(result['videos'][0]['codec'],'h264')
        self.assertEqual(result['media'][0]['sourceIndex'],1)

    def test_view_more_branch_and_noop_expansion_are_bounded(self):
        for label in ('View 7 replies','View more comments','More replies','More comments','查看更多评论','展开4条回复'):
            self.assertEqual(self.execute(SCROLL_SCRIPT,self.comment_setup(label))['clicked'],1)
        setup=self.comment_setup('View 7 replies')
        code=setup+'\nconst script='+json.dumps(SCROLL_SCRIPT)+';console.log(JSON.stringify([eval(script),eval(script),eval(script)]));'
        result=subprocess.run(['node','-e',code],capture_output=True,encoding='utf-8',timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        rows=json.loads(result.stdout)
        self.assertEqual([row['clicked'] for row in rows],[1,1,0])
        self.assertEqual(rows[-1]['blockedBranches'],1)

    def test_decorative_text_does_not_become_a_comment_control(self):
        setup=self.comment_setup('View more comments').replace("className:'show-more'","className:'comment-text',tagName:'SPAN'")
        self.assertEqual(self.execute(SCROLL_SCRIPT,setup)['clicked'],0)

    def test_comment_avatar_is_observed_as_a_separate_local_asset_role(self):
        setup='''
const visible=()=>[{}];
const root={getClientRects:visible,querySelector:()=>null,querySelectorAll:s=>s.startsWith('[data-comment-id]')?[comment]:[]};
const name={innerText:'Fixture Reader',getClientRects:visible};
const author={querySelectorAll:s=>s.startsWith('.name')?[name]:[],closest:()=>comment};
const content={innerText:'Opaque fixture',querySelectorAll:()=>[],closest:()=>comment};
const avatar={currentSrc:'https://img.xhscdn.com/avatar.png?signature=transient',naturalWidth:48,naturalHeight:48,closest:()=>comment};
const comment={id:'',parentElement:root,getClientRects:visible,getAttribute:s=>s==='data-comment-id'?'native-comment-fixture':'',closest:s=>s.startsWith('.comment-item')?comment:null,querySelectorAll:s=>s.startsWith('.content')?[content]:s.startsWith('.author-wrapper')?[author]:s.includes('a[href*="/user/profile/"] img')?[avatar]:[]};
global.getComputedStyle=()=>({visibility:'visible'});
global.document={querySelectorAll:s=>s==='.note-container,.note-detail-mask,.note-detail'?[root]:[],querySelector:()=>null};
global.location={hostname:'www.rednote.com',pathname:'/discovery/item/bbbbbbbbbbbbbbbbbbbbbbbb',origin:'https://www.rednote.com'};
global.window={__INITIAL_STATE__:{}};
'''
        row=self.execute(NOTE_SCRIPT,setup)['comments'][0]
        self.assertEqual(row['id'],'native-comment-fixture')
        self.assertEqual(row['attachments'][0]['role'],'author_avatar')
        self.assertEqual(row['attachments'][0]['sourceUrl'],'https://img.xhscdn.com/avatar.png')
        self.assertIn('signature=transient',row['attachments'][0]['requestUrl'])

    def test_literal_comment_terminal_marker_is_recognized(self):
        for label,expected in [('- The End -','- The End -'),('THE END','THE END'),('The End of a caption','')]:
            setup=self.comment_setup('Collapse')
            setup=setup.replace('const root=', 'const marker={innerText:'+json.dumps(label)+',getClientRects:()=>[{}]};\nconst root=')
            setup=setup.replace("s.includes('.show-more')?[button]:[]","s.includes('.show-more')?[button]:s.includes('.end-container')?[marker]:[]")
            self.assertEqual(self.execute(SCROLL_SCRIPT,setup)['endMarker'],expected)


if __name__=='__main__':unittest.main()
