"""Exercise the real GTK widgets, VTE PTYs and notify callback without model calls."""
import json
import os
from pathlib import Path
import subprocess
import shlex
import time

from gi.repository import Gdk, GLib, Gtk
from .core import ROOT
from .appearance import layer_geometry
from .screenshot import save_widget_png


class SmokeCheck:
    def __init__(self, app, output):
        self.app = app
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.checks = []
        self.passed = False
        self.deadline = time.monotonic() + 35
        self.stage = 0
        self.background_marker = app.runtime / "background-ready"
        self.selected_directory = app.runtime / "另一个 project"
        self.selected_directory.mkdir()

    def check(self, name, condition):
        self.checks.append({"name": name, "passed": bool(condition)})
        if not condition:
            raise AssertionError(name)

    def text(self, session):
        return self.app.views[session.id].get_text(None, None)[0] or ""

    def compact_text(self, session):
        # VTE inserts physical row breaks when a hidden terminal has a small grid.
        return "".join(self.text(session).split())

    def start(self):
        try:
            self.one = self.app.sessions[0]
            self.check("one_terminal_has_no_background_layers", not self.app.layers.get_visible())
            self.one.name = "前端 · 界面工作台"
            # Choose a different project through the actual + button's folder dialog.
            selection_started = time.monotonic()
            requested = False
            def select_directory():
                nonlocal requested
                dialog = self.app.directory_dialog
                if time.monotonic() - selection_started > 5:
                    if dialog:
                        dialog.response(Gtk.ResponseType.CANCEL)
                    return False
                if not dialog:
                    return True
                if not requested:
                    dialog.set_current_folder(str(self.selected_directory))
                    requested = True
                if dialog.get_filename() == str(self.selected_directory):
                    dialog.response(Gtk.ResponseType.OK)
                    return False
                return True
            GLib.timeout_add(100, select_directory)
            self.app.add_button.clicked()
            self.check("plus_selects_directory_before_creating_terminal", len(self.app.sessions) == 2)
            self.two = self.app.sessions[1]
            self.two.name = "后端 · 完成提醒"
            self.three = self.app.new_session(name="测试 · 独立会话", activate=False)
            self.four = self.app.new_session(name="灵感 · 随时开始", activate=False)
            self.app.activate(self.one.id)
            GLib.timeout_add(160, self.step)
        except Exception as error:
            self.finish(error)
        return False

    def screenshot(self, name):
        save_widget_png(self.app, self.output / name)

    def step(self):
        try:
            if time.monotonic() > self.deadline:
                raise TimeoutError(f"Smoke test stage {self.stage} timed out")
            if self.stage == 0:
                if len(self.app.pids) < 4:
                    return True
                self.check("plus_creates_real_independent_ptys", len(set(self.app.pids.values())) == 4)
                self.check("pets_do_not_capture_pointer_or_keyboard",
                           self.app.workspace_overlay.get_overlay_pass_through(self.app.pet_layer)
                           and not self.app.pet_layer.get_can_focus())
                self.app.pet_layer.update_layout()
                self.pet_positions = [(x, y) for _, x, y, _ in self.app.pet_layer.scene.poses()]
                self.check("pets_have_routes_for_window_and_sidebar", len(self.pet_positions) == 3)
                self.check("anime_pets_load_three_character_packs", self.app.pet_layer.uses_sprites
                           and len(self.app.pet_layer.sprites.characters) == 3)
                self.check("anime_pets_have_readable_size", bool(self.app.pet_layer.route_heights)
                           and min(self.app.pet_layer.route_heights) >= 80)
                height, edges = layer_geometry(4, self.app.layers.get_allocated_width())
                self.check("four_terminals_have_four_layers_including_front",
                           len(edges) == 3 and self.app.layers.get_size_request()[1] == height)
                self.app.activate(self.two.id)
                self.check("forward_tab_switch_slides_up", self.app.stack.get_transition_type() == Gtk.StackTransitionType.SLIDE_UP)
                self.app.activate(self.one.id)
                self.check("backward_tab_switch_slides_down", self.app.stack.get_transition_type() == Gtk.StackTransitionType.SLIDE_DOWN)
                self.check("new_terminal_starts_in_selected_directory",
                           self.two.cwd == str(self.selected_directory)
                           and os.readlink(f"/proc/{self.app.pids[self.two.id]}/cwd") == str(self.selected_directory))
                self.check("new_directory_does_not_change_existing_terminal",
                           self.one.cwd != self.two.cwd
                           and os.readlink(f"/proc/{self.app.pids[self.one.id]}/cwd") == self.one.cwd)
                self.app.views[self.one.id].feed_child(b"export DECK_TEST=ONE; printf '\\nDECK_ONE:%s\\n' \"$DECK_TEST\"; stty size\r")
                self.app.views[self.two.id].feed_child("printf '\\nDECK_TWO:%s\\n' \"${DECK_TEST-unset}\"; printf '中文%s\\n' '输入正常'\r".encode())
                command = "sleep 0.6; printf '\\nBACKGROUND_%s\\n' FINISHED; : > " + shlex.quote(str(self.background_marker)) + "\r"
                self.app.views[self.three.id].feed_child(command.encode())
                self.stage = 1
            elif self.stage == 1:
                if not self.background_marker.exists() or "DECK_ONE:ONE" not in self.compact_text(self.one):
                    return True
                self.check("terminal_executes_shell_commands", "DECK_ONE:ONE" in self.compact_text(self.one))
                self.check("shell_environment_is_isolated", "DECK_TWO:unset" in self.compact_text(self.two))
                self.check("unicode_terminal_output", "中文输入正常" in self.compact_text(self.two))
                self.check("background_process_continues_after_tab_switch", self.background_marker.exists() and self.app.active_id == self.one.id)
                positions = [(x, y) for _, x, y, _ in self.app.pet_layer.scene.poses()]
                if Gtk.Settings.get_default().get_property("gtk-enable-animations"):
                    self.check("pets_move_while_terminal_works", positions != self.pet_positions)
                self.app.activate(self.three.id)
                self.stage = 11
            elif self.stage == 11:
                if "BACKGROUND_FINISHED" not in self.compact_text(self.three):
                    return True
                self.check("background_output_retained_when_tab_becomes_visible", True)
                self.app.activate(self.one.id)
                self.app.resize(1280, 880)
                self.stage = 2
            elif self.stage == 2:
                term = self.app.views[self.one.id]
                self.check("vte_resizes_with_window", term.get_column_count() > 70 and term.get_row_count() > 20)
                self.pids_before_theme = dict(self.app.pids)
                self.app.theme_button.clicked()
                self.stage = 21
            elif self.stage == 21:
                self.check("theme_button_switches_to_day", self.app.settings.values["theme"] == "day")
                self.check("day_theme_updates_existing_terminal", self.app.views[self.one.id].get_color_background_for_draw().red > .9)
                self.screenshot("codex-deck-day.png")
                self.app.theme_button.clicked()
                self.check("theme_switch_preserves_terminal_processes", self.app.pids == self.pids_before_theme)
                self.check("night_theme_restores_terminal_background", self.app.views[self.one.id].get_color_background_for_draw().red < .2)
                self.app.set_preference("motion", False)
                self.app.activate(self.two.id)
                self.check("switch_animation_can_be_disabled", self.app.stack.get_transition_duration() == 0)
                self.app.set_preference("motion", True)
                self.app.activate(self.one.id)
                group, count, errors = self.app.avatar_library.import_files(
                    [ROOT / "assets/avatars/portraits/portrait-01.png", ROOT / "assets/avatars/portraits/portrait-02.png"], "测试头像集")
                avatar = self.app.avatar_library.for_pack(group)[0]
                self.three.avatar = avatar.id
                self.app.refresh()
                self.check("imported_avatar_appears_in_sidebar", count == 2 and not errors and self.app.cards[self.three.id][1].get_pixbuf().get_width() == 46)
                callback = ["/usr/bin/python3", str(ROOT / "scripts" / "notify.py"), str(self.app.runtime / self.two.id / "events")]
                payload = {"type": "agent-turn-complete", "turn-id": "smoke-turn", "last-assistant-message": "通知链路测试完成。独立终端、中文输入和后台运行均已验证。"}
                subprocess.run(callback + [json.dumps(payload)], check=True)
                self.stage = 3
            elif self.stage == 3:
                if not self.two.unread:
                    return True
                self.check("notify_callback_targets_correct_terminal", self.two.unread and not self.one.unread and not self.three.unread)
                self.check("completion_does_not_steal_focus", self.app.active_id == self.one.id)
                self.check("unread_avatar_enlarges", self.app.cards[self.two.id][1].get_pixbuf().get_width() >= 58)
                self.app.pulse = False
                self.app.animate()
                high = self.app.cards[self.two.id][0].get_style_context().has_class("pulse-high")
                self.app.animate()
                low = self.app.cards[self.two.id][0].get_style_context().has_class("pulse-high")
                self.check("completion_avatar_flashes", high and not low)
                self.app.views[self.one.id].feed_child("clear; printf '\\n  \\033[38;5;115mCODEX DECK\\033[0m  /  终端集成测试\\n\\n  ✓ 独立 PTY 与中文输入\\n  ✓ 切换标签，任务继续运行\\n  ✓ Codex 官方格式完成回调\\n  ✓ 左侧头像放大、闪烁、点击确认\\n\\n  当前界面来自真实 GTK / VTE 终端。\\n  此次提醒为测试事件，未调用模型。\\n\\n'\r".encode())
                self.stage = 4
            elif self.stage == 4:
                if "此次提醒为测试事件，未调用模型。" not in self.text(self.one):
                    return True
                self.screenshot("codex-deck.png")
                self.app.cards[self.two.id][0].clicked()
                self.check("click_selects_and_acknowledges", self.app.active_id == self.two.id and not self.two.unread)
                self.check("acknowledged_avatar_returns_to_normal", self.app.cards[self.two.id][1].get_pixbuf().get_width() == 46)
                self.app.settings.values["animate"] = False
                self.two.apply({"type": "agent-turn-complete", "turn-id": "reduced-motion"})
                self.app.refresh()
                self.app.animate()
                self.check("reduced_motion_keeps_static_notification", self.two.unread and not self.app.cards[self.two.id][0].get_style_context().has_class("pulse-high"))
                self.app.settings.values["animate"] = True
                self.app.activate(self.one.id)
                self.closed_pid = self.app.pids[self.four.id]
                self.app.close_session(self.four.id, force=True)
                self.stage = 5
                self.close_at = time.monotonic()
            elif self.stage == 5:
                if time.monotonic() - self.close_at < 1:
                    return True
                self.check("closing_session_reaps_shell", not Path(f"/proc/{self.closed_pid}").exists())
                self.check("closing_session_preserves_other_sessions", len(self.app.pids) == 3 and self.app.active_id == self.one.id)
                self.check("closing_terminal_removes_a_stack_layer",
                           self.app.layers.get_size_request()[1] == layer_geometry(3, self.app.layers.get_allocated_width())[0])
                self.app.views[self.three.id].feed_child(b"exit\r")
                self.stage = 6
            elif self.stage == 6:
                if not self.three.exited:
                    return True
                self.check("shell_exit_updates_state", self.three.id not in self.app.pids and self.three.exited)
                self.app.set_preference("pets_enabled", False)
                self.check("disabling_pets_hides_overlay_and_stops_animation",
                           not self.app.pet_layer.get_visible() and self.app.pet_layer.tick_id is None)
                self.app.set_preference("pet_count", 5)
                self.app.set_preference("pets_enabled", True)
                self.check("pets_can_be_reenabled_with_selected_count", self.app.pet_layer.get_visible()
                           and len(self.app.pet_layer.scene.pets) == 5)
                self.app.set_preference("pet_character", "oneesan-silver")
                self.check("pet_character_can_be_selected", self.app.pet_layer.character == "oneesan-silver")
                self.app.set_preference("pet_style", "cats")
                self.check("classic_pet_style_still_available", not self.app.pet_layer.uses_sprites)
                self.app.set_preference("pet_style", "anime")
                self.check("anime_style_can_be_restored", self.app.pet_layer.uses_sprites)
                self.finish()
                return False
        except Exception as error:
            self.finish(error)
            return False
        return True

    def finish(self, error=None):
        self.passed = error is None
        result = {"passed": self.passed, "checks": self.checks, "error": str(error) if error else None,
                  "note": "Real GTK/VTE/PTY tests; completion payload injected through production notify callback. No model call."}
        if error:
            result["terminal_tails"] = {s.name: self.text(s)[-4000:] for s in self.app.sessions}
            result["terminal_sizes"] = {s.name: [self.app.views[s.id].get_column_count(), self.app.views[s.id].get_row_count()] for s in self.app.sessions}
        (self.output / "smoke-results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        if error:
            try:
                self.screenshot("failure.png")
            except Exception:
                pass
        self.app.shutdown()
