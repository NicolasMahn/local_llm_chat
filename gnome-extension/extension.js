import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import {Extension, InjectionManager} from 'resource:///org/gnome/shell/extensions/extension.js';

const APP_ID = 'io.github.nicolasmahn.LocalLlmChat';
const WIDTH = 420; // until the chat asks for another width
const SLIDE_MS = 200;
// The chat calls these on the shell's own bus name: Hide for its hide button, and
// both to step aside while you take a screenshot for it. Present tells it whether
// the drawer is there, as GNOME turns extensions off while the screen is locked.
const DBUS_PATH = '/io/github/nicolasmahn/LocalLlmChat';
const DBUS_INTERFACE = `<node>
  <interface name="io.github.nicolasmahn.LocalLlmChat.Drawer">
    <method name="Hide"/>
    <method name="Show"/>
    <method name="SetWidth">
      <arg type="u" name="width" direction="in"/>
    </method>
    <signal name="Present">
      <arg type="b" name="present"/>
    </signal>
  </interface>
</node>`;

// The chat as a drawer on the right edge of the primary monitor: it slides in
// over the other windows and out again, keeping its state while hidden.
class Drawer {
    constructor() {
        this._window = null;
        this._shown = false;
        this._width = WIDTH;
        global.display.connectObject(
            'window-created', (_display, window) => this._adopt(window),
            'notify::focus-window', () => this._onFocusChanged(),
            this);
        Main.layoutManager.connectObject('monitors-changed', () => this._place(), this);
        // A chat already open (the extension was off, say while the screen was locked)
        // stays hidden until you ask for it.
        for (const actor of global.get_window_actors())
            this._adopt(actor.meta_window, false);
    }

    Hide() {
        if (this._window && this._shown)
            this._hide();
    }

    Show() {
        if (this._window && !this._shown)
            this._show();
    }

    // Wider, for example, while the chat list is pinned beside the conversation.
    SetWidth(width) {
        this._width = width;
        if (this._shown)
            this._place();
    }

    toggle() {
        const window = this._window;
        if (!window) {
            const app = Shell.AppSystem.get_default().lookup_app(`${APP_ID}.desktop`);
            if (app)
                app.activate();
            else
                Main.notify('Local LLM Chat', 'The chat is not installed; run install.sh in local_llm_chat.');
        } else if (!this._shown) {
            this._show();
        } else if (!window.has_focus()) {
            window.activate(global.get_current_time());
        } else {
            this._hide();
        }
    }

    // Clicking another window hides the drawer, as with any drawer. Its own dialogs,
    // such as the file chooser, keep it open; so does the shell taking the focus.
    _onFocusChanged() {
        const focused = global.display.focus_window;
        if (this._shown && focused && !this._owns(focused))
            this._hide(false);
    }

    _owns(window) {
        for (let w = window; w; w = w.get_transient_for()) {
            if (w === this._window)
                return true;
        }
        return false;
    }

    _adopt(window, reveal = true) {
        if (window.get_wm_class() === null) {
            // A new Wayland window says which app it belongs to a moment later.
            window.connectObject('notify::wm-class', () => {
                window.disconnectObject(this);
                this._adopt(window, reveal);
            }, this);
            return;
        }
        if (this._window || window.get_wm_class() !== APP_ID)
            return;
        this._window = window;
        // A dock stays above other windows but below fullscreen ones, out of
        // Alt+Tab and the overview, and may wait off screen while hidden.
        window.set_type(Meta.WindowType.DOCK);
        window.stick();
        window.connectObject('unmanaged', () => {
            window.disconnectObject(this);
            this._window = null;
            this._shown = false;
        }, this);
        if (!reveal) {
            this._place();
            return;
        }
        // Mutter only places a new window once it has drawn itself.
        const actor = window.get_compositor_private();
        if (actor.visible)
            this._show();
        else
            actor.connectObject('first-frame', () => this._show(), this);
    }

    _place() {
        if (!this._window)
            return;
        if (!this._shown) {
            this._window.move_frame(false, global.screen_width, 0);
            return;
        }
        const monitor = Main.layoutManager.primaryMonitor;
        // A new window belongs to the monitor it opened on, and Mutter would fit the
        // drawer (and its menus and tooltips) to that one.
        if (this._window.get_monitor() !== monitor.index)
            this._window.move_to_monitor(monitor.index);
        const top = monitor.y + Main.layoutManager.panelBox.height;
        const width = Math.min(this._width, monitor.width);
        this._window.move_resize_frame(false, monitor.x + monitor.width - width, top,
            width, monitor.y + monitor.height - top);
    }

    _show() {
        this._shown = true;
        this._place();
        const actor = this._window.get_compositor_private();
        actor.remove_all_transitions();
        actor.translation_x = this._width;
        actor.ease({translation_x: 0, duration: SLIDE_MS, mode: Clutter.AnimationMode.EASE_OUT_CUBIC});
        this._window.activate(global.get_current_time());
    }

    _hide(refocus = true) {
        this._shown = false;
        // Otherwise your typing would still go to the hidden chat.
        if (refocus)
            global.display.focus_default_window(global.get_current_time());
        const actor = this._window.get_compositor_private();
        actor.ease({
            translation_x: this._width,
            duration: SLIDE_MS,
            mode: Clutter.AnimationMode.EASE_IN_CUBIC,
            onStopped: () => {
                actor.translation_x = 0;
                this._place();
            },
        });
    }

    destroy() {
        global.display.disconnectObject(this);
        Main.layoutManager.disconnectObject(this);
        for (const actor of global.get_window_actors()) {
            actor.meta_window.disconnectObject(this);
            actor.disconnectObject(this);
        }
        const window = this._window;
        if (!window)
            return;
        const actor = window.get_compositor_private();
        actor.remove_all_transitions();
        actor.translation_x = 0;
        window.set_type(Meta.WindowType.NORMAL);
        window.unstick();
        if (!this._shown)
            window.move_frame(false, 0, 0); // back where a normal window can be found
    }
}

const ChatButton = GObject.registerClass(
class ChatButton extends PanelMenu.Button {
    _init(drawer) {
        super._init(0.5, 'Local LLM chat', true);
        this._drawer = drawer;
        this.add_child(new St.Icon({
            iconName: 'chat-message-new-symbolic',
            styleClass: 'system-status-icon',
        }));
    }

    vfunc_event(event) {
        if (event.type() === Clutter.EventType.BUTTON_RELEASE ||
            event.type() === Clutter.EventType.TOUCH_END) {
            this._drawer.toggle();
            return Clutter.EVENT_STOP;
        }
        return Clutter.EVENT_PROPAGATE;
    }
});

export default class LocalLlmChatExtension extends Extension {
    enable() {
        this._drawer = new Drawer();
        this._dbus = Gio.DBusExportedObject.wrapJSObject(DBUS_INTERFACE, this._drawer);
        this._dbus.export(Gio.DBus.session, DBUS_PATH);
        this._dbus.emit_signal('Present', new GLib.Variant('(b)', [true]));
        this._button = new ChatButton(this._drawer);
        Main.panel.addToStatusArea(this.uuid, this._button);

        // The top-bar button is how you reach the chat, so it stays out of the dock and
        // the app switcher, which both list the running apps.
        this._injections = new InjectionManager();
        this._injections.overrideMethod(Shell.AppSystem.prototype, 'get_running', original => function () {
            return original.call(this).filter(app => app.get_id() !== `${APP_ID}.desktop`);
        });
        Main.overview.dash._queueRedisplay();
    }

    disable() {
        this._injections?.clear();
        Main.overview.dash._queueRedisplay();
        this._dbus?.emit_signal('Present', new GLib.Variant('(b)', [false]));
        this._dbus?.unexport();
        this._button?.destroy();
        this._drawer?.destroy();
        this._dbus = null;
        this._injections = null;
        this._button = null;
        this._drawer = null;
    }
}
