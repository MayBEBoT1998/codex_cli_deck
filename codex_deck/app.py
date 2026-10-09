"""Native GTK/VTE multi-terminal workspace. No web server or model API proxy."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile
import time
from urllib.parse import unquote, urlparse

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango, Vte

from .core import (DEFAULT_AVATAR, ROOT, Session, Settings, drain_events,
                   prepare_session, reap_stubborn, stop_processes)
from .appearance import THEMES, ease_out, layer_geometry, paint_layers, scroll_target
from .avatars import AvatarLibrary, SUPPORTED_IMAGES
from .pet_layer import PetLayer
from .pets import build_routes
from .pet_sprites import build_sprite_routes
from .coordination import Coordinator


def styled(widget, *classes):
    for name in classes:
        widget.get_style_context().add_class(name)
    return widget


def label(text, css=None, xalign=0):
    widget = Gtk.Label(label=text, xalign=xalign)
    if css:
        styled(widget, css)
    return widget


def box(vertical=False, spacing=0, margin=0):
    widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL,
                     spacing=spacing)
    if margin:
        widget.set_border_width(margin)
    return widget


def button(text, callback, css=None, tooltip=None):
    widget = Gtk.Button(label=text)
    if css:
        styled(widget, css)
    widget.connect("clicked", lambda *_: callback())
    if tooltip:
        widget.set_tooltip_text(tooltip)
    return widget


def rgba(value):
    color = Gdk.RGBA()
    color.parse(value)
    return color


class Deck(Gtk.Window):
    def __init__(self, args):
        super().__init__(title="Codex Deck")
        self.args = args
        self.runtime = Path(tempfile.mkdtemp(prefix="codex-deck-"))
        state_dir = self.runtime / "state" if args.smoke_test else Path(args.state_dir)
        self.settings = Settings(state_dir / "settings.json")
        self.avatar_library = AvatarLibrary(state_dir)
        self.codex = shutil.which("codex")
        self.coordinator = Coordinator(self.runtime, None if args.demo or args.smoke_test else state_dir / "coordination.json")
        self.coordination_panel = None
        self.sessions = []
        self.views = {}
        self.cards = {}
        self.pids = {}
        self.pixbufs = {}
        self.active_id = None
        self.directory_dialog = None
        self.opened_any = False
        self.pulse = False
        self.closing = False
        self.scroll_tick = None
        self.wheel_delta = 0
        self.last_wheel_switch = 0
        self.layer_count = -1
        self.event_log = []
        self.font_size = self.settings.values["font_size"]
        self.set_default_size(1180, 780)
        self.set_size_request(820, 520)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_icon_from_file(str(ROOT / "assets" / "icon.svg"))
        self.style_provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), self.style_provider,
                                                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.apply_theme()
        self.build()
        self.pet_layer = PetLayer(self.pet_layout, lambda: self.settings.values["theme"],
                                  self.settings.values["pet_count"], self.settings.values["pet_style"],
                                  self.settings.values["pet_character"])
        self.workspace_overlay.add_overlay(self.pet_layer)
        self.workspace_overlay.set_overlay_pass_through(self.pet_layer, True)
        self.pet_layer.set_enabled(self.settings.values["pets_enabled"])
        self.apply_theme()
        self.connect("delete-event", self.on_delete)
        self.connect("key-press-event", self.on_key)
        self.connect("window-state-event", self.on_window_state)
        self.connect("destroy", lambda *_: Gtk.main_quit())
        self.show_all()
        self.refresh()
        GLib.idle_add(self.open_initial_sessions)
        GLib.timeout_add(160, self.poll_events)
        GLib.timeout_add(650, self.animate)
        if args.smoke_test:
            from .smoke import SmokeCheck
            self.smoke = SmokeCheck(self, args.smoke_test)
            GLib.timeout_add(500, self.smoke.start)
        elif args.demo:
            GLib.timeout_add(500, self.setup_demo)

    def open_initial_sessions(self):
        if self.closing:
            return False
        if self.args.restore and not (self.args.cwd or self.args.smoke_test or self.args.demo):
            for metadata in self.settings.values["sessions"][:24]:
                if not isinstance(metadata, dict):
                    continue
                cwd = metadata.get("cwd")
                # A missing project must never silently start Codex in another directory.
                if isinstance(cwd, str) and Path(cwd).is_dir():
                    self.new_session(name=str(metadata.get("name", "终端"))[:60], cwd=cwd,
                                     avatar=metadata.get("avatar"), activate=False, persist=False)
        if self.sessions:
            self.activate(self.sessions[0].id)
        elif self.args.cwd or self.args.smoke_test or self.args.demo:
            self.new_session(cwd=self.default_cwd(), persist=False)
        else:
            self.choose_directory()
        return False

    def default_cwd(self):
        value = self.args.cwd or self.settings.values["default_cwd"]
        path = Path(value).expanduser()
        return str(path.resolve()) if path.is_dir() else str(ROOT)

    def avatar_image(self, avatar, size=46):
        key = (avatar, size)
        if key not in self.pixbufs:
            path = self.avatar_library.get(avatar).path
            try:
                self.pixbufs[key] = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), size, size, True)
            except GLib.Error:
                fallback = self.avatar_library.get(DEFAULT_AVATAR).path
                self.pixbufs[key] = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(fallback), size, size, True)
        return self.pixbufs[key]

    def apply_terminal_theme(self, terminal):
        colors = THEMES[self.settings.values["theme"]]
        terminal.set_colors(rgba(colors["foreground"]), rgba(colors["background"]),
                            [rgba(c) for c in colors["palette"]])
        terminal.set_color_cursor(rgba(colors["cursor"]))
        terminal.set_color_cursor_foreground(rgba(colors["background"]))
        terminal.set_color_highlight(rgba(colors["selection"]))
        terminal.set_color_highlight_foreground(rgba(colors["foreground"]))

    def apply_theme(self):
        night = self.settings.values["theme"] == "night"
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", night)
        css = (ROOT / "assets" / "style.css").read_text()
        if not night:
            css += "\n" + (ROOT / "assets" / "day.css").read_text()
        self.style_provider.load_from_data(css.encode())
        for terminal in self.views.values():
            self.apply_terminal_theme(terminal)
        if hasattr(self, "theme_button"):
            self.theme_button.set_label("☀  日间" if night else "☾  夜晚")
            self.theme_button.set_tooltip_text("切换到日间模式" if night else "切换到夜晚模式")
            self.layers.queue_draw()
        if hasattr(self, "pet_layer"):
            self.pet_layer.queue_draw()

    def toggle_theme(self):
        self.set_preference("theme", "day" if self.settings.values["theme"] == "night" else "night")

    def motion_enabled(self):
        return self.settings.values["motion"] and Gtk.Settings.get_default().get_property("gtk-enable-animations")

    def build(self):
        header = Gtk.HeaderBar(title="Codex Deck", subtitle="你的多终端工作台")
        header.set_show_close_button(True)
        self.set_titlebar(header)
        header.pack_start(styled(label("▱", "brand-icon")))
        self.settings_button = button("⚙  设置", self.preferences, tooltip="外观、切换动画与终端偏好")
        header.pack_end(self.settings_button)
        self.theme_button = button("☀  日间", self.toggle_theme)
        header.pack_end(self.theme_button)
        header.pack_end(button("⇄  Agent 协作", self.open_coordination, tooltip="拖动 Agent 卡片建立协同关系"))
        root = box()
        self.workspace_overlay = Gtk.Overlay()
        self.workspace_overlay.add(root)
        self.add(self.workspace_overlay)
        sidebar = styled(box(vertical=True), "sidebar")
        self.sidebar = sidebar
        sidebar.set_hexpand(False)
        sidebar.set_size_request(264, -1)
        root.pack_start(sidebar, False, True, 0)
        branding = box(vertical=True, spacing=6, margin=22)
        branding.pack_start(label("codex deck", "brand"), False, False, 0)
        branding.pack_start(label("A LITTLE COMPANY FOR YOUR CODE", "eyebrow"), False, False, 0)
        sidebar.pack_start(branding, False, False, 0)
        newbox = box(margin=14)
        self.add_button = button("＋  新建终端", self.choose_directory, "primary", "选择工作目录并新建终端 · Ctrl+Shift+T")
        styled(self.add_button, "new-session")
        self.add_button.set_hexpand(True)
        newbox.pack_start(self.add_button, True, True, 0)
        sidebar.pack_start(newbox, False, False, 0)
        section = box(margin=20)
        section.pack_start(label("我的终端", "section-label"), True, True, 0)
        self.count_label = label("01", "muted")
        section.pack_end(self.count_label, False, False, 0)
        sidebar.pack_start(section, False, False, 0)
        scroll = Gtk.ScrolledWindow()
        self.card_scroll = scroll
        scroll.connect("scroll-event", self.cancel_sidebar_scroll)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.card_list = box(vertical=True)
        scroll.add(self.card_list)
        sidebar.pack_start(scroll, True, True, 0)
        foot = styled(box(vertical=True, spacing=10), "sidebar-bottom")
        self.sidebar_footer = foot
        hint = label("一起写代码，也一起等好消息。", "muted")
        hint.set_margin_start(8)
        foot.pack_start(hint, False, False, 0)
        footrow = box()
        footrow.pack_start(button("⚙  偏好设置", self.preferences), True, True, 0)
        footrow.pack_end(button("?", self.help_dialog, tooltip="快捷键与使用说明"), False, False, 0)
        foot.pack_start(footrow, False, False, 0)
        sidebar.pack_end(foot, False, False, 0)
        main = box(vertical=True, margin=26)
        main.set_hexpand(True)
        root.pack_start(main, True, True, 0)
        heading = box(spacing=12)
        intro = box(vertical=True, spacing=8)
        intro.pack_start(label("WORKSPACE  /  本地工作区", "eyebrow"), False, False, 0)
        self.heading_label = label("终端工作台", "workspace-heading")
        intro.pack_start(self.heading_label, False, False, 0)
        heading.pack_start(intro, True, True, 0)
        self.unread_button = button("没有待查看的任务", self.next_unread, tooltip="跳转到完成的终端")
        heading.pack_end(self.unread_button, False, False, 0)
        main.pack_start(heading, False, False, 0)
        self.path_label = label("", "subtle")
        self.path_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.path_label.set_margin_top(12)
        self.path_label.set_margin_bottom(14)
        main.pack_start(self.path_label, False, False, 0)
        self.notice = button("", self.next_unread, "notice")
        self.notice.set_no_show_all(True)
        self.notice.set_margin_bottom(10)
        main.pack_start(self.notice, False, False, 0)
        self.layers = Gtk.DrawingArea()
        self.layers.set_no_show_all(True)
        self.layers.connect("draw", self.draw_layers)
        self.layers.add_events(Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)
        self.layers.connect("scroll-event", self.switch_scroll)
        main.pack_start(self.layers, False, False, 0)
        panel = styled(box(vertical=True), "terminal-panel")
        self.terminal_panel = panel
        main.pack_start(panel, True, True, 0)
        toolbar = styled(box(spacing=8), "terminal-toolbar")
        self.header_avatar = Gtk.Image()
        toolbar.pack_start(self.header_avatar, False, False, 0)
        self.terminal_title = label("", "terminal-title")
        self.terminal_title.set_ellipsize(Pango.EllipsizeMode.END)
        toolbar.pack_start(self.terminal_title, True, True, 0)
        self.mode_label = styled(label("BASH", "terminal-mode"), "terminal-mode-box")
        toolbar.pack_start(self.mode_label, False, False, 0)
        self.codex_button = button("启动 Codex  ↗", self.launch_codex, tooltip="在当前空闲 shell 中启动 Codex")
        toolbar.pack_start(self.codex_button, False, False, 0)
        toolbar.pack_start(button("⋯", self.active_menu, tooltip="重命名、角色、工作目录与关闭"), False, False, 0)
        toolbar_events = Gtk.EventBox()
        toolbar_events.set_visible_window(False)
        toolbar_events.add(toolbar)
        toolbar_events.add_events(Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)
        toolbar_events.connect("scroll-event", self.switch_scroll)
        toolbar_events.set_tooltip_text("在标题栏滚动可切换终端")
        panel.pack_start(toolbar_events, False, False, 0)
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_UP_DOWN)
        self.stack.set_transition_duration(260 if self.motion_enabled() else 0)
        self.stack.set_hexpand(True)
        self.stack.set_vexpand(True)
        self.empty = box(vertical=True, spacing=18)
        self.empty.set_valign(Gtk.Align.CENTER)
        self.empty.set_halign(Gtk.Align.CENTER)
        self.empty.pack_start(label("▱", "empty-art", .5), False, False, 0)
        self.empty.pack_start(label("给下一个想法，一个终端。", "empty-title", .5), False, False, 0)
        self.empty.pack_start(label("先选择一个项目目录，再开始工作。", "subtle", .5), False, False, 0)
        self.empty.pack_start(button("＋  选择目录并新建", self.choose_directory, "primary"), False, False, 0)
        self.stack.add_named(self.empty, "empty")
        panel.pack_start(self.stack, True, True, 0)
        panel_footer = styled(box(), "terminal-bottom")
        self.state_label = label("●  本地会话", "muted")
        self.dim_label = label("UTF-8", "muted")
        panel_footer.pack_start(self.state_label, True, True, 0)
        panel_footer.pack_end(self.dim_label, False, False, 0)
        panel.pack_end(panel_footer, False, False, 0)
        footer = box()
        footer.set_margin_top(17)
        footer.pack_start(label("●  独立终端 · 后台持续运行", "muted"), True, True, 0)
        footer.pack_end(label("新建  Ctrl ⇧ T     切换  Ctrl ↑ / ↓", "muted"), False, False, 0)
        main.pack_end(footer, False, False, 0)

    def pet_layout(self):
        def bounds(widget, text_only=False):
            if not widget.get_visible():
                return None
            point = widget.translate_coordinates(self.workspace_overlay, 0, 0)
            if point is None:
                return None
            # PyGObject versions may expose GTK's success flag before the coords.
            if len(point) == 3:
                if not point[0]:
                    return None
                _, x, y = point
            else:
                x, y = point
            allocation = widget.get_allocation()
            width = allocation.width
            if text_only and isinstance(widget, Gtk.Label):
                width = min(width, widget.get_layout().get_pixel_size()[0] + 8)
            return x, y, width, allocation.height
        panel, sidebar, scroll, foot = (bounds(widget) for widget in
                                        (self.terminal_panel, self.sidebar, self.card_scroll, self.sidebar_footer))
        if any(rect is None for rect in (panel, sidebar, scroll, foot)):
            return [], [], []
        cards = [rect for card, *_ in self.cards.values() if (rect := bounds(card)) is not None]
        last_card_y = max((y + h for _, y, _, h in cards), default=scroll[1])
        play_sidebar = (sidebar[0], scroll[1], sidebar[2], max(0, foot[1] - scroll[1]))
        protected = [bounds(widget, True) for widget in
                     (self.stack, self.path_label, self.heading_label, self.unread_button,
                      self.notice, self.sidebar_footer)]
        blocked = cards + [rect for rect in protected if rect is not None]
        width, height = self.workspace_overlay.get_allocated_width(), self.workspace_overlay.get_allocated_height()
        if self.pet_layer.uses_sprites:
            routes, sizes = build_sprite_routes(width, height, panel, play_sidebar, last_card_y + 10,
                                                 blocked, self.settings.values["pet_height"],
                                                 self.pet_layer.sprites.aspect_ratio)
            return routes, blocked, sizes
        routes = build_routes(width, height, panel, play_sidebar, last_card_y + 10)
        return routes, blocked, []

    def draw_layers(self, widget, cr):
        paint_layers(cr, len(self.sessions), widget.get_allocated_width(), self.settings.values["theme"])
        return False

    def update_layers(self):
        count = len(self.sessions)
        if count != self.layer_count:
            self.layer_count = count
            height, _ = layer_geometry(count, self.layers.get_allocated_width())
            self.layers.set_size_request(-1, height)
            self.layers.set_visible(count > 1)
            self.layers.set_tooltip_text(f"{count} 个终端 · 滚动切换")
            self.layers.queue_draw()

    def switch_scroll(self, _widget, event):
        if len(self.sessions) < 2:
            return False
        if event.direction == Gdk.ScrollDirection.SMOOTH:
            valid, dx, dy = event.get_scroll_deltas()
            if not valid:
                return False
            delta = dy if abs(dy) >= abs(dx) else dx
            if self.wheel_delta * delta < 0:
                self.wheel_delta = 0
            self.wheel_delta += delta
            if abs(self.wheel_delta) < 1:
                return True
            direction = 1 if self.wheel_delta > 0 else -1
        elif event.direction in (Gdk.ScrollDirection.DOWN, Gdk.ScrollDirection.RIGHT):
            direction = 1
        elif event.direction in (Gdk.ScrollDirection.UP, Gdk.ScrollDirection.LEFT):
            direction = -1
        else:
            return False
        self.wheel_delta = 0
        now = time.monotonic()
        if now - self.last_wheel_switch >= .22:
            self.last_wheel_switch = now
            index = next((i for i, session in enumerate(self.sessions) if session.id == self.active_id), 0)
            self.activate(self.sessions[(index + direction) % len(self.sessions)].id, direction)
        return True

    def cancel_sidebar_scroll(self, *_):
        if self.scroll_tick is not None:
            self.card_scroll.remove_tick_callback(self.scroll_tick)
            self.scroll_tick = None
        return False

    def reveal_card(self, session_id):
        self.cancel_sidebar_scroll()
        started = None
        start_value = 0
        def tick(_widget, clock, _data):
            nonlocal started, start_value
            if self.closing or session_id not in self.cards:
                self.scroll_tick = None
                return False
            adjustment = self.card_scroll.get_vadjustment()
            card = self.cards[session_id][0]
            allocation = card.get_allocation()
            if allocation.height <= 1 or adjustment.get_page_size() <= 1:
                return True  # A newly added card needs its first layout allocation.
            now = clock.get_frame_time()
            if started is None:
                started, start_value = now, adjustment.get_value()
            target = scroll_target(allocation.y, allocation.y + allocation.height, start_value,
                                   adjustment.get_page_size(), adjustment.get_upper())
            progress = (now - started) / 220000 if self.motion_enabled() else 1
            adjustment.set_value(start_value + (target - start_value) * ease_out(progress))
            if progress >= 1 or abs(target - start_value) < .5:
                self.scroll_tick = None
                return False
            return True
        self.scroll_tick = self.card_scroll.add_tick_callback(tick, None)

    def new_session(self, name=None, cwd=None, avatar=None, activate=True, persist=True):
        if len(self.sessions) >= 24:
            self.message("终端已满", "最多同时打开 24 个终端。关闭不用的终端后再新建。")
            return None
        self.avatar_library.reload()
        characters = self.avatar_library.for_pack(self.settings.values["avatar_pack"])
        character = characters[len(self.sessions) % len(characters)]
        avatar = avatar if isinstance(avatar, str) and avatar in self.avatar_library.items else character.id
        names = {s.name for s in self.sessions}
        index = len(self.sessions) + 1
        while f"终端 {index:02}" in names:
            index += 1
        current = self.get_session()
        session = Session(name=name or f"终端 {index:02}", cwd=cwd or (current.cwd if current else self.default_cwd()), avatar=avatar)
        self.sessions.append(session)
        self.opened_any = True
        terminal = Vte.Terminal()
        terminal.set_size(80, 24)
        terminal.set_font(Pango.FontDescription(f"Monospace {self.font_size}"))
        terminal.set_scrollback_lines(30000)
        terminal.set_scroll_on_keystroke(True)
        terminal.set_scroll_on_output(False)
        terminal.set_mouse_autohide(True)
        terminal.set_audible_bell(False)
        terminal.set_allow_hyperlink(True)
        terminal.set_cursor_blink_mode(Vte.CursorBlinkMode.ON)
        self.apply_terminal_theme(terminal)
        terminal.set_margin_start(16)
        terminal.set_margin_end(8)
        terminal.set_margin_top(14)
        terminal.set_margin_bottom(12)
        terminal.connect("child-exited", self.child_exited, session.id)
        terminal.connect("button-press-event", self.terminal_button, session.id)
        terminal.connect("notify::current-directory-uri", self.cwd_changed, session.id)
        terminal.connect("size-allocate", lambda *_: self.update_dimensions())
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.add(terminal)
        self.stack.add_named(scroller, session.id)
        self.views[session.id] = terminal
        self.make_card(session)
        endpoint = self.coordinator.register(session, self.runtime / session.id)
        auto = self.settings.values["auto_codex"] and not (self.args.shell or self.args.demo or self.args.smoke_test)
        argv = prepare_session(self.runtime / session.id, session, self.codex, auto,
                               clean_shell=bool(self.args.smoke_test or self.args.demo),
                               coordination=None if self.args.smoke_test or self.args.demo else endpoint)
        env = dict(os.environ)
        # A new terminal is a peer, not a child tool of the session hosting this app.
        for key in ("CODEX_THREAD_ID", "CODEX_TURN_ID"):
            env.pop(key, None)
        env.update(TERM="xterm-256color", COLORTERM="truecolor")
        terminal.spawn_async(Vte.PtyFlags.DEFAULT, session.cwd, argv,
                             [f"{k}={v}" for k, v in env.items()], GLib.SpawnFlags.DEFAULT,
                             None, None, -1, None, self.spawned, session.id)
        scroller.show_all()
        if activate:
            self.activate(session.id)
        if persist:
            self.save()
        self.refresh()
        return session

    def spawned(self, terminal, pid, error, session_id):
        session = self.get_session(session_id)
        if not session:
            if pid > 0:
                victims = stop_processes(pid)
                GLib.timeout_add(400, lambda: reap_stubborn(victims))
            return
        if error:
            session.error = str(error)
            terminal.feed(f"\r\n无法启动终端：{error}\r\n".encode())
        else:
            self.pids[session_id] = pid
        self.refresh()

    def make_card(self, session):
        card = styled(Gtk.Button(), "session-card")
        card.set_relief(Gtk.ReliefStyle.NONE)
        content = box(spacing=3)
        avatar_holder = box()
        avatar_holder.set_size_request(70, 64)
        image = Gtk.Image.new_from_pixbuf(self.avatar_image(session.avatar))
        image.set_hexpand(True)
        image.set_halign(Gtk.Align.CENTER)
        avatar_holder.pack_start(image, True, True, 0)
        content.pack_start(avatar_holder, False, False, 0)
        texts = box(vertical=True, spacing=4)
        texts.set_valign(Gtk.Align.CENTER)
        title = label(session.name, "session-name")
        title.set_max_width_chars(13)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        state = label(session.status, "session-status")
        directory = label(session.cwd, "session-status")
        directory.set_max_width_chars(16)
        directory.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        texts.pack_start(title, False, False, 0)
        texts.pack_start(directory, False, False, 0)
        texts.pack_start(state, False, False, 0)
        content.pack_start(texts, True, True, 0)
        index = label(str(len(self.sessions)), "number")
        index.set_margin_end(6)
        content.pack_end(index, False, False, 0)
        card.add(content)
        card.connect("clicked", lambda *_: self.activate(session.id))
        card.connect("button-press-event", self.card_button, session.id)
        self.cards[session.id] = (card, image, title, state, index, directory)
        self.card_list.pack_start(card, False, False, 0)
        card.show_all()

    def get_session(self, session_id=None):
        return next((s for s in self.sessions if s.id == (session_id or self.active_id)), None)

    def activate(self, session_id, direction=None):
        session = self.get_session(session_id)
        if not session:
            return
        previous = self.get_session()
        if previous and previous.id != session.id:
            forward = direction > 0 if direction is not None else self.sessions.index(session) > self.sessions.index(previous)
            transition = Gtk.StackTransitionType.SLIDE_UP if forward else Gtk.StackTransitionType.SLIDE_DOWN
        else:
            transition = Gtk.StackTransitionType.NONE
        self.stack.set_transition_duration(260 if self.motion_enabled() else 0)
        self.stack.set_transition_type(transition)
        self.active_id = session.id
        session.unread = False
        self.stack.set_visible_child_name(session.id)
        self.views[session.id].grab_focus()
        self.refresh()
        self.reveal_card(session.id)

    def refresh(self):
        if self.closing:
            return
        self.update_layers()
        unread = [s for s in self.sessions if s.unread]
        self.count_label.set_text(f"{len(self.sessions):02}")
        self.unread_button.set_label(f"✦  {len(unread)} 个任务待查看" if unread else "✓  暂无待查看任务")
        self.unread_button.set_sensitive(bool(unread))
        self.notice.set_visible(bool(unread))
        if unread:
            self.notice.set_label(f"✦  {unread[0].name} 完成了本轮工作  ·  点击查看 →")
            self.notice.set_tooltip_text(unread[0].summary[:600])
        self.set_title(f"{'● ' if unread else ''}Codex Deck · {len(self.sessions)} 个终端")
        for index, session in enumerate(self.sessions, 1):
            card, image, title, state, number, directory = self.cards[session.id]
            context = card.get_style_context()
            for flag, enabled in (("active", session.id == self.active_id), ("unread", session.unread),
                                  ("pulse-high", session.unread and self.pulse and self.settings.values["animate"])):
                (context.add_class if enabled else context.remove_class)(flag)
            size = 58 if session.unread else 46
            if session.unread and self.pulse and self.settings.values["animate"]:
                size = 64
            image.set_from_pixbuf(self.avatar_image(session.avatar, size))
            title.set_text(session.name)
            directory.set_text(session.cwd)
            state.set_text(session.status)
            number.set_text("✦" if session.unread else str(index))
            card.set_tooltip_text(f"{session.name}\n{session.cwd}\n{session.summary[:240] if session.unread else session.status}\n右键管理 · Alt+数字切换")
        session = self.get_session()
        for widget in (self.codex_button,):
            widget.set_sensitive(bool(session and not session.exited and not session.codex_running and not session.error and self.codex))
        if session:
            self.heading_label.set_text(session.name)
            self.terminal_title.set_text(session.name)
            self.path_label.set_text("⌁  " + session.cwd)
            self.path_label.set_tooltip_text(session.cwd)
            self.header_avatar.set_from_pixbuf(self.avatar_image(session.avatar, 30))
            self.mode_label.set_text("CODEX" if session.codex_running else "BASH")
            self.state_label.set_text("●  " + session.status)
        else:
            self.stack.set_visible_child_name("empty")
            self.heading_label.set_text("终端工作台")
            self.terminal_title.set_text("准备开始")
            self.path_label.set_text("⌁  " + self.default_cwd())
            self.header_avatar.clear()
            self.state_label.set_text("没有运行中的终端")
        self.update_dimensions()

    def update_dimensions(self):
        term = self.views.get(self.active_id)
        if term:
            self.dim_label.set_text(f"{term.get_column_count()} × {term.get_row_count()}    UTF-8")

    def poll_events(self):
        if self.closing:
            return False
        changed = False
        for session in self.sessions:
            agent = self.coordinator.agents.get(session.id)
            if agent and (agent.name, agent.cwd) != (session.name, session.cwd):
                agent.name, agent.cwd = session.name, session.cwd
                self.coordinator.revision += 1
            pid = self.pids.get(session.id)
            if pid:
                try:
                    cwd = os.readlink(f"/proc/{pid}/cwd")
                    if cwd != session.cwd and Path(cwd).is_dir():
                        session.cwd = cwd
                        changed = True
                except OSError:
                    pass
            for event in drain_events(self.runtime / session.id / "events"):
                if event.get("epoch") and event.get("type") != "agent-starting" and agent and event["epoch"] != agent.epoch:
                    continue
                self.coordinator.handle_event(session.id, event)
                completed = session.apply(event)
                changed = True
                if completed:
                    self.event_log.append((session.id, session.summary))
                    self.event_log = self.event_log[-100:]
                    self.set_urgency_hint(True)
                    if self.settings.values["sound"]:
                        self.get_display().beep()
        if changed:
            self.refresh()
        self.coordinator.tick()
        return True

    def animate(self):
        if self.closing:
            return False
        if any(s.unread for s in self.sessions):
            self.pulse = not self.pulse
            self.refresh()
        else:
            self.set_urgency_hint(False)
        return True

    def next_unread(self):
        session = next((s for s in self.sessions if s.unread), None)
        if session:
            self.activate(session.id)

    def child_exited(self, terminal, status, session_id):
        session = self.get_session(session_id)
        self.pids.pop(session_id, None)
        if session:
            session.exited = True
            session.codex_running = False
            terminal.feed(f"\r\n\x1b[38;5;245m终端已退出（状态 {os.waitstatus_to_exitcode(status)}）。点击 + 新建终端。\x1b[0m\r\n".encode())
        self.refresh()

    def cwd_changed(self, terminal, _param, session_id):
        session = self.get_session(session_id)
        uri = terminal.get_current_directory_uri()
        if session and uri:
            path = unquote(urlparse(uri).path)
            if Path(path).is_dir():
                session.cwd = path
                self.refresh()

    def launch_codex(self):
        session = self.get_session()
        if not session or session.codex_running or session.exited:
            return
        # Never inject a command into vim, ssh, or a running foreground process.
        term = self.views[session.id]
        pty = term.get_pty()
        try:
            foreground = os.tcgetpgrp(pty.get_fd())
            shell_group = os.getpgid(self.pids[session.id])
        except (OSError, KeyError, AttributeError):
            return
        if foreground != shell_group:
            self.message("终端正在运行程序", "请先回到 shell 提示符，再启动 Codex。")
            return
        # Clear a partially typed line before sending the explicit launch command.
        term.feed_child(b"\x01\x0bcodex\r")
        term.grab_focus()

    def card_button(self, _widget, event, session_id):
        if event.button == 3:
            self.session_menu(session_id, event)
            return True
        return False

    def terminal_button(self, terminal, event, session_id):
        if event.button != 3:
            return False
        menu = Gtk.Menu()
        actions = [("复制  Ctrl+Shift+C", lambda: terminal.copy_clipboard_format(Vte.Format.TEXT)),
                   ("粘贴  Ctrl+Shift+V", terminal.paste_clipboard),
                   ("全选", terminal.select_all),
                   ("清屏", lambda: terminal.reset(True, True)),
                   ("终端设置…", lambda: self.session_menu(session_id, None))]
        for text, callback in actions:
            item = Gtk.MenuItem(label=text)
            item.connect("activate", lambda _, action=callback: action())
            menu.append(item)
        menu.show_all()
        menu.popup_at_pointer(event)
        return True

    def active_menu(self):
        if self.active_id:
            self.session_menu(self.active_id, None)

    def session_menu(self, session_id, event):
        menu = Gtk.Menu()
        for text, callback in [("重命名…", lambda: self.rename(session_id)),
                               ("管理此 Agent 的协同关系…", lambda: self.open_coordination(session_id)),
                               ("更换角色…", lambda: self.choose_avatar(session_id)),
                               ("在指定目录新建…", self.choose_directory),
                               ("关闭终端", lambda: self.close_session(session_id))]:
            item = Gtk.MenuItem(label=text)
            item.connect("activate", lambda _, action=callback: action())
            menu.append(item)
        menu.show_all()
        if event:
            menu.popup_at_pointer(event)
        else:
            menu.popup_at_widget(self.codex_button, Gdk.Gravity.SOUTH_EAST, Gdk.Gravity.NORTH_EAST, None)

    def open_coordination(self, source=None):
        if self.coordination_panel is None:
            from .coordination_ui import CoordinationPanel
            self.coordination_panel = CoordinationPanel(self)
        if source:
            self.coordination_panel.source.set_active_id(source)
        self.coordination_panel.present()

    def rename(self, session_id):
        session = self.get_session(session_id)
        if not session:
            return
        dialog = Gtk.Dialog(title="重命名终端", transient_for=self, modal=True)
        dialog.add_buttons("取消", Gtk.ResponseType.CANCEL, "保存", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        content = dialog.get_content_area()
        content.set_border_width(20)
        entry = Gtk.Entry(text=session.name)
        entry.set_max_length(40)
        entry.set_activates_default(True)
        content.add(entry)
        dialog.show_all()
        entry.select_region(0, -1)
        if dialog.run() == Gtk.ResponseType.OK and entry.get_text().strip():
            session.name = entry.get_text().strip()
            self.save()
            self.refresh()
        dialog.destroy()

    def choose_avatar(self, session_id):
        session = self.get_session(session_id)
        if not session:
            return
        dialog = Gtk.Dialog(title="选择你的终端搭档", transient_for=self, modal=True)
        dialog.add_button("关闭", Gtk.ResponseType.CLOSE)
        dialog.set_default_size(550, 480)
        self.avatar_library.reload()
        content = dialog.get_content_area()
        content.set_border_width(16)
        pack = Gtk.ComboBoxText()
        for key, title in self.avatar_library.packs.items():
            pack.append(key, title)
        pack.set_active_id(self.avatar_library.get(session.avatar).pack)
        content.pack_start(pack, False, False, 8)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        content.pack_start(scroll, True, True, 0)
        grid = Gtk.Grid(column_spacing=8, row_spacing=8, margin=12)
        scroll.add(grid)
        def populate(*_):
            for child in grid.get_children():
                child.destroy()
            for index, avatar in enumerate(self.avatar_library.for_pack(pack.get_active_id())):
                choice = Gtk.Button()
                tile = box(vertical=True, spacing=8, margin=4)
                tile.pack_start(Gtk.Image.new_from_pixbuf(self.avatar_image(avatar.id, 80)), False, False, 0)
                name = label(avatar.name, "session-name", .5)
                name.set_max_width_chars(10)
                name.set_ellipsize(Pango.EllipsizeMode.END)
                tile.pack_start(name, False, False, 0)
                choice.add(tile)
                choice.set_tooltip_text(avatar.name)
                def select(_button, avatar_id=avatar.id):
                    session.avatar = avatar_id
                    self.save()
                    self.refresh()
                    dialog.response(Gtk.ResponseType.CLOSE)
                choice.connect("clicked", select)
                grid.attach(choice, index % 4, index // 4, 1, 1)
            grid.show_all()
        pack.connect("changed", populate)
        populate()
        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def import_avatar_pack(self, parent, folders=False):
        title = "导入头像文件夹" if folders else "导入头像集（可多选图片）"
        chooser = Gtk.FileChooserDialog(title=title, transient_for=parent, modal=True,
                                       action=Gtk.FileChooserAction.SELECT_FOLDER if folders else Gtk.FileChooserAction.OPEN)
        styled(chooser, "directory-chooser")
        chooser.add_buttons("取消", Gtk.ResponseType.CANCEL, "导入头像", Gtk.ResponseType.OK)
        styled(chooser.get_widget_for_response(Gtk.ResponseType.OK), "primary")
        chooser.set_local_only(True)
        if not folders:
            chooser.set_select_multiple(True)
            image_filter = Gtk.FileFilter()
            image_filter.set_name("图片（PNG、JPEG、GIF、BMP、SVG）")
            image_filter.add_pixbuf_formats()
            chooser.add_filter(image_filter)
        try:
            if chooser.run() != Gtk.ResponseType.OK:
                return
            if folders:
                folder = Path(chooser.get_filename())
                paths = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_IMAGES)
                name = folder.name
            else:
                paths = [Path(p) for p in chooser.get_filenames()]
                name = paths[0].parent.name if paths else "我的头像"
        except OSError as error:
            self.message("读取头像失败", str(error))
            return
        finally:
            chooser.destroy()
        try:
            group, count, errors = self.avatar_library.import_files(paths, name)
        except (OSError, ValueError) as error:
            self.message("导入头像失败", str(error))
            return
        self.pixbufs.clear()
        self.set_preference("avatar_pack", group)
        text = f"已导入 {count} 张头像，新终端将轮流使用。\n现有终端可右键标签 → 更换角色。"
        if errors:
            text += f"\n有 {len(errors)} 张未导入：\n" + "\n".join(errors[:4])
        self.message("头像集已保存", text)

    def choose_directory(self):
        if self.closing:
            return
        if self.directory_dialog:
            self.directory_dialog.present()
            return
        if len(self.sessions) >= 24:
            self.message("终端已满", "最多同时打开 24 个终端。关闭不用的终端后再新建。")
            return
        dialog = Gtk.FileChooserDialog(title="选择新终端的工作目录", transient_for=self,
                                       action=Gtk.FileChooserAction.SELECT_FOLDER, modal=True)
        styled(dialog, "directory-chooser")
        self.directory_dialog = dialog
        dialog.add_buttons("取消", Gtk.ResponseType.CANCEL, "在此目录启动", Gtk.ResponseType.OK)
        styled(dialog.get_widget_for_response(Gtk.ResponseType.OK), "primary")
        dialog.set_local_only(True)
        dialog.set_default_size(780, 540)
        dialog.set_current_folder(self.default_cwd())
        try:
            response = dialog.run()
            selected = dialog.get_filename() if response == Gtk.ResponseType.OK else None
        finally:
            self.directory_dialog = None
            dialog.destroy()
        if not selected or self.closing:
            return
        path = Path(selected).expanduser().resolve()
        if not path.is_dir():
            self.message("目录不可用", "所选目录已不存在，请重新选择工作目录。")
            return
        self.settings.values["default_cwd"] = str(path)
        name = base_name = path.name or str(path)
        existing = {s.name for s in self.sessions}
        index = 2
        while name in existing:
            name = f"{base_name} · {index}"
            index += 1
        return self.new_session(cwd=str(path), name=name)

    def preferences(self):
        dialog = Gtk.Dialog(title="偏好设置", transient_for=self, modal=True)
        dialog.add_button("完成", Gtk.ResponseType.OK)
        dialog.set_default_size(530, 650)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        dialog.get_content_area().pack_start(scroller, True, True, 0)
        content = box(vertical=True)
        scroller.add(content)
        content.set_border_width(24)
        content.set_spacing(10)
        content.add(label("让工作台更像你", "settings-title"))
        theme_row = styled(box(spacing=24), "setting-row")
        theme_row.pack_start(label("外观模式"), True, True, 0)
        theme = Gtk.ComboBoxText()
        theme.append("day", "☀  日间模式")
        theme.append("night", "☾  夜晚模式")
        theme.set_active_id(self.settings.values["theme"])
        theme.connect("changed", lambda widget: self.set_preference("theme", widget.get_active_id()))
        theme_row.pack_end(theme, False, False, 0)
        content.add(theme_row)
        packs_row = styled(box(vertical=True, spacing=10), "setting-row")
        packs_row.add(label("新终端使用的头像集"))
        packs = Gtk.ComboBoxText()
        def reload_packs():
            self.avatar_library.reload()
            packs.remove_all()
            for key, title in self.avatar_library.packs.items():
                packs.append(key, title)
            wanted = self.settings.values["avatar_pack"]
            packs.set_active_id(wanted if wanted in self.avatar_library.packs else "portraits")
        reload_packs()
        packs.connect("changed", lambda widget: self.set_preference("avatar_pack", widget.get_active_id()) if widget.get_active_id() else None)
        packs_row.add(packs)
        imports = box(spacing=8)
        def import_pack(folders=False):
            self.import_avatar_pack(dialog, folders)
            reload_packs()
        imports.pack_start(button("＋  导入图片…", import_pack), True, True, 0)
        imports.pack_start(button("导入文件夹…", lambda: import_pack(True)), True, True, 0)
        packs_row.add(imports)
        import_hint = label("图片会保存在本地，移走原图也可使用。", "muted")
        import_hint.set_line_wrap(True)
        packs_row.add(import_hint)
        content.add(packs_row)
        for key, title, detail in [("auto_codex", "新终端自动启动 Codex", "使用本机已安装的 Codex 与登录状态"),
                                    ("pets_enabled", "桌面宠物", "角色在空白区散步、眨眼、攀爬，不遮挡输入"),
                                    ("motion", "平滑切换动画", "终端上下滑动，侧栏自动滚动到当前标签"),
                                    ("animate", "头像放大与闪烁", "关闭动画后，仍保留放大头像与完成标记"),
                                    ("sound", "任务完成提示音", "收到真实完成事件时播放系统提示音")]:
            row = styled(box(spacing=24), "setting-row")
            texts = box(vertical=True, spacing=4)
            texts.add(label(title))
            texts.add(label(detail, "muted"))
            toggle = Gtk.Switch(active=self.settings.values[key])
            toggle.set_valign(Gtk.Align.CENTER)
            toggle.connect("notify::active", lambda switch, _, setting=key: self.set_preference(setting, switch.get_active()))
            row.pack_start(texts, True, True, 0)
            row.pack_end(toggle, False, False, 0)
            content.add(row)
        pet_row = styled(box(spacing=20), "setting-row")
        pet_row.pack_start(label("宠物数量"), True, True, 0)
        pet_count = Gtk.SpinButton.new_with_range(1, 5, 1)
        pet_count.set_value(self.settings.values["pet_count"])
        pet_count.connect("value-changed", lambda widget: self.set_preference("pet_count", widget.get_value_as_int()))
        pet_row.pack_end(pet_count, False, False, 0)
        content.add(pet_row)
        for setting, title, options in (
            ("pet_style", "宠物风格", [("anime", "二次元女伴"), ("cats", "小猫")]),
            ("pet_character", "女伴角色", [("all", "混合三位角色")] +
             [(c.id, c.name) for c in self.pet_layer.sprites.characters]),
        ):
            row = styled(box(spacing=20), "setting-row")
            row.pack_start(label(title), True, True, 0)
            choice = Gtk.ComboBoxText()
            for key, text in options:
                choice.append(key, text)
            selected = self.settings.values[setting]
            choice.set_active_id(selected if selected in dict(options) else options[0][0])
            choice.connect("changed", lambda widget, key=setting: self.set_preference(key, widget.get_active_id()) if widget.get_active_id() else None)
            row.pack_end(choice, False, False, 0)
            content.add(row)
        height_row = styled(box(spacing=20), "setting-row")
        height_row.pack_start(label("女伴大小（空白不足时自动缩小）"), True, True, 0)
        height_spin = Gtk.SpinButton.new_with_range(96, 192, 8)
        height_spin.set_value(self.settings.values["pet_height"])
        height_spin.connect("value-changed", lambda widget: self.set_preference("pet_height", widget.get_value_as_int()))
        height_row.pack_end(height_spin, False, False, 0)
        content.add(height_row)
        fontrow = styled(box(spacing=20), "setting-row")
        fontrow.pack_start(label("终端字号"), True, True, 0)
        spin = Gtk.SpinButton.new_with_range(9, 24, 1)
        spin.set_value(self.font_size)
        spin.connect("value-changed", lambda widget: self.set_font(widget.get_value_as_int()))
        fontrow.pack_end(spin, False, False, 0)
        content.add(fontrow)
        content.add(label("每次启动先选目录；使用 --restore 可恢复上次终端列表。", "muted"))
        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def set_preference(self, key, value):
        self.settings.values[key] = value
        if key == "theme":
            self.apply_theme()
        elif key == "pets_enabled":
            self.pet_layer.set_enabled(value)
        elif key == "pet_count":
            self.pet_layer.set_count(value)
        elif key == "pet_style":
            self.pet_layer.set_style(value)
        elif key == "pet_character":
            self.pet_layer.character = value
            self.pet_layer.queue_draw()
        elif key == "pet_height":
            self.pet_layer.queue_draw()
        elif key == "motion":
            self.stack.set_transition_duration(260 if self.motion_enabled() else 0)
            if not value:
                self.cancel_sidebar_scroll()
        self.save()
        self.refresh()

    def set_font(self, size):
        self.font_size = max(9, min(24, size))
        self.settings.values["font_size"] = self.font_size
        for terminal in self.views.values():
            terminal.set_font(Pango.FontDescription(f"Monospace {self.font_size}"))
        self.save()

    def help_dialog(self):
        self.message("Codex Deck · 使用说明",
                     "＋ / Ctrl+Shift+T    选择目录并新建终端\nCtrl+↑ / Ctrl+↓    上一个 / 下一个终端（循环切换）\nAlt+1…9    切换到对应终端\nCtrl+Tab / Ctrl+Shift+Tab    前后切换\nCtrl+Shift+C / V    复制 / 粘贴\nCtrl+Shift+W    关闭当前终端\nCtrl+加号 / 减号    调整字号\n\n"
                     "右键角色标签可重命名或更换头像。\n完成后角色会放大闪烁；点击该标签即可确认。\n\n"
                     "本地 codex 命令自动接入完成提醒。\nSSH 远端 Codex 不会自动接入本地通知。\n关闭页面不会保留进程；切换标签会继续运行。".replace("关闭页面", "关闭应用"))

    def message(self, title, text):
        dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.INFO,
                                   buttons=Gtk.ButtonsType.OK, text=title)
        dialog.format_secondary_text(text)
        dialog.run()
        dialog.destroy()

    def confirm(self, title, text):
        dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                                   buttons=Gtk.ButtonsType.NONE, text=title)
        dialog.format_secondary_text(text)
        dialog.add_buttons("取消", Gtk.ResponseType.CANCEL, "结束并关闭", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        result = dialog.run() == Gtk.ResponseType.OK
        dialog.destroy()
        return result

    def close_session(self, session_id, force=False):
        session = self.get_session(session_id)
        if not session:
            return
        if session_id in self.pids and not force and not self.confirm(f"关闭“{session.name}”？", "此终端内运行的 shell、Codex 和其他程序都会结束。"):
            return
        index = self.sessions.index(session)
        self.coordinator.remove(session_id)
        victims = stop_processes(self.pids.pop(session_id, None))
        GLib.timeout_add(600, lambda: reap_stubborn(victims))
        self.sessions.remove(session)
        card = self.cards.pop(session_id)[0]
        card.destroy()
        terminal = self.views.pop(session_id)
        self.stack.get_child_by_name(session_id).destroy()
        shutil.rmtree(self.runtime / session_id, ignore_errors=True)
        if self.active_id == session_id:
            self.active_id = None
            if self.sessions:
                self.activate(self.sessions[min(index, len(self.sessions) - 1)].id)
        self.save()
        self.refresh()

    def save(self):
        if self.args.smoke_test or self.args.demo:
            return
        try:
            # Changing appearance on the empty welcome page must still persist,
            # without deleting the previously saved terminal list.
            sessions = self.sessions if self.opened_any else [
                Session(str(s.get("name", "终端")), str(s.get("cwd", self.default_cwd())), str(s.get("avatar", DEFAULT_AVATAR)))
                for s in self.settings.values["sessions"] if isinstance(s, dict)]
            self.settings.save(sessions)
        except OSError as error:
            print(f"无法保存工作台设置：{error}", file=sys.stderr)

    def on_key(self, _widget, event):
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(event.state & Gdk.ModifierType.SHIFT_MASK)
        alt = bool(event.state & Gdk.ModifierType.MOD1_MASK)
        system_modifier = bool(event.state & (Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.HYPER_MASK
                                             | Gdk.ModifierType.META_MASK | Gdk.ModifierType.MOD4_MASK))
        key = Gdk.keyval_name(event.keyval) or ""
        terminal = self.views.get(self.active_id)
        if ctrl and shift and key.lower() == "t":
            self.choose_directory()
        elif ctrl and shift and key.lower() == "w":
            if self.active_id:
                self.close_session(self.active_id)
        elif ctrl and shift and key.lower() in ("c", "v") and terminal:
            terminal.copy_clipboard_format(Vte.Format.TEXT) if key.lower() == "c" else terminal.paste_clipboard()
        elif (ctrl and not shift and not alt and not system_modifier
              and key in ("Up", "Down", "KP_Up", "KP_Down") and self.sessions):
            index = next((i for i, s in enumerate(self.sessions) if s.id == self.active_id), 0)
            direction = -1 if key in ("Up", "KP_Up") else 1
            self.activate(self.sessions[(index + direction) % len(self.sessions)].id, direction)
        elif ctrl and key in ("Tab", "ISO_Left_Tab") and self.sessions:
            index = next((i for i, s in enumerate(self.sessions) if s.id == self.active_id), 0)
            direction = -1 if shift else 1
            self.activate(self.sessions[(index + direction) % len(self.sessions)].id, direction)
        elif alt and key in "123456789" and len(key) == 1 and int(key) <= len(self.sessions):
            self.activate(self.sessions[int(key) - 1].id)
        elif ctrl and key in ("plus", "equal", "KP_Add", "minus", "KP_Subtract"):
            self.set_font(self.font_size + (-1 if key in ("minus", "KP_Subtract") else 1))
        else:
            return False
        return True

    def on_delete(self, *_):
        if self.pids and not self.confirm("关闭 Codex Deck？", f"{len(self.pids)} 个终端中的进程将结束。终端名称、头像和目录会保存。"):
            return True
        self.shutdown()
        return True

    def on_window_state(self, _widget, event):
        if event.new_window_state & (Gdk.WindowState.ICONIFIED | Gdk.WindowState.WITHDRAWN):
            self.pet_layer.stop()
        elif not self.closing:
            self.pet_layer.start()
        return False

    def shutdown(self):
        if self.closing:
            return
        self.save()
        self.closing = True
        self.coordinator.close()
        self.cancel_sidebar_scroll()
        self.pet_layer.stop()
        victims = []
        for pid in self.pids.values():
            victims.extend(stop_processes(pid))
        self.pids.clear()
        def finish():
            reap_stubborn(victims)
            shutil.rmtree(self.runtime, ignore_errors=True)
            self.destroy()
            return False
        GLib.timeout_add(650, finish)

    def setup_demo(self):
        self.get_session().name = "前端 · 界面打磨"
        for name in ("后端 · 接口联调", "测试 · 回归验证", "灵感 · 下一步"):
            self.new_session(name=name, activate=False, persist=False)
        self.heading_label.set_text("前端 · 界面打磨")
        self.views[self.sessions[0].id].feed("\r\n  \x1b[38;5;115mCODEX DECK\x1b[0m   /   外观演示\r\n\r\n  每个想法，一个终端。每次完成，一个回应。\r\n\r\n  左侧 + 新建  ·  点击角色切换  ·  右键管理\r\n\r\n".encode())
        self.sessions[1].apply({"type": "agent-turn-complete", "turn-id": "demo", "last-assistant-message": "这是外观演示提醒，不是真实 Codex 任务。"})
        self.refresh()
        return False


def main():
    GLib.set_prgname("codex-deck")
    GLib.set_application_name("Codex Deck")
    parser = argparse.ArgumentParser(description="Codex Deck — 左侧角色标签、多终端与 Codex 完成提醒")
    parser.add_argument("--cwd", help="直接在指定目录打开首个终端；省略则先选择目录")
    parser.add_argument("--restore", action="store_true", help="在各自目录重新打开上次的终端列表")
    parser.add_argument("--shell", action="store_true", help="新终端仅打开 shell，不自动启动 Codex")
    parser.add_argument("--demo", action="store_true", help="用真实 shell 展示四个角色及标注的演示提醒")
    parser.add_argument("--state-dir", default=str(ROOT / ".state"), help="设置保存目录")
    parser.add_argument("--smoke-test", metavar="OUTPUT_DIR", help="运行隔离的 GUI 集成测试并保存截图/结果")
    args = parser.parse_args()
    if args.cwd and not Path(args.cwd).expanduser().is_dir():
        parser.error("--cwd 必须是存在的目录")
    if not Gtk.init_check()[0]:
        print("无法连接桌面显示。请在 Ubuntu 图形桌面或 WSLg 中运行 codex-deck（源码版用 bash launch.sh）。", file=sys.stderr)
        sys.exit(1)
    window = Deck(args)
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, lambda *_: GLib.idle_add(window.shutdown))
    Gtk.main()
    if args.smoke_test:
        sys.exit(0 if getattr(window.smoke, "passed", False) else 1)
