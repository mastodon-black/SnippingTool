# Snipping Tool for Linux

<img src="icons/snipping-tool-128.png" alt="" width="96" align="right">

A screenshot tool for Linux desktops, modeled on the Windows Snipping Tool.
Freeze the screen, pick what to capture, then mark it up, recolor it, and
copy or save it. It is a single Python file built on GTK 3, with a dark
interface.

Made for Linux Mint (Cinnamon) on X11.

## Features

### Capturing

- **Four snip modes**
  - **Rectangle:** drag a box. Its size in pixels is shown while you drag.
  - **Free-form:** draw any shape. Everything outside it is transparent.
  - **Window:** hover to highlight a window (title bar included), then click.
  - **Full screen:** captures the whole screen at once.
- **Frozen screen:** the screen is frozen and dimmed while you choose, so
  menus and tooltips stay put. Switch modes from the bar at the top, or press
  Esc to cancel.
- **Delay:** wait 3, 5 or 10 seconds before capturing, to catch menus and
  hover states.
- **Keyboard shortcut:** one menu switch sets up **Shift+Super+S** in
  Cinnamon, so you can snip from anywhere.

### Editing

- **Pen and highlighter:** each has 12 colors and an adjustable size. The
  highlighter darkens like real ink, so text under it stays readable.
- **Eraser:** removes whole strokes.
- **Crop:** drag the edges or corners, or draw a new area.
- **Undo and redo:** covers every change, including crops and color
  adjustments.
- **Zoom and pan:** the snip fits the window by default. Ctrl+scroll zooms,
  and dragging pans.

### Colors and presets

A side panel has sliders for:

| Group | Sliders |
|---|---|
| Light | Brightness, Contrast, Shadows, Highlights, Whites, Blacks |
| Color | Saturation, Vibrance, Hue, Temperature, Tint, Invert |
| Split tone | Shadow hue and tint, Highlight hue and tint |
| Effects | Clarity, Sharpen, Vignette, Grain |

There are 28 built-in looks, plus Original:

| Group | Presets |
|---|---|
| Everyday | Clean Pop, Vivid, HDR Detail, Golden Hour, Soft Glow |
| Film | Warm Film, Cool Film, Faded Matte, Expired Film, Cross Process |
| Cinema | Teal & Orange, Blockbuster, Bleach Bypass, Moody, Moonlight, Neon Noir |
| Black & white | Grayscale, Noir, Silver, Soft Mono, Sepia, Cyanotype |
| Screenshots | Crisp Text, Clean Paper, Brighten Dark, High Contrast, Dark Mode, Invert |

- **Your own presets:** save the current sliders under a name, and set any
  preset as the default for new snips.
- **Original is never lost:** adjustments don't change the captured image,
  and pen and highlighter marks keep their colors.
- **Free-form snips stay transparent** outside the shape.
- **Large images stay responsive:** you edit a smaller preview while zoomed
  out, and full size is rendered only when you copy, save or zoom in.

### Opening and saving

- **Open images:** open an existing image (PNG, JPEG, WebP, AVIF, TIFF, GIF,
  BMP and more) to edit it the same way. Rotated phone photos open the right
  way up.
- **Clipboard:** new snips are copied automatically, and Ctrl+C copies the
  current version. The clipboard survives closing the app.
- **Save:** saves as PNG or JPEG. Snips go to `~/Pictures/Screenshots` with
  dated names. An opened image is offered as `name edited.jpg` (or `.png`)
  next to the original, and nothing is overwritten without asking.
- **Auto-save:** optionally saves every snip to `~/Pictures/Screenshots`.
  Opened images are never auto-copied or auto-saved.

## Requirements

- Linux with an **X11** session (Wayland is not supported)
- Python 3 with GTK 3 bindings, pycairo, Pillow and python-xlib

On Linux Mint, Ubuntu or Debian:

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 python3-cairo python3-pil python3-xlib
```

Linux Mint ships most of these already.

## Install

```bash
./install.sh
```

The installer:

- **Checks the packages above.** If any are missing, it offers to install
  them with `sudo apt`.
- **Adds a desktop icon** (marked trusted, so it opens without a warning).
- **Adds an entry to the app menu.** Both run the app from the folder you
  ran `install.sh` in.

Nothing is copied and no root access is needed otherwise. If you move the
folder, run `install.sh` again from the new place. To uninstall, delete
`snipping-tool.desktop` from your desktop and from
`~/.local/share/applications`.

## Running

```bash
./run.sh                                 # open the window
./run.sh --snip                          # go straight to capturing
./run.sh --snip --mode window --delay 3
```

`run.sh` works from any folder and through a symlink, so you can link it
somewhere on your `PATH`. Running `python3 snipping_tool.py` directly works
too.

- `--mode` is one of `rect`, `free`, `window` or `full`. Without it, the
  last mode you used is picked.
- `--delay` is one of `0`, `3`, `5` or `10` seconds.
- Only one copy runs at a time. Launching it again, from a launcher or a
  shortcut, hands the request to the copy that's already open.
- Right-clicking the desktop icon or menu entry offers **New snip**, which
  goes straight to capturing.

## Keyboard shortcuts

| Keys | Action |
|---|---|
| Shift+Super+S | Start a snip from anywhere (turn it on in the ☰ menu) |
| Ctrl+N | New snip |
| Ctrl+O | Open an image |
| Ctrl+C | Copy |
| Ctrl+S | Save as |
| Ctrl+Z / Ctrl+Y | Undo / redo |
| Ctrl++ / Ctrl+- | Zoom in / out (or Ctrl+scroll) |
| Ctrl+0 | Fit to window |
| Esc | Cancel a snip or a crop, or put the current tool down |
| Enter | Apply a crop |

## Settings

Settings and your saved presets live in
`~/.config/snipping-tool/settings.json`. Most options are in the ☰ menu:
automatic copy, automatic save, the Shift+Super+S shortcut, and opening the
Screenshots folder.

## Files

| Path | What it is |
|---|---|
| `snipping_tool.py` | The whole app |
| `run.sh` | Starts the app; options are passed on |
| `install.sh` | Adds the desktop icon and menu entry |
| `make_icons.py` | Redraws the icons in `icons/` |
| `icons/` | The app icon at several sizes |
| `fonts/` | Cinzel and Space Grotesk, used by the interface (SIL Open Font License) |

## Limitations

- **Linux and X11 only.** Capture reads the X screen directly, and window
  mode uses the X window list, so neither works under Wayland or on Windows.
  (Windows has the original Snipping Tool, on Win+Shift+S.)
- **The shortcut switch is Cinnamon-specific.** On other desktops, bind
  `snipping_tool.py --snip` to a key in your desktop's keyboard settings.
- **Very large images are slow at full size.** Copying or saving a 4K image
  with adjustments takes about a second, and a 50-megapixel image several
  seconds.
