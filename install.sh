#!/usr/bin/env bash
# Installs Snipping Tool for the current user: checks the Python packages it
# needs, then adds a desktop icon and an app-menu entry that run it from this
# folder. Run it again after moving the folder to point the icons at the new
# place. Nothing is copied and no root access is needed (except, if you agree,
# to install missing packages with apt).
set -euo pipefail

APP_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
SCRIPT="$APP_DIR/snipping_tool.py"
ICON="$APP_DIR/icons/snipping-tool-256.png"
DESKTOP_NAME="snipping-tool.desktop"

if [[ ! -f "$SCRIPT" || ! -f "$ICON" ]]; then
    echo "Can't find snipping_tool.py and icons/ next to this script ($APP_DIR)." >&2
    exit 1
fi

# The system Python, which is where apt puts the GTK bindings.
PYTHON=/usr/bin/python3
[[ -x "$PYTHON" ]] || PYTHON="$(command -v python3 || true)"
if [[ -z "$PYTHON" ]]; then
    echo "Python 3 is not installed. Install it with: sudo apt install python3" >&2
    exit 1
fi

# --- dependencies --------------------------------------------------------
missing="$("$PYTHON" - <<'EOF'
import importlib.util
missing = []
try:
    import gi
    try:
        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
    except ValueError:
        missing.append("gir1.2-gtk-3.0")
    try:
        gi.require_foreign("cairo")
    except ImportError:
        missing.append("python3-gi-cairo")
except ImportError:
    missing += ["python3-gi", "python3-gi-cairo", "gir1.2-gtk-3.0"]
for module, package in (("cairo", "python3-cairo"), ("PIL", "python3-pil"), ("Xlib", "python3-xlib")):
    if importlib.util.find_spec(module) is None:
        missing.append(package)
print(" ".join(dict.fromkeys(missing)))
EOF
)"

if [[ -n "$missing" ]]; then
    echo "Snipping Tool needs these packages: $missing"
    if [[ -t 0 ]] && command -v apt >/dev/null; then
        read -r -p "Install them now with sudo apt? [y/N] " answer
        if [[ "$answer" == [yY]* ]]; then
            sudo apt install -y $missing
        else
            echo "Skipped. Install them later with: sudo apt install $missing"
        fi
    else
        echo "Install them with: sudo apt install $missing"
    fi
fi

if [[ "${XDG_SESSION_TYPE:-}" == "wayland" ]]; then
    echo "Note: this is a Wayland session. Snipping Tool needs X11 to capture the screen," \
         "so it will only work after logging in to an X11 session."
fi

# --- launchers -----------------------------------------------------------
chmod +x "$SCRIPT"

# Exec= takes its argument in double quotes, where \ " ` $ must be escaped
# and a literal % is written %%.
quoted="$SCRIPT"
quoted="${quoted//\\/\\\\\\\\}"
quoted="${quoted//\"/\\\\\"}"
quoted="${quoted//\`/\\\\\`}"
quoted="${quoted//\$/\\\\\$}"
quoted="${quoted//%/%%}"

launcher="[Desktop Entry]
Type=Application
Name=Snipping Tool
GenericName=Screenshot Tool
Comment=Snip a rectangle, free-form shape, window or the whole screen, then mark it up
Exec=$PYTHON \"$quoted\"
Icon=$ICON
Terminal=false
Categories=Utility;
Keywords=screenshot;snip;capture;screen;
StartupNotify=false
StartupWMClass=snipping-tool
Actions=NewSnip;

[Desktop Action NewSnip]
Name=New snip
Exec=$PYTHON \"$quoted\" --snip
"

menu_dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
desktop_dir="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
[[ -n "$desktop_dir" && "$desktop_dir" != "$HOME" ]] || desktop_dir="$HOME/Desktop"

mkdir -p "$menu_dir" "$desktop_dir"
for target in "$menu_dir/$DESKTOP_NAME" "$desktop_dir/$DESKTOP_NAME"; do
    printf '%s' "$launcher" > "$target"
    chmod +x "$target"
done
# Mark the desktop icon as trusted, so Nemo opens it without asking first.
gio set -t string "$desktop_dir/$DESKTOP_NAME" metadata::trusted true 2>/dev/null || true
command -v update-desktop-database >/dev/null && update-desktop-database "$menu_dir" 2>/dev/null || true

echo "Installed Snipping Tool from $APP_DIR"
echo "  desktop icon: $desktop_dir/$DESKTOP_NAME"
echo "  menu entry:   $menu_dir/$DESKTOP_NAME"
echo "To uninstall, delete those two files."
