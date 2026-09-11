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

| step | cost |
| --- | --- |
| capture a frame (`grim`) | 40 ms |
| decide whether it changed | 0.4 ms, no subprocess |
| store a changed frame (WebP) | ~48 KB, 99 ms |
| read the words (`tesseract`) | ~1.4 s, off the capture thread |
| search 60k characters | 50 ms |

An unchanged screen costs only the first two steps, so an idle desktop is
nearly free and stores nothing at all. Frames are kept in a ring buffer inside
a size budget you set; the oldest go first.

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

Every dependency ships in Omarchy's base install — `grim`, `tesseract`,
`imagemagick`, `hyprctl` — and the recorder is Python standard library only.
There is nothing to compile.

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
- **Forget.** `bin/hindsight forget today`, a specific day, or `all`.
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
hindsight status                what the recorder is doing
hindsight pause | resume | toggle
hindsight prune                 enforce the size and age budget now
hindsight budget [size]         show, or set, disk budget as days of history
hindsight forget <all|today|YYYY-MM-DD>
hindsight doctor                check dependencies and capture
hindsight config                print (creating if needed) the config path
```

## Configuration

`~/.config/omarchy-hindsight/config.json`. Both block lists are shown here in
full, because writing one replaces the defaults rather than adding to them:
drop an entry and you lose that protection. Setting a list to `[]` disables it
entirely; removing the key restores the defaults.

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

Frames and the index live in `~/.local/share/omarchy-hindsight/`.

## Tests

```
python3 tests/test-index.py
```

216 offline checks covering the hashing, the blocklist, query sanitising, the
search round trip, ring-buffer pruning, age retention, index migration, and the
frame-vanished-under-the-backfill case — none of which need a screen.

## License

MIT
