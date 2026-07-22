# Browser

A minimal web browser for Linux, written in Python with PyQt6 and Qt WebEngine (Chromium).

## Features

- Chromium-based rendering with persistent cookies and sessions
- Tabbed browsing with Chrome-style tab dimensions
- Address bar with combined URL and Google search input
- Autocomplete suggestions from known domains, visited sites, and Google
- Dark mode: sites are requested in dark theme, light-only sites are darkened automatically
- Download bar with progress, transfer speed, estimated time remaining, and cancel
- Browsing history (Ctrl+H) grouped by day with search; recording can be disabled in the privacy settings, and history can be cleared at any time
- Configurable start page: clock, search, editable quick links, and optional background images (bundled photos or user-supplied)
- Single-instance behavior: subsequent launches open a new tab in the running window
- Can act as the system default browser; links from other applications open in the running window
- Fullscreen media support; downloads are saved to `~/Downloads`

## Requirements

- Python 3
- PyQt6 WebEngine

## Installation

```sh
git clone https://github.com/hypervierx-netizen/browser.git
cd browser
./install.sh
```

The install script installs PyQt6 WebEngine through the system package manager
(dnf, apt, or pacman; pip as fallback) and registers a desktop entry with icon.

To run without installing:

```sh
python3 browser.py
```

## Keyboard shortcuts

| Key | Action |
|-----|--------|
| Ctrl+T | New tab |
| Ctrl+W | Close tab |
| Ctrl+L | Focus address bar |
| Ctrl+Tab / Ctrl+Shift+Tab | Next / previous tab |
| Ctrl+R / F5 | Reload |
| Ctrl+H | History |
| F11 | Fullscreen |
| Ctrl+Q | Quit |

## Configuration

There is no configuration file. The relevant sources are:

| Path | Description |
|------|-------------|
| `browser.py` | Application code. UI colors are defined in the `STYLE` string. |
| `start.html` | Start page. Quick links, background selection, and privacy settings are managed in the page itself. |
| `history.html` | History page. |
| `backgrounds/` | Bundled background images. |

User data (history, settings, cookies) is stored under `~/.local/share/browser/`.

The color scheme follows Catppuccin Mocha.

## Uninstall

```sh
rm ~/.local/share/applications/browser.desktop
rm ~/.local/share/icons/hicolor/scalable/apps/browser.svg
```

Browsing data is stored in `~/.local/share/browser/` and can be deleted
separately. Remove the cloned repository to complete the uninstall.
