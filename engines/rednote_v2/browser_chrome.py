"""Compact native chrome outside the source viewport; GPT renders a reversible view.

Workspace owns navigation guards and the shared login. This module never reads
cookies or circumvents website access responses. Archive originals are unchanged.
"""
import threading
import time

_install_lock = threading.RLock()
_fault_lock = threading.Lock()
_last_fault_dump = 0.0


def dispatch_native_ui(native, callback, timeout=5):
    """Post first, then wait outside CLR; never synchronously Invoke from a worker."""
    if not native.InvokeRequired:
        callback()
        return
    from System import Action
    done, cancelled = threading.Event(), threading.Event()
    errors = []
    def run():
        try:
            if not cancelled.is_set():callback()
        except Exception as exc:errors.append(exc)
        finally:done.set()
    native.BeginInvoke(Action(run))
    if not done.wait(timeout):
        cancelled.set()
        raise TimeoutError('Native UI handoff timed out')
    if errors:raise errors[0]


def record_ui_fault(exc):
    """Safe metadata plus Python stacks (no locals, clipboard or page text)."""
    global _last_fault_dump
    try:
        import app
        app.v2_blueprint.store.event('host.ui_handoff','failed',counts={'errorClass':type(exc).__name__})
        if isinstance(exc,TimeoutError):
            with _fault_lock:
                now=time.monotonic()
                if now-_last_fault_dump<30:return
                _last_fault_dump=now
                import faulthandler
                from datetime import datetime,timezone
                folder=app.BASE_DIR/'development-exports';folder.mkdir(exist_ok=True)
                with (folder/'ui-hang-stacks.log').open('a',encoding='utf-8') as f:
                    f.write('\nUI handoff timeout '+datetime.now(timezone.utc).isoformat()+'\n');f.flush()
                    faulthandler.dump_traceback(file=f,all_threads=True)
    except Exception:pass

class NativeBrowserChrome:
    def __init__(self, workspace, window, mode):
        from System import Action
        self.Action = Action
        self.workspace, self.window, self.mode = workspace, window, mode
        self.ready = False
        self.epoch = 0
        self.results = []
        self.view = 'Original'
        self.busy = False
        self.last_url = None
        self.panel_open = False
        self._snapshot_saved = False
        self._snapshot_path = None
        self._translation_lock = threading.Lock()
        self._page_render_lock = threading.Lock()
        self.auto_page=False
        self.auto_url=None
        self.auto_selection=None
        self.page_cache={}
        self.translation_error=None
        self.translation_phase='idle'
        self._closed=threading.Event()
        window.events.closed += lambda:self._closed.set()
        if not self.ui(self._build):raise RuntimeError('Native toolbar initialization failed')
        threading.Thread(target=self._watch_page_translation,name='rednote-page-watch',daemon=True).start()

    def px(self, value):
        return int(round(value * self.scale))

    def _build(self):
        from System.Drawing import Color, Font, FontStyle, ContentAlignment
        from System.Windows.Forms import (TableLayoutPanel, Panel, TextBox, Button,
            Label, DockStyle, RowStyle, ColumnStyle, SizeType, Padding, ScrollBars,
            ComboBox, ComboBoxStyle, FlatStyle, BorderStyle, ContextMenuStrip,
            ToolStripMenuItem, ToolStripSeparator, ToolStripRenderMode, ToolTip, TextImageRelation)

        native = self.window.native
        self.web = native.browser.webview
        # BrowserForm._scale uses GetDpiForWindow. DeviceDpi alone reported 96
        # on this 150% host, clipping labels against already scaled fonts.
        self.scale = max(1.0, float(native._scale))
        px = self.px
        self.color = {k: Color.FromArgb(*v) for k, v in {
            'bar': (18, 18, 21), 'panel': (24, 24, 28), 'input': (32, 32, 37),
            'line': (52, 52, 59), 'hover': (48, 48, 55), 'text': (238, 238, 242),
            'muted': (162, 162, 173), 'red': (255, 37, 73), 'redButton': (181, 28, 53),
        }.items()}
        self.font, self.small_font = Font('Segoe UI', 9.5), Font('Segoe UI', 9)
        self.tooltip = ToolTip()
        self.tooltip.ShowAlways, self.tooltip.InitialDelay = True, 450
        self.nav = []
        self.toolbar_buttons = []

        def layout(columns, rows, background='bar'):
            panel = TableLayoutPanel()
            panel.Dock, panel.Margin, panel.Padding = DockStyle.Fill, Padding(0), Padding(0)
            panel.ColumnCount, panel.RowCount = len(columns), len(rows)
            panel.BackColor, panel.Font = self.color[background], self.font
            for width in columns:
                panel.ColumnStyles.Add(ColumnStyle(SizeType.Percent, 100) if width is None
                    else ColumnStyle(SizeType.Absolute, px(width)))
            for height in rows:
                panel.RowStyles.Add(RowStyle(SizeType.Percent, 100) if height is None
                    else RowStyle(SizeType.Absolute, px(height)))
            return panel

        def label(text, muted=False, bold=False):
            control = Label()
            control.Text, control.Dock, control.Margin = text, DockStyle.Fill, Padding(0)
            control.ForeColor = self.color['muted' if muted else 'text']
            control.TextAlign, control.AutoEllipsis = ContentAlignment.MiddleLeft, True
            control.Font = Font('Segoe UI', 9 if muted else 10,
                FontStyle.Bold if bold else FontStyle.Regular)
            return control

        def button(text, callback, tip=None, accent=False, immediate=False, icon=None):
            control = Button()
            control.Text, control.AccessibleName = text, tip or text
            control.Dock = DockStyle.Fill
            control.Margin = Padding(px(3), px(7), px(3), px(7))
            control.FlatStyle = FlatStyle.Flat
            control.FlatAppearance.BorderSize = 0
            control.FlatAppearance.MouseOverBackColor = self.color['hover']
            control.FlatAppearance.MouseDownBackColor = self.color['line']
            control.UseVisualStyleBackColor = False
            control.BackColor, control.ForeColor = self.color['redButton' if accent else 'bar'], self.color['text']
            control.Font, control.TabStop = self.font, True
            if icon:
                control.Image = self._icon(icon)
                control.TextImageRelation = TextImageRelation.ImageBeforeText
                control.Padding = Padding(px(6), 0, px(6), 0) if text else Padding(0)
            control.FlatAppearance.BorderColor = self.color['red']
            control.GotFocus += lambda sender, event: setattr(control.FlatAppearance, 'BorderSize', 1)
            control.LostFocus += lambda sender, event: setattr(control.FlatAppearance, 'BorderSize', 0)
            self._round_control(control, px(7))
            self.tooltip.SetToolTip(control, tip or text)
            control.Click += (lambda sender, event: callback()) if immediate else (lambda sender, event: self._async(callback))
            return control

        def text_box(multiline=False, readonly=False):
            control = TextBox()
            control.Multiline, control.ReadOnly, control.BorderStyle = multiline, readonly, getattr(BorderStyle, 'None')
            control.BackColor, control.ForeColor, control.Font = self.color['input'], self.color['text'], self.font
            control.Dock, control.Margin = DockStyle.Fill, Padding(0)
            if multiline:
                control.ScrollBars, control.WordWrap = ScrollBars.Vertical, True
            return control

        self.table = layout([None, 0], [72, None])
        self.bar = layout([None], [48, 24])
        self.bar.Padding = Padding(px(8), 0, px(8), 0)
        top = layout([112, 102, None, 190, 84, 90, 42], [None])
        self.navigation_row = top
        brand = layout([34, None], [None])
        badge = label('R', bold=True)
        badge.TextAlign = ContentAlignment.MiddleCenter
        badge.BackColor = self.color['red']
        badge.Margin = Padding(0, px(10), px(6), px(10))
        badge.Font = Font('Segoe UI', 11, FontStyle.Bold)
        self._round_control(badge, px(6))
        brand.Controls.Add(badge, 0, 0)
        self.brand_label = label('RedNote', bold=True)
        self.brand_label.Font = Font('Segoe UI', 9.5, FontStyle.Bold)
        brand.Controls.Add(self.brand_label, 1, 0)
        top.Controls.Add(brand, 0, 0)
        navigation = layout([34, 34, 34], [None])
        for column, (icon, action, tip) in enumerate([
            ('back', 'back', 'Back'), ('forward', 'forward', 'Forward'), ('reload', 'reload', 'Reload (F5)')]):
            control = button('', lambda a=action: self.navigate_history(a), tip, icon=icon)
            navigation.Controls.Add(control, column, 0)
            self.nav.append(control)
            self.toolbar_buttons.append(control)
        top.Controls.Add(navigation, 1, 0)

        frame = Panel()
        frame.Dock, frame.Margin = DockStyle.Fill, Padding(px(8), px(7), px(8), px(7))
        frame.Padding, frame.BackColor = Padding(px(12), px(6), px(10), px(4)), self.color['bar']
        self._round_frame(frame, self.color['input'], self.color['line'], px(9))
        self.address = text_box()
        self.address.AccessibleName = 'RedNote address or search'
        self.address.KeyDown += lambda sender, event: self._enter(event)
        self.address.GotFocus += lambda sender, event: frame.Invalidate()
        self.address.LostFocus += lambda sender, event: frame.Invalidate()
        self.tooltip.SetToolTip(self.address, 'Enter a RedNote link or search term, then press Enter. Ctrl+L focuses this field.')
        frame.Controls.Add(self.address)
        self.address_frame = frame
        self.navigation_row = top
        top.Controls.Add(frame, 2, 0)
        language = layout([None, None], [None])
        for style in language.ColumnStyles: style.Width = 50
        self.view_buttons = {}
        for column, title in enumerate(['Original', 'Translated']):
            control = button(title, lambda t=title: self.choose_language(t),
                'Restore source text' if title=='Original' else 'Translate visible page text into English', immediate=True)
            language.Controls.Add(control, column, 0)
            self.view_buttons[title] = control
            self.toolbar_buttons.append(control)
        self.language_controls = language
        top.Controls.Add(language, 3, 0)
        studio_button = button('Studio', self.workspace.focus_controls, icon='studio')
        browse_button = button('Browse', self.workspace.open_explorer, 'Independent browsing window', icon='browse')
        self.toolbar_buttons.extend([studio_button, browse_button])
        top.Controls.Add(studio_button, 4, 0)
        top.Controls.Add(browse_button, 5, 0)
        self.more_button = button('', self.open_menu, 'Browser actions', immediate=True, icon='more')
        self.toolbar_buttons.append(self.more_button)
        self.toolbar_columns = [(3, 190, self.language_controls), (4, 84, studio_button), (5, 90, browse_button), (6, 42, self.more_button)]
        top.Controls.Add(self.more_button, 6, 0)
        self.bar.Controls.Add(top, 0, 0)
        status_row = layout([None, 154], [None])
        self.status = label('Shared app session · Select and copy text normally', muted=True)
        self.mining_status = label('Mining stopped', muted=True)
        self.mining_status.TextAlign = ContentAlignment.MiddleRight
        status_row.Controls.Add(self.status, 0, 0)
        status_row.Controls.Add(self.mining_status, 1, 0)
        self.bar.Controls.Add(status_row, 0, 1)

        self.menu = ContextMenuStrip()
        self.menu.BackColor, self.menu.ForeColor, self.menu.Font = self.color['panel'], self.color['text'], self.font
        self.menu.RenderMode, self.menu.ShowImageMargin = ToolStripRenderMode.System, False

        def menu_item(title, callback, navigation=False):
            control = ToolStripMenuItem(title)
            control.BackColor, control.ForeColor = self.color['panel'], self.color['text']
            control.Click += lambda sender, event: self._async(callback)
            self.menu.Items.Add(control)
            if navigation:
                self.nav.append(control)
            return control

        menu_item('Copy page link', self.copy_link)
        menu_item('Find on page…', self.open_find)
        zoom = ToolStripMenuItem('Zoom')
        zoom.ForeColor, zoom.BackColor = self.color['text'], self.color['panel']
        for title, amount in [('Zoom in', 0.1), ('Zoom out', -0.1), ('Reset to 100%', 0)]:
            item = ToolStripMenuItem(title)
            item.ForeColor, item.BackColor = self.color['text'], self.color['panel']
            item.Click += lambda sender, event, a=amount: self._async(lambda: self.zoom(a))
            zoom.DropDownItems.Add(item)
        self.menu.Items.Add(zoom)
        if self.mode == 'miner':
            self.menu.Items.Add(ToolStripSeparator())
            menu_item('Clone this profile…', lambda: self.workspace.start_profile_clone(self.window.get_current_url(), {}), True)
            menu_item('Save this note', self.workspace.capture_post, True)
            menu_item('Load this note’s comments', self.workspace.scroll_comments, True)
            menu_item('Save this note’s media', lambda: self.window._js_api.dispatch('download-open-media'), True)
        self.menu.Items.Add(ToolStripSeparator())
        self.pause_item = menu_item('Pause mining', self.pause_mining)
        menu_item('Open Studio / saved jobs', self.workspace.focus_controls)
        menu_item('Open independent Browse window', self.workspace.open_explorer)

        native.SuspendLayout()
        native.Controls.Remove(self.web)
        self.table.Controls.Add(self.bar, 0, 0)
        self.table.SetColumnSpan(self.bar, 2)
        self.table.Controls.Add(self.web, 0, 1)
        self.web.Dock, self.web.Margin = DockStyle.Fill, Padding(0)
        native.Controls.Add(self.table)
        self.table.BringToFront()
        native.KeyPreview = True
        native.KeyDown += self._shortcut
        self.web.KeyDown += self._shortcut
        native.Resize += lambda sender, event: self._fit_panel()
        native.ResumeLayout(True)
        self._show('Original')
        self.ready = True
        import app
        app.v2_blueprint.store.event('host.native_chrome', 'ready', counts={
            'viewportReserved': True, 'compactToolbar': True, 'translationSidePanel': False, 'visiblePageOnly': True,
            'textSelectionEnabled': bool(self.window.text_select)})

    def _icon(self, kind):
        from System.Drawing import Bitmap, Graphics, Pen, RectangleF, PointF
        from System.Drawing.Drawing2D import SmoothingMode, LineCap, LineJoin
        bitmap = Bitmap(self.px(20), self.px(20))
        graphics = Graphics.FromImage(bitmap)
        graphics.SmoothingMode = SmoothingMode.AntiAlias
        graphics.ScaleTransform(float(bitmap.Width) / 24, float(bitmap.Height) / 24)
        pen = Pen(self.color['text'], 1.6)
        pen.StartCap, pen.EndCap, pen.LineJoin = LineCap.Round, LineCap.Round, LineJoin.Round
        def line(x1, y1, x2, y2):
            graphics.DrawLine(pen, PointF(x1, y1), PointF(x2, y2))
        try:
            if kind in {'back', 'forward'}:
                if kind == 'back':
                    line(19, 12, 5, 12); line(5, 12, 11, 6); line(5, 12, 11, 18)
                else:
                    line(5, 12, 19, 12); line(19, 12, 13, 6); line(19, 12, 13, 18)
            elif kind == 'reload':
                graphics.DrawArc(pen, RectangleF(4, 4, 16, 16), 30, 290)
                line(20, 4, 20, 10); line(20, 10, 14, 10)
            elif kind == 'translate':
                graphics.DrawRectangle(pen, 3, 3, 11, 14)
                graphics.DrawRectangle(pen, 10, 8, 11, 14)
                line(5, 7, 11, 7); line(7, 5, 7, 12)
                line(13, 18, 16, 12); line(16, 12, 19, 18); line(14, 16, 18, 16)
            elif kind == 'studio':
                line(6, 7, 18, 7); line(6, 7, 12, 18); line(18, 7, 12, 18)
                for x, y in [(3, 4), (15, 4), (9, 15)]:
                    graphics.DrawEllipse(pen, RectangleF(x, y, 6, 6))
            elif kind == 'browse':
                for x, y in [(4, 4), (14, 4), (4, 14), (14, 14)]:
                    graphics.DrawRectangle(pen, x, y, 6, 6)
            elif kind == 'more':
                for x in [5, 11, 17]:
                    graphics.DrawEllipse(pen, RectangleF(x, 11, 2, 2))
        finally:
            pen.Dispose(); graphics.Dispose()
        return bitmap

    def _round_control(self, control, radius):
        from System.Drawing import Region
        from System.Drawing.Drawing2D import GraphicsPath
        def resize(sender, event):
            width, height = control.Width, control.Height
            if width <= 0 or height <= 0:
                return
            diameter = min(radius * 2, width, height)
            path = GraphicsPath()
            path.AddArc(0, 0, diameter, diameter, 180, 90)
            path.AddArc(width - diameter, 0, diameter, diameter, 270, 90)
            path.AddArc(width - diameter, height - diameter, diameter, diameter, 0, 90)
            path.AddArc(0, height - diameter, diameter, diameter, 90, 90)
            path.CloseFigure()
            previous = control.Region
            control.Region = Region(path)
            path.Dispose()
            if previous is not None:
                previous.Dispose()
        control.Resize += resize

    def _round_frame(self, panel, background, border, radius):
        from System.Drawing import SolidBrush, Pen
        from System.Drawing.Drawing2D import GraphicsPath, SmoothingMode
        def paint(sender, event):
            width, height = panel.ClientSize.Width - 1, panel.ClientSize.Height - 1
            if width <= 0 or height <= 0:
                return
            diameter = min(radius * 2, width, height)
            path = GraphicsPath()
            path.AddArc(0, 0, diameter, diameter, 180, 90)
            path.AddArc(width - diameter, 0, diameter, diameter, 270, 90)
            path.AddArc(width - diameter, height - diameter, diameter, diameter, 0, 90)
            path.AddArc(0, height - diameter, diameter, diameter, 90, 90)
            path.CloseFigure()
            brush, pen = SolidBrush(background), Pen(self.color['red'] if panel.ContainsFocus else border)
            try:
                event.Graphics.SmoothingMode = SmoothingMode.AntiAlias
                event.Graphics.FillPath(brush, path)
                event.Graphics.DrawPath(pen, path)
            finally:
                pen.Dispose()
                brush.Dispose()
                path.Dispose()
        panel.Paint += paint
        panel.Resize += lambda sender, event: panel.Invalidate()

    def _enter(self, event):
        from System.Windows.Forms import Keys
        if event.KeyCode == Keys.Enter:
            event.SuppressKeyPress = True
            # TextBox.Text can SendMessage to its owning UI thread. Capture it
            # here rather than reading the native textbox from the worker.
            value = self.address.Text
            self._async(lambda: self.navigate(value))

    def _shortcut(self, sender, event):
        from System.Windows.Forms import Keys
        if event.Control and event.KeyCode == Keys.L and self.address.Enabled:
            event.SuppressKeyPress = True
            self.address.Focus()
            self.address.SelectAll()

    def ui(self, callback):
        try:
            dispatch_native_ui(self.window.native, callback)
            return True
        except Exception as exc:
            record_ui_fault(exc)
            return False

    def _message(self, message):
        self.ui(lambda: setattr(self.status, 'Text', str(message)[:240]))

    def _async(self, callback):
        def run():
            try:
                result = callback()
                if isinstance(result, dict):
                    message = result.get('error') or result.get('message')
                    if message:
                        self._message(message)
            except Exception as exc:
                self._message('Action unavailable. Open Studio for saved jobs or retry after the page loads.')
                try:
                    import app
                    app.v2_blueprint.store.event('host.browser_action', 'unavailable', counts={'errorClass': type(exc).__name__})
                except Exception:
                    pass
        threading.Thread(target=run, daemon=True).start()

    def navigate_history(self, action):
        if self.mode == 'explorer':
            return self.workspace.explorer_history(action)
        return getattr(self.workspace, action)()

    def navigate(self, value):
        if self.mode == 'explorer':
            return self.workspace.embedded_navigate(value)
        return self.workspace.navigate(self.workspace._explorer_target(value))

    def copy_link(self):
        from System.Windows.Forms import Clipboard
        value = self.window.get_current_url() or ''
        self.ui(lambda: Clipboard.SetText(value))
        return {'message': 'Page link copied. Paste it into Studio to choose how to save it.'}

    def pause_mining(self):
        state = self.workspace._source_control_state()
        job_id = state.get('activeJobId') or state.get('jobId')
        return self.workspace.pause_profile_clone(job_id) if job_id else {'message': 'No mining job is active.'}

    def open_menu(self):
        from System.Drawing import Point
        self.menu.Show(self.more_button, Point(0, self.more_button.Height))

    def open_find(self):
        from System.Windows.Forms import SendKeys
        opened = []
        def apply():
            # A menu click in this app owns focus. Never send keys to another app.
            if self.window.native.ContainsFocus and self.web.Enabled and self.web.Focus():
                SendKeys.Send('^f')
                opened.append(True)
        self.ui(apply)
        return {'message': 'Find on page (Ctrl+F).' if opened else 'Click inside the page and press Ctrl+F.'}

    def zoom(self, amount):
        values = []
        def apply():
            factor = 1.0 if amount == 0 else max(0.5, min(2.0, self.web.ZoomFactor + amount))
            self.web.ZoomFactor = factor
            values.append(round(factor * 100))
        self.ui(apply)
        return {'message': f'Page zoom: {values[0]}%'} if values else {'error': 'Page zoom unavailable.'}

    def choose_language(self, view):
        if view not in {'Original','Translated'}:
            return {'error':'Choose Original or Translated.'}
        self.show(view)
        if view=='Translated':
            if self.mode!='explorer':
                self._message('Use independent Browse for visible page translation.')
                return {'error':'Use independent Browse for visible page translation.'}
            if not self.busy:
                def start():
                    result=self.translate()
                    if result.get('error'):self._message(result['error'])
                self._async(start)
        return {'message':'Original text restored.' if view=='Original' else 'Visible page translation enabled.'}

    def _fit_panel(self):
        self.panel_open = False
        self.table.ColumnStyles[1].Width = 0

    def _fit_toolbar(self):
        from System.Windows.Forms import TextRenderer
        for column, minimum, control in self.toolbar_columns:
            required = TextRenderer.MeasureText(control.Text, control.Font).Width if control.Text else 0
            required += control.Padding.Horizontal + control.Margin.Horizontal + self.px(8)
            if getattr(control,'Image',None) is not None:
                required += control.Image.Width + self.px(2)
            self.navigation_row.ColumnStyles[column].Width = max(self.px(minimum), required)

    def refresh(self):
        try:
            address = self.window.get_current_url() or ''
            if self.last_url and address != self.last_url:
                self.epoch += 1
                self.auto_page=False
                self.page_cache.clear()
                self._cancel_owned_request()
                self.results = []
                self.show(self.view)
            self.last_url = address
            state = self.workspace._source_control_state()
            collecting = bool(state.get('busy'))
            busy = self.mode == 'miner' and collecting
            def apply():
                if not self.address.Focused:
                    self.address.Text = address
                for control in self.nav:
                    control.Enabled = not busy
                self.address.Enabled = not busy
                if self.mode == 'miner':
                    self.web.Enabled = not busy
                self.view_buttons['Translated'].Enabled = self.mode=='explorer' and not busy and not self.busy
                self.pause_item.Enabled = collecting
                self.mining_status.Text = 'Mining active' if collecting else ('Mining paused' if state.get('status') == 'paused' else 'Mining stopped')
                if state.get('cooldownRemainingSeconds', 0) > 0:
                    self.mining_status.Text = 'Website pause required'
                self._fit_panel()
                self._fit_toolbar()
            self.ui(apply)
        except Exception:
            pass

    def navigation_loaded(self):
        self.epoch += 1
        self.auto_page=False
        self.page_cache.clear()
        self._cancel_owned_request()
        self.results = []
        self.ui(lambda: self._show('Original'))
        self.refresh()

    def show(self, view):
        if view not in {'Original','Translated'}:raise ValueError('Choose Original or Translated.')
        if view=='Original' and self.auto_page:
            self.auto_page=False
            self.epoch+=1
            self._cancel_owned_request()
        if view=='Original':self.translation_phase='original'
        self.ui(lambda: self._show(view))
        if self.mode=='explorer':
            epoch=self.epoch
            def render_page():
                with self._page_render_lock:
                    if self.view!=view or self.epoch!=epoch:return
                    try:
                        from .page_translation import switch
                        self.window.evaluate_js(switch(view))
                        if view=='Original':self._message('Original text restored · automatic translation off.')
                        elif not self.auto_page and not self.busy:self._message('Saved translations shown · Translated enables visible page translation.')
                    except Exception:pass
            threading.Thread(target=render_page,name='rednote-page-language',daemon=True).start()

    def _show(self, view):
        self.view = view
        for title, button in self.view_buttons.items():
            button.BackColor = self.color['redButton' if title == view else 'panel']
            button.ForeColor = self.color['text' if title == view else 'muted']

    def cancel_translation(self):
        self.auto_page=False
        self.epoch += 1
        self._cancel_owned_request()
        self.translation_phase='stopped'
        return {'message': 'Page translation stopped. Use Original to restore Chinese; completed translations remain available.'}

    def _cancel_owned_request(self):
        try:
            import app
            client=app.v2_blueprint.codex_engine.client
            active=client.active
            if not active or active[2]!='browser-'+self.mode:return
        except Exception:return
        def interrupt():
            try:
                client.cancel(expected_owner='browser-'+self.mode,expected_active=active)
            except Exception:pass
        threading.Thread(target=interrupt,name='rednote-owned-interrupt',daemon=True).start()

    def _watch_page_translation(self):
        while not self._closed.wait(1.5):
            if not self.auto_page or self.busy or self.mode!='explorer':continue
            try:
                if self.window.get_current_url()!=self.auto_url:
                    self.auto_page=False;self.epoch+=1;self.page_cache.clear();continue
                import app
                if app.v2_blueprint.codex_engine.client.turn_lock.locked():
                    self.translation_phase='waiting_for_gpt';continue
                result=self.translate(automatic=True)
                if result.get('error'):
                    self.auto_page=False;self.translation_error=result['error'];self._message(result['error'])
            except Exception:
                self.auto_page=False;self.translation_error='Browse page is unavailable';self.translation_phase='failed'

    def translate(self,automatic=False):
        if self.mode != 'explorer':
            return {'error': 'Use the independent Browse window while mining is active.'}
        if not self._translation_lock.acquire(blocking=False):
            return {'error': 'Local translation is already running.'}
        self.busy = True
        self.translation_error=None
        self.translation_phase='inspecting'
        epoch = self.epoch
        import app
        if not automatic:app.v2_blueprint.store.event('browser.gpt_translation', 'started')
        self.ui(lambda: setattr(self.view_buttons['Translated'], 'Enabled', False))
        try:
            target = 'en'
            from .page_translation import snapshot
            page_snapshot=self.window.evaluate_js(snapshot(target,pending_only=automatic))
            if not isinstance(page_snapshot,dict) or page_snapshot.get('error'):return {'error':'Visible page text is unavailable.'}
            texts=[entry['text'] for entry in page_snapshot['entries']]
            if not automatic:
                self.auto_page=True;self.auto_url=page_snapshot['url'];self.show('Translated')
            texts = [str(t) for t in texts if t]
            if not texts:
                self.translation_phase='watching' if self.auto_page else 'idle'
                if automatic:return {'message':'No new visible text.'}
                return {'message': 'Visible page translation is on; new visible text will follow as you scroll.'}
            if sum(map(len, texts)) > 6000:
                return {'error': 'Visible text exceeds this translation batch budget.'}
            if epoch != self.epoch:
                return {'message': 'Page changed; translation cancelled.'}
            if page_snapshot and not automatic:
                self.auto_page=True;self.auto_url=page_snapshot['url']
                if self.view=='Original':self.show('Translated')
            self.results = []
            self.show(self.view)
            self._message('Requesting GPT translation…')
            results = []
            for start in range(0,len(texts),12):
                if epoch != self.epoch:
                    return {'message': 'Page changed; translation cancelled.'}
                batch=texts[start:start+12]
                engine=app.v2_blueprint.codex_engine
                revision=(target,engine.settings['model'],engine.settings['rules'],engine.glossary_stamp())
                missing=[text for text in batch if revision+(text,) not in self.page_cache]
                self.translation_phase='translating' if missing else 'using_cache'
                result = self.workspace.translate_visible({'texts': missing, 'target': target,'mode':self.mode}) if missing else {'translations':[]}
                if result.get('error'):
                    self.auto_page=False;self.translation_error=result['error'];self.translation_phase='failed'
                    app.v2_blueprint.store.event('browser.gpt_translation', 'failed')
                    return result
                if epoch != self.epoch:
                    return {'message': 'Translation cancelled; the original page is unchanged.'}
                translations = result.get('translations', [])
                if len(translations)!=len(missing) or not all(isinstance(t,str) and t.strip() for t in translations):
                    self.auto_page=False;return {'error':'Incomplete translation result; originals retained.'}
                for text,translated in zip(missing,translations):self.page_cache[revision+(text,)]=translated
                translations=[self.page_cache[revision+(text,)] for text in batch]
                while len(self.page_cache)>256:self.page_cache.pop(next(iter(self.page_cache)))
                results.extend(zip(batch,translations))
                if page_snapshot:
                    from .page_translation import apply
                    entries=[dict(entry,translation=translated) for entry,translated in zip(page_snapshot['entries'][start:start+12],translations)]
                    with self._page_render_lock:
                        applied=self.window.evaluate_js(apply(page_snapshot['url'],entries,self.view,target))
                    if isinstance(applied,dict) and applied.get('stale'):return {'message':'Page changed; stale translations were discarded.'}
                self.results = list(results)
                self.show(self.view)
                self._message(f'GPT translation: {len(results)}/{len(texts)} sections')
            app.v2_blueprint.store.event('browser.gpt_translation', 'completed', counts={'segments': len(results)})
            self.translation_phase='watching' if self.auto_page else 'complete'
            return {'message': 'Visible page translation on · new visible text follows as you scroll. Original stops it.'}
        finally:
            self.busy = False
            self._translation_lock.release()
            self.refresh()

    def geometry(self):
        result = {'toolbarReady': self.ready, 'nativeChrome': True,
            'textSelectionEnabled': bool(self.window.text_select), 'easyDragEnabled': bool(self.window.easy_drag),
            'layout': 'compact-toolbar-visible-language-toggle'}
        def read():
            from System.Windows.Forms import TextRenderer
            core = self.web.CoreWebView2
            result.update(toolbarHeight=self.bar.Height, viewportTop=self.web.Top,
                viewportWidth=self.web.Width, viewportHeight=self.web.Height,
                toolbarWidth=self.bar.Width, addressWidth=self.address.Width,
                translationPanelOpen=False, translationPanelWidth=0, visiblePageOnly=True, languageViews=['Original','Translated'],
                viewportReserved=True, toolbarWraps=False,
                contextMenusEnabled=bool(core.Settings.AreDefaultContextMenusEnabled),
                browserAcceleratorKeysEnabled=bool(core.Settings.AreBrowserAcceleratorKeysEnabled),
                userDataFolder=str(core.Environment.UserDataFolder), profileName=str(core.Profile.ProfileName))
            metrics = []
            for control in self.toolbar_buttons + [self.brand_label]:
                text_size = TextRenderer.MeasureText(control.Text, control.Font)
                image = getattr(control, 'Image', None)
                required = text_size.Width if control.Text else 0
                required += control.Padding.Horizontal + (image.Width + self.px(2) if image is not None else 0)
                metrics.append({'control': control.AccessibleName or control.Text,
                    'width': control.ClientSize.Width, 'height': control.ClientSize.Height,
                    'requiredWidth': required, 'textClips': required > control.ClientSize.Width})
            result.update(dpiScale=self.scale, toolbarControlMetrics=metrics,
                toolbarLabelsFit=all(not metric['textClips'] for metric in metrics))
            # Render only app controls, never the source viewport. The address
            # field is masked in the bitmap so signed links cannot leak into evidence.
            if not self._snapshot_saved and self.bar.Width > 0 and self.bar.Height > 0:
                try:
                    from System.Drawing import Bitmap, Rectangle, Graphics, SolidBrush, PointF
                    from System.Drawing.Imaging import ImageFormat
                    import app
                    bitmap = Bitmap(self.bar.Width, self.bar.Height)
                    self.bar.DrawToBitmap(bitmap, Rectangle(0, 0, bitmap.Width, bitmap.Height))
                    graphics = Graphics.FromImage(bitmap)
                    brush = SolidBrush(self.color['input'])
                    text_brush = SolidBrush(self.color['muted'])
                    frame = self.address_frame
                    x, y = frame.Left + self.navigation_row.Left, frame.Top + self.navigation_row.Top
                    try:
                        graphics.FillRectangle(brush, Rectangle(x, y, frame.Width, frame.Height))
                        graphics.DrawString('RedNote address / search', self.small_font, text_brush, PointF(x + self.px(12), y + self.px(8)))
                        folder = app.BASE_DIR / 'development-exports'
                        folder.mkdir(exist_ok=True)
                        import time
                        target = folder / ('chrome-' + self.mode + '-' + str(time.time_ns()) + '.png')
                        bitmap.Save(str(target), ImageFormat.Png)
                        self._snapshot_path = str(target)
                        self._snapshot_saved = True
                    finally:
                        text_brush.Dispose(); brush.Dispose(); graphics.Dispose(); bitmap.Dispose()
                except Exception as exc:
                    result['chromePreviewErrorClass'] = type(exc).__name__
        self.ui(read)
        if self._snapshot_path:
            result['chromePreview'] = self._snapshot_path
        try:
            # Style metadata only; never return source bodies or selected text.
            style = self.window.evaluate_js("(() => ({bodyUserSelect:document.body?getComputedStyle(document.body).userSelect:null, selectionDisabledByHost:Array.from(document.querySelectorAll('style')).some(s=>s.textContent.includes('body')&&s.textContent.includes('user-select: none')&&s.textContent.includes('cursor: default'))}))()")
            if isinstance(style, dict):
                result.update(style)
        except Exception:
            result['selectionStyleAvailable'] = False
        return result


def install(workspace, window, mode):
    with _install_lock:
        chrome = getattr(window, '_rednote_chrome', None)
        if chrome is None:
            # The watchdog can arrive before WebView2 has initialized. Do not
            # partially replace native controls until the underlying core exists.
            native=window.native
            if native is None:return None
            readiness=[]
            from System import Action
            def probe():
                browser=getattr(native,'browser',None)
                web=getattr(browser,'webview',None)
                readiness.append(web is not None and getattr(web,'CoreWebView2',None) is not None)
            try:dispatch_native_ui(native,probe)
            except Exception:return None
            if not readiness or not readiness[0]:return None
            chrome = NativeBrowserChrome(workspace, window, mode)
            window._rednote_chrome = chrome
            window.events.loaded += chrome.navigation_loaded
    chrome.refresh()
    return chrome
