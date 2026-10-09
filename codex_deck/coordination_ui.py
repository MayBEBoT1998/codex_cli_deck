"""Collaboration relationships: drag one Agent onto another to connect them."""
from pathlib import Path
import uuid

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Pango

from .relationships import connect_drop, drag_payload, relation_edges, set_relationship


AGENT_STATUS = {"offline": "未连接", "connecting": "连接中", "idle": "空闲", "active": "执行中",
                "waiting": "等待输入", "notLoaded": "未加载", "systemError": "连接错误"}
DRAG_TARGET = "application/x-codex-deck-agent"


def styled(widget, *classes):
    for name in classes:
        widget.get_style_context().add_class(name)
    return widget


def text(value, css=None):
    widget = Gtk.Label(label=value, xalign=0)
    if css:
        styled(widget, css)
    return widget


def short_text(value, css=None, width=18):
    widget = text(value, css)
    widget.set_ellipsize(Pango.EllipsizeMode.END)
    widget.set_max_width_chars(width)
    widget.set_tooltip_text(value)
    return widget


def column(spacing=8):
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)


class CoordinationPanel(Gtk.Window):
    def __init__(self, deck):
        super().__init__(title="Agent 协同关系", transient_for=deck)
        self.deck = deck
        self.manager = deck.coordinator
        self.panel_id = uuid.uuid4().hex
        self.rebuilding = False
        self.dragging = False
        self.signature = None
        self.cards = {}
        self.relationship_rows = {}
        self.set_default_size(880, 580)
        self.set_size_request(740, 460)
        self.set_destroy_with_parent(True)
        styled(self, "collaboration-window")
        root = column(18)
        root.set_border_width(22)
        self.add(root)
        heading = Gtk.Box(spacing=12)
        titles = column(5)
        titles.pack_start(text("让 Agent 一起工作", "collab-title"), False, False, 0)
        titles.pack_start(text("把一个 Agent 拖到另一个上，建立有方向的协作关系。", "collab-description"), False, False, 0)
        heading.pack_start(titles, True, True, 0)
        self.count = styled(text(""), "collab-count")
        self.count.set_valign(Gtk.Align.START)
        heading.pack_end(self.count, False, False, 0)
        root.pack_start(heading, False, False, 0)
        body = Gtk.Box(spacing=20)
        roster = column(10)
        roster.set_size_request(245, -1)
        roster.pack_start(text("我的 Agent", "collab-section"), False, False, 0)
        self.agents_box = column(8)
        roster_scroll = Gtk.ScrolledWindow()
        roster_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        roster_scroll.add(self.agents_box)
        roster.pack_start(roster_scroll, True, True, 0)
        roster.pack_end(text("拖动卡片连接 · 点击选择发起者", "collab-caption"), False, False, 0)
        body.pack_start(roster, False, False, 0)
        relationships = column(10)
        label_row = Gtk.Box(spacing=12)
        label_row.pack_start(text("协同关系", "collab-section"), True, True, 0)
        label_row.pack_end(text("发起者  →  可调度的 Agent", "collab-caption"), False, False, 0)
        relationships.pack_start(label_row, False, False, 0)
        self.relations_box = column(10)
        self.empty = column(12)
        self.empty.set_valign(Gtk.Align.CENTER)
        self.empty.set_halign(Gtk.Align.CENTER)
        self.empty.pack_start(styled(Gtk.Label(label="↗"), "collab-empty-icon"), False, False, 0)
        self.empty.pack_start(Gtk.Label(label="还没有协同关系"), False, False, 0)
        self.empty.pack_start(styled(Gtk.Label(label="拖动连接，或使用下方选择器"), "collab-description"), False, False, 0)
        self.relation_stack = Gtk.Stack()
        self.relation_stack.add_named(self.empty, "empty")
        relation_scroll = Gtk.ScrolledWindow()
        relation_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        relation_scroll.add(self.relations_box)
        self.relation_stack.add_named(relation_scroll, "relations")
        styled(self.relation_stack, "collab-canvas")
        relationships.pack_start(self.relation_stack, True, True, 0)
        body.pack_start(relationships, True, True, 0)
        root.pack_start(body, True, True, 0)
        connect = styled(column(9), "collab-connect")
        connect.pack_start(text("也可以选择两个 Agent 建立关系", "collab-caption"), False, False, 0)
        controls = Gtk.Box(spacing=10)
        self.source = Gtk.ComboBoxText()
        self.source.set_hexpand(True)
        self.source.set_tooltip_text("发起者：可以指挥其他 Agent")
        self.source.connect("changed", self.source_changed)
        self.target = Gtk.ComboBoxText()
        self.target.set_hexpand(True)
        self.target.set_tooltip_text("协作对象：接收发起者派发的工作")
        self.target.connect("changed", lambda *_: self.update_connect_button())
        self.connect_button = styled(Gtk.Button(label="建立协作"), "primary")
        self.connect_button.connect("clicked", self.connect_selected)
        controls.pack_start(self.source, True, True, 0)
        controls.pack_start(text("→", "collab-arrow"), False, False, 0)
        controls.pack_start(self.target, True, True, 0)
        controls.pack_start(self.connect_button, False, False, 0)
        connect.pack_start(controls, False, False, 0)
        root.pack_start(connect, False, False, 0)
        self.feedback = text("连接后，在发起者终端描述工作；结果自动回传。", "collab-feedback")
        self.feedback.set_line_wrap(True)
        root.pack_start(self.feedback, False, False, 0)
        footer = text("解除关系会撤销后续派发，进行中的工作继续。重启或切换会话后，需要重新连接。", "collab-caption")
        footer.set_line_wrap(True)
        root.pack_start(footer, False, False, 0)
        self.refresh()
        self.source.set_active_id(deck.active_id or "")
        self.show_all()
        self.timer = GLib.timeout_add(300, self.refresh)
        self.connect("destroy", self.destroyed)

    def destroyed(self, *_):
        GLib.source_remove(self.timer)
        self.deck.coordination_panel = None

    def avatar(self, identity, size=36):
        session = self.deck.get_session(identity)
        return Gtk.Image.new_from_pixbuf(self.deck.avatar_image(session.avatar, size)) if session else Gtk.Image()

    def agent_name(self, identity):
        return self.manager.agents[identity].name

    def source_changed(self, *_):
        if not self.rebuilding:
            self.build_targets()
            self.highlight_source()

    def build_targets(self):
        saved = self.target.get_active_id()
        self.target.remove_all()
        source = self.source.get_active_id()
        for agent in self.manager.agents.values():
            if agent.id != source:
                self.target.append(agent.id, agent.name)
        self.target.set_active_id(saved or "")
        if self.target.get_active() < 0:
            self.target.set_active(0)
        self.update_connect_button()

    def update_connect_button(self):
        source, target = self.source.get_active_id(), self.target.get_active_id()
        connected = target in self.manager.grants.get(source, set())
        self.connect_button.set_sensitive(bool(source and target and not connected))
        self.connect_button.set_label("已连接" if connected else "建立协作")

    def highlight_source(self):
        selected = self.source.get_active_id()
        for identity, card in self.cards.items():
            style = card.get_style_context()
            (style.add_class if identity == selected else style.remove_class)("selected")
        for (source, _), row in self.relationship_rows.items():
            style = row.get_style_context()
            (style.add_class if source == selected else style.remove_class)("selected")

    def connect_selected(self, *_):
        self.change_relationship(self.source.get_active_id(), self.target.get_active_id(), True)

    def change_relationship(self, source, target, enabled):
        try:
            changed = set_relationship(self.manager, source, target, enabled)
            action = "已连接" if enabled else "已解除"
            if enabled and not changed:
                action = "关系已存在"
            self.feedback.set_text(f"{action}：{self.agent_name(source)} → {self.agent_name(target)}")
            self.refresh()
            return True
        except ValueError as error:
            self.feedback.set_text(str(error))
            return False

    def drag_begin(self, widget, context, identity):
        self.dragging = True
        self.drag_data = drag_payload(self.manager, identity, self.panel_id)
        self.source.set_active_id(identity)
        session = self.deck.get_session(identity)
        if session:
            Gtk.drag_set_icon_pixbuf(context, self.deck.avatar_image(session.avatar, 48), 24, 24)

    def drag_get(self, widget, context, selection, info, time, identity):
        if self.dragging:
            selection.set(selection.get_target(), 8, self.drag_data)

    def drag_end(self, *_):
        self.dragging = False
        for card in self.cards.values():
            card.get_style_context().remove_class("drop-target")
        self.refresh()

    def drag_motion(self, widget, context, x, y, time):
        source = Gtk.drag_get_source_widget(context)
        if source is widget or source not in self.cards.values():
            Gdk.drag_status(context, Gdk.DragAction(0), time)
            return False
        widget.get_style_context().add_class("drop-target")
        Gdk.drag_status(context, Gdk.DragAction.LINK, time)
        return True

    def drag_drop(self, widget, context, x, y, time):
        source = Gtk.drag_get_source_widget(context)
        if source is widget or source not in self.cards.values():
            return False
        widget.drag_get_data(context, Gdk.Atom.intern_static_string(DRAG_TARGET), time)
        return True

    def receive_drop(self, payload, target):
        try:
            source = connect_drop(self.manager, payload, target, self.panel_id)
            self.source.set_active_id(source)
            self.feedback.set_text(f"已连接：{self.agent_name(source)} → {self.agent_name(target)}")
            self.refresh()
            return True
        except ValueError as error:
            self.feedback.set_text(str(error))
            return False

    def drag_received(self, widget, context, x, y, selection, info, time, target):
        success = self.receive_drop(selection.get_data(), target)
        widget.get_style_context().remove_class("drop-target")
        Gtk.drag_finish(context, success, False, time)

    def make_agent(self, agent):
        card = styled(Gtk.Button(), "collab-agent")
        card.set_relief(Gtk.ReliefStyle.NONE)
        card.connect("clicked", lambda *_: self.source.set_active_id(agent.id))
        card.set_tooltip_text(f"{agent.name}\n{agent.cwd}\n拖到另一个 Agent 建立协作关系")
        content = Gtk.Box(spacing=10)
        content.pack_start(self.avatar(agent.id, 44), False, False, 0)
        details = column(5)
        details.pack_start(short_text(agent.name, "collab-agent-name", 17), False, False, 0)
        status = AGENT_STATUS.get(agent.state, agent.state)
        ready = " · 协作就绪" if agent.tools_ready else ""
        details.pack_start(text("● " + status + ready, "collab-agent-status"), False, False, 0)
        details.pack_start(short_text(Path(agent.cwd).name or agent.cwd, "collab-caption", 20), False, False, 0)
        content.pack_start(details, True, True, 0)
        content.pack_end(text("⠿", "collab-grip"), False, False, 0)
        card.add(content)
        targets = [Gtk.TargetEntry.new(DRAG_TARGET, Gtk.TargetFlags.SAME_APP, 0)]
        card.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, targets, Gdk.DragAction.LINK)
        card.drag_dest_set(Gtk.DestDefaults.MOTION | Gtk.DestDefaults.HIGHLIGHT, targets, Gdk.DragAction.LINK)
        card.connect("drag-begin", self.drag_begin, agent.id)
        card.connect("drag-data-get", self.drag_get, agent.id)
        card.connect("drag-end", self.drag_end)
        card.connect("drag-motion", self.drag_motion)
        card.connect("drag-drop", self.drag_drop)
        card.connect("drag-leave", lambda widget, *_: widget.get_style_context().remove_class("drop-target"))
        card.connect("drag-data-received", self.drag_received, agent.id)
        return card

    def endpoint(self, identity):
        widget = Gtk.Button()
        widget.set_relief(Gtk.ReliefStyle.NONE)
        widget.set_hexpand(True)
        styled(widget, "collab-endpoint")
        agent = self.manager.agents[identity]
        content = Gtk.Box(spacing=9)
        content.pack_start(self.avatar(identity, 32), False, False, 0)
        details = column(3)
        details.pack_start(short_text(agent.name, "collab-agent-name", 15), False, False, 0)
        details.pack_start(text(AGENT_STATUS.get(agent.state, agent.state), "collab-caption"), False, False, 0)
        content.pack_start(details, True, True, 0)
        widget.add(content)
        widget.set_tooltip_text("打开终端：" + agent.name)
        widget.connect("clicked", lambda *_: self.open_agent(identity))
        return widget

    def open_agent(self, identity):
        if identity in self.manager.agents:
            self.deck.activate(identity)
            self.deck.present()

    def refresh(self):
        if self.manager.closed:
            return False
        if self.dragging:
            return True
        edges = relation_edges(self.manager)
        signature = (tuple((a.id, a.name, a.cwd, a.state, a.tools_ready,
                            getattr(self.deck.get_session(a.id), "avatar", None)) for a in self.manager.agents.values()),
                     tuple(edges))
        if signature == self.signature:
            return True
        self.signature = signature
        selected = self.source.get_active_id()
        self.rebuilding = True
        self.source.remove_all()
        for child in self.agents_box.get_children():
            child.destroy()
        self.cards.clear()
        for agent in self.manager.agents.values():
            self.source.append(agent.id, agent.name)
            card = self.make_agent(agent)
            self.cards[agent.id] = card
            self.agents_box.pack_start(card, False, False, 0)
        self.source.set_active_id(selected or self.deck.active_id or "")
        if self.source.get_active() < 0:
            self.source.set_active(0)
        self.rebuilding = False
        self.build_targets()
        self.count.set_text(f"{len(self.manager.agents)} 个 Agent · {len(edges)} 条关系")
        for child in self.relations_box.get_children():
            child.destroy()
        self.relationship_rows.clear()
        for source, target in edges:
            row = styled(Gtk.Box(spacing=5), "collab-relation")
            row.pack_start(self.endpoint(source), True, True, 0)
            row.pack_start(text("→", "collab-arrow"), False, False, 0)
            row.pack_start(self.endpoint(target), True, True, 0)
            remove = styled(Gtk.Button(label="×"), "collab-remove")
            remove.set_tooltip_text("解除：" + self.agent_name(source) + " → " + self.agent_name(target))
            remove.connect("clicked", lambda _, a=source, b=target: self.change_relationship(a, b, False))
            row.pack_end(remove, False, False, 0)
            self.relations_box.pack_start(row, False, False, 0)
            self.relationship_rows[source, target] = row
        self.relation_stack.set_visible_child_name("relations" if edges else "empty")
        self.agents_box.show_all()
        self.relations_box.show_all()
        self.highlight_source()
        return True
