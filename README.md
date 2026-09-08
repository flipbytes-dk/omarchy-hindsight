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
  and so is the screensaver: idle time is not a memory worth keeping.
- **Forget.** `bin/hindsight forget today`, a specific day, or `all`.
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

`~/.config/omarchy-hindsight/config.json`:

```json
{
  "interval": 4.0,
  "budgetMB": 4096,
  "retentionDays": 0,
  "quality": 60,
  "changeBits": 12,
  "ocr": true,
  "blocklist": ["1password", "bitwarden", "keepassxc", "incognito", "private browsing"],
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

41 offline checks covering the hashing, the blocklist, query sanitising, the
search round trip, ring-buffer pruning, age retention, index migration, and the
frame-vanished-under-the-backfill case — none of which need a screen.

## License

MIT
