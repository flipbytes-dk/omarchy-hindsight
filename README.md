# Hindsight

A local, searchable memory of your screen.

You saw an error an hour ago. A one-time code. A config value in a terminal you
have since closed. Hindsight quietly remembers the screens you have already
looked at, reads the words on them, and lets you search for them later.

Everything stays on your machine. It never opens a network socket.

```
  ⏱  Search what you have seen
  ┌──────────────────────────────────────────┐
  │ coolify                                  │
  ├──────────────────────────────────────────┤
  │ [▪] Tue 11:26 · ghostty                  │
  │     ...RENDER_DISPATCH_TOKEN is almost   │
  │     certainly not set in ‹Coolify›...    │
  └──────────────────────────────────────────┘
```

## Install

```bash
omarchy-plugin-add https://github.com/flipbytes-dk/omarchy-hindsight
omarchy-plugin-enable dhirajkhanna.hindsight
```

Add the **Hindsight** widget to your bar from the bar's widget picker if it is
not there already. The recorder starts with the shell; the bar icon shows `󰑊`
while it is recording.

Nothing is compiled and nothing is downloaded at runtime. Check your machine
with:

```bash
~/.config/omarchy/plugins/dhirajkhanna.hindsight/bin/hindsight doctor
```

## Remove

```bash
omarchy-plugin-disable dhirajkhanna.hindsight     # stop recording
omarchy-plugin-remove dhirajkhanna.hindsight      # remove the plugin
rm -rf ~/.local/share/omarchy-hindsight           # delete every frame and the index
rm -rf ~/.config/omarchy-hindsight                # delete your settings
```

Disabling stops the recorder immediately. The two `rm` lines are what actually
erase the history — until you run them, the frames stay on disk.

## Why this works on Omarchy and almost nowhere else

On GNOME and KDE under Wayland, a program cannot quietly capture the screen:
every frame goes through the XDG desktop portal, which asks permission each
time. That is why the open Recall-style tools are all best-effort on Linux.

Hyprland exposes `wlr-screencopy`, so `grim` captures directly, with no prompt
and no portal. Measured on a 1280x800 panel: **40 ms a frame**.

## What it costs

Measured, not estimated:

| step | wall | cpu |
| --- | --- | --- |
| capture a frame (`grim`) | 15 ms | 6 ms |
| decide whether it changed | 0.4 ms | no subprocess |
| store a changed frame (`magick`) | 130 ms | 130 ms, about 72 KB |
| read the words (`tesseract`) | 2.7 s | 2.7 s, one thread, nice 19 |
| search 60k characters | 50 ms | |

An unchanged screen costs only the first two steps, so an idle desktop stores
nothing and reads nothing. Frames are kept in a ring buffer inside a size
budget you set; the oldest go first.

OCR is the whole cost, and it is capped at one core. Tesseract builds with
OpenMP and will take every core it can see: the same page finished in 2.0
seconds of wall clock by spending 7 CPU seconds across 3.2 threads. Hindsight
gives it `OMP_THREAD_LIMIT=1`, which reads the identical characters for 2.7
CPU seconds. A second of wall clock is worth nothing here, because OCR runs
off the capture thread behind a queue, and on a laptop those four CPU seconds
are four seconds of fan.

Measured over three minutes of ordinary work at a 4 second interval: 8 frames
kept, 28.8 CPU seconds, 16% of one core. The same three minutes before the
thread limit cost 38%. A quiet desktop costs a screenshot and a hash every 4
seconds and nothing else.

Memory stays flat because every helper has a ceiling. Hindsight drains the
pipes from `grim`, `magick` and `tesseract` as they fill, and kills a helper
that writes past its ceiling (128 MB of raw pixels, 32 MB of WebP, 4 MB of
text) along with anything it started. Nothing waits for the timeout to notice,
so resident memory does not grow with the size of your display or the length
of the session.

## How far back it reaches is your call

Disk is the only limit, and you set it — in the panel, or from the shell. The
question worth answering is not how many gigabytes but how many days, so
Hindsight answers in days, measured from **your** capture rate rather than a
brochure figure:

```
$ hindsight budget
budget    4 GB (1.2 GB used, 14203 frames)
reaches   about 8 weeks  (from 9 full days of your own history)
rate      11.9 MB per active hour

   512 MB   about 7 days
   1 GB     about 2 weeks
   2 GB     about 4 weeks
-> 4 GB     about 8 weeks
   8 GB     about 4 months
   16 GB    about 8 months
   32 GB    about 15 months

$ hindsight budget 8G
```

Until there are a couple of full days to learn from, the estimate uses a rate
measured on a 1280x800 panel (~88 KB a frame, ~12 MB an active hour) and says
so. Sleep, lock and pause do not count as usage, so a machine that is shut
overnight is not credited with recording all night.

Lowering the budget prunes immediately; it does not wait for the disk to fill.
`retentionDays` is off by default so that disk stays the single limit — set it
if you want a hard age ceiling regardless of space.

## Dependencies

Every dependency ships in Omarchy's base install: `grim`, `tesseract`,
`imagemagick`, `hyprctl`, and `wl-clipboard` for the copy key. The recorder is
Python standard library only, and there is nothing to compile.

Each one gets looked up in `/usr/local/bin:/usr/bin:/bin` and nowhere else,
and only runs if it is a regular file that neither group nor other can write,
in a directory with the same property. `doctor` prints the path it settled on.

Run `bin/hindsight doctor` to confirm all of it on your machine.

## Privacy, concretely

This plugin exists to remember your screen, so it is built to be told no.

- **Blocklist.** Windows matching a blocked name are never captured. Not
  captured and deleted — never captured. Password managers and private browsing
  windows are blocked out of the box.
- **Pause.** Middle-click the bar icon, or `bin/hindsight pause`.
- **A visible indicator.** The bar icon shows `󰑊` while recording and `󰏤`
  while paused. It is never ambiguous about what it is doing.
- **Locked and asleep screens are skipped**, as is a monitor whose DPMS is off,
  and so is the screensaver: idle time is not a memory worth keeping. The lock
  is read from the compositor rather than from logind, because Omarchy locks
  with a session-lock surface drawn by the shell itself: there is no locker
  process to find by name, and nothing sets logind's `LockedHint`, which
  answers "not locked" for the whole time the screen is locked.
- **Names are never trusted.** Permissions are repaired through file
  descriptors opened with `O_NOFOLLOW`, so a symlink planted in the archive
  cannot redirect a `chmod` onto something else. Deletion walks into the
  archive one component at a time and removes only a regular file you own
  that is genuinely inside it, so a tampered database cannot turn pruning
  into a way to delete your files. Config and state are read no-follow and
  size-bounded.
- **The whole screen is checked, not just the focused window.** A capture
  takes the entire output, so every window sharing it has to clear the
  blocklist. A blocked app tiled beside the one you are using stops the
  frame. Overlays count too: notifications, launchers and logout screens are
  drawn as layer surfaces rather than windows, and `blocklistLayers` matches
  them by the name of the program that drew them, because a notification
  carries the daemon's name and not the name of whatever raised it. A frame
  is skipped while such a surface is actually drawn; one kept mapped at zero
  size or fully transparent between notifications does not stop recording.
- **The gates are checked twice.** Every probe is answered before the
  screenshot is taken, and a screen can lock or a notification can appear in
  the gap. Any frame that is about to be kept is checked again first, while it
  is still only in memory, and dropped if anything changed.
- **Every check fails closed.** If the compositor cannot say which window is
  focused, or whether the session is locked, or whether the screen is on, the
  frame is not taken. A blocklist is worth no more than the probe behind it,
  so a timed-out `hyprctl` costs you a gap in the archive rather than a
  recorded password.
- **Your screen only ever reaches five programs.** `grim`, `magick`,
  `tesseract`, `hyprctl` and `wl-copy`, each resolved to an absolute path
  before it runs. They get a seven-variable environment built from scratch:
  the PATH Hindsight chose, and what the compositor needs to answer. No
  `LD_PRELOAD`, no `BASH_ENV`, and no `HOME`, because ImageMagick reads a
  delegates file out of a home directory and runs what it finds there. No
  shell is involved at any point.
- **A frame is whole or it never lands.** A frame grabber killed halfway
  through still hands back what it wrote, and half a screenshot decodes into a
  readable picture of the top of your screen. The PPM and the WebP each state
  their own length in their header, so Hindsight drops a frame whose bytes do
  not match before anything stores, reads or searches it.
- **Forget.** `bin/hindsight forget today`, a specific day, or `all`. The
  archive is swept for frames the index does not know about, so a frame whose
  row never landed cannot outlive a `forget all` by hiding from it.
- **No other user can read it.** Every directory is `0700` and every file
  `0600` - frames, the index and its WAL, config, state. The recorder also
  repairs anything an earlier version left loose when it starts, and
  `bin/hindsight doctor` reports the state of it.
- **No network.** There is no code here that opens a socket.

## Commands

```
hindsight watch                 run the recorder (the bar does this for you)
hindsight search <words>        full-text search across captured screens
hindsight timeline [day]        frames for a day, oldest first
hindsight text <id>             the recognised text of one frame
hindsight copy <id>             put that text on the clipboard
hindsight status                what the recorder is doing
hindsight pause | resume | toggle
hindsight prune                 enforce the size and age budget now
hindsight budget [size]         show, or set, disk budget as days of history
hindsight forget <all|today|YYYY-MM-DD>
hindsight doctor                check dependencies and capture
hindsight config                print (creating if needed) the config path
```

## Configuration

`~/.config/omarchy-hindsight/config.json`. Block lists **add to** the built-in
rules rather than replacing them, so naming your own bank does not quietly
delete the password managers. The defaults are shown here for reference; you
only need to write the lines you are adding.

To take a built-in rule back out, write it with a leading `-`. That matters for
`blocklistLayers`, where a status bar named `notifications-bar` cannot be told
apart from a notification daemon by its name alone:

```json
{ "blocklistLayers": ["-notifications"] }
```

`[]` no longer switches a list off — a stray empty list should not be able to
clear a privacy default. Remove rules one at a time with `-`.

```json
{
  "interval": 4.0,
  "budgetMB": 4096,
  "retentionDays": 0,
  "quality": 60,
  "changeBits": 12,
  "ocr": true,
  "blocklist": ["1password", "bitwarden", "keepassxc", "keepass",
                "gnome-keyring", "seahorse", "private browsing", "incognito",
                "inprivate", "screensaver", "org.omarchy.screensaver"],
  "blocklistLayers": ["mako", "swaync", "dunst", "fnott",
                      "notifications", "notification",
                      "swaylock", "hyprlock", "lockscreen", "wlogout",
                      "rofi", "fuzzel", "launcher",
                      "wofi", "anyrun", "tofi"],
  "blocklistTitles": []
}
```

`changeBits` is how many of the 1024 bits of a frame's signature must differ
before the screen counts as a new one. Lower keeps more; higher keeps less.
`blocklistTitles` entries are regular expressions matched against window titles.
They are the one exception to the rules above: the default list is empty, so
there is nothing to add to, and a pattern is kept exactly as written — a leading
`-` there is part of your regex, not a removal.

Frames and the index live in `~/.local/share/omarchy-hindsight/`.

## Tests

```
python3 tests/test-index.py
```

302 offline checks covering the hashing, the blocklist, query sanitising, the
search round trip, ring-buffer pruning, age retention, index migration, the
frame-vanished-under-the-backfill case, the helper ceilings, and where a
helper is allowed to come from — none of which
need a screen. The ceiling checks run real processes, because a stubbed
subprocess cannot show a deadlock, a leaked descriptor or a surviving
grandchild.

## License

MIT
