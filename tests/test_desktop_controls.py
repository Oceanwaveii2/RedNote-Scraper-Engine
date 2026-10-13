import unittest
from unittest.mock import Mock, patch
from desktop_bridge import Workspace, BrowserToolbarAPI
import app

class DesktopControlsTests(unittest.TestCase):
    def test_closed_window_reference_is_cleared_and_restore_recreates_it(self):
        workspace=Workspace();old=Mock();new=Mock();workspace._controls=old
        workspace._window_closed('_controls',old)
        self.assertIsNone(workspace._controls)
        workspace._controls_factory=Mock(return_value=new)
        with patch('webview.windows',[]):result=workspace.focus_controls()
        self.assertIn('message',result);self.assertIs(workspace._controls,new)
        new.restore.assert_called_once();new.show.assert_called_once()

    def test_download_retention_never_removes_older_originals(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);nested=root/'original';nested.mkdir();media=nested/'video.mp4';media.write_bytes(b'original bytes')
            with patch.object(app,'DOWNLOAD_DIR',root):app.cleanup_old_jobs()
            self.assertEqual(media.read_bytes(),b'original bytes')

    def test_toolbar_has_studio_entry(self):
        from source_toolbar import LIVE_TOOLBAR_SCRIPT
        self.assertIn('data-action="focus-controls">Studio',LIVE_TOOLBAR_SCRIPT)
    def test_bridge_exposes_commands_only(self):
        workspace=Workspace()
        workspace._source=Mock()
        names=[name for name in dir(workspace) if not name.startswith('_')]
        self.assertEqual(set(names),{'capture','navigate','download','progress','open_downloads','capture_post','scroll_comments','download_open_media','current_page','back','forward','reload','profile_preview','start_profile_batch','stop_profile_batch','start_profile_clone','profile_clone_status','pause_profile_clone','stop_profile_clone','automatic_profile_batch','resume_profile_clone','retry_profile_clone','shutdown','open_browser','eval_js','focus_controls','restore_windows','inject_live_toolbar'})
        self.assertTrue(all(callable(getattr(workspace,name)) for name in names))

    def test_source_toolbar_has_restricted_native_commands(self):
        workspace=Workspace()
        api=BrowserToolbarAPI(workspace)
        self.assertEqual([name for name in dir(api) if not name.startswith('_')],['dispatch'])
        self.assertIn('error',api.dispatch('eval-js',{'script':'document.cookie'}))

    def test_open_profile_and_reject_non_rednote(self):
        workspace=Workspace()
        workspace._source=Mock()
        self.assertIn('message',workspace.navigate('https://www.rednote.com/user/profile/abc'))
        workspace._source.load_url.assert_called_once()
        self.assertIn('error',workspace.navigate('https://example.com'))

    def test_private_close_preserves_queue_before_closing_windows(self):
        workspace=Workspace()
        workspace._source=Mock();workspace._controls=Mock()
        workspace._clone_service=Mock()
        callbacks=[]
        with patch('desktop_bridge.threading.Thread') as thread:
            thread.side_effect=lambda **kwargs: callbacks.append(kwargs['target']) or Mock()
            result=workspace._request_shutdown()
            self.assertIn('message',result)
            workspace._request_shutdown()
        self.assertEqual(len(callbacks),1)
        with patch('desktop_bridge.time.sleep'):
            callbacks[0]()
        workspace._clone_service.shutdown.assert_called_once()
        workspace._source.destroy.assert_called_once()
        workspace._controls.destroy.assert_called_once()

    def test_browser_commands_wait_for_paused_worker_to_finish(self):
        workspace=Workspace();workspace._source=Mock();workspace._clone_service=Mock()
        workspace._clone_service.cooldown_remaining.return_value=0
        workspace._clone_service.status.return_value={'activeJobId':'inflight','jobs':[{'status':'paused'}]}
        for action in [lambda:workspace.navigate('https://www.rednote.com/'),workspace.back,workspace.forward,workspace.reload,lambda:workspace.eval_js('location.reload()')]:
            self.assertIn('error',action())
        workspace._source.load_url.assert_not_called()
        workspace._source.evaluate_js.assert_not_called()

    def test_site_pause_blocks_navigation_and_legacy_batches(self):
        workspace=Workspace();workspace._source=Mock();workspace._clone_service=Mock()
        workspace._clone_service.cooldown_remaining.return_value=120
        workspace._clone_service.status.return_value={'activeJobId':None,'jobs':[{'status':'needs_attention'}]}
        for action in [lambda:workspace.navigate('https://www.rednote.com/'),workspace.back,workspace.forward,workspace.reload,lambda:workspace.eval_js('location.reload()')]:
            self.assertIn('cooldown',action()['error'])
        with self.assertRaisesRegex(ValueError,'cooldown'):
            workspace.start_profile_batch('name',[],confirm_permission=True)
        workspace._source.load_url.assert_not_called()

    def test_full_clone_waits_for_existing_rednote_download(self):
        workspace=Workspace()
        job=app.Job(id='guard-fixture',url='https://www.rednote.com/discovery/item/'+'a'*24,platform='xiaohongshu')
        with patch.dict(app.jobs,{job.id:job},clear=True):
            for action in [lambda:workspace.start_profile_clone('https://www.rednote.com/user/profile/'+'a'*24),lambda:workspace.resume_profile_clone('job'),lambda:workspace.retry_profile_clone('job')]:
                with self.assertRaisesRegex(ValueError,'existing RedNote download'):
                    action()

    def test_queue_post_without_blocking_ui(self):
        workspace=Workspace()
        with patch('desktop_bridge.threading.Thread') as thread:
            result=workspace.download('https://www.rednote.com/discovery/item/6ab090fb0000000011031bfd','video')
            self.assertIn('jobId',result)
            thread.return_value.start.assert_called_once()
            self.assertEqual(workspace.progress(result['jobId'])['state'],'queued')
        self.assertIn('error',workspace.download('https://www.rednote.com/user/profile/5bdbb48db999950001f44190'))

