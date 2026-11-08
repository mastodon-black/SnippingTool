#!/usr/bin/env python3
"""
A snipping tool for the Cinnamon desktop (X11), modeled on the Windows one.

The screen is frozen and dimmed; drag a rectangle, draw a free-form shape,
click a window, or take the whole screen. The snip is copied to the
clipboard and opened in an editor where you can draw on it with a pen or
highlighter, erase strokes and crop it, recolor it with sliders and presets,
then copy it or save it as PNG/JPG. Existing images can be opened in the editor too.

Run with no arguments to open the window, or with --snip to go straight to
the capture overlay. That's what the Shift+Super+S shortcut runs (switch it
on from the menu button). Only one copy runs at a time: launching it again
hands the request to the copy that is already running.

    snipping_tool.py [--snip] [--mode rect|free|window|full] [--delay SECONDS]
"""
import argparse
import colorsys
import ctypes
import ctypes.util
import datetime
import json
import math
import pathlib
import sys

import cairo
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import GLib

APP_ID = "local.SnippingTool"
SCRIPT = pathlib.Path(__file__).resolve()
ICON_FILE = SCRIPT.parent / "icons" / "snipping-tool-256.png"
FONT_DIR = SCRIPT.parent / "fonts"


def register_fonts():
    """Make the bundled fonts usable by Pango and cairo without installing
    them system-wide (Pango 1.52 has no API for this, fontconfig does)."""
    library = ctypes.util.find_library("fontconfig")
    if not library:
        return
    fc = ctypes.CDLL(library)
    fc.FcConfigGetCurrent.restype = ctypes.c_void_p
    fc.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    for font in FONT_DIR.glob("*.ttf"):
        fc.FcConfigAppFontAddFile(fc.FcConfigGetCurrent(), str(font).encode())


# Both have to happen before Gtk loads: the WM_CLASS is fixed when the
# display opens, and Pango reads the font list once.
GLib.set_prgname("snipping-tool")
register_fonts()
from gi.repository import Gdk, GdkPixbuf, Gio, Gtk  # noqa: E402

try:
    from PIL import Image, ImageChops, ImageFilter, ImageOps   # color adjustments
except ImportError:
    Image = None

CONFIG_FILE = pathlib.Path(GLib.get_user_config_dir()) / "snipping-tool" / "settings.json"

MODES = {"rect": "Rectangle", "free": "Free-form", "window": "Window", "full": "Full screen"}
DELAYS = (0, 3, 5, 10)

DEFAULT_SETTINGS = {
    "mode": "rect",
    "delay": 0,
    "auto_copy": True,
    "auto_save": False,
    "last_dir": "",
    "pen_color": [0.86, 0.15, 0.15, 1.0],
    "pen_width": 4,
    "highlighter_color": [1.0, 0.9, 0.0, 1.0],
    "highlighter_width": 18,
    "presets": {},                  # your saved color presets: name -> changed values
    "default_preset": "Original",   # applied to every new snip
}

# Color adjustments: key, label, lowest, highest, unit.
ADJUSTMENTS = (
    ("brightness", "Brightness", -100, 100, ""),
    ("contrast", "Contrast", -100, 100, ""),
    ("shadows", "Shadows", -100, 100, ""),
    ("highlights", "Highlights", -100, 100, ""),
    ("whites", "Whites", -100, 100, ""),
    ("blacks", "Blacks", -100, 100, ""),
    ("saturation", "Saturation", -100, 100, ""),
    ("vibrance", "Vibrance", -100, 100, ""),
    ("hue", "Hue", -180, 180, "°"),
    ("temperature", "Temperature", -100, 100, ""),
    ("tint", "Tint", -100, 100, ""),
    ("shadow_hue", "Shadow hue", 0, 360, "°"),
    ("shadow_tint", "Shadow tint", 0, 100, ""),
    ("highlight_hue", "Highlight hue", 0, 360, "°"),
    ("highlight_tint", "Highlight tint", 0, 100, ""),
    ("clarity", "Clarity", -100, 100, ""),
    ("sharpen", "Sharpen", 0, 100, ""),
    ("vignette", "Vignette", 0, 100, ""),
    ("grain", "Grain", 0, 100, ""),
)
ADJUST_HEADINGS = {"brightness": "Light", "saturation": "Color", "shadow_hue": "Split tone", "clarity": "Effects"}
HUE_SLIDERS = ("shadow_hue", "highlight_hue")
NEUTRAL = {**{key: 0 for key, *_ in ADJUSTMENTS}, "shadow_hue": 210, "highlight_hue": 40, "invert": False}
# Presets list only the values they change.
BUILTIN_PRESETS = {
    "Original": {},
    # everyday
    "Clean Pop": {"vibrance": 30, "contrast": 12, "clarity": 10, "sharpen": 12},
    "Vivid": {"vibrance": 55, "saturation": 8, "contrast": 18, "clarity": 8},
    "HDR Detail": {"shadows": 28, "highlights": -24, "contrast": 20, "clarity": 24, "vibrance": 26, "sharpen": 12},
    "Golden Hour": {"temperature": 26, "vibrance": 20, "shadows": 10, "highlights": -6, "contrast": 8, "vignette": 14,
                    "shadow_hue": 20, "shadow_tint": 8, "highlight_hue": 38, "highlight_tint": 22},
    "Soft Glow": {"clarity": -40, "blacks": -6, "vibrance": 12, "temperature": 6, "highlights": 8, "brightness": 4},
    # film
    "Warm Film": {"blacks": -16, "whites": -5, "contrast": 14, "saturation": -8, "temperature": 14, "grain": 12,
                  "shadow_hue": 205, "shadow_tint": 14, "highlight_hue": 42, "highlight_tint": 16},
    "Cool Film": {"blacks": -10, "whites": -3, "contrast": 16, "vibrance": 8, "temperature": -10, "tint": -4, "grain": 10,
                  "shadow_hue": 215, "shadow_tint": 20, "highlight_hue": 60, "highlight_tint": 8},
    "Faded Matte": {"blacks": -34, "whites": -10, "contrast": 6, "saturation": -16, "temperature": 6, "highlights": -6,
                    "shadow_hue": 230, "shadow_tint": 12, "highlight_hue": 45, "highlight_tint": 8},
    "Expired Film": {"blacks": -24, "whites": -8, "contrast": 6, "saturation": -14, "temperature": 14, "tint": 8,
                     "shadow_hue": 170, "shadow_tint": 26, "highlight_hue": 48, "highlight_tint": 22,
                     "grain": 18, "vignette": 18},
    "Cross Process": {"contrast": 28, "vibrance": 25, "tint": -12,
                      "shadow_hue": 225, "shadow_tint": 28, "highlight_hue": 75, "highlight_tint": 24},
    # cinema
    "Teal & Orange": {"contrast": 16, "saturation": -8, "vibrance": 16, "vignette": 10, "clarity": 8,
                      "shadow_hue": 190, "shadow_tint": 38, "highlight_hue": 32, "highlight_tint": 26},
    "Blockbuster": {"contrast": 24, "saturation": -14, "vignette": 20, "clarity": 10,
                    "shadow_hue": 205, "shadow_tint": 32, "highlight_hue": 38, "highlight_tint": 14},
    "Bleach Bypass": {"saturation": -48, "contrast": 42, "clarity": 16, "highlights": -8},
    "Moody": {"brightness": -10, "contrast": 14, "saturation": -24, "shadows": -8, "temperature": -6, "blacks": -7,
              "shadow_hue": 215, "shadow_tint": 20, "vignette": 28},
    "Moonlight": {"brightness": -28, "saturation": -50, "temperature": -40, "contrast": 14, "vignette": 26,
                  "shadow_hue": 225, "shadow_tint": 30, "highlight_hue": 210, "highlight_tint": 14},
    "Neon Noir": {"contrast": 24, "vibrance": 38, "vignette": 18,
                  "shadow_hue": 265, "shadow_tint": 38, "highlight_hue": 320, "highlight_tint": 16},
    # black and white
    "Grayscale": {"saturation": -100},
    "Noir": {"saturation": -100, "contrast": 36, "highlights": -6, "clarity": 14, "vignette": 24, "grain": 12},
    "Silver": {"saturation": -100, "contrast": 18, "clarity": 10, "blacks": -3, "shadows": 6},
    "Soft Mono": {"saturation": -100, "contrast": -6, "blacks": -18, "whites": -6, "grain": 8},
    "Sepia": {"saturation": -100, "temperature": 38, "tint": 4, "contrast": 8, "blacks": -10, "whites": -3,
              "shadow_hue": 26, "shadow_tint": 26, "highlight_hue": 44, "highlight_tint": 18, "vignette": 10, "grain": 6},
    "Cyanotype": {"saturation": -100, "temperature": -30, "contrast": 10, "blacks": -6, "whites": -2,
                  "shadow_hue": 215, "shadow_tint": 60, "highlight_hue": 195, "highlight_tint": 16},
    # for screenshots
    "Crisp Text": {"contrast": 18, "sharpen": 35},
    "Clean Paper": {"saturation": -50, "whites": 55, "blacks": 35, "contrast": 15, "sharpen": 20},
    "Brighten Dark": {"shadows": 60, "brightness": 14, "highlights": -8, "vibrance": 10},
    "High Contrast": {"contrast": 50, "vibrance": 10},
    "Dark Mode": {"invert": True, "hue": 180, "blacks": -8, "whites": -2},   # flips lightness, keeps hues
    "Invert": {"invert": True},
}
CUSTOM_LABEL = "Custom"
LUMA = (0.2126, 0.7152, 0.0722)
ADJUST_PANEL_WIDTH = 300
PREVIEW_PIXELS = 1_500_000  # larger images are edited through a copy this size while zoomed out

PEN_COLORS = [
    (0.0, 0.0, 0.0), (1.0, 1.0, 1.0), (0.55, 0.55, 0.55), (0.86, 0.15, 0.15),
    (0.96, 0.49, 0.0), (1.0, 0.84, 0.0), (0.18, 0.65, 0.2), (0.0, 0.6, 0.6),
    (0.1, 0.4, 0.9), (0.45, 0.25, 0.8), (0.9, 0.3, 0.6), (0.5, 0.3, 0.15),
]
HIGHLIGHTER_COLORS = [
    (1.0, 0.9, 0.0), (0.5, 1.0, 0.3), (0.3, 0.9, 1.0),
    (1.0, 0.55, 0.85), (1.0, 0.65, 0.2), (0.75, 0.6, 1.0),
]
WIDTH_RANGE = {"pen": (1, 24), "highlighter": (6, 48)}

CANVAS_MARGIN = 20      # space around the snip in the editor
SNIP_FRAME = 1          # outline drawn around the snip, in screen px
HANDLE_REACH = 10       # screen px within which a crop handle can be grabbed
MIN_CROP = 8            # smallest crop, in image px
OUTER_MARGIN = (12, 10)  # window edge to panels (horizontal, vertical)
PANEL_GAP = 8

# Cinnamon custom keyboard shortcut that runs `--snip`.
SHORTCUT_KEYS = "<Super><Shift>s"
KB_SCHEMA = "org.cinnamon.desktop.keybindings"
KB_CUSTOM_SCHEMA = KB_SCHEMA + ".custom-keybinding"
KB_PATH = "/org/cinnamon/desktop/keybindings/custom-keybindings/{}/"
KB_DUMMY = "__dummy__"

# Dark theme, the same palette and fonts as MetalEQ.
BG = "#1e1f24"
BG_ALT = "#26282f"
PANEL = "#2c2e36"
BORDER = "#3a3d47"
ACCENT = "#2ecc71"
ACCENT_DIM = "#1f7a4d"
ACCENT_LIGHT = "#58e090"
TEXT = "#e6e6eb"
TEXT_DIM = "#9a9ca5"
GOOD = "#4cd787"
BODY_FONT = "Space Grotesk"
HEADER_FONT = "Cinzel"


def rgb(color, alpha=None):
    """'#rrggbb' as a cairo color tuple."""
    value = tuple(int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return value if alpha is None else value + (alpha,)


CSS = f"""
window, dialog, messagedialog, popover, tooltip, menu {{
    font-family: "{BODY_FONT}", "Ubuntu Sans", "Noto Sans", sans-serif;
    font-size: 13px;
}}
window.background, dialog.background, messagedialog.background {{ background-color: {BG}; color: {TEXT}; }}

.panel {{
    background-color: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 4px;
}}
.snip-toolbar {{ padding: 8px 10px; margin-bottom: {PANEL_GAP}px; }}
.snip-cropbar {{ padding: 8px 12px; margin-bottom: {PANEL_GAP}px; }}
.snip-content {{ padding: 1px; }}
.snip-content scrolledwindow, .snip-content viewport {{ background-color: transparent; }}
.section-header {{
    font-family: "{HEADER_FONT}", "Ubuntu Sans", serif;
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 1px;
    color: {TEXT};
}}
.app-title {{
    font-family: "{HEADER_FONT}", "Ubuntu Sans", serif;
    font-size: 22px;
    font-weight: 600;
    letter-spacing: 2px;
    color: {TEXT};
}}
.dim {{ color: {TEXT_DIM}; }}
.value {{ color: {ACCENT}; font-size: 12px; font-weight: 600; }}
button:disabled .value {{ color: alpha({TEXT_DIM}, 0.55); }}
.snip-side {{ margin-left: {PANEL_GAP}px; }}
.snip-adjust, .snip-presets {{ padding: 10px 12px; }}
.snip-adjust scrolledwindow, .snip-adjust viewport {{ background-color: transparent; }}
.snip-adjust undershoot, .snip-adjust overshoot, .snip-content undershoot, .snip-content overshoot {{
    background: none;
    border: none;
    box-shadow: none;
}}
.adjust-heading {{
    font-family: "{HEADER_FONT}", "Ubuntu Sans", serif;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 1px;
    color: {TEXT_DIM};
    margin-top: 6px;
}}
.snip-presets {{ margin-top: {PANEL_GAP}px; }}
.adjust-label {{ color: {TEXT_DIM}; font-size: 11px; }}
.adjust-value {{ font-size: 11px; }}
.status {{ color: {TEXT_DIM}; font-size: 12px; }}
.panel separator {{ background-color: {BORDER}; min-height: 1px; }}
checkbutton.option {{ color: {TEXT_DIM}; font-weight: 600; }}
checkbutton.option:checked {{ color: {GOOD}; }}
checkbutton.option check {{ margin-right: 8px; }}
menu, .menu {{
    background-color: {BG_ALT};
    border: 1px solid {BORDER};
    padding: 4px 0;
    color: {TEXT};
}}
menu menuitem {{ padding: 5px 12px; color: {TEXT}; }}
menu menuitem:hover {{ background-color: {ACCENT_DIM}; color: #ffffff; }}
/* The preset list hangs off the text cell inside the box, so the cell (not the
   button) holds the padding; the list then lines up with the box's edges. Its
   width is the box's, less the cell's offset, the list's border and item padding. */
.snip-presets combobox button {{ padding: 0 9px 0 0; }}
#gtk-combobox-popup-menu menuitem {{
    padding: 0 3px;
    min-width: {(ADJUST_PANEL_WIDTH - PANEL_GAP - 26 - 1 - 2) // 2 - 6}px;   /* two columns */
}}

button {{
    background-image: none;
    background-color: {BG_ALT};
    border: 1px solid {BORDER};
    border-radius: 3px;
    box-shadow: none;
    text-shadow: none;
    -gtk-icon-shadow: none;
    color: {TEXT};
    padding: 5px 9px;
    min-height: 20px;
    min-width: 20px;
    outline-color: alpha({ACCENT}, 0.4);
}}
button:hover {{ border-color: {ACCENT_DIM}; }}
button:active {{ background-color: {ACCENT_DIM}; }}
button:checked {{ background-color: {ACCENT_DIM}; border-color: {ACCENT}; color: #ffffff; }}
button:checked:hover {{ border-color: {ACCENT_LIGHT}; }}
button:disabled {{ background-color: {BG_ALT}; border-color: {BORDER}; color: alpha({TEXT_DIM}, 0.55); }}
button label, button image {{ color: inherit; }}
button.suggested-action {{
    background-color: {ACCENT};
    border-color: {ACCENT};
    color: {BG};
    font-weight: 600;
}}
button.suggested-action:hover {{ background-color: {ACCENT_LIGHT}; border-color: {ACCENT_LIGHT}; }}
button.suggested-action:active {{ background-color: {ACCENT_DIM}; border-color: {ACCENT_DIM}; color: {TEXT}; }}
button.swatch {{
    background-color: transparent;
    border-color: transparent;
    padding: 3px;
    min-width: 0;
    min-height: 0;
    border-radius: 99px;
}}
button.swatch:hover {{ background-color: {PANEL}; }}

popover, popover.background {{
    background-color: {BG_ALT};
    border: 1px solid {BORDER};
    border-radius: 4px;
    box-shadow: none;
    color: {TEXT};
}}
popover modelbutton {{ border-radius: 3px; padding: 5px 10px; color: {TEXT}; }}
popover modelbutton:hover {{ background-color: {ACCENT_DIM}; color: #ffffff; }}
popover separator {{ background-color: {BORDER}; }}
check, radio {{
    min-width: 12px;
    min-height: 12px;
    border: 2px solid {BORDER};
    background-color: {BG};
    background-image: none;
    box-shadow: none;
    -gtk-icon-source: none;
}}
check {{ border-radius: 4px; }}
radio {{ border-radius: 99px; }}
check:checked {{
    background-color: {GOOD};
    border-color: {GOOD};
    color: {BG};
    -gtk-icon-source: -gtk-icontheme("object-select-symbolic");
}}
radio:checked {{
    border-color: {GOOD};
    color: {GOOD};
    -gtk-icon-source: -gtk-icontheme("media-record-symbolic");
}}

scale trough {{
    min-height: 6px;
    background-color: {BG};
    background-image: none;
    border: none;
    border-radius: 2px;
    box-shadow: none;
}}
scale highlight {{ background-color: {ACCENT}; background-image: none; border: none; border-radius: 2px; }}
scale slider {{
    min-width: 14px;
    min-height: 14px;
    margin: -5px;
    background-color: {ACCENT};
    background-image: none;
    border: none;
    border-radius: 3px;
    box-shadow: none;
}}
scale slider:hover {{ background-color: {ACCENT_LIGHT}; }}
scale.hue-scale trough {{ background-image: linear-gradient(to right, #f33, #ff3, #3f3, #3ff, #33f, #f3f, #f33); }}
scale.hue-scale slider {{ background-color: {TEXT}; }}
scale.hue-scale slider:hover {{ background-color: #ffffff; }}
scale value {{ color: {ACCENT}; font-size: 11px; font-weight: 600; }}

tooltip, tooltip.background {{
    background-color: {BG_ALT};
    border: 1px solid {BORDER};
    border-radius: 3px;
    box-shadow: none;
}}
tooltip label {{ color: {TEXT}; }}

scrollbar {{ background-color: transparent; border: none; }}
scrollbar slider {{ background-color: {BORDER}; border: none; border-radius: 5px; min-width: 6px; min-height: 6px; }}
scrollbar slider:hover {{ background-color: {TEXT_DIM}; }}

entry {{
    background-color: {BG_ALT};
    background-image: none;
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 3px;
    box-shadow: none;
}}
entry:focus {{ border-color: {ACCENT_DIM}; }}
selection, treeview.view:selected, row:selected {{ background-color: {ACCENT_DIM}; color: #ffffff; }}

.snip-toast {{
    background-color: {BG_ALT};
    border: 1px solid {ACCENT_DIM};
    border-radius: 4px;
    padding: 8px 16px;
    margin-bottom: 20px;
    box-shadow: none;   /* a shadow paints outside the toast and is left behind when it hides */
}}
.snip-toast label {{ color: {GOOD}; font-weight: 600; }}
.snip-bar {{
    background-color: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 6px;
    margin-top: 18px;
    box-shadow: 0 6px 18px rgba(0, 0, 0, 0.55);
}}
.snip-bar button {{ min-width: 26px; min-height: 22px; }}
.snip-bar separator {{ background-color: {BORDER}; margin: 4px 3px; min-width: 1px; }}
""".encode()


def load_settings():
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))   # deep copy
    try:
        settings.update(json.loads(CONFIG_FILE.read_text()))
    except (OSError, ValueError):
        pass
    if settings["mode"] not in MODES:
        settings["mode"] = "rect"
    if settings["delay"] not in DELAYS:
        settings["delay"] = 0
    if not isinstance(settings["presets"], dict):
        settings["presets"] = {}
    # A saved preset whose name has since become a built-in keeps its values under a new name.
    settings["presets"] = {f"{name} (mine)" if name in BUILTIN_PRESETS else name:
                           {k: v for k, v in values.items() if k in NEUTRAL}
                           for name, values in settings["presets"].items() if isinstance(values, dict)}
    if settings["default_preset"] not in BUILTIN_PRESETS and settings["default_preset"] not in settings["presets"]:
        settings["default_preset"] = "Original"
    return settings


def save_settings(settings):
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(settings, indent=2))
    except OSError as e:
        print(f"snipping-tool: can't save settings: {e}", file=sys.stderr)


def screenshots_dir():
    pictures = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
    return pathlib.Path(pictures or pathlib.Path.home() / "Pictures") / "Screenshots"


def default_filename(ext="png"):
    return f"Screenshot {datetime.datetime.now():%Y-%m-%d %H%M%S}.{ext}"


# ---------------------------------------------------------------- shortcut

def _custom_binding(name):
    return Gio.Settings.new_with_path(KB_CUSTOM_SCHEMA, KB_PATH.format(name))


def shortcut_supported():
    source = Gio.SettingsSchemaSource.get_default()
    return bool(source and source.lookup(KB_SCHEMA, True) and source.lookup(KB_CUSTOM_SCHEMA, True))


def find_shortcut():
    """Slot name ("customN") of our Cinnamon shortcut, or None."""
    if not shortcut_supported():
        return None
    for name in Gio.Settings.new(KB_SCHEMA).get_strv("custom-list"):
        if name == KB_DUMMY:
            continue
        command = _custom_binding(name).get_string("command")
        if SCRIPT.name in command and "--snip" in command:
            return name
    return None


def _touch_custom_list(names):
    # Cinnamon only rebuilds its custom shortcuts when custom-list changes,
    # so toggle a dummy entry the same way cinnamon-settings does.
    if KB_DUMMY in names:
        names.remove(KB_DUMMY)
    else:
        names.append(KB_DUMMY)
    Gio.Settings.new(KB_SCHEMA).set_strv("custom-list", names)


def set_shortcut(enabled):
    parent = Gio.Settings.new(KB_SCHEMA)
    names = list(parent.get_strv("custom-list"))
    current = find_shortcut()
    if enabled and not current:
        used = {n for n in names if n != KB_DUMMY}
        i = 0
        while f"custom{i}" in used:
            i += 1
        name = f"custom{i}"
        binding = _custom_binding(name)
        binding.set_string("name", "Snipping Tool")
        binding.set_string("command", f'{sys.executable} "{SCRIPT}" --snip')
        binding.set_strv("binding", [SHORTCUT_KEYS])
        _touch_custom_list(names + [name])
    elif not enabled and current:
        binding = _custom_binding(current)
        for key in ("name", "command", "binding"):
            binding.reset(key)
        _touch_custom_list([n for n in names if n != current])
    Gio.Settings.sync()


# ---------------------------------------------------------------- capture

def grab_screen():
    """The whole screen as a cairo surface, plus its size in logical pixels."""
    root = Gdk.get_default_root_window()
    width, height = root.get_width(), root.get_height()
    pixbuf = Gdk.pixbuf_get_from_window(root, 0, 0, width, height)
    return Gdk.cairo_surface_create_from_pixbuf(pixbuf, 1, None), pixbuf.get_width(), width, height


def list_windows():
    """Visible windows on the current workspace, topmost first, as frame
    rectangles (x, y, w, h) in X screen pixels."""
    try:
        from Xlib import X, display, error
    except ImportError:
        return []
    try:
        d = display.Display()
    except Exception:
        return []
    root = d.screen().root

    def prop(win, name):
        try:
            p = win.get_full_property(d.intern_atom(name), X.AnyPropertyType)
        except error.XError:
            return None
        return p.value if p else None

    current_desktop = (prop(root, "_NET_CURRENT_DESKTOP") or [0])[0]
    hidden = d.intern_atom("_NET_WM_STATE_HIDDEN")
    rects = []
    for xid in reversed(list(prop(root, "_NET_CLIENT_LIST_STACKING") or [])):
        win = d.create_resource_object("window", xid)
        try:
            if win.get_attributes().map_state != X.IsViewable:
                continue
            desktop = prop(win, "_NET_WM_DESKTOP")
            if desktop is not None and len(desktop) and desktop[0] not in (current_desktop, 0xFFFFFFFF):
                continue
            if hidden in (prop(win, "_NET_WM_STATE") or []):
                continue
            geo = win.get_geometry()
            origin = root.translate_coords(win, 0, 0)
        except error.XError:
            continue
        x, y, w, h = origin.x, origin.y, geo.width, geo.height
        frame = prop(win, "_NET_FRAME_EXTENTS")      # server-side decorations
        if frame is not None and len(frame) == 4:
            left, right, top, bottom = frame
            x, y, w, h = x - left, y - top, w + left + right, h + top + bottom
        shadow = prop(win, "_GTK_FRAME_EXTENTS")     # client-side decoration shadows
        if shadow is not None and len(shadow) == 4:
            left, right, top, bottom = shadow
            x, y, w, h = x + left, y + top, w - left - right, h - top - bottom
        if w > 0 and h > 0:
            rects.append((x, y, w, h))
    d.close()
    return rects


def cut_out(shot, x, y, w, h, outline=None, scale=1):
    """Copy the box (x, y, w, h) of `shot`, in device pixels. With `outline`
    (points in logical pixels), everything outside that shape is transparent."""
    out = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(out)
    if outline:
        cr.translate(-x, -y)
        cr.scale(scale, scale)
        cr.move_to(*outline[0])
        for p in outline[1:]:
            cr.line_to(*p)
        cr.close_path()
        cr.clip()
        cr.identity_matrix()
    cr.set_source_surface(shot, -x, -y)
    cr.paint()
    return out


# ---------------------------------------------------------------- drawing

def rounded_rect(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def draw_glyph(cr, name, size, rgba, accent=None):
    """Draw the line-art icon `name` into a size×size box."""
    cr.save()
    cr.scale(size / 20, size / 20)
    cr.set_source_rgba(*rgba)
    cr.set_line_width(1.5)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    if name == "rect":
        cr.set_dash([2.6, 2.2])
        cr.rectangle(3, 4.5, 14, 11)
        cr.stroke()
    elif name == "free":
        cr.set_dash([2.6, 2.2])
        cr.move_to(4.5, 6.5)
        cr.curve_to(8, 1.5, 17.5, 2.5, 16.5, 8.5)
        cr.curve_to(16, 13, 11.5, 11.5, 9.5, 15)
        cr.curve_to(7, 19, 1.5, 11, 4.5, 6.5)
        cr.close_path()
        cr.stroke()
    elif name == "window":
        rounded_rect(cr, 2.75, 3.75, 14.5, 12.5, 2)
        cr.stroke()
        cr.move_to(2.75, 7.5)
        cr.line_to(17.25, 7.5)
        cr.stroke()
    elif name == "full":
        for x, y, dx, dy in ((3, 3, 1, 1), (17, 3, -1, 1), (3, 17, 1, -1), (17, 17, -1, -1)):
            cr.move_to(x, y + 4.5 * dy)
            cr.line_to(x, y)
            cr.line_to(x + 4.5 * dx, y)
        cr.stroke()
    elif name == "pen":
        cr.move_to(3.5, 16.5)
        cr.line_to(4.6, 12.6)
        cr.line_to(13.4, 3.8)
        cr.line_to(16.2, 6.6)
        cr.line_to(7.4, 15.4)
        cr.close_path()
        cr.stroke()
        cr.move_to(11.6, 5.6)
        cr.line_to(14.4, 8.4)
        cr.stroke()
        cr.move_to(3.5, 16.5)
        cr.line_to(4.6, 12.6)
        cr.line_to(7.4, 15.4)
        cr.close_path()
        cr.set_source_rgba(*(accent or rgba))
        cr.fill()
    elif name == "highlighter":
        cr.move_to(6.5, 13.5)
        cr.line_to(14, 3.5)
        cr.line_to(17, 6)
        cr.line_to(9, 15)
        cr.close_path()
        cr.stroke()
        cr.move_to(6.5, 13.5)
        cr.line_to(9, 15)
        cr.line_to(7.5, 16.5)
        cr.line_to(5, 15)
        cr.close_path()
        cr.stroke_preserve()
        cr.set_source_rgba(*(accent or rgba))
        cr.fill()
        cr.rectangle(2, 17.5, 9, 2)
        cr.fill()
    elif name == "eraser":
        cr.save()
        cr.translate(10.5, 9.5)
        cr.rotate(-math.pi / 4)
        rounded_rect(cr, -7, -3.75, 14, 7.5, 1.5)
        cr.stroke()
        cr.move_to(-1.5, -3.75)
        cr.line_to(-1.5, 3.75)
        cr.stroke()
        cr.restore()
        cr.move_to(9, 17.5)
        cr.line_to(17.5, 17.5)
        cr.stroke()
    elif name == "crop":
        cr.move_to(6, 2)
        cr.line_to(6, 14)
        cr.line_to(18, 14)
        cr.move_to(2, 6)
        cr.line_to(14, 6)
        cr.line_to(14, 18)
        cr.stroke()
    elif name == "timer":
        cr.arc(10, 11, 6.5, 0, 2 * math.pi)
        cr.stroke()
        cr.move_to(10, 11)
        cr.line_to(10, 7.5)
        cr.move_to(10, 11)
        cr.line_to(12.5, 12.5)
        cr.move_to(8, 2.25)
        cr.line_to(12, 2.25)
        cr.stroke()
    elif name == "close":
        cr.move_to(5, 5)
        cr.line_to(15, 15)
        cr.move_to(15, 5)
        cr.line_to(5, 15)
        cr.stroke()
    elif name == "adjust":
        knobs = ((5, 13), (10, 6.5), (15, 11))
        for y, _ in knobs:
            cr.move_to(3, y)
            cr.line_to(17, y)
        cr.stroke()
        for y, x in knobs:
            cr.arc(x, y, 2.4, 0, 2 * math.pi)
            cr.fill()
    cr.restore()


class Glyph(Gtk.DrawingArea):
    """A draw_glyph icon that follows the theme's text color."""

    def __init__(self, glyph, size=20, accent=None):
        super().__init__(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.glyph, self.size, self.accent = glyph, size, accent
        self.set_size_request(size, size)
        self.connect("draw", self._draw)

    def set_glyph(self, glyph, accent=None):
        self.glyph, self.accent = glyph, accent
        self.queue_draw()

    def _draw(self, widget, cr):
        color = self.get_style_context().get_color(self.get_state_flags())
        draw_glyph(cr, self.glyph, self.size, (color.red, color.green, color.blue, color.alpha), self.accent)


class Stroke:
    __slots__ = ("tool", "color", "width", "points")

    def __init__(self, tool, color, width, points):
        self.tool, self.color, self.width, self.points = tool, tuple(color), width, points


def draw_stroke(cr, stroke):
    cr.save()
    cr.set_source_rgba(*stroke.color)
    if stroke.tool == "highlighter":
        # Multiply keeps dark text dark under the ink, like a real highlighter.
        cr.set_operator(cairo.OPERATOR_MULTIPLY)
    pts = stroke.points
    if len(pts) == 1:
        cr.arc(pts[0][0], pts[0][1], stroke.width / 2, 0, 2 * math.pi)
        cr.fill()
    else:
        cr.set_line_width(stroke.width)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.move_to(*pts[0])
        # Quadratic curves through the midpoints smooth out the mouse jitter.
        for (x1, y1), (x2, y2) in zip(pts[1:-1], pts[2:]):
            x0, y0 = cr.get_current_point()
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            cr.curve_to(x0 + 2 / 3 * (x1 - x0), y0 + 2 / 3 * (y1 - y0),
                        mx + 2 / 3 * (x1 - mx), my + 2 / 3 * (y1 - my), mx, my)
        cr.line_to(*pts[-1])
        cr.stroke()
    cr.restore()


def segment_distance(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0 if length2 == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / length2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def stroke_hit(stroke, x, y, radius):
    reach = radius + stroke.width / 2
    pts = stroke.points
    if len(pts) == 1:
        return math.hypot(x - pts[0][0], y - pts[0][1]) <= reach
    return any(segment_distance(x, y, *a, *b) <= reach for a, b in zip(pts, pts[1:]))


def checker_pattern():
    tile = cairo.ImageSurface(cairo.FORMAT_RGB24, 16, 16)
    cr = cairo.Context(tile)
    cr.set_source_rgb(*rgb(BG_ALT))
    cr.paint()
    cr.set_source_rgb(*rgb(BORDER))
    cr.rectangle(0, 0, 8, 8)
    cr.rectangle(8, 8, 8, 8)
    cr.fill()
    pattern = cairo.SurfacePattern(tile)
    pattern.set_extend(cairo.EXTEND_REPEAT)
    return pattern


def saturation_matrix(k):
    return [[(1 - k) * LUMA[c] + (k if r == c else 0) for c in range(3)] for r in range(3)]


def color_matrix(adjust):
    """Invert, saturation, hue, temperature and tint as one affine RGB transform:
    three rows of (r, g, b, offset), offsets in 0-255 units, flattened for Pillow."""
    m = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    offset = [0, 0, 0]

    def then(step, step_offset=(0, 0, 0)):
        nonlocal m, offset
        m = [[sum(step[r][k] * m[k][c] for k in range(3)) for c in range(3)] for r in range(3)]
        offset = [sum(step[r][k] * offset[k] for k in range(3)) + step_offset[r] for r in range(3)]

    if adjust["invert"]:
        then([[-1, 0, 0], [0, -1, 0], [0, 0, -1]], (255, 255, 255))
    then(saturation_matrix(1 + adjust["saturation"] / 100))
    # hue rotation around the grey axis (the SVG hueRotate matrix)
    cos, sin = math.cos(math.radians(adjust["hue"])), math.sin(math.radians(adjust["hue"]))
    then([[0.213 + cos * 0.787 - sin * 0.213, 0.715 - cos * 0.715 - sin * 0.715, 0.072 - cos * 0.072 + sin * 0.928],
          [0.213 - cos * 0.213 + sin * 0.143, 0.715 + cos * 0.285 + sin * 0.140, 0.072 - cos * 0.072 - sin * 0.283],
          [0.213 - cos * 0.213 - sin * 0.787, 0.715 - cos * 0.715 + sin * 0.715, 0.072 + cos * 0.928 + sin * 0.072]])
    # The color cast comes after saturation, so a grayscale can still be warmed.
    warm, tint = adjust["temperature"] / 100, adjust["tint"] / 100
    then([[1 + 0.25 * warm, 0, 0], [0, 1 - 0.2 * tint, 0], [0, 0, 1 - 0.25 * warm]])
    return tuple(v for r in range(3) for v in (*m[r], offset[r]))


def lighten_darks(x, amount):
    """Lighten (amount > 0) or darken the dark end of x, 0-1. Black and the top
    end stay put, and the curve never folds back on itself."""
    if amount >= 0:
        return x + amount * 0.4 * (256 / 27) * x * (1 - x) ** 3
    return x * (1 + amount * 0.6 * (1 - x) ** 3)


def tint_vector(hue, amount):
    """How far a hue pushes each channel, with the push itself carrying no brightness."""
    color = colorsys.hsv_to_rgb(hue / 360, 1, 1)
    luma = sum(c * w for c, w in zip(color, LUMA))
    return [(c - luma) * amount / 100 * 0.24 for c in color]


def tone_lut(adjust):
    """The tone adjustments as one curve per channel, for Image.point. Curves keep
    black and white in range, so unlike plain gain and offset they never clip."""
    gamma = 2 ** (-adjust["brightness"] / 100)
    contrast = adjust["contrast"] / 100 / (2 * math.pi)
    shadows, highlights = adjust["shadows"] / 100, adjust["highlights"] / 100
    # Whites and blacks move the endpoints: positive cuts into the range (paper
    # goes fully white, ink fully black), negative pulls it in (matte, faded).
    whites, blacks = adjust["whites"] / 100, adjust["blacks"] / 100
    black_cut, white_cut = max(blacks, 0) * 0.35, 1 - max(whites, 0) * 0.35
    floor, ceiling = max(-blacks, 0) * 0.16, 1 - max(-whites, 0) * 0.16
    shadow_tint = tint_vector(adjust["shadow_hue"], adjust["shadow_tint"])
    highlight_tint = tint_vector(adjust["highlight_hue"], adjust["highlight_tint"])
    lut = []
    for channel in range(3):
        for i in range(256):
            x = (i / 255) ** gamma
            x -= contrast * math.sin(2 * math.pi * x)
            x = lighten_darks(x, shadows)
            x = 1 - lighten_darks(1 - x, -highlights)
            x = min(max((x - black_cut) / (white_cut - black_cut), 0), 1)
            x = floor + x * (ceiling - floor)
            x += shadow_tint[channel] * (1 - x) ** 2 + highlight_tint[channel] * x ** 2
            lut.append(int(min(max(x, 0), 1) * 255 + 0.5))
    return lut


def apply_vibrance(image, amount):
    """Saturation that goes easy on what is already vivid, so colors get richer
    without turning garish (or, when negative, tames only the loudest colors)."""
    r, g, b = image.split()
    chroma = ImageChops.difference(ImageChops.lighter(ImageChops.lighter(r, g), b),
                                   ImageChops.darker(ImageChops.darker(r, g), b))
    mask = ImageChops.invert(chroma) if amount > 0 else chroma
    matrix = saturation_matrix(1 + amount)
    saturated = image.convert("RGB", matrix=tuple(v for row in matrix for v in (*row, 0)))
    return Image.composite(saturated, image, mask)


_grain_noise = {}


def grain_layer(size, amount):
    """Soft monochrome film grain around mid-grey, for an overlay blend. The noise
    is kept per size, so the grain doesn't crawl as other sliders move."""
    if size not in _grain_noise:
        _grain_noise.clear()
        coarse = Image.effect_noise((size[0] // 2 + 1, size[1] // 2 + 1), 70)
        _grain_noise[size] = coarse.resize(size, Image.BILINEAR)
    noise = _grain_noise[size].point([min(max(128 + (v - 128) * amount, 0), 255) for v in range(256)])
    return Image.merge("RGB", (noise, noise, noise))


def paint_vignette(surface, amount, box):
    """Darken toward the corners of the box (x, y, w, h), keeping transparency."""
    x, y, w, h = box
    gradient = cairo.RadialGradient(0, 0, 0, 0, 0, 1)
    for i in range(11):
        t = i / 10
        edge = max(0, (t - 0.45) / 0.55)
        gradient.add_color_stop_rgba(t, 0, 0, 0, amount * 0.8 * edge * edge * (3 - 2 * edge))
    cr = cairo.Context(surface)
    cr.set_operator(cairo.OPERATOR_ATOP)
    cr.translate(x + w / 2, y + h / 2)
    cr.scale(w / 2 * math.sqrt(2), h / 2 * math.sqrt(2))
    cr.set_source(gradient)
    cr.paint()


def adjust_surface(surface, adjust, box=None, scale=1):
    """A copy of `surface` with the adjustments applied; alpha is kept. The vignette
    frames `box` (default: everything); `scale` is how much smaller this surface is
    than the real image, so a preview sharpens like the final result."""
    surface.flush()
    w, h = surface.get_width(), surface.get_height()
    # cairo stores premultiplied BGRA; Pillow's "BGRa" raw mode undoes that.
    image = Image.frombuffer("RGBA", (w, h), surface.get_data(), "raw", "BGRa", surface.get_stride(), 1)
    alpha = image.getchannel("A")
    image = image.convert("RGB")
    if any(adjust[k] for k in ("invert", "saturation", "hue", "temperature", "tint")):
        image = image.convert("RGB", matrix=color_matrix(adjust))
    if adjust["vibrance"]:
        image = apply_vibrance(image, adjust["vibrance"] / 100)
    if any(adjust[k] for k in ("brightness", "contrast", "shadows", "highlights", "whites", "blacks",
                               "shadow_tint", "highlight_tint")):
        image = image.point(tone_lut(adjust))
    if adjust["clarity"] < 0:
        # A soft glow.
        soft = image.filter(ImageFilter.GaussianBlur(max(w, h) / 150))
        image = Image.blend(image, soft, -adjust["clarity"] / 100 * 0.6)
    if adjust["clarity"] > 0 or adjust["sharpen"]:
        # Local contrast (a wide unsharp mask) and sharpening (a tight one), on
        # brightness only: doing it per color channel shifts hues and adds halos.
        y, cb, cr = image.convert("YCbCr").split()
        if adjust["clarity"] > 0:
            y = y.filter(ImageFilter.UnsharpMask(max(w, h) / 50, int(adjust["clarity"] * 0.8), 0))
        if adjust["sharpen"]:
            y = y.filter(ImageFilter.UnsharpMask(max(1.3 * scale, 0.6), adjust["sharpen"] * 2, 2))
        image = Image.merge("YCbCr", (y, cb, cr)).convert("RGB")
    if adjust["grain"]:
        image = ImageChops.overlay(image, grain_layer((w, h), adjust["grain"] / 100))
    image.putalpha(alpha)
    out = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    out.get_data()[:] = image.tobytes("raw", "BGRa")   # ARGB32 rows are exactly w * 4 bytes
    out.mark_dirty()
    if adjust["vignette"]:
        paint_vignette(out, adjust["vignette"] / 100, box or (0, 0, w, h))
    return out


def format_adjustment(value, low, unit):
    if low >= 0 or not value:
        return f"{value}{unit}"
    return f"{value:+d}{unit}"


def load_image_file(path):
    """The image at `path` as a cairo surface, the right way up.
    Raises OSError with a readable message if it can't be loaded."""
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(path)).apply_embedded_orientation()
        surface = Gdk.cairo_surface_create_from_pixbuf(pixbuf, 1, None)
        return cut_out(surface, 0, 0, pixbuf.get_width(), pixbuf.get_height())
    except GLib.Error as e:
        raise OSError("it isn't a readable image" if e.domain == "gdk-pixbuf-error-quark" else e.message) from e
    except cairo.Error as e:
        raise OSError("the image is too large") from e


def scaled_copy(surface, width, height):
    """A smaller copy of `surface`. Pillow is many times faster than cairo at this."""
    out = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    if Image is None:
        cr = cairo.Context(out)
        cr.scale(width / surface.get_width(), height / surface.get_height())
        cr.set_source_surface(surface, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_BEST)
        cr.paint()
    else:
        surface.flush()
        image = Image.frombuffer("RGBA", (surface.get_width(), surface.get_height()), surface.get_data(),
                                 "raw", "BGRA", surface.get_stride(), 1)
        out.get_data()[:] = image.resize((width, height), Image.BILINEAR, reducing_gap=2).tobytes("raw", "BGRA")
        out.mark_dirty()
    return out


def write_image(surface, path):
    if path.suffix.lower() in (".jpg", ".jpeg"):
        w, h = surface.get_width(), surface.get_height()
        flat = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
        cr = cairo.Context(flat)
        cr.set_source_rgb(1, 1, 1)
        cr.paint()
        cr.set_source_surface(surface, 0, 0)
        cr.paint()
        Gdk.pixbuf_get_from_surface(flat, 0, 0, w, h).savev(str(path), "jpeg", ["quality"], ["92"])
    else:
        surface.write_to_png(str(path))


def set_cursor(widget, name):
    window = widget.get_window()
    if window:
        window.set_cursor(Gdk.Cursor.new_from_name(widget.get_display(), name) if name else None)


# ---------------------------------------------------------------- overlay

class Overlay(Gtk.Window):
    """A frozen, dimmed copy of the screen where the snip is picked."""

    def __init__(self, shot, device_width, width, height, mode, windows, on_mode, on_done):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.shot = shot
        self.scale = device_width / width          # device px per logical px
        self.width, self.height = width, height
        self.mode, self.on_mode, self.on_done = mode, on_mode, on_done
        self.windows = [(x / self.scale, y / self.scale, w / self.scale, h / self.scale)
                        for x, y, w, h in windows]
        self.drag_start = None
        self.sel = None          # rectangle being dragged (x, y, w, h)
        self.path = []           # free-form outline
        self.hover = None        # window under the pointer
        self.finished = False

        self.move(0, 0)
        self.set_default_size(width, height)
        self.set_size_request(width, height)

        self.area = Gtk.DrawingArea()
        self.area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                             | Gdk.EventMask.POINTER_MOTION_MASK)
        self.area.connect("draw", self._draw)
        self.area.connect("button-press-event", self._press)
        self.area.connect("motion-notify-event", self._motion)
        self.area.connect("button-release-event", self._release)
        self.area.connect("realize", lambda a: set_cursor(a, "crosshair"))

        bar = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER, valign=Gtk.Align.START)
        bar.get_style_context().add_class("snip-bar")
        self.mode_buttons = {}
        group = None
        for key, label in MODES.items():
            button = Gtk.RadioButton.new_from_widget(group)
            group = group or button
            button.set_mode(False)
            button.set_tooltip_text(label)
            button.add(Glyph(key))
            button.set_active(key == mode)
            button.connect("toggled", self._mode_toggled, key)
            bar.pack_start(button, False, False, 0)
            self.mode_buttons[key] = button
        bar.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 0)
        close = Gtk.Button(tooltip_text="Cancel (Esc)")
        close.add(Glyph("close"))
        close.connect("clicked", lambda b: self._finish(None))
        bar.pack_start(close, False, False, 0)

        overlay = Gtk.Overlay()
        overlay.add(self.area)
        overlay.add_overlay(bar)
        self.add(overlay)
        self.connect("key-press-event", self._key)
        self.connect("map-event", lambda *a: self._grab(40))

    def _grab(self, tries):
        # Grab keyboard and pointer so Esc works; the hotkey that launched us may
        # still hold a grab for a moment, so retry briefly.
        if self.finished:
            return False
        seat = self.get_display().get_default_seat()
        status = seat.grab(self.get_window(), Gdk.SeatCapabilities.ALL, True, None, None, None, None)
        if status != Gdk.GrabStatus.SUCCESS and tries > 0:
            GLib.timeout_add(50, self._grab, tries - 1)
        if status == Gdk.GrabStatus.SUCCESS:
            _, x, y = seat.get_pointer().get_position()
            self._update_hover(x, y)
        return False

    def _mode_toggled(self, button, mode):
        if not button.get_active():
            return
        self.mode = mode
        self.sel, self.path, self.drag_start = None, [], None
        self.on_mode(mode)
        if mode == "full":
            self._finish(self._crop_rect(0, 0, self.width, self.height))
        else:
            self.area.queue_draw()

    def _key(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self._finish(None)
            return True
        return False

    def _update_hover(self, x, y):
        if self.mode != "window":
            return
        hover = next((r for r in self.windows if r[0] <= x < r[0] + r[2] and r[1] <= y < r[1] + r[3]),
                     (0, 0, self.width, self.height))
        if hover != self.hover:
            self.hover = hover
            self.area.queue_draw()

    def _press(self, area, event):
        if event.button != 1:
            return False
        if self.mode == "rect":
            self.drag_start = (event.x, event.y)
            self.sel = (event.x, event.y, 0, 0)
        elif self.mode == "free":
            self.drag_start = (event.x, event.y)
            self.path = [(event.x, event.y)]
        elif self.mode == "window":
            self._update_hover(event.x, event.y)
            self._finish(self._crop_rect(*self._clip(self.hover)))
        return True

    def _motion(self, area, event):
        x, y = min(max(event.x, 0), self.width), min(max(event.y, 0), self.height)
        if self.mode == "rect" and self.drag_start:
            sx, sy = self.drag_start
            self.sel = (min(sx, x), min(sy, y), abs(x - sx), abs(y - sy))
            self.area.queue_draw()
        elif self.mode == "free" and self.drag_start:
            lx, ly = self.path[-1]
            if math.hypot(x - lx, y - ly) >= 2:
                self.path.append((x, y))
                self.area.queue_draw()
        elif self.mode == "window":
            self._update_hover(x, y)
        return True

    def _release(self, area, event):
        if event.button != 1 or not self.drag_start:
            return False
        self.drag_start = None
        if self.mode == "rect":
            x, y, w, h = self.sel
            if w >= 2 and h >= 2:
                self._finish(self._crop_rect(x, y, w, h))
                return True
            self.sel = None
        elif self.mode == "free":
            xs, ys = [p[0] for p in self.path], [p[1] for p in self.path]
            if len(self.path) >= 3 and max(xs) - min(xs) >= 2 and max(ys) - min(ys) >= 2:
                self._finish(self._crop_path(self.path))
                return True
            self.path = []
        self.area.queue_draw()
        return True

    def _clip(self, rect):
        x, y, w, h = rect
        x0, y0 = max(x, 0), max(y, 0)
        x1, y1 = min(x + w, self.width), min(y + h, self.height)
        return x0, y0, max(x1 - x0, 1), max(y1 - y0, 1)

    def _device_box(self, x0, y0, x1, y1):
        s = self.scale
        dw, dh = self.shot.get_width(), self.shot.get_height()
        X0, Y0 = max(int(math.floor(x0 * s)), 0), max(int(math.floor(y0 * s)), 0)
        X1, Y1 = min(int(math.ceil(x1 * s)), dw), min(int(math.ceil(y1 * s)), dh)
        return X0, Y0, max(X1 - X0, 1), max(Y1 - Y0, 1)

    def _crop_rect(self, x, y, w, h):
        return cut_out(self.shot, *self._device_box(x, y, x + w, y + h))

    def _crop_path(self, path):
        xs, ys = [p[0] for p in path], [p[1] for p in path]
        box = self._device_box(min(xs), min(ys), max(xs), max(ys))
        return cut_out(self.shot, *box, outline=path, scale=self.scale)

    def _finish(self, surface):
        if self.finished:
            return
        self.finished = True
        self.get_display().get_default_seat().ungrab()
        self.destroy()
        self.on_done(surface)

    def _selection(self):
        """The undimmed area as a list of points, or a rect, or None."""
        if self.mode == "rect" and self.sel:
            return self.sel
        if self.mode == "window" and self.hover:
            return self.hover
        return None

    def _draw(self, area, cr):
        cr.save()
        cr.scale(1 / self.scale, 1 / self.scale)
        cr.set_source_surface(self.shot, 0, 0)
        cr.paint()
        cr.restore()

        rect = self._selection()
        cr.new_path()
        cr.rectangle(0, 0, self.width, self.height)
        if rect:
            cr.rectangle(*rect)
        elif self.mode == "free" and len(self.path) > 1:
            cr.move_to(*self.path[0])
            for p in self.path[1:]:
                cr.line_to(*p)
            cr.close_path()
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        cr.set_source_rgba(*rgb(BG, 0.6))
        cr.fill()

        cr.set_source_rgb(*rgb(ACCENT))
        if self.mode == "rect" and rect:
            x, y, w, h = rect
            cr.set_line_width(2)
            cr.rectangle(round(x), round(y), round(w), round(h))
            cr.stroke()
            self._draw_size_label(cr, x, y, w, h)
        elif self.mode == "window" and rect:
            x, y, w, h = self._clip(rect)
            cr.set_line_width(3)
            cr.rectangle(x + 1.5, y + 1.5, w - 3, h - 3)
            cr.stroke()
        elif self.mode == "free" and len(self.path) > 1:
            cr.set_line_width(2)
            cr.set_line_join(cairo.LINE_JOIN_ROUND)
            cr.move_to(*self.path[0])
            for p in self.path[1:]:
                cr.line_to(*p)
            cr.stroke()

    def _draw_size_label(self, cr, x, y, w, h):
        text = f"{round(w * self.scale)} × {round(h * self.scale)}"
        cr.select_font_face(BODY_FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(12)
        ext = cr.text_extents(text)
        bw, bh = ext.x_advance + 16, 24
        bx = min(max(x + w - bw, 4), self.width - bw - 4)
        by = y + h + 6 if y + h + 6 + bh < self.height else max(y - bh - 6, 4)
        rounded_rect(cr, bx + 0.5, by + 0.5, bw - 1, bh - 1, 3)
        cr.set_source_rgba(*rgb(BG_ALT, 0.95))
        cr.fill_preserve()
        cr.set_line_width(1)
        cr.set_source_rgb(*rgb(BORDER))
        cr.stroke()
        cr.set_source_rgb(*rgb(ACCENT))
        cr.move_to(bx + 8, by + bh / 2 - ext.y_bearing - ext.height / 2)
        cr.show_text(text)


# ---------------------------------------------------------------- editor

class Editor(Gtk.ApplicationWindow):
    """The main window: snip controls, the annotated snip, copy and save."""

    def __init__(self, app):
        super().__init__(application=app, title="Snipping Tool")
        self.app = app
        self.settings = app.settings
        self.set_default_size(760, 480)

        self.base = None            # the captured image (cairo.ImageSurface)
        self.adjust = dict(NEUTRAL)  # color adjustments, replaced (not mutated) on change
        self.adjusted = None        # base with the adjustments applied (None: not computed yet)
        self.proxy = self.preview = None   # large images: a smaller base, and that recolored
        self.source_path = None     # the opened file, or None for a snip
        self.adjust_source = self.adjust_group = None
        self._preferred_preset = None  # shown when several presets match the values
        self.panel_grew = 0         # how much opening the side panel widened the window
        self.crop = None            # visible part of base, (x, y, w, h)
        self.strokes = []           # replaced, never mutated, so undo can share lists
        self.undo_stack, self.redo_stack = [], []
        self.tool = None            # None, "pen", "highlighter", "eraser" or "crop"
        self.current = None         # stroke being drawn
        self.erase_snapshot = None
        self.crop_box = None        # pending crop while the crop tool is active
        self.crop_drag = None
        self.pan_start = None
        self.pointer = None
        self.zoom, self.fit = 1.0, True
        self.capturing = False
        self.restore_position = None
        self.copy_source = self.toast_source = None
        self.checker = checker_pattern()

        self._build_actions()
        self._build_ui()
        self.connect("key-press-event", self._key)
        self.connect("delete-event", self._on_delete)
        self._update_actions()

    # ---- setup

    def _build_actions(self):
        def add(name, callback, state=None, param=None):
            if state is None:
                action = Gio.SimpleAction.new(name, param)
                action.connect("activate", lambda a, p: callback())
            else:
                action = Gio.SimpleAction.new_stateful(name, param, state)
                action.connect("change-state", callback)
            self.add_action(action)

        add("new", self.start_snip)
        add("open", self.open_image)
        add("copy", lambda: self.copy())
        add("save", self.save)
        add("undo", self.undo)
        add("redo", self.redo)
        add("zoom-in", lambda: self._set_zoom(self.zoom * 1.25))
        add("zoom-out", lambda: self._set_zoom(self.zoom / 1.25))
        add("zoom-fit", lambda: self._set_zoom(None))
        add("zoom-toggle", lambda: self._set_zoom(None if not self.fit and self.zoom == 1 else 1))
        add("open-folder", self._open_folder)
        add("help", self._show_help)
        add("mode", self._on_mode, GLib.Variant("s", self.settings["mode"]), GLib.VariantType("s"))
        add("delay", self._on_delay, GLib.Variant("s", str(self.settings["delay"])), GLib.VariantType("s"))
        add("auto-copy", self._on_toggle_setting, GLib.Variant("b", self.settings["auto_copy"]))
        add("auto-save", self._on_toggle_setting, GLib.Variant("b", self.settings["auto_save"]))
        add("shortcut", self._on_shortcut, GLib.Variant("b", find_shortcut() is not None))
        self.lookup_action("shortcut").set_enabled(shortcut_supported())

    def _build_ui(self):
        toolbar = Gtk.Box(spacing=12)
        self._add_classes(toolbar, "panel", "snip-toolbar")
        self.toolbar = toolbar

        new = Gtk.Button(action_name="win.new", tooltip_text="New snip (Ctrl+N)")
        box = Gtk.Box(spacing=4)
        box.pack_start(Gtk.Image.new_from_icon_name("list-add-symbolic", Gtk.IconSize.BUTTON), False, False, 0)
        box.pack_start(Gtk.Label(label="New"), False, False, 0)
        new.add(box)

        mode_menu = Gio.Menu()
        for key, label in MODES.items():
            mode_menu.append(label, f"win.mode::{key}")
        self.mode_glyph = Glyph(self.settings["mode"])
        self.mode_button = self._menu_button(mode_menu, self.mode_glyph, "Snipping mode")

        delay_menu = Gio.Menu()
        for d in DELAYS:
            delay_menu.append("No delay" if d == 0 else f"Delay {d} seconds", f"win.delay::{d}")
        self.delay_label = Gtk.Label(no_show_all=True)
        self._add_classes(self.delay_label, "value")
        delay_box = Gtk.Box(spacing=3)
        delay_box.pack_start(Glyph("timer"), False, False, 0)
        delay_box.pack_start(self.delay_label, False, False, 0)
        self.delay_button = self._menu_button(delay_menu, delay_box, "Delay before snipping")
        self._update_delay_label()

        open_button = Gtk.Button(action_name="win.open", tooltip_text="Open an image (Ctrl+O)")
        box = Gtk.Box(spacing=4)
        box.pack_start(Gtk.Image.new_from_icon_name("document-open-symbolic", Gtk.IconSize.BUTTON), False, False, 0)
        box.pack_start(Gtk.Label(label="Open"), False, False, 0)
        open_button.add(box)

        snip_group = self._group(new, open_button, self.mode_button, self.delay_button)

        self.tool_buttons = {}
        tips = {"pen": "Pen (click again for colors)", "highlighter": "Highlighter (click again for colors)",
                "eraser": "Eraser: removes whole strokes", "crop": "Crop"}
        for tool in ("pen", "highlighter", "eraser", "crop"):
            button = Gtk.ToggleButton(tooltip_text=tips[tool])
            button.add(Glyph(tool, accent=self._tool_accent(tool)))
            button.connect("toggled", self._on_tool_toggled, tool)
            self.tool_buttons[tool] = button
        self._syncing_tools = False
        self.popovers = {t: self._ink_popover(t) for t in ("pen", "highlighter")}
        tool_group = self._group(*self.tool_buttons.values())

        history_group = self._group(
            self._icon_button("edit-undo-symbolic", "win.undo", "Undo (Ctrl+Z)"),
            self._icon_button("edit-redo-symbolic", "win.redo", "Redo (Ctrl+Y)"))

        self.zoom_label = Gtk.Label(label="100%", width_chars=5)
        self._add_classes(self.zoom_label, "value")
        zoom_reset = Gtk.Button(action_name="win.zoom-toggle", tooltip_text="Fit to window / actual size")
        zoom_reset.add(self.zoom_label)
        zoom_group = self._group(
            self._icon_button("zoom-out-symbolic", "win.zoom-out", "Zoom out (Ctrl+-)"),
            zoom_reset,
            self._icon_button("zoom-in-symbolic", "win.zoom-in", "Zoom in (Ctrl++)"))

        main_menu = Gio.Menu()
        section = Gio.Menu()
        section.append("Copy snips to the clipboard automatically", "win.auto-copy")
        section.append("Save snips to Pictures/Screenshots automatically", "win.auto-save")
        main_menu.append_section(None, section)
        section = Gio.Menu()
        section.append("Shift+Super+S starts a snip", "win.shortcut")
        main_menu.append_section(None, section)
        section = Gio.Menu()
        section.append("Open Screenshots folder", "win.open-folder")
        section.append("Keyboard shortcuts", "win.help")
        main_menu.append_section(None, section)
        menu_button = Gtk.MenuButton(menu_model=main_menu, tooltip_text="Settings")
        menu_button.add(Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON))

        output_group = self._group(
            self._icon_button("edit-copy-symbolic", "win.copy", "Copy (Ctrl+C)"),
            self._icon_button("document-save-symbolic", "win.save", "Save as (Ctrl+S)"))

        self.adjust_button = Gtk.ToggleButton(tooltip_text="Colors, hue, contrast and presets")
        self.adjust_button.add(Glyph("adjust"))
        self.adjust_button.connect("toggled", self._on_side_panel_toggled)
        if Image is None:
            self.adjust_button.set_sensitive(False)
            self.adjust_button.set_tooltip_text("Color adjustments need Pillow (sudo apt install python3-pil)")
        side_group = self._group(self.adjust_button, menu_button)

        for widget in (snip_group, tool_group, history_group):
            toolbar.pack_start(widget, False, False, 0)
        for widget in (side_group, output_group, zoom_group):
            toolbar.pack_end(widget, False, False, 0)
        self.editing_widgets = (tool_group, history_group, zoom_group, output_group, self.adjust_button)

        # crop bar
        cropbar = Gtk.Box(spacing=8)
        self._add_classes(cropbar, "panel", "snip-cropbar")
        header = Gtk.Label(label="Crop:")
        self._add_classes(header, "section-header")
        hint = Gtk.Label(label="Drag the edges, or draw a new area.")
        self._add_classes(hint, "dim")
        cropbar.pack_start(header, False, False, 0)
        cropbar.pack_start(hint, False, False, 4)
        apply_button = Gtk.Button(label="Apply")
        apply_button.get_style_context().add_class("suggested-action")
        apply_button.connect("clicked", lambda b: self._leave_crop(apply=True))
        cancel_button = Gtk.Button(label="Cancel")
        cancel_button.connect("clicked", lambda b: self._leave_crop(apply=False))
        cropbar.pack_end(apply_button, False, False, 0)
        cropbar.pack_end(cancel_button, False, False, 0)
        self.cropbar = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
                                    transition_duration=120)
        self.cropbar.add(cropbar)

        # canvas
        self.area = Gtk.DrawingArea(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.area.set_margin_start(CANVAS_MARGIN)
        self.area.set_margin_end(CANVAS_MARGIN)
        self.area.set_margin_top(CANVAS_MARGIN)
        self.area.set_margin_bottom(CANVAS_MARGIN)
        self.area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                             | Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
                             | Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)
        self.area.connect("draw", self._draw)
        self.area.connect("button-press-event", self._press)
        self.area.connect("motion-notify-event", self._motion)
        self.area.connect("button-release-event", self._release)
        self.area.connect("leave-notify-event", self._leave)
        self.area.connect("scroll-event", self._scroll)
        self.scroller = Gtk.ScrolledWindow()
        self.scroller.add(self.area)
        self.scroller.get_child().set_shadow_type(Gtk.ShadowType.NONE)
        self.scroller.connect("size-allocate", self._on_scroller_allocate)

        # empty state
        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                        halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        if ICON_FILE.exists():
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(str(ICON_FILE), 96, 96)
            empty.pack_start(Gtk.Image.new_from_pixbuf(pixbuf), False, False, 0)
        title = Gtk.Label(label="Snipping Tool")
        self._add_classes(title, "app-title")
        empty.pack_start(title, False, False, 0)
        hint = Gtk.Label(justify=Gtk.Justification.CENTER)
        hint.set_markup("Press <b>Ctrl+N</b> to take a snip, or <b>Ctrl+O</b> to open an image.\n"
                        "Choose the snip mode and delay with the buttons in the toolbar.")
        self._add_classes(hint, "dim")
        empty.pack_start(hint, False, False, 0)
        start = Gtk.Button(label="New snip", action_name="win.new")
        start.get_style_context().add_class("suggested-action")
        starters = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        starters.pack_start(start, False, False, 0)
        starters.pack_start(Gtk.Button(label="Open image", action_name="win.open"), False, False, 0)
        empty.pack_start(starters, False, False, 8)

        self.stack = Gtk.Stack()
        self.stack.add_named(empty, "empty")
        self.stack.add_named(self.scroller, "canvas")

        self.toast_label = Gtk.Label(wrap=True, max_width_chars=70, justify=Gtk.Justification.CENTER)
        toast_box = Gtk.Box()
        toast_box.get_style_context().add_class("snip-toast")
        toast_box.add(self.toast_label)
        self.toast = Gtk.Revealer(halign=Gtk.Align.CENTER, valign=Gtk.Align.END,
                                  transition_type=Gtk.RevealerTransitionType.CROSSFADE)
        self.toast.add(toast_box)
        overlay = Gtk.Overlay()
        overlay.add(self.stack)
        overlay.add_overlay(self.toast)
        overlay.set_overlay_pass_through(self.toast, True)
        canvas_panel = Gtk.Box()
        self._add_classes(canvas_panel, "panel", "snip-content")
        canvas_panel.pack_start(overlay, True, True, 0)
        self.side_panel = self._build_side_panel()
        content = Gtk.Box()
        content.pack_start(canvas_panel, True, True, 0)
        content.pack_start(self.side_panel, False, False, 0)

        # The panel gap lives in each panel's bottom margin, so the crop bar
        # takes its gap with it when it slides away.
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        vbox.set_margin_start(OUTER_MARGIN[0])
        vbox.set_margin_end(OUTER_MARGIN[0])
        vbox.set_margin_top(OUTER_MARGIN[1])
        vbox.set_margin_bottom(OUTER_MARGIN[1])
        vbox.pack_start(toolbar, False, False, 0)
        vbox.pack_start(self.cropbar, False, False, 0)
        vbox.pack_start(content, True, True, 0)
        self.add(vbox)
        vbox.show_all()

    def _build_side_panel(self):
        """The right-hand column: color adjustments above, presets below,
        laid out like MetalEQ's effect and preset panels."""
        adjust_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._add_classes(adjust_panel, "panel", "snip-adjust")
        header = Gtk.Label(label="Adjust:", xalign=0,
                           tooltip_text="Double-click a slider to reset it")
        self._add_classes(header, "section-header")
        adjust_panel.pack_start(header, False, False, 0)

        grid = Gtk.Grid(column_spacing=8, row_spacing=7)
        grid.set_margin_end(10)     # room for the scrollbar
        self.adjust_scales, self.adjust_values = {}, {}
        self._syncing_adjust = False
        self.invert_check = Gtk.CheckButton(label="Invert colors")
        self._add_classes(self.invert_check, "option")
        self.invert_check.connect("toggled", self._on_invert_toggled)
        row = 0
        for key, label, low, high, unit in ADJUSTMENTS:
            if key in ADJUST_HEADINGS:
                heading = Gtk.Label(label=ADJUST_HEADINGS[key], xalign=0)
                self._add_classes(heading, "adjust-heading")
                grid.attach(heading, 0, row, 3, 1)
                row += 1
            name = Gtk.Label(label=label, xalign=0)
            self._add_classes(name, "adjust-label")
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, low, high, 1)
            scale.set_value(NEUTRAL[key])      # new_with_range starts at the minimum
            scale.set_draw_value(False)
            scale.set_hexpand(True)
            if key in HUE_SLIDERS:
                scale.set_has_origin(False)
                self._add_classes(scale, "hue-scale")
            scale.connect("value-changed", self._on_adjust_scale, key)
            scale.connect("button-press-event", self._on_adjust_scale_press, key)
            scale.connect("scroll-event", self._on_adjust_scale_scroll)
            value = Gtk.Label(label=format_adjustment(NEUTRAL[key], low, unit), xalign=1, width_chars=5)
            self._add_classes(value, "value", "adjust-value")
            grid.attach(name, 0, row, 1, 1)
            grid.attach(scale, 1, row, 1, 1)
            grid.attach(value, 2, row, 1, 1)
            self.adjust_scales[key], self.adjust_values[key] = scale, value
            row += 1
            if key == "tint":
                grid.attach(self.invert_check, 0, row, 3, 1)
                row += 1
        # Scrolls rather than demanding its full height, so the column can't
        # push the window past the bottom of a small screen.
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                      min_content_height=80, propagate_natural_height=True)
        scroller.add(grid)
        scroller.get_child().set_shadow_type(Gtk.ShadowType.NONE)
        adjust_panel.pack_start(scroller, True, True, 0)

        preset_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._add_classes(preset_panel, "panel", "snip-presets")
        header_row = Gtk.Box(spacing=8)
        header = Gtk.Label(label="Preset:", xalign=0)
        self._add_classes(header, "section-header")
        self.default_label = Gtk.Label(xalign=1)
        self._add_classes(self.default_label, "status")
        header_row.pack_start(header, False, False, 0)
        header_row.pack_end(self.default_label, False, False, 0)
        preset_panel.pack_start(header_row, False, False, 0)
        self.preset_combo = Gtk.ComboBoxText()
        # By default GTK lines the selected item up over the box, which leaves a
        # blank gap above the items when the box is low on the screen. With a
        # wrap width the list opens below the box, or above when there's no room.
        self.preset_combo.set_wrap_width(2)     # two columns keep the long list on screen
        self.preset_combo.get_cells()[0].set_padding(9, 6)
        self._syncing_presets = False
        self._shown_preset = None
        self.preset_combo.connect("changed", self._on_preset_chosen)
        preset_panel.pack_start(self.preset_combo, False, False, 0)
        buttons = Gtk.Box(spacing=6, homogeneous=True)
        for label, handler in (("Save As…", self._save_preset_as), ("Delete", self._delete_preset),
                               ("Reset", lambda b: self._set_adjust(NEUTRAL))):
            button = Gtk.Button(label=label)
            button.connect("clicked", handler)
            buttons.pack_start(button, True, True, 0)
            if label == "Delete":
                self.delete_preset_button = button
        preset_panel.pack_start(buttons, False, False, 0)
        self.default_button = Gtk.Button(label="Set as Default", halign=Gtk.Align.END,
                                         tooltip_text="Use this preset for every new snip")
        self.default_button.connect("clicked", self._set_default_preset)
        preset_panel.pack_start(self.default_button, False, False, 0)

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._add_classes(column, "snip-side")
        column.set_size_request(ADJUST_PANEL_WIDTH, -1)
        column.pack_start(adjust_panel, True, True, 0)
        column.pack_start(preset_panel, False, False, 0)
        # hexpand=False stops the sliders' hexpand from propagating up and
        # making the box share the canvas's spare width with this column.
        revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT,
                                transition_duration=150, hexpand=False)
        revealer.add(column)
        self._refresh_presets()
        return revealer

    @staticmethod
    def _add_classes(widget, *classes):
        context = widget.get_style_context()
        for name in classes:
            context.add_class(name)

    @staticmethod
    def _group(*widgets):
        box = Gtk.Box(spacing=4)
        for w in widgets:
            box.pack_start(w, False, False, 0)
        return box

    @staticmethod
    def _icon_button(icon, action, tooltip):
        button = Gtk.Button(action_name=action, tooltip_text=tooltip)
        button.add(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.BUTTON))
        return button

    @staticmethod
    def _menu_button(model, content, tooltip):
        button = Gtk.MenuButton(menu_model=model, tooltip_text=tooltip)
        box = Gtk.Box(spacing=2)
        box.pack_start(content, False, False, 0)
        box.pack_start(Gtk.Image.new_from_icon_name("pan-down-symbolic", Gtk.IconSize.BUTTON), False, False, 0)
        button.add(box)
        return button

    def _ink_popover(self, tool):
        popover = Gtk.Popover(relative_to=self.tool_buttons[tool])
        grid = Gtk.Grid(row_spacing=4, column_spacing=4, margin=10)
        colors = PEN_COLORS if tool == "pen" else HIGHLIGHTER_COLORS
        swatches = []

        def draw_swatch(area, cr, color):
            w, h = area.get_allocated_width(), area.get_allocated_height()
            r = min(w, h) / 2
            selected = tuple(self.settings[f"{tool}_color"][:3]) == color
            if selected:
                cr.set_source_rgb(*rgb(ACCENT))
                cr.arc(w / 2, h / 2, r, 0, 2 * math.pi)
                cr.fill()
            cr.set_source_rgb(*color)
            cr.arc(w / 2, h / 2, r - (4 if selected else 1.5), 0, 2 * math.pi)
            cr.fill_preserve()
            cr.set_source_rgb(*rgb(BG) if selected else rgb(BORDER))
            cr.set_line_width(1.5 if selected else 1)
            cr.stroke()

        def pick(button, color):
            self.settings[f"{tool}_color"] = [*color, 1.0]
            save_settings(self.settings)
            self.tool_buttons[tool].get_child().set_glyph(tool, self._tool_accent(tool))
            for s in swatches:
                s.queue_draw()

        for i, color in enumerate(colors):
            area = Gtk.DrawingArea()
            area.set_size_request(24, 24)
            area.connect("draw", draw_swatch, color)
            swatches.append(area)
            button = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
            button.get_style_context().add_class("swatch")
            button.add(area)
            button.connect("clicked", pick, color)
            grid.attach(button, i % 6, i // 6, 1, 1)

        low, high = WIDTH_RANGE[tool]
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, low, high, 1)
        scale.set_value(self.settings[f"{tool}_width"])
        scale.set_digits(0)
        scale.set_value_pos(Gtk.PositionType.RIGHT)
        scale.set_hexpand(True)

        def on_width(s):
            self.settings[f"{tool}_width"] = int(s.get_value())
            save_settings(self.settings)

        scale.connect("value-changed", on_width)
        row = len(colors) // 6 + 1
        size_label = Gtk.Label(label="Size", xalign=0)
        self._add_classes(size_label, "dim")
        grid.attach(size_label, 0, row, 2, 1)
        grid.attach(scale, 0, row + 1, 6, 1)
        grid.show_all()
        popover.add(grid)
        return popover

    def _tool_accent(self, tool):
        if tool in ("pen", "highlighter"):
            return tuple(self.settings[f"{tool}_color"])
        return None

    # ---- actions

    def _on_mode(self, action, value):
        action.set_state(value)
        self.settings["mode"] = value.get_string()
        save_settings(self.settings)
        self.mode_glyph.set_glyph(self.settings["mode"])

    def _on_delay(self, action, value):
        action.set_state(value)
        self.settings["delay"] = int(value.get_string())
        save_settings(self.settings)
        self._update_delay_label()

    def _update_delay_label(self):
        delay = self.settings["delay"]
        self.delay_label.set_text(f"{delay}s" if delay else "")
        self.delay_label.set_visible(bool(delay))

    def _on_toggle_setting(self, action, value):
        action.set_state(value)
        self.settings[action.get_name().replace("-", "_")] = value.get_boolean()
        save_settings(self.settings)

    def _on_shortcut(self, action, value):
        try:
            set_shortcut(value.get_boolean())
        except GLib.Error as e:
            self.show_toast(f"Couldn't change the shortcut: {e.message}")
            return
        action.set_state(GLib.Variant("b", find_shortcut() is not None))
        self.show_toast("Shift+Super+S now starts a snip" if value.get_boolean() else "Shortcut removed")

    def _update_actions(self):
        has_image = self.base is not None
        for name in ("copy", "save", "zoom-in", "zoom-out", "zoom-fit", "zoom-toggle"):
            self.lookup_action(name).set_enabled(has_image)
        self.lookup_action("undo").set_enabled(bool(self.undo_stack) and self.tool != "crop")
        self.lookup_action("redo").set_enabled(bool(self.redo_stack) and self.tool != "crop")
        for widget in self.editing_widgets:
            widget.set_sensitive(has_image)

    def _open_folder(self):
        folder = screenshots_dir()
        folder.mkdir(parents=True, exist_ok=True)
        Gio.AppInfo.launch_default_for_uri(folder.as_uri(), None)

    def _show_help(self):
        dialog = Gtk.MessageDialog(transient_for=self, modal=True, buttons=Gtk.ButtonsType.CLOSE,
                                   message_type=Gtk.MessageType.OTHER,
                                   text="Keyboard shortcuts")
        dialog.format_secondary_markup(
            "<b>Ctrl+N</b>\tNew snip\n"
            "<b>Ctrl+O</b>\tOpen an image\n"
            "<b>Esc</b>\tCancel a snip, a crop, or put the tool down\n"
            "<b>Enter</b>\tApply the crop\n"
            "<b>Ctrl+C</b>\tCopy\n"
            "<b>Ctrl+S</b>\tSave as\n"
            "<b>Ctrl+Z</b>\tUndo\n"
            "<b>Ctrl+Y</b>\tRedo\n"
            "<b>Ctrl++ / Ctrl+-</b>\tZoom in / out (or Ctrl+scroll)\n"
            "<b>Ctrl+0</b>\tFit to window\n\n"
            "Turn on <b>Shift+Super+S</b> in the menu to snip from anywhere, or bind\n"
            f"<tt>{GLib.markup_escape_text(str(SCRIPT))} --snip</tt>\nto a key of your choice.")
        dialog.run()
        dialog.destroy()

    # ---- capture

    def start_snip(self, mode=None, delay=None):
        if self.capturing:
            return
        mode = mode or self.settings["mode"]
        delay = self.settings["delay"] if delay is None else delay
        if self.tool == "crop":
            self._leave_crop(apply=True)
        self.capturing = True
        self.app.hold()
        was_visible = self.get_visible()
        if was_visible:
            self.restore_position = self.get_position()
            self.hide()
        # Give the compositor a moment to take our window off the screen.
        GLib.timeout_add(int(delay * 1000) + (300 if was_visible else 100), self._capture, mode)

    def _capture(self, mode):
        try:
            shot, device_width, width, height = grab_screen()
        except Exception as e:
            print(f"snipping-tool: screen grab failed: {e}", file=sys.stderr)
            self._snip_done(None)
            return False
        if mode == "full":
            self._snip_done(cut_out(shot, 0, 0, device_width, shot.get_height()))
        else:
            Overlay(shot, device_width, width, height, mode, list_windows(),
                    self._overlay_mode, self._snip_done).show_all()
        return False

    def _overlay_mode(self, mode):
        self.lookup_action("mode").change_state(GLib.Variant("s", mode))

    def _snip_done(self, surface):
        self.capturing = False
        if surface is not None:
            self.load_snip(surface)
        if surface is not None or self.base is not None or self.restore_position is not None:
            self._show_again()
        else:
            # Launched by --snip and cancelled: nothing to show.
            self.destroy()
        self.app.release()
        return False

    def _show_again(self):
        if self.restore_position is not None:
            self.move(*self.restore_position)
        self.present()

    def load_snip(self, surface, path=None):
        """Show a new image: a fresh snip, or the file at `path`."""
        self.base = surface
        self.source_path = path
        self.set_title(f"{path.name} – Snipping Tool" if path else "Snipping Tool")
        width, height = surface.get_width(), surface.get_height()
        self.crop = (0, 0, width, height)
        self.proxy = self.preview = None
        if width * height > PREVIEW_PIXELS:
            shrink = math.sqrt(PREVIEW_PIXELS / (width * height))
            self.proxy = scaled_copy(surface, max(round(width * shrink), 1), max(round(height * shrink), 1))
        self.strokes, self.undo_stack, self.redo_stack = [], [], []
        self.current = None
        self._end_adjust_group()
        # The default preset is for snips; an opened image starts as it is.
        self.adjust = dict(NEUTRAL) if path else self._presets().get(self.settings["default_preset"], dict(NEUTRAL))
        self._sync_adjust_widgets()
        self._refresh_presets()
        self._update_adjusted()
        self.stack.set_visible_child_name("canvas")
        self.fit = True
        self._fit_window()
        self._resize_area()
        self._update_actions()
        if path:
            return      # opening a file shouldn't touch the clipboard or save a copy
        messages = []
        if self.settings["auto_copy"]:
            self.copy(quiet=True)
            messages.append("Copied to clipboard")
        if self.settings["auto_save"]:
            path = self._save_to(screenshots_dir() / default_filename())
            if path:
                messages.append(f"saved to {path.parent.name}/{path.name}")
        if messages:
            text = " and ".join(messages)
            self.show_toast(text[0].upper() + text[1:])

    def open_image(self):
        if self.capturing:
            return
        dialog = Gtk.FileChooserDialog(title="Open image", transient_for=self, action=Gtk.FileChooserAction.OPEN)
        dialog.add_buttons("_Cancel", Gtk.ResponseType.CANCEL, "_Open", Gtk.ResponseType.ACCEPT)
        dialog.set_default_response(Gtk.ResponseType.ACCEPT)
        dialog.get_widget_for_response(Gtk.ResponseType.ACCEPT).get_style_context().add_class("suggested-action")
        last_dir = pathlib.Path(self.settings["last_dir"]) if self.settings["last_dir"] else None
        folder = last_dir if last_dir and last_dir.is_dir() else screenshots_dir()
        if not folder.is_dir():
            folder = pathlib.Path(GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES) or pathlib.Path.home())
        dialog.set_current_folder(str(folder))
        images = Gtk.FileFilter()
        images.set_name("Images")
        images.add_pixbuf_formats()
        dialog.add_filter(images)
        everything = Gtk.FileFilter()
        everything.set_name("All files")
        everything.add_pattern("*")
        dialog.add_filter(everything)
        response = dialog.run()
        filename = dialog.get_filename()
        dialog.destroy()
        if response != Gtk.ResponseType.ACCEPT or not filename:
            return
        while Gtk.events_pending():     # take the dialog down before a big image loads
            Gtk.main_iteration()
        path = pathlib.Path(filename)
        try:
            surface = load_image_file(path)
        except OSError as e:
            self.show_toast(f"Couldn't open {path.name}: {e}")
            return
        if self.tool == "crop":
            self._leave_crop(apply=False)
        self.settings["last_dir"] = str(path.parent)
        save_settings(self.settings)
        self.load_snip(surface, path)
        self.show_toast(f"Opened {path.name}")

    def _workarea(self):
        display = self.get_display()
        window = self.get_window()
        monitor = (display.get_monitor_at_window(window) if window else None) \
            or display.get_primary_monitor() or display.get_monitor(0)
        return monitor.get_workarea()

    def _fit_window(self):
        if self.is_maximized():
            return
        area = self._workarea()
        _, toolbar_h = self.toolbar.get_preferred_height()
        _, toolbar_w = self.toolbar.get_preferred_width()
        img_w, img_h = self.crop[2], self.crop[3]
        # Window margins, the content panel's border and padding, the snip's
        # frame, and a little slack so "fit" lands on 100% when it can.
        canvas = 2 * (CANVAS_MARGIN + SNIP_FRAME + 2) + 6
        side = ADJUST_PANEL_WIDTH + PANEL_GAP if self.side_panel.get_reveal_child() else 0
        want_w = max(toolbar_w + 2 * OUTER_MARGIN[0], 640, img_w + canvas + side + 2 * OUTER_MARGIN[0])
        want_h = max(420, img_h + canvas + toolbar_h + PANEL_GAP + 2 * OUTER_MARGIN[1])
        self.panel_grew = side      # closing the panel gives that width back
        self.resize(min(want_w, int(area.width * 0.92)), min(want_h, int(area.height * 0.92)))

    # ---- output

    def render(self):
        x, y, w, h = self.crop
        out = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(out)
        cr.translate(-x, -y)
        self._paint_image(cr, cairo.FILTER_NEAREST)
        return out

    def copy(self, quiet=False):
        if self.base is None:
            return
        surface = self.render()
        pixbuf = Gdk.pixbuf_get_from_surface(surface, 0, 0, surface.get_width(), surface.get_height())
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_image(pixbuf)
        if not quiet:
            self.show_toast("Copied to clipboard")

    def save(self):
        if self.base is None:
            return
        source = self.source_path
        dialog = Gtk.FileChooserDialog(title="Save image" if source else "Save snip", transient_for=self,
                                       action=Gtk.FileChooserAction.SAVE)
        dialog.add_buttons("_Cancel", Gtk.ResponseType.CANCEL, "_Save", Gtk.ResponseType.ACCEPT)
        dialog.set_default_response(Gtk.ResponseType.ACCEPT)
        dialog.set_do_overwrite_confirmation(True)
        if source:
            folder = source.parent
        else:
            folder = pathlib.Path(self.settings["last_dir"]) if self.settings["last_dir"] else screenshots_dir()
        if not folder.is_dir():
            folder = screenshots_dir()
            folder.mkdir(parents=True, exist_ok=True)
        dialog.set_current_folder(str(folder))
        # An opened image is offered under a new name, beside the original.
        ext = ".jpg" if source and source.suffix.lower() in (".jpg", ".jpeg") else ".png"
        dialog.set_current_name(f"{source.stem} edited{ext}" if source else default_filename())
        filters = {}
        for name, patterns, file_ext in (("PNG image", ("*.png",), ".png"), ("JPEG image", ("*.jpg", "*.jpeg"), ".jpg")):
            f = Gtk.FileFilter()
            f.set_name(name)
            for p in patterns:
                f.add_pattern(p)
            dialog.add_filter(f)
            filters[name] = file_ext
            if file_ext == ext:
                dialog.set_filter(f)

        def on_filter(d, _):
            chosen = d.get_filter()
            if chosen is None:
                return
            name = pathlib.Path(d.get_current_name() or default_filename())
            d.set_current_name(name.stem + filters[chosen.get_name()])

        dialog.connect("notify::filter", on_filter)
        if dialog.run() == Gtk.ResponseType.ACCEPT:
            path = pathlib.Path(dialog.get_filename())
            if path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
                chosen = dialog.get_filter()
                path = path.with_name(path.name + (filters[chosen.get_name()] if chosen else ".png"))
            dialog.destroy()
            if self._save_to(path):
                self.settings["last_dir"] = str(path.parent)
                save_settings(self.settings)
                self.show_toast(f"Saved {path.name}")
        else:
            dialog.destroy()

    def _save_to(self, path):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_image(self.render(), path)
            return path
        except (OSError, GLib.Error, cairo.Error) as e:
            self.show_toast(f"Couldn't save {path.name}: {getattr(e, 'message', e)}")
            return None

    def show_toast(self, text):
        self.toast_label.set_text(text)
        self.toast.set_reveal_child(True)
        if self.toast_source:
            GLib.source_remove(self.toast_source)
        self.toast_source = GLib.timeout_add(2600, self._hide_toast)

    def _hide_toast(self):
        self.toast.set_reveal_child(False)
        self.toast_source = None
        return False

    def _changed(self):
        self.area.queue_draw()
        self._update_actions()
        if self.settings["auto_copy"] and self.source_path is None:
            if self.copy_source:
                GLib.source_remove(self.copy_source)
            self.copy_source = GLib.timeout_add(300, self._auto_copy)

    def _auto_copy(self):
        self.copy_source = None
        self.copy(quiet=True)
        return False

    def _on_delete(self, *args):
        # Hand the clipboard to the clipboard manager so it outlives us.
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).store()
        return False

    # ---- undo

    def _snapshot(self):
        return self.strokes, self.crop, self.adjust

    def _push_undo(self, snapshot=None):
        self.undo_stack.append(snapshot or self._snapshot())
        self.redo_stack.clear()

    def undo(self):
        if self.undo_stack and self.tool != "crop":
            self.redo_stack.append(self._snapshot())
            self._restore(self.undo_stack.pop())

    def redo(self):
        if self.redo_stack and self.tool != "crop":
            self.undo_stack.append(self._snapshot())
            self._restore(self.redo_stack.pop())

    def _restore(self, snapshot):
        resized = snapshot[1] != self.crop
        recolored = snapshot[2] != self.adjust
        self.strokes, self.crop, self.adjust = snapshot
        self._end_adjust_group()
        if recolored:
            self._sync_adjust_widgets()
            self._refresh_presets()
        if recolored or (resized and self.adjust["vignette"]):
            self._update_adjusted()
        if resized:
            self._resize_area()
        self._changed()

    # ---- color adjustments

    def _on_side_panel_toggled(self, button):
        show = button.get_active()
        self.side_panel.set_reveal_child(show)
        if self.is_maximized():
            return
        # Widen the window for the panel (when there's room) rather than
        # squeezing the snip, and give the width back when it closes.
        width, height = self.get_size()
        if show:
            room = int(self._workarea().width * 0.92) - width
            self.panel_grew = max(0, min(ADJUST_PANEL_WIDTH + PANEL_GAP, room))
            self.resize(width + self.panel_grew, height)
        elif self.panel_grew:
            self.resize(width - self.panel_grew, height)
            self.panel_grew = 0

    def _on_adjust_scale(self, scale, key):
        value = int(round(scale.get_value()))
        _, _, low, _, unit = next(a for a in ADJUSTMENTS if a[0] == key)
        self.adjust_values[key].set_text(format_adjustment(value, low, unit))
        if not self._syncing_adjust and value != self.adjust[key]:
            self._nudge_adjust({**self.adjust, key: value})

    def _on_adjust_scale_press(self, scale, event, key):
        if event.type == Gdk.EventType.DOUBLE_BUTTON_PRESS and event.button == 1:
            scale.set_value(NEUTRAL[key])
            return True
        return False

    def _on_adjust_scale_scroll(self, scale, event):
        # The wheel scrolls the panel; it shouldn't nudge whichever slider
        # happens to pass under the pointer on the way.
        scale.stop_emission_by_name("scroll-event")
        return False

    def _on_invert_toggled(self, check):
        if not self._syncing_adjust and check.get_active() != self.adjust["invert"]:
            self._nudge_adjust({**self.adjust, "invert": check.get_active()})

    def _nudge_adjust(self, adjust):
        """A slider moved. A burst of moves (one drag) is a single undo step."""
        if self.adjust_group:
            GLib.source_remove(self.adjust_group)
        else:
            self._push_undo()
        self.adjust_group = GLib.timeout_add(700, self._end_adjust_group)
        self.adjust = adjust
        self._refresh_presets()
        self._schedule_adjust()

    def _end_adjust_group(self):
        if self.adjust_group:
            GLib.source_remove(self.adjust_group)
        self.adjust_group = None
        return False

    def _set_adjust(self, adjust):
        """Jump to a whole set of values (a preset, or Reset) as one undo step."""
        adjust = {**NEUTRAL, **adjust}
        if adjust == self.adjust or self.base is None:
            return
        self._end_adjust_group()
        self._push_undo()
        self.adjust = adjust
        self._sync_adjust_widgets()
        self._refresh_presets()
        self._schedule_adjust()

    def _sync_adjust_widgets(self):
        self._syncing_adjust = True
        for key, label, low, high, unit in ADJUSTMENTS:
            self.adjust_scales[key].set_value(self.adjust[key])
            # set_value() is silent when the value doesn't change, so don't
            # count on the signal to update the readout
            self.adjust_values[key].set_text(format_adjustment(self.adjust[key], low, unit))
        self.invert_check.set_active(self.adjust["invert"])
        self._syncing_adjust = False

    def _schedule_adjust(self):
        # Slider drags fire faster than a full-screen snip can be recolored,
        # so only the latest values get rendered.
        if not self.adjust_source:
            self.adjust_source = GLib.timeout_add(15, self._apply_adjust)

    def _apply_adjust(self):
        self.adjust_source = None
        self._update_adjusted()
        self._changed()
        return False

    def _update_adjusted(self):
        if self.base is None or self.adjust == NEUTRAL or Image is None:
            self.adjusted, self.preview = self.base, None
        elif self.proxy is None:
            self.adjusted = adjust_surface(self.base, self.adjust, self.crop)
        else:
            # Recolor the small copy now; the full size waits until it's needed.
            shrink = self.proxy.get_width() / self.base.get_width()
            self.adjusted = None
            self.preview = adjust_surface(self.proxy, self.adjust, [v * shrink for v in self.crop], shrink)

    def _full_image(self):
        if self.adjusted is None:
            # This can take seconds on a very large image, so show that we're busy.
            set_cursor(self, "wait")
            set_cursor(self.area, "wait")
            self.get_display().flush()
            self.adjusted = adjust_surface(self.base, self.adjust, self.crop)
            set_cursor(self, None)
            self._update_cursor()
        return self.adjusted

    def _presets(self):
        """Every preset as a full set of values: built-ins, then yours A–Z."""
        names = list(BUILTIN_PRESETS) + sorted(self.settings["presets"])
        named = {**BUILTIN_PRESETS, **self.settings["presets"]}
        return {name: {**NEUTRAL, **named[name]} for name in names}

    def _refresh_presets(self, prefer=None):
        """Show the preset matching the current values, or "Custom"."""
        presets = self._presets()
        if prefer is not None:
            self._preferred_preset = prefer
        prefer = self._preferred_preset
        if prefer in presets and presets[prefer] == self.adjust:
            active = prefer
        else:
            active = next((name for name, values in presets.items() if values == self.adjust), CUSTOM_LABEL)
        names = ([CUSTOM_LABEL] if active == CUSTOM_LABEL else []) + list(presets)
        if (active, names) != self._shown_preset:
            self._shown_preset = (active, names)
            self._syncing_presets = True
            self.preset_combo.remove_all()
            for name in names:
                self.preset_combo.append(name, name)
            self.preset_combo.set_active_id(active)
            self._syncing_presets = False
        self.delete_preset_button.set_sensitive(active in self.settings["presets"])
        self.default_button.set_sensitive(active not in (CUSTOM_LABEL, self.settings["default_preset"]))
        self.default_label.set_text(f"Default: {self.settings['default_preset']}")

    def _on_preset_chosen(self, combo):
        name = combo.get_active_id()
        if self._syncing_presets or name in (None, CUSTOM_LABEL):
            return
        # Rebuilding the list from inside its own "changed" signal is asking
        # for trouble, so apply the preset once the signal is done.
        GLib.idle_add(self._apply_preset, name)

    def _apply_preset(self, name):
        self._preferred_preset = name
        self._set_adjust(self._presets()[name])
        self._refresh_presets()     # also when the values already matched
        return False

    def _save_preset_as(self, button):
        dialog = Gtk.Dialog(title="Save preset", transient_for=self, modal=True)
        dialog.add_buttons("_Cancel", Gtk.ResponseType.CANCEL, "_Save", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        dialog.get_widget_for_response(Gtk.ResponseType.OK).get_style_context().add_class("suggested-action")
        current = self.preset_combo.get_active_id()
        entry = Gtk.Entry(activates_default=True, width_chars=28,
                          text=current if current in self.settings["presets"] else "")
        label = Gtk.Label(label="Preset name:", xalign=0)
        self._add_classes(label, "section-header")
        box = dialog.get_content_area()
        box.set_spacing(8)
        for side in ("start", "end", "top", "bottom"):
            getattr(box, f"set_margin_{side}")(12)
        box.pack_start(label, False, False, 0)
        box.pack_start(entry, False, False, 0)
        dialog.show_all()
        response = dialog.run()
        name = entry.get_text().strip()
        dialog.destroy()
        if response != Gtk.ResponseType.OK or not name:
            return
        if name in BUILTIN_PRESETS or name == CUSTOM_LABEL:
            self.show_toast(f"“{name}” is a built-in preset; pick another name")
            return
        self.settings["presets"][name] = {k: v for k, v in self.adjust.items() if v != NEUTRAL[k]}
        save_settings(self.settings)
        self._refresh_presets(prefer=name)
        self.show_toast(f"Saved preset “{name}”")

    def _delete_preset(self, button):
        name = self.preset_combo.get_active_id()
        if name not in self.settings["presets"]:
            return
        del self.settings["presets"][name]
        if self.settings["default_preset"] == name:
            self.settings["default_preset"] = "Original"
        save_settings(self.settings)
        self._refresh_presets()
        self.show_toast(f"Deleted preset “{name}”")

    def _set_default_preset(self, button):
        name = self.preset_combo.get_active_id()
        if name in (None, CUSTOM_LABEL):
            return
        self.settings["default_preset"] = name
        save_settings(self.settings)
        self._refresh_presets()
        self.show_toast(f"New snips will use “{name}”")

    # ---- tools

    def _on_tool_toggled(self, button, tool):
        if self._syncing_tools:
            return
        if button.get_active():
            self._select_tool(tool)
        elif tool in self.popovers:
            # Clicking the active pen or highlighter opens its options.
            self._set_button(tool, True)
            self.popovers[tool].popup()
        else:
            self._select_tool(None)

    def _set_button(self, tool, active):
        self._syncing_tools = True
        self.tool_buttons[tool].set_active(active)
        self._syncing_tools = False

    def _select_tool(self, tool):
        if self.tool == "crop" and tool != "crop":
            self._leave_crop(apply=True, next_tool=tool)
            return
        self.tool = tool
        for t in self.tool_buttons:
            self._set_button(t, t == tool)
        if tool == "crop":
            self.crop_box = self.crop
            self.cropbar.set_reveal_child(True)
            self._resize_area()
        self._update_actions()
        self._update_cursor()
        self.area.queue_draw()

    def _leave_crop(self, apply, next_tool=None):
        box = self.crop_box
        self.tool = None
        self.crop_box = self.crop_drag = None
        self.cropbar.set_reveal_child(False)
        if apply and box != self.crop:
            self._push_undo()
            self.crop = box
            if self.adjust["vignette"]:     # it frames the crop
                self._update_adjusted()
            self._changed()
        self._resize_area()
        self._select_tool(next_tool)

    def _update_cursor(self, hit=None):
        if self.tool in ("pen", "highlighter"):
            name = "crosshair"
        elif self.tool == "eraser":
            name = "none"
        elif self.tool == "crop":
            name = {"move": "move", "new": "crosshair", None: None}.get(hit, f"{hit}-resize")
        elif self.pan_start or self._can_pan():
            name = "grabbing" if self.pan_start else "grab"
        else:
            name = None
        set_cursor(self.area, name)

    def _can_pan(self):
        h, v = self.scroller.get_hadjustment(), self.scroller.get_vadjustment()
        return h.get_upper() > h.get_page_size() + 1 or v.get_upper() > v.get_page_size() + 1

    def _key(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            if self.tool == "crop":
                self._leave_crop(apply=False)
                return True
            if self.tool:
                self._select_tool(None)
                return True
        elif event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and self.tool == "crop":
            self._leave_crop(apply=True)
            return True
        return False

    # ---- canvas geometry

    def _view(self):
        """The part of the base image the canvas shows, (x, y, w, h)."""
        if self.tool == "crop":
            return 0, 0, self.base.get_width(), self.base.get_height()
        return self.crop

    def _to_image(self, x, y):
        vx, vy, _, _ = self._view()
        return (x - SNIP_FRAME) / self.zoom + vx, (y - SNIP_FRAME) / self.zoom + vy

    def _fit_zoom(self):
        alloc = self.scroller.get_allocation()
        _, _, w, h = self._view()
        inset = 2 * (CANVAS_MARGIN + SNIP_FRAME)
        avail_w, avail_h = alloc.width - inset, alloc.height - inset
        if avail_w <= 0 or avail_h <= 0:
            return self.zoom
        return min(1.0, avail_w / w, avail_h / h)

    def _set_zoom(self, zoom):
        """Set the zoom level; None means fit to the window."""
        if self.base is None:
            return
        self.fit = zoom is None
        self.zoom = self._fit_zoom() if zoom is None else min(max(zoom, 0.05), 16)
        self._resize_area()

    def _resize_area(self):
        if self.base is None:
            return
        if self.fit:
            self.zoom = self._fit_zoom()
        _, _, w, h = self._view()
        self.area.set_size_request(max(1, math.ceil(w * self.zoom)) + 2 * SNIP_FRAME,
                                   max(1, math.ceil(h * self.zoom)) + 2 * SNIP_FRAME)
        self.zoom_label.set_text(f"{round(self.zoom * 100)}%")
        self.area.queue_draw()

    def _on_scroller_allocate(self, widget, alloc):
        if self.fit and self.base is not None and abs(self._fit_zoom() - self.zoom) > 1e-3:
            GLib.idle_add(lambda: self._resize_area() and False)

    # ---- canvas drawing

    def _paint_image(self, cr, image_filter, for_screen=False):
        base_w, base_h = self.base.get_width(), self.base.get_height()
        if for_screen and self.proxy is not None and self.zoom <= self.proxy.get_width() / base_w:
            # A large image zoomed out: the small copy looks the same and draws far faster.
            cr.save()
            cr.scale(base_w / self.proxy.get_width(), base_h / self.proxy.get_height())
            cr.set_source_surface(self.preview or self.proxy, 0, 0)
            cr.get_source().set_filter(image_filter)
            cr.paint()
            cr.restore()
        else:
            cr.set_source_surface(self._full_image(), 0, 0)
            cr.get_source().set_filter(image_filter)
            cr.paint()
        # Pen and highlighter strokes go on top, so they keep their colors.
        for stroke in self.strokes:
            draw_stroke(cr, stroke)

    def _draw(self, area, cr):
        if self.base is None:
            return
        vx, vy, vw, vh = self._view()
        z = self.zoom
        # A thin border keeps dark snips from melting into the dark panel.
        cr.set_source_rgb(*rgb(BORDER))
        cr.set_line_width(SNIP_FRAME)
        cr.rectangle(SNIP_FRAME / 2, SNIP_FRAME / 2, area.get_allocated_width() - SNIP_FRAME,
                     area.get_allocated_height() - SNIP_FRAME)
        cr.stroke()
        cr.translate(SNIP_FRAME, SNIP_FRAME)
        cr.set_source(self.checker)
        cr.rectangle(0, 0, vw * z, vh * z)
        cr.fill()
        cr.save()
        cr.scale(z, z)
        cr.translate(-vx, -vy)
        cr.rectangle(vx, vy, vw, vh)
        cr.clip()
        self._paint_image(cr, cairo.FILTER_NEAREST if z >= 2 else cairo.FILTER_GOOD, for_screen=True)
        if self.current:
            draw_stroke(cr, self.current)
        cr.restore()
        if self.tool == "crop":
            self._draw_crop(cr)
        elif self.tool == "eraser" and self.pointer:
            cr.set_line_width(1.5)
            cr.arc(*self.pointer, self._eraser_radius() * z, 0, 2 * math.pi)
            cr.set_source_rgba(*rgb(BG, 0.8))
            cr.stroke_preserve()
            cr.set_dash([3, 3])
            cr.set_source_rgb(*rgb(ACCENT))
            cr.stroke()

    def _draw_crop(self, cr):
        z = self.zoom
        x, y, w, h = (v * z for v in self.crop_box)
        cr.rectangle(0, 0, self.base.get_width() * z, self.base.get_height() * z)
        cr.rectangle(x, y, w, h)
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        cr.set_source_rgba(*rgb(BG, 0.65))
        cr.fill()
        cr.set_line_width(1)
        cr.set_source_rgb(*rgb(ACCENT))
        cr.rectangle(x + 0.5, y + 0.5, w - 1, h - 1)
        cr.stroke()
        # corner brackets and edge bars
        cr.set_line_width(4)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        arm = min(20, w / 3, h / 3)
        for cx, cy, dx, dy in ((x, y, 1, 1), (x + w, y, -1, 1), (x, y + h, 1, -1), (x + w, y + h, -1, -1)):
            cr.move_to(cx + 2 * dx, cy + (arm + 2) * dy)
            cr.line_to(cx + 2 * dx, cy + 2 * dy)
            cr.line_to(cx + (arm + 2) * dx, cy + 2 * dy)
        if w > 4 * arm:
            cr.move_to(x + w / 2 - arm / 2, y + 2)
            cr.line_to(x + w / 2 + arm / 2, y + 2)
            cr.move_to(x + w / 2 - arm / 2, y + h - 2)
            cr.line_to(x + w / 2 + arm / 2, y + h - 2)
        if h > 4 * arm:
            cr.move_to(x + 2, y + h / 2 - arm / 2)
            cr.line_to(x + 2, y + h / 2 + arm / 2)
            cr.move_to(x + w - 2, y + h / 2 - arm / 2)
            cr.line_to(x + w - 2, y + h / 2 + arm / 2)
        cr.set_source_rgba(*rgb(BG, 0.6))
        cr.set_line_width(6)
        cr.stroke_preserve()
        cr.set_source_rgb(*rgb(ACCENT))
        cr.set_line_width(4)
        cr.stroke()

    def _eraser_radius(self):
        return 10 / self.zoom

    # ---- canvas input

    def _crop_hit(self, ix, iy):
        x, y, w, h = self.crop_box
        reach = HANDLE_REACH / self.zoom
        near_x = x - reach <= ix <= x + w + reach
        near_y = y - reach <= iy <= y + h + reach
        edge = ""
        if near_x and near_y:
            if abs(iy - y) <= reach:
                edge += "n"
            elif abs(iy - (y + h)) <= reach:
                edge += "s"
            if abs(ix - x) <= reach:
                edge += "w"
            elif abs(ix - (x + w)) <= reach:
                edge += "e"
        if edge:
            return edge
        if x < ix < x + w and y < iy < y + h:
            return "move"
        return "new"

    def _crop_drag_to(self, ix, iy):
        hit, (sx, sy), (x, y, w, h) = self.crop_drag
        bw, bh = self.base.get_width(), self.base.get_height()

        def clamp(v, lo, hi):
            return min(max(v, lo), hi)

        dx, dy = ix - sx, iy - sy
        if hit == "move":
            box = (clamp(x + dx, 0, bw - w), clamp(y + dy, 0, bh - h), w, h)
        elif hit == "new":
            x0, y0 = clamp(sx, 0, bw), clamp(sy, 0, bh)
            x1, y1 = clamp(ix, 0, bw), clamp(iy, 0, bh)
            box = (min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
        else:
            left, top, right, bottom = x, y, x + w, y + h
            if "w" in hit:
                left = clamp(left + dx, 0, right - MIN_CROP)
            if "e" in hit:
                right = clamp(right + dx, left + MIN_CROP, bw)
            if "n" in hit:
                top = clamp(top + dy, 0, bottom - MIN_CROP)
            if "s" in hit:
                bottom = clamp(bottom + dy, top + MIN_CROP, bh)
            box = (left, top, right - left, bottom - top)
        self.crop_box = tuple(int(round(v)) for v in box)

    def _press(self, area, event):
        if self.base is None or event.button != 1 or event.type != Gdk.EventType.BUTTON_PRESS:
            return False
        ix, iy = self._to_image(event.x, event.y)
        if self.tool in ("pen", "highlighter"):
            # When zoomed out, ink that looks right on screen is thicker in the image.
            self.current = Stroke(self.tool, self.settings[f"{self.tool}_color"],
                                  self.settings[f"{self.tool}_width"] / min(self.zoom, 1), [(ix, iy)])
        elif self.tool == "eraser":
            self.erase_snapshot = self._snapshot()
            self._erase_at(ix, iy)
        elif self.tool == "crop":
            self.crop_drag = (self._crop_hit(ix, iy), (ix, iy), self.crop_box)
        elif self._can_pan():
            h, v = self.scroller.get_hadjustment(), self.scroller.get_vadjustment()
            self.pan_start = (event.x_root, event.y_root, h.get_value(), v.get_value())
            self._update_cursor()
        self.area.queue_draw()
        return True

    def _motion(self, area, event):
        if self.base is None:
            return False
        ix, iy = self._to_image(event.x, event.y)
        self.pointer = (event.x - SNIP_FRAME, event.y - SNIP_FRAME)
        if self.current:
            lx, ly = self.current.points[-1]
            if math.hypot(ix - lx, iy - ly) * self.zoom >= 1.5:
                self.current.points.append((ix, iy))
        elif self.tool == "eraser":
            if self.erase_snapshot:
                self._erase_at(ix, iy)
        elif self.tool == "crop":
            if self.crop_drag:
                self._crop_drag_to(ix, iy)
            else:
                self._update_cursor(self._crop_hit(ix, iy))
        elif self.pan_start:
            x0, y0, h0, v0 = self.pan_start
            self.scroller.get_hadjustment().set_value(h0 - (event.x_root - x0))
            self.scroller.get_vadjustment().set_value(v0 - (event.y_root - y0))
        else:
            self._update_cursor()
        self.area.queue_draw()
        return True

    def _release(self, area, event):
        if event.button != 1:
            return False
        if self.current:
            self._push_undo()
            self.strokes = self.strokes + [self.current]
            self.current = None
            self._changed()
        elif self.erase_snapshot:
            if self.strokes is not self.erase_snapshot[0]:
                self._push_undo(self.erase_snapshot)
                self._changed()
            self.erase_snapshot = None
        elif self.crop_drag:
            if self.crop_drag[0] == "new" and (self.crop_box[2] < MIN_CROP or self.crop_box[3] < MIN_CROP):
                self.crop_box = self.crop_drag[2]
            self.crop_drag = None
            self.area.queue_draw()
        elif self.pan_start:
            self.pan_start = None
            self._update_cursor()
        return True

    def _leave(self, area, event):
        self.pointer = None
        self.area.queue_draw()
        return False

    def _erase_at(self, ix, iy):
        radius = self._eraser_radius()
        kept = [s for s in self.strokes if not stroke_hit(s, ix, iy, radius)]
        if len(kept) != len(self.strokes):
            self.strokes = kept

    def _scroll(self, area, event):
        if not event.state & Gdk.ModifierType.CONTROL_MASK or self.base is None:
            return False
        ok, dx, dy = event.get_scroll_deltas()
        if not ok:
            dy = {Gdk.ScrollDirection.UP: -1, Gdk.ScrollDirection.DOWN: 1}.get(event.direction, 0)
        if dy:
            self._set_zoom(self.zoom * (1.25 if dy < 0 else 1 / 1.25))
        return True


# ---------------------------------------------------------------- app

class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.settings = load_settings()
        self.editor = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        # The theme's dark variant covers what our CSS doesn't (file chooser,
        # title bar); the CSS then brings everything to MetalEQ's palette.
        Gtk.Settings.get_default().props.gtk_application_prefer_dark_theme = True
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        if ICON_FILE.exists():
            Gtk.Window.set_default_icon_from_file(str(ICON_FILE))
        for action, accels in {
            "win.new": ["<Control>n"],
            "win.open": ["<Control>o"],
            "win.copy": ["<Control>c"],
            "win.save": ["<Control>s"],
            "win.undo": ["<Control>z"],
            "win.redo": ["<Control>y", "<Control><Shift>z"],
            "win.zoom-in": ["<Control>plus", "<Control>equal", "<Control>KP_Add"],
            "win.zoom-out": ["<Control>minus", "<Control>KP_Subtract"],
            "win.zoom-fit": ["<Control>0"],
        }.items():
            self.set_accels_for_action(action, accels)

    def do_command_line(self, command_line):
        args = parse_args(command_line.get_arguments()[1:])
        if self.editor is None:
            self.editor = Editor(self)
            self.editor.connect("destroy", self._editor_gone)
        if args.snip:
            self.editor.start_snip(args.mode, args.delay)
        else:
            self.editor.present()
        return 0

    def _editor_gone(self, editor):
        self.editor = None


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="snipping_tool.py", description="Snipping tool for Cinnamon (X11).")
    parser.add_argument("--snip", action="store_true", help="start a snip right away")
    parser.add_argument("--mode", choices=list(MODES), help="snip mode for --snip (default: last used)")
    parser.add_argument("--delay", type=int, choices=DELAYS, help="seconds to wait before --snip")
    return parser.parse_args(argv)


def main():
    parse_args(sys.argv[1:])  # handle --help and bad arguments here, not in a running copy
    GLib.set_application_name("Snipping Tool")
    return App().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
