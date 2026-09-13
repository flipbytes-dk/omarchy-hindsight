#!/usr/bin/env python3
"""Offline tests for hindsight: hashing, blocklist, search, pruning.

Everything here runs without a compositor, a screen, or tesseract, so the
paths that only misbehave at 3am - a full disk, a pruned frame, a query full
of punctuation - can be exercised on demand instead of by luck.
"""
import importlib.machinery
import importlib.util
import os
import sqlite3
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="hindsight-test-")
os.environ["HINDSIGHT_DATA"] = os.path.join(TMP, "data")
os.environ["HINDSIGHT_CONFIG"] = os.path.join(TMP, "config", "config.json")

# bin/hindsight has no .py suffix, so it needs an explicit source loader.
_loader = importlib.machinery.SourceFileLoader(
    "hindsight", os.path.join(ROOT, "bin", "hindsight"))
spec = importlib.util.spec_from_loader("hindsight", _loader)
hs = importlib.util.module_from_spec(spec)
_loader.exec_module(hs)

PASS = []
FAIL = []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print(("  ok   " if condition else "  FAIL ") + name +
          (("  -> " + str(detail)) if detail and not condition else ""))


def ppm(width, height, pixel):
    """A synthetic P6 frame. pixel(x, y) -> (r, g, b)."""
    body = bytearray()
    for y in range(height):
        for x in range(width):
            body.extend(pixel(x, y))
    return b"P6\n%d %d\n255\n" % (width, height) + bytes(body)


print("\n-- ppm parsing and hashing --")
flat = ppm(64, 40, lambda x, y: (10, 10, 10))
check("parses a well-formed P6 header", hs.parse_ppm(flat) == (64, 40, len(b"P6\n64 40\n255\n")))
check("rejects a non-PPM buffer", hs.parse_ppm(b"\x89PNG\r\n") is None)
check("rejects a truncated header", hs.parse_ppm(b"P6\n64 ") is None)

commented = b"P6\n# grim wrote this\n64 40\n255\n" + b"\x00" * (64 * 40 * 3)
check("skips comments in the header", hs.parse_ppm(commented) is not None)

half = ppm(64, 40, lambda x, y: (250, 250, 250) if x < 32 else (0, 0, 0))
gradient = ppm(64, 40, lambda x, y: (x * 4 % 256,) * 3)
check("hashes a valid frame", hs.average_hash(flat) is not None)
check("returns None for an unparsable frame", hs.average_hash(b"nope") is None)
check("identical frames hash identically", hs.average_hash(half) == hs.average_hash(half))
check("different frames hash differently",
      hs.distance(hs.average_hash(half), hs.average_hash(gradient)) > 12)

# A cursor blinking in one cell must not count as a new screen, or the disk
# fills with the same picture.
blink = bytearray(half)
head = len(b"P6\n64 40\n255\n")
for i in range(head + 3 * (5 * 64 + 5), head + 3 * (5 * 64 + 9), 3):
    blink[i] = blink[i + 1] = blink[i + 2] = 128
check("a blinking cursor is not a new screen",
      hs.distance(hs.average_hash(half), hs.average_hash(bytes(blink))) < 12,
      hs.distance(hs.average_hash(half), hs.average_hash(bytes(blink))))
check("distance to nothing is maximal", hs.distance(None, 5) == hs.HASH_BITS)

print("\n-- blocklist: a blocked frame must never be taken --")
cfg = dict(hs.DEFAULTS)
check("matches a password manager by class",
      hs.blocked_by(cfg, "1Password", "vault") == "1password")
check("matches an incognito window by title",
      hs.blocked_by(cfg, "chromium", "Search (Incognito)") == "incognito")
check("matches private browsing by title",
      hs.blocked_by(cfg, "firefox", "Bank — Private Browsing") == "private browsing")
check("leaves ordinary windows alone",
      hs.blocked_by(cfg, "ghostty", "vim notes.md") == "")
check("is case insensitive", hs.blocked_by(cfg, "BITWARDEN", "") == "bitwarden")

regex_cfg = dict(hs.DEFAULTS)
regex_cfg["blocklistTitles"] = [r"acct[-_]\d{4}"]
check("honours a title regex", hs.blocked_by(regex_cfg, "ghostty", "acct_9931 ledger") == r"acct[-_]\d{4}")
broken = dict(hs.DEFAULTS)
broken["blocklistTitles"] = ["(unclosed"]
check("an unusable regex blocks rather than being silently discarded",
      hs.blocked_by(broken, "ghostty", "anything") != "",
      hs.blocked_by(broken, "ghostty", "anything"))

print("\n-- query sanitising: people type punctuation, FTS5 treats it as syntax --")
check("quotes words, matching long ones as prefixes",
      hs.fts_query("connection refused") == '"connection"* "refused"*',
      hs.fts_query("connection refused"))
check("short words stay exact, so 'in' does not match the whole day",
      hs.fts_query("in it") == '"in" "it"', hs.fts_query("in it"))
check("a partial word still finds the whole one",
      hs.fts_query("modul") == '"modul"*', hs.fts_query("modul"))
check("survives an apostrophe", hs.fts_query("didn't work") != "")
check("strips a stray double quote", '"' not in hs.fts_query('say "hi"').replace('" "', "").strip('"'))
check("empty query yields empty match", hs.fts_query("   ") == "")
check("punctuation-only yields empty match", hs.fts_query("*** ---") == "")
check("keeps paths and hosts intact", "api.example.com" in hs.fts_query("api.example.com"))

print("\n-- search index round trip --")
conn = hs.connect()
now = time.time()
os.makedirs(hs.FRAMES, exist_ok=True)
ids = []
for n, (text, app) in enumerate([
        ("ECONNREFUSED connecting to postgres on port 5432", "ghostty"),
        ("Your verification code is 449120", "chromium"),
        ("git rebase --onto main feature", "ghostty")]):
    path = os.path.join(hs.FRAMES, "frame%d.webp" % n)
    open(path, "wb").write(b"x" * 100)
    fid = hs.store(conn, now - n * 60, path, app, "title%d" % n, "DP-1", 1 << n, 100)
    hs.attach_text(conn, fid, text, app, "title%d" % n)
    ids.append(fid)

rows = conn.execute(
    "SELECT snippet(frames_fts,0,'[',']','...',8) FROM frames_fts"
    " WHERE frames_fts MATCH ?", (hs.fts_query("postgres"),)).fetchall()
check("finds an indexed word", len(rows) == 1, rows)
check("snippet highlights the match", rows and "[postgres]" in rows[0][0], rows)
check("text is retrievable for one frame",
      "449120" in (conn.execute("SELECT text FROM frames_fts WHERE rowid=?",
                                (ids[1],)).fetchone() or [""])[0])
check("marks frames as read", conn.execute(
    "SELECT COUNT(*) FROM frames WHERE ocr=1").fetchone()[0] == 3)

print("\n-- pruning: the ring buffer must actually close --")
# This is the bug that shipped once: deleting rows from under a live cursor
# skips frames, so the archive creeps over budget forever.
big = hs.connect()
BULK_BYTES = 200 * 1024
for n in range(40):
    path = os.path.join(hs.FRAMES, "bulk%d.webp" % n)
    open(path, "wb").write(b"y" * BULK_BYTES)
    hs.store(big, now - (100 - n) * 60, path, "app", "t", "DP-1", n, BULK_BYTES)
before, bytes_before = hs.usage(big)
cfg_small = dict(hs.DEFAULTS)
cfg_small["budgetMB"] = 4
cfg_small["retentionDays"] = 0
removed = hs.prune(big, cfg_small)
after, bytes_after = hs.usage(big)
check("prune removed frames", removed > 0, removed)
check("prune brought the archive inside budget",
      bytes_after <= 10 * 1024 * 1024, "%d bytes left" % bytes_after)
check("prune kept the newest frames", after > 0 and after < before)
kept = big.execute("SELECT MIN(ts) FROM frames").fetchone()[0]
check("prune deleted oldest first", kept > now - 100 * 60)
check("prune deleted the files too",
      not os.path.exists(os.path.join(hs.FRAMES, "bulk0.webp")))
check("pruned text leaves the search index",
      big.execute("SELECT COUNT(*) FROM frames_fts").fetchone()[0]
      <= big.execute("SELECT COUNT(*) FROM frames").fetchone()[0] + 3)

print("\n-- retention by age --")
aged = hs.connect()
old_path = os.path.join(hs.FRAMES, "ancient.webp")
open(old_path, "wb").write(b"z" * 10)
hs.store(aged, now - 90 * 86400, old_path, "app", "t", "DP-1", 1, 10)
cfg_age = dict(hs.DEFAULTS)
cfg_age["retentionDays"] = 30
cfg_age["budgetMB"] = 100000
hs.prune(aged, cfg_age)
check("retention deletes frames past the horizon",
      aged.execute("SELECT COUNT(*) FROM frames WHERE path=?",
                   (old_path,)).fetchone()[0] == 0)
check("retention removed the file", not os.path.exists(old_path))

print("\n-- contentless index migration --")
legacy_dir = os.path.join(TMP, "legacy")
os.makedirs(legacy_dir, exist_ok=True)
legacy = sqlite3.connect(os.path.join(legacy_dir, "index.db"))
legacy.execute("""CREATE TABLE frames(id INTEGER PRIMARY KEY, ts REAL, day TEXT,
    path TEXT, app TEXT, title TEXT, monitor TEXT, hash TEXT, bytes INTEGER,
    ocr INTEGER DEFAULT 1)""")
legacy.execute("INSERT INTO frames(ts,day,path,ocr) VALUES(1,'d','/tmp/x',1)")
legacy.execute("CREATE VIRTUAL TABLE frames_fts USING fts5(text, app, title, content='')")
legacy.commit()
hs.migrate_fts(legacy)
legacy.execute("CREATE VIRTUAL TABLE IF NOT EXISTS frames_fts USING fts5(text, app, title)")
legacy.execute("INSERT INTO frames_fts(rowid,text,app,title) VALUES(1,'hello world','a','b')")
check("migration makes text readable again",
      legacy.execute("SELECT text FROM frames_fts WHERE rowid=1").fetchone()[0] == "hello world")
check("migration marks frames for re-reading",
      legacy.execute("SELECT ocr FROM frames WHERE id=1").fetchone()[0] == 0)

print("\n-- a frame whose file vanished must not be retried forever --")
gone = hs.connect()
missing = os.path.join(hs.FRAMES, "vanished.webp")
open(missing, "wb").write(b"q")
fid = hs.store(gone, now, missing, "app", "t", "DP-1", 1, 1)
os.remove(missing)
rec = hs.Recorder.__new__(hs.Recorder)
rec.cfg = dict(hs.DEFAULTS)
rec.backfill(gone)
check("backfill drops a frame whose image is gone",
      gone.execute("SELECT COUNT(*) FROM frames WHERE id=?", (fid,)).fetchone()[0] == 0)

print("\n-- config --")
check("interval is floored to something sane",
      hs.load_config()["interval"] >= 1.0)
os.makedirs(os.path.dirname(os.environ["HINDSIGHT_CONFIG"]), exist_ok=True)
open(os.environ["HINDSIGHT_CONFIG"], "w").write("{ this is not json")
check("a corrupt config falls back to defaults rather than dying",
      hs.load_config()["interval"] == hs.DEFAULTS["interval"])
open(os.environ["HINDSIGHT_CONFIG"], "w").write('{"budgetMB": 1}')
check("an absurd budget is clamped up", hs.load_config()["budgetMB"] >= 64)

print("\n-- capacity: gigabytes translated into days --")
for text, want in [("8G", 8192), ("8gb", 8192), ("512M", 512), ("4096", 4096),
                   ("1t", 1048576), ("2.5G", 2560), (" 2 GB ", 2048)]:
    check("size %r reads as %d MB" % (text, want),
          hs.parse_size_mb(text) == want, hs.parse_size_mb(text))
for bad in ["", "lots", "-4G", "0", "G", None]:
    check("size %r is refused" % (bad,), hs.parse_size_mb(bad) is None,
          hs.parse_size_mb(bad))

cfg = dict(hs.DEFAULTS)
cfg["budgetMB"] = 4096

# Earlier tests have filled the shared database, so a fresh install has to be
# asked for explicitly rather than assumed.
_was_db = hs.DB_PATH
hs.DB_PATH = os.path.join(TMP, "empty.db")
empty = hs.connect()
first = hs.capacity(empty, cfg)
check("with no history the estimate still answers",
      first["basis"] == "default", first["basis"])
check("the first guess is in the right order of magnitude",
      5e6 < first["bytesPerActiveHour"] < 40e6,
      hs.human_bytes(first["bytesPerActiveHour"]))
hs.DB_PATH = _was_db
cap = hs.connect()

# Five days of six active hours, a 88 KB frame every 26s, with a real night
# in between. The night must not be counted as usage or the estimate flatters.
KB88 = 88 * 1024
base = time.time() - 5 * 86400
row = 0
for day in range(5):
    start = time.time() - (5 - day) * 86400
    stamp = start
    while stamp < start + 6 * 3600:
        row += 1
        cap.execute(
            "INSERT INTO frames(ts,day,path,app,title,monitor,hash,bytes,ocr)"
            " VALUES(?,?,?,?,?,?,?,?,1)",
            (stamp, time.strftime("%Y-%m-%d", time.localtime(stamp)),
             "/x/%d.webp" % row, "app", "t", "DP-1", "0", KB88))
        stamp += 26
cap.commit()

est = hs.capacity(cap, cfg)
truth_hour = KB88 * 3600 / 26.0
check("the hourly rate ignores time the machine was asleep",
      abs(est["bytesPerActiveHour"] - truth_hour) / truth_hour < 0.02,
      "%d vs %d" % (est["bytesPerActiveHour"], truth_hour))
check("a daily rate is measured once whole days exist",
      est["basis"] == "measured" and est["sampleDays"] >= 2, est["basis"])
check("4 GB of this usage is roughly eight weeks",
      50 <= est["coverageDays"] <= 65, est["coverageDays"])
check("a bigger budget reaches further back",
      hs.capacity(cap, cfg, 8192)["coverageDays"] >
      hs.capacity(cap, cfg, 1024)["coverageDays"])
check("coverage scales linearly with the disk given",
      abs(hs.capacity(cap, cfg, 8192)["coverageDays"] /
          hs.capacity(cap, cfg, 2048)["coverageDays"] - 4.0) < 0.01)

check("a short span is phrased in hours",
      "hours" in hs.coverage_text(hs.capacity(cap, cfg, 64)),
      hs.coverage_text(hs.capacity(cap, cfg, 64)))
check("a long span is phrased in months",
      "months" in hs.coverage_text(hs.capacity(cap, cfg, 32768)),
      hs.coverage_text(hs.capacity(cap, cfg, 32768)))
capped = dict(cfg)
capped["retentionDays"] = 7
check("an age limit is reported instead of the disk figure it overrides",
      "age limit" in hs.coverage_text(hs.capacity(cap, capped, 32768)),
      hs.coverage_text(hs.capacity(cap, capped, 32768)))
check("retention is off by default, so disk is the only limit",
      hs.DEFAULTS["retentionDays"] == 0)

# Lowering the budget has to actually free the disk, not just record an
# intention: this is the promise the picker makes when you click a smaller chip.
cfg["budgetMB"] = 64
hs.prune(cap, cfg)
_, left = hs.usage(cap)
check("choosing a smaller size drops the oldest frames at once",
      left <= 64 * 1024 * 1024, hs.human_bytes(left))

open(os.environ["HINDSIGHT_CONFIG"], "w").write(
    '{"budgetMB": 4096, "blocklist": ["mine"], "interval": 9.5}')
hs.save_config({"budgetMB": 2048})
saved = hs.load_config()
check("setting a budget keeps the settings it was not asked about",
      saved["budgetMB"] == 2048 and saved["blocklist"] == ["mine"]
      and saved["interval"] == 9.5, saved)

print("\n-- the budget must cover everything on disk, not just pictures --")
check("the search index is counted as occupied space",
      hs.disk_usage(cap)[1] > hs.usage(cap)[1], 
      "%d vs %d" % (hs.disk_usage(cap)[1], hs.usage(cap)[1]))
check("index size is reported",
      hs.index_bytes() > 0, hs.index_bytes())

# A pathological index must not be able to prune the archive down to nothing.
tight = dict(hs.DEFAULTS)
tight["budgetMB"] = 64
hs.prune(cap, tight)
kept, _ = hs.usage(cap)
check("a large index cannot prune the archive to zero", kept > 0, kept)

print("\n-- a screensaver is not a memory --")
check("the screensaver is blocked out of the box",
      hs.blocked_by(hs.DEFAULTS, "org.omarchy.screensaver", "omarchy-screensaver"),
      hs.blocked_by(hs.DEFAULTS, "org.omarchy.screensaver", "omarchy-screensaver"))
check("a normal window is still captured",
      not hs.blocked_by(hs.DEFAULTS, "com.mitchellh.ghostty", "omarchy: plugin"))
check("frames need real text to be worth returning", hs.MIN_RESULT_CHARS >= 40)

print("\n-- one moment should occupy one row --")
dedup = hs.connect()
now = time.time()
# Same screen, read three times with the small OCR differences that are
# normal, plus genuinely different pictures each time.
variants = [
    "the quarterly forecast spreadsheet shows revenue climbing steadily",
    "the quarterly forecast spreadsheet shows revenue clinbing steadily",
    "the quarterIy forecast spreadsheet shows revenue climbing steadily",
]
# Real hashes have around half of the 1024 bits set, so unrelated screens sit
# ~500 bits apart. Give every frame here a distinct one, so that collapsing
# can only happen because of what the frames say, not what they look like.
import random
random.seed(11)
os.makedirs(os.path.join(hs.FRAMES, "2021-05-05"), exist_ok=True)
for index, text in enumerate(variants):
    dup_path = os.path.join(hs.FRAMES, "2021-05-05", "dup%d.webp" % index)
    open(dup_path, "wb").write(b"d" * 1000)
    fid = hs.store(dedup, now + index, dup_path,
                   "app", "t", "DP-1", random.getrandbits(hs.HASH_BITS), 1000)
    hs.attach_text(dedup, fid, text, "app", "t")
other_path = os.path.join(hs.FRAMES, "2021-05-05", "other.webp")
open(other_path, "wb").write(b"o" * 1000)
fid = hs.store(dedup, now + 9, other_path,
               "app", "t", "DP-1", random.getrandbits(hs.HASH_BITS), 1000)
hs.attach_text(dedup, fid,
               "a different screen entirely about forecast wind speeds offshore",
               "app", "t")

import io, contextlib, json as _json

def _search(conn, term):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        hs.cmd_search([term])
    return len(_json.loads(buf.getvalue())["results"])
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    hs.cmd_search(["forecast"])
found = _json.loads(buf.getvalue())["results"]
check("three readings of one screen collapse to one row",
      len(found) == 2, [r["snippet"][:40] for r in found])

print("\n-- a screen history must not be readable by other local users --")
import stat as statmod

def mode_of(path):
    return os.stat(path).st_mode & 0o777

# Simulate what a lax umask produced before this was enforced.
os.umask(0o022)
hs.ensure_dirs()
loose_dir = os.path.join(hs.FRAMES, "2020-01-01")
os.makedirs(loose_dir, exist_ok=True)
os.chmod(loose_dir, 0o755)
loose_file = os.path.join(loose_dir, "leak.webp")
open(loose_file, "wb").write(b"pretend frame")
os.chmod(loose_file, 0o644)
os.chmod(hs.DATA, 0o755)
check("the test really did create a world-readable frame",
      mode_of(loose_file) == 0o644 and mode_of(hs.DATA) == 0o755)

fixed = hs.harden_all()
check("startup repair tightens an archive an older version left open",
      fixed > 0, fixed)
check("the data directory becomes private", mode_of(hs.DATA) == 0o700,
      oct(mode_of(hs.DATA)))
check("a day directory becomes private", mode_of(loose_dir) == 0o700,
      oct(mode_of(loose_dir)))
check("an existing frame becomes private", mode_of(loose_file) == 0o600,
      oct(mode_of(loose_file)))

os.umask(0o077)
hs.ensure_dirs()
check("new directories are created private", mode_of(hs.FRAMES) == 0o700,
      oct(mode_of(hs.FRAMES)))

hs.save_config({"budgetMB": 1024})
check("the config file is private", mode_of(hs.CONFIG) == 0o600,
      oct(mode_of(hs.CONFIG)))
check("no config temp file is left behind",
      not os.path.exists(hs.CONFIG + ".tmp"))

perms = hs.connect()
for suffix in ("", "-wal"):
    target = hs.DB_PATH + suffix
    if os.path.exists(target):
        check("the index (%s) is private" % (suffix or "db"),
              mode_of(target) == 0o600, oct(mode_of(target)))

hs.set_paused(True)
check("the pause marker is private", mode_of(hs.PAUSE_MARKER) == 0o600,
      oct(mode_of(hs.PAUSE_MARKER)))
hs.set_paused(False)

check("nothing under the data directory is group or world readable",
      not [os.path.join(b, n)
           for b, ds, fs in os.walk(hs.DATA)
           for n in list(ds) + list(fs)
           if os.stat(os.path.join(b, n)).st_mode & 0o077])

print("\n-- a probe that cannot answer must stop the capture, not allow it --")
real_run = hs.run

def with_run(fake, call):
    hs.run = fake
    try:
        return call()
    finally:
        hs.run = real_run

dead = lambda *a, **k: (1, b"", b"boom")
timeout = lambda *a, **k: (124, b"", b"timeout")
garbage = lambda *a, **k: (0, b"<html>not json</html>", b"")

check("a failed window probe reports failure, not an empty window",
      with_run(dead, hs.active_window)[0] is False)
check("a timed-out window probe reports failure",
      with_run(timeout, hs.active_window)[0] is False)
check("unparseable window output reports failure",
      with_run(garbage, hs.active_window)[0] is False)
check("a failed monitor probe does not claim the screen is awake",
      with_run(dead, hs.focused_monitor) == (False, None, False),
      with_run(dead, hs.focused_monitor))
# pgrep exits 1 for "ran fine, found nothing", which is an answer rather than
# a failure, so the unknown case needs a code that means the probe broke.
broken = lambda *a, **k: (2, b"", b"error")
check("an unanswerable lock probe is unknown, not unlocked",
      with_run(broken, hs.session_locked) is None,
      with_run(broken, hs.session_locked))
check("a missing pgrep does not crash the recorder",
      with_run(lambda *a, **k: (127, b"", b"missing"), hs.session_locked) is None)

def answered_no(cmd, **kwargs):
    if cmd[0] == "pgrep":
        return 1, b"", b""          # no locker running: a real answer
    return 2, b"", b"loginctl gone"
check("no named locker plus no loginctl answer is still unknown",
      with_run(answered_no, hs.session_locked) is None,
      with_run(answered_no, hs.session_locked))

# Omarchy locks with an ext-session-lock surface drawn by the shell process
# that is already running. There is no locker to find by name, and nothing
# sets LockedHint - the session answers "no" for the whole time the screen is
# locked. Believing that "no" recorded the locked screen. The compositor is
# asked first now, and logind's "no" can no longer grant permission on its
# own.
def hypr(blockers, hint=b"no", hyprctl_ok=True):
    def fake(cmd, **kwargs):
        if cmd[0] == "hyprctl":
            if not hyprctl_ok:
                return 1, b"", b"gone"
            mon = {"name": "DP-1", "focused": True}
            if blockers is not None:
                mon["solitaryBlockedBy"] = blockers
            return 0, _json.dumps([mon]).encode(), b""
        if cmd[0] == "pgrep":
            return 1, b"", b""      # no locker by that name: a real answer
        return 0, hint, b""
    return fake

check("a lock the compositor holds is locked, whatever logind answers",
      with_run(hypr(["LOCK"]), hs.session_locked) is True,
      with_run(hypr(["LOCK"]), hs.session_locked))
check("and a compositor with no lock among its blockers is unlocked",
      with_run(hypr(["WINDOWED", "CANDIDATE"]), hs.session_locked) is False)
# Hyprland stops at the first reason, so a monitor still waiting for a
# workspace was never asked about the lock at all.
check("a monitor with no workspace yet is unknown, not unlocked",
      with_run(hypr(["WORKSPACE"]), hs.session_locked) is None,
      with_run(hypr(["WORKSPACE"]), hs.session_locked))
check("a compositor too old to report the field is unknown, not unlocked",
      with_run(hypr(None), hs.session_locked) is None)
check("loginctl reporting a locked session is believed when the compositor "
      "cannot be asked",
      with_run(hypr(None, hint=b"yes", hyprctl_ok=False),
               hs.session_locked) is True)
check("and its 'no' alone is not permission to record",
      with_run(hypr(None, hint=b"no", hyprctl_ok=False),
               hs.session_locked) is None,
      with_run(hypr(None, hint=b"no", hyprctl_ok=False), hs.session_locked))

# The gate itself: a recorder whose probes all fail must not reach capture.
captured = []
real_capture = hs.capture
hs.capture = lambda monitor: (captured.append(monitor), (None, "should not run"))[1]

gate = hs.Recorder.__new__(hs.Recorder)
gate.cfg = dict(hs.DEFAULTS)
gate.conn = hs.connect()
gate.emitted = None
gate.dropped_ocr = 0
gate.last_hash = None
gate.coverage_cache = {"coverageDays": 0, "coverageText": "", "coverageBasis": "default"}
gate.coverage_at = time.time()
before = hs.usage(gate.conn)[0]

for name, fake in (("lock", dead), ("display", dead), ("window", dead)):
    hs.run = fake
    try:
        gate.tick()
    finally:
        hs.run = real_run
check("a recorder with failing probes never calls capture", not captured, captured)
check("and stores no frame", hs.usage(gate.conn)[0] == before)
hs.capture = real_capture

print("\n-- a planted symlink must not redirect a chmod or a delete --")
bait_dir = os.path.join(TMP, "bait")
os.makedirs(bait_dir, exist_ok=True)
bait = os.path.join(bait_dir, "someone-elses-secret")
open(bait, "w").write("not ours")
os.chmod(bait, 0o644)

# A link inside the archive pointing at a file outside it.
link = os.path.join(hs.FRAMES, "innocent.webp")
if os.path.lexists(link):
    os.remove(link)
os.symlink(bait, link)

hs.harden_all()
check("the repair does not chmod through a symlink",
      (os.stat(bait).st_mode & 0o777) == 0o644,
      oct(os.stat(bait).st_mode & 0o777))
check("and leaves the link itself in place", os.path.islink(link))

# A database row naming that link must not delete what it points at.
attack = hs.connect()
fid = hs.store(attack, time.time(), link, "app", "t", "DP-1", 1, 10)
hs.drop_frames(attack, [(fid, link)])
check("deleting a frame does not follow a symlink out of the archive",
      os.path.exists(bait))
check("the poisoned row is dropped so pruning keeps making progress",
      attack.execute("SELECT COUNT(*) FROM frames WHERE id=?",
                     (fid,)).fetchone()[0] == 0)
os.remove(link)

# A row pointing straight at something outside the archive.
outside = os.path.join(bait_dir, "keep-me")
open(outside, "w").write("keep")
fid = hs.store(attack, time.time(), outside, "app", "t", "DP-1", 1, 10)
hs.drop_frames(attack, [(fid, outside)])
check("a row naming a path outside the archive deletes nothing",
      os.path.exists(outside))

escape = os.path.join(hs.FRAMES, "..", "..", "escape.txt")
open(os.path.join(TMP, "escape.txt"), "w").write("x")
fid = hs.store(attack, time.time(), escape, "app", "t", "DP-1", 1, 10)
hs.drop_frames(attack, [(fid, escape)])
check("a row using .. to climb out deletes nothing",
      os.path.exists(os.path.join(TMP, "escape.txt")))

# A real frame in the real place still gets deleted.
real_day = os.path.join(hs.FRAMES, "2021-02-03")
os.makedirs(real_day, exist_ok=True)
real = os.path.join(real_day, "120000-001.webp")
open(real, "wb").write(b"frame")
fid = hs.store(attack, time.time(), real, "app", "t", "DP-1", 1, 5)
hs.drop_frames(attack, [(fid, real)])
check("a genuine frame is still deleted", not os.path.exists(real))

check("a result whose path escapes the archive is not returned",
      (lambda: (
          hs.attach_text(dedup,
                         hs.store(dedup, time.time(), "/etc/shadow", "app", "t",
                                  "DP-1", random.getrandbits(hs.HASH_BITS), 10),
                         "forecast of an unrelated secret file", "app", "t"),
          _search(dedup, "unrelated"))[1])() == 0)

print("\n-- config and state reads are bounded and refuse links --")
cfg_link = os.path.join(TMP, "config-link.json")
if os.path.lexists(cfg_link):
    os.remove(cfg_link)
os.symlink(bait, cfg_link)
def raises_unreadable(path):
    try:
        hs.read_private(path)
        return False
    except hs.Unreadable:
        return True

check("a symlinked config is refused, not silently ignored",
      raises_unreadable(cfg_link))

huge = os.path.join(TMP, "huge.json")
with open(huge, "w") as fh:
    fh.write("{}" + " " * (hs.MAX_TEXT_BYTES + 10))
check("an oversized config is refused", raises_unreadable(huge))
check("a missing file is None, which is not the same thing",
      hs.read_private(os.path.join(TMP, "no-such-file")) is None)

# The dotfiles case: a config symlinked elsewhere must survive a settings write.
real_cfg = os.path.join(TMP, "dotfiles-config.json")
open(real_cfg, "w").write('{"blocklist": ["my-bank"], "interval": 8.0}')
saved_cfg = os.environ["HINDSIGHT_CONFIG"]
try:
    os.remove(hs.CONFIG)
except OSError:
    pass
os.symlink(real_cfg, hs.CONFIG)
check("a settings write refuses to replace a config it cannot read",
      hs.save_config({"budgetMB": 2048}) is None)
check("and the symlink is still a symlink", os.path.islink(hs.CONFIG))
check("and the real file still holds the user's rules",
      "my-bank" in open(real_cfg).read())
os.remove(hs.CONFIG)

small = os.path.join(TMP, "small.json")
open(small, "w").write('{"ok": true}')
check("a normal file still reads", hs.read_private(small) == '{"ok": true}')

check("writing refuses to go through a symlink",
      hs.write_private(cfg_link, "overwritten") is False)
check("and the link target is untouched",
      open(bait).read() == "not ours")
os.remove(cfg_link)

print("\n-- a blocked window anywhere on the screen stops the capture --")
check("hyprctl answering {} is not 'no window', it is 'cannot tell'",
      with_run(lambda *a, **k: (0, b"{}", b""), hs.active_window)[0] is False)
check("a real answer is still accepted",
      with_run(lambda *a, **k: (0, b'{"class":"ghostty","title":"x"}', b""),
               hs.active_window) == (True, "ghostty", "x"))

def hypr(monitors, clients):
    def fake(cmd, **kwargs):
        if cmd[:2] == ["hyprctl", "monitors"]:
            return 0, json.dumps(monitors).encode(), b""
        if cmd[:2] == ["hyprctl", "clients"]:
            return 0, json.dumps(clients).encode(), b""
        return 1, b"", b"no"
    return fake

import json
one_screen = [{"name": "DP-1", "activeWorkspace": {"id": 1}}]
tiled = [
    {"class": "com.mitchellh.ghostty", "title": "work", "workspace": {"id": 1},
     "mapped": True, "hidden": False},
    {"class": "1Password", "title": "Vault", "workspace": {"id": 1},
     "mapped": True, "hidden": False},
]
probed, windows = with_run(hypr(one_screen, tiled),
                           lambda: hs.visible_windows("DP-1"))
check("both tiled windows are seen", probed and len(windows) == 2, windows)
check("the password manager beside the focused window is caught",
      any(hs.blocked_by(hs.DEFAULTS, a, t) for a, t in windows))

elsewhere = [dict(tiled[0]), dict(tiled[1], workspace={"id": 9})]
probed, windows = with_run(hypr(one_screen, elsewhere),
                           lambda: hs.visible_windows("DP-1"))
check("a window on another workspace is not on screen and does not block",
      probed and len(windows) == 1 and
      not any(hs.blocked_by(hs.DEFAULTS, a, t) for a, t in windows), windows)

check("an unreadable client list is not an empty one",
      with_run(lambda *a, **k: (1, b"", b"boom"),
               lambda: hs.visible_windows("DP-1")) == (False, []))

# The gate end to end: a blocked window that is NOT focused must stop capture.
seen = []
real_capture = hs.capture
hs.capture = lambda monitor: (seen.append(monitor), (None, "must not run"))[1]
gate2 = hs.Recorder.__new__(hs.Recorder)
gate2.cfg = dict(hs.DEFAULTS)
gate2.cfg_stamp = hs.config_stamp()
gate2.conn = hs.connect()
gate2.emitted = None
gate2.dropped_ocr = 0
gate2.last_hash = None
gate2.coverage_cache = {"coverageDays": 0, "coverageText": "", "coverageBasis": "default"}
gate2.coverage_at = time.time()
real_locked, real_monitor, real_active, real_visible = (
    hs.session_locked, hs.focused_monitor, hs.active_window, hs.visible_windows)
hs.session_locked = lambda: False
hs.focused_monitor = lambda: (True, "DP-1", True)
hs.active_window = lambda: (True, "com.mitchellh.ghostty", "work")
hs.visible_windows = lambda monitor: (True, [("com.mitchellh.ghostty", "work"),
                                             ("1Password", "Vault")])
try:
    gate2.tick()
finally:
    hs.session_locked, hs.focused_monitor = real_locked, real_monitor
    hs.active_window, hs.visible_windows = real_active, real_visible
    hs.capture = real_capture
check("the recorder never reaches capture with a blocked window on screen",
      not seen, seen)

print("\n-- the fail-opens the fixes themselves introduced --")

def sessions(listing, answers):
    def fake(cmd, **kwargs):
        if cmd[0] == "pgrep":
            return 1, b"", b""
        if cmd[:2] == ["loginctl", "list-sessions"]:
            return 0, listing.encode(), b""
        if cmd[:2] == ["loginctl", "show-session"]:
            return (0, answers.get(cmd[2], "no").encode(), b"")
        return 1, b"", b""
    return fake

_saved_session_id = os.environ.pop("XDG_SESSION_ID", None)
two = "2 1000 me - 920 manager - no -\n5 1000 me seat0 999 user tty2 no -\n"
check("a locked session is not masked by the systemd user manager",
      with_run(sessions(two, {"5": "yes", "2": "no"}), hs.session_locked) is True)
check("the manager session is not asked at all",
      "2" not in with_run(sessions(two, {}), hs.login_sessions),
      with_run(sessions(two, {}), hs.login_sessions))
# This used to assert False. It was the bug: with no compositor to ask, a
# desktop whose sessions all answer "no" has told us nothing, because nothing
# writes that field. Unknown, and unknown does not capture.
check("every session saying no, with no compositor to ask, is still unknown",
      with_run(sessions(two, {"5": "no"}), hs.session_locked) is None,
      with_run(sessions(two, {"5": "no"}), hs.session_locked))

blind = [{"name": "DP-1", "activeWorkspace": None, "specialWorkspace": None}]
one_client = [{"class": "1Password", "title": "V", "workspace": {"id": 1},
               "mapped": True, "hidden": False}]
check("a monitor that reports no workspaces is not an empty screen",
      with_run(hypr(blind, one_client),
               lambda: hs.visible_windows("DP-1")) == (False, []))
good_mon = [{"name": "DP-1", "activeWorkspace": {"id": 1}}]
for shape in ("1", None, {"id": None}):
    bad = [{"class": "1Password", "title": "V", "workspace": shape,
            "mapped": True, "hidden": False}]
    check("a client with workspace %r is not silently dropped" % (shape,),
          with_run(hypr(good_mon, bad),
                   lambda: hs.visible_windows("DP-1")) == (False, []))

layers = {"DP-1": {"levels": {"2": [{"namespace": "mako"}]}}}
def layer_run(cmd, **kwargs):
    if cmd[:2] == ["hyprctl", "layers"]:
        return 0, json.dumps(layers).encode(), b""
    return 1, b"", b""
probed, names = with_run(layer_run, lambda: hs.visible_layers("DP-1"))
check("layer surfaces are enumerated", probed and names == ["mako"], names)
# A notification daemon names its surface after itself, never after the app
# that raised the toast, so matching against the app blocklist catches nothing.
check("a real notification daemon is caught by the layer rules",
      hs.blocked_layer(hs.DEFAULTS, ["mako"]) != "")
check("and so are the other common ones",
      all(hs.blocked_layer(hs.DEFAULTS, [n]) for n in
          ("swaync", "dunst", "fnott", "rofi", "fuzzel")))
check("the bar and the wallpaper are not blocked",
      hs.blocked_layer(hs.DEFAULTS, ["omarchy-bar", "omarchy-background"]) == "")
check("matching a toast against the app blocklist would catch nothing",
      hs.blocked_by(hs.DEFAULTS, "mako", "") == "")
check("a monitor missing from the layer map is not an empty screen",
      with_run(layer_run, lambda: hs.visible_layers("DP-9")) == (False, []))
check("an unreadable layer list is not an empty one",
      with_run(lambda *a, **k: (1, b"", b"x"),
               lambda: hs.visible_layers("DP-1")) == (False, []))

bad_cfg = dict(hs.DEFAULTS)
open(os.environ["HINDSIGHT_CONFIG"], "w").write('{"interval": Infinity}')
check("Infinity in the config cannot stop the recorder",
      hs.load_config()["interval"] == hs.DEFAULTS["interval"],
      hs.load_config()["interval"])
os.remove(os.environ["HINDSIGHT_CONFIG"])

if _saved_session_id is not None:
    os.environ["XDG_SESSION_ID"] = _saved_session_id

print("\n-- deletion, repair and OCR must survive a hostile index --")
poison = hs.connect()
pday = os.path.join(hs.FRAMES, "2019-01-01")
os.makedirs(pday, exist_ok=True)
for i in range(60):
    fp = os.path.join(pday, "%06d-000.webp" % i)
    open(fp, "wb").write(b"x" * 1000)
    hs.store(poison, 5000 + i, fp, "app", "t", "DP-1", i, 1000)
kept_before = poison.execute(
    "SELECT COUNT(*) FROM frames WHERE path LIKE ?", (pday + "%",)).fetchone()[0]
poison.execute("UPDATE frames SET bytes=? WHERE path LIKE ?",
               (hs.MAX_FRAME_BYTES, pday + "%"))
poison.commit()
big_cfg = dict(hs.DEFAULTS); big_cfg["budgetMB"] = 4096; big_cfg["retentionDays"] = 0
hs.prune(poison, big_cfg)
kept_after = poison.execute(
    "SELECT COUNT(*) FROM frames WHERE path LIKE ?", (pday + "%",)).fetchone()[0]
check("an inflated bytes column cannot delete the archive",
      kept_after == kept_before, "%d -> %d" % (kept_before, kept_after))

unreadable = os.path.join(pday, "000001-000.webp")
os.chmod(unreadable, 0o044)
hs.harden_all()
check("a frame its owner cannot read is still repaired",
      (os.stat(unreadable).st_mode & 0o777) == 0o600,
      oct(os.stat(unreadable).st_mode & 0o777))

sym = os.path.join(pday, "888888-000.webp")
outside_target = os.path.join(TMP, "not-a-frame.png")
open(outside_target, "wb").write(b"S")
if os.path.lexists(sym):
    os.remove(sym)
os.symlink(outside_target, sym)
check("a symlinked frame never reaches the panel", not hs.inside_archive(sym))
check("a real frame still does",
      hs.inside_archive(os.path.join(pday, "000002-000.webp")))

fid = hs.store(poison, 6000, os.path.join(pday, "000003-000.webp"),
               "app", "t", "DP-1", 1, 1000)
hs._ocr_probe.update({"ok": None, "at": 0.0})   # the probe is cached now
with_run(lambda *a, **k: (127, b"", b"missing"),
         lambda: hs.note_ocr_failure(poison, fid))
hs._ocr_probe.update({"ok": None, "at": 0.0})
check("a frame captured without tesseract stays queued for retry",
      poison.execute("SELECT ocr FROM frames WHERE id=?",
                     (fid,)).fetchone()[0] == hs.OCR_TOOL_MISSING)
check("tesseract failing is not the same as a blank screen",
      with_run(lambda *a, **k: (127, b"", b""),
               lambda: hs.ocr_text(b"x", 5)) is None)

print("\n-- the fixes must converge, not merely act --")
conv = hs.connect()
cday = os.path.join(hs.FRAMES, "2018-03-03")
os.makedirs(cday, exist_ok=True)
for i in range(50):
    fp = os.path.join(cday, "%06d-000.webp" % i)
    open(fp, "wb").write(b"c" * 1000)
    hs.store(conv, 300 + i, fp, "app", "t", "DP-1", i, 1000)
conv.execute("UPDATE frames SET bytes=? WHERE path LIKE ?",
             (hs.MAX_FRAME_BYTES, cday + "%"))
conv.commit()
wide = dict(hs.DEFAULTS); wide["budgetMB"] = 4096; wide["retentionDays"] = 0
kept_before = conv.execute("SELECT COUNT(*) FROM frames WHERE path LIKE ?",
                           (cday + "%",)).fetchone()[0]
hs.prune(conv, wide)
lied = conv.execute("SELECT COUNT(*) FROM frames WHERE bytes=? AND path LIKE ?",
                    (hs.MAX_FRAME_BYTES, cday + "%")).fetchone()[0]
check("a lying size column is corrected from disk rather than obeyed",
      lied == 0, lied)
check("and nothing was deleted to achieve it",
      conv.execute("SELECT COUNT(*) FROM frames WHERE path LIKE ?",
                   (cday + "%",)).fetchone()[0] == kept_before)

spawned = []
def counting(cmd, **kwargs):
    spawned.append(" ".join(cmd))
    return 127, b"", b"gone"
hs._ocr_probe.update({"ok": None, "at": 0.0})
spinner = hs.Recorder.__new__(hs.Recorder)
spinner.cfg = dict(hs.DEFAULTS)
spinner.cfg_stamp = hs.config_stamp()
fid = hs.store(conv, 400, os.path.join(cday, "000009-000.webp"),
               "app", "t", "DP-1", 1, 1000)
conv.execute("UPDATE frames SET ocr=? WHERE id=?", (hs.OCR_TOOL_MISSING, fid))
conv.commit()
hs.run = counting
try:
    for _ in range(5):
        spinner.backfill(conv)
finally:
    hs.run = real_run
    hs._ocr_probe.update({"ok": None, "at": 0.0})
# Other frames legitimately still need reading, so the measure is how often
# backfill re-asks whether the tool exists, not how many processes run.
probes = [cmd for cmd in spawned if cmd.startswith("tesseract --version")]
check("the tesseract probe is cached rather than run every pass",
      len(probes) <= 1, "%d probes over 5 passes" % len(probes))
check("a frame marked tool-missing is not retried while the tool is missing",
      conv.execute("SELECT ocr FROM frames WHERE id=?",
                   (fid,)).fetchone()[0] == hs.OCR_TOOL_MISSING)

print("\n-- pause must not claim what it did not do --")
import io as _io, contextlib as _ctx
if os.path.lexists(hs.PAUSE_MARKER):
    os.remove(hs.PAUSE_MARKER)
os.symlink(os.path.join(TMP, "no", "such", "place"), hs.PAUSE_MARKER)
_buf = _io.StringIO()
with _ctx.redirect_stdout(_buf):
    rc = hs.set_paused(True)
check("a pause that could not be written reports failure", rc == 1, rc)
check("and reports the state the marker is actually in",
      '"paused": false' in _buf.getvalue(), _buf.getvalue().strip())
os.remove(hs.PAUSE_MARKER)
_buf = _io.StringIO()
with _ctx.redirect_stdout(_buf):
    rc = hs.set_paused(True)
check("a pause that worked still reports success",
      rc == 0 and '"paused": true' in _buf.getvalue(), _buf.getvalue().strip())
hs.set_paused(False)

print("\n-- session classes --")
def greeter(cmd, **kwargs):
    if cmd[0] == "pgrep":
        return 1, b"", b""
    if cmd[:2] == ["loginctl", "list-sessions"]:
        return 0, b"7 1000 me seat0 900 greeter tty1 no -\n", b""
    if cmd[:2] == ["loginctl", "show-session"]:
        return (0, b"yes", b"") if cmd[2] == "7" else (1, b"", b"")
    return 1, b"", b""
_had = os.environ.pop("XDG_SESSION_ID", None)
check("a greeter session is asked, not discarded",
      with_run(greeter, hs.session_locked) is True)
if _had is not None:
    os.environ["XDG_SESSION_ID"] = _had

print("\n-- the index and its sidecars are all private --")
sidecar = hs.connect()
sidecar.execute("CREATE TABLE IF NOT EXISTS touch(x)")
sidecar.execute("INSERT INTO touch VALUES(1)")
sidecar.commit()
# Under the suite's 0o077 umask SQLite would create these privately anyway,
# so the reordering of secure_db_files() is only actually tested at 0o000.
_umask_was = os.umask(0o000)
try:
    for _suffix in ("", "-wal", "-shm"):
        try:
            os.chmod(hs.DB_PATH + _suffix, 0o644)
        except OSError:
            pass
    hs.connect().execute("CREATE TABLE IF NOT EXISTS touch2(x)")
finally:
    os.umask(_umask_was)
present = [s for s in ("", "-wal", "-shm") if os.path.exists(hs.DB_PATH + s)]
check("the WAL exists to be checked at all", "-wal" in present, present)
for suffix in present:
    check("the index%s is private" % (suffix or ""),
          (os.stat(hs.DB_PATH + suffix).st_mode & 0o777) == 0o600,
          oct(os.stat(hs.DB_PATH + suffix).st_mode & 0o777))

print("\n-- the budget must survive a column edited in either direction --")
both = hs.connect()
bday = os.path.join(hs.FRAMES, "2017-04-04")
os.makedirs(bday, exist_ok=True)
for i in range(40):
    fp = os.path.join(bday, "%06d-000.webp" % i)
    open(fp, "wb").write(b"b" * 200 * 1024)
    hs.store(both, 200 + i, fp, "app", "t", "DP-1", i, 200 * 1024)
tight_cfg = dict(hs.DEFAULTS)
tight_cfg["budgetMB"] = 4
tight_cfg["retentionDays"] = 0

both.execute("UPDATE frames SET bytes=0")
both.commit()
hs._archive_measure.update({"bytes": None, "at": 0.0})
check("a column edited down to zero does not switch the budget off",
      hs.measure_archive() > 4 * 1024 * 1024, hs.measure_archive())
removed_down = hs.prune(both, tight_cfg)
check("and pruning still happens", removed_down > 0, removed_down)

both.execute("UPDATE frames SET bytes=NULL")
both.commit()
hs._archive_measure.update({"bytes": None, "at": 0.0})
check("a NULL column is handled the same way",
      hs.archive_total(hs.usage(both)[1]) > 0)

# convergence, measured rather than assumed
calls = {"n": 0}
real_descend = hs.archive_descend
def counted(path):
    calls["n"] += 1
    return real_descend(path)
hs.archive_descend = counted
try:
    roomy = dict(hs.DEFAULTS); roomy["budgetMB"] = 4096; roomy["retentionDays"] = 0
    hs._archive_measure.update({"bytes": None, "at": 0.0})
    hs.prune(both, roomy)
    first_pass = calls["n"]
    calls["n"] = 0
    hs.prune(both, roomy)
    second_pass = calls["n"]
finally:
    hs.archive_descend = real_descend
check("prune converges instead of re-walking the archive every tick",
      second_pass == 0, "first %d calls, second %d" % (first_pass, second_pass))

print("\n-- overlays: drawn ones block, mapped-but-invisible ones do not --")
def layer_map(surfaces):
    def fake(cmd, **kwargs):
        if cmd[:2] == ["hyprctl", "layers"]:
            return 0, json.dumps({"DP-1": {"levels": {"2": surfaces}}}).encode(), b""
        return 1, b"", b""
    return fake

drawn = [{"namespace": "swaync-control-center", "w": 400, "h": 300, "alpha": 1}]
hidden = [{"namespace": "swaync-control-center", "w": 0, "h": 0, "alpha": 0}]
check("a drawn notification surface is seen",
      with_run(layer_map(drawn), lambda: hs.visible_layers("DP-1"))[1] ==
      ["swaync-control-center"])
check("one kept mapped at zero size is not",
      with_run(layer_map(hidden), lambda: hs.visible_layers("DP-1")) == (True, []))
check("a surface with no namespace is unreadable, not harmless",
      with_run(layer_map([{"w": 10, "h": 10}]),
               lambda: hs.visible_layers("DP-1")) == (False, []))
check("a widget merely containing a rule word is not blocked",
      hs.blocked_layer(hs.DEFAULTS, ["eww-notifications-bar"]) == "")
check("but the daemon's own surfaces are",
      hs.blocked_layer(hs.DEFAULTS, ["swaync-control-center"]) == "swaync")
check("an empty answer from hyprctl is not an empty screen",
      with_run(lambda *a, **k: (0, b"   ", b""),
               lambda: hs.visible_layers("DP-1")) == (False, []))
check("an explicit empty list disables layer blocking",
      hs.blocked_layer({"blocklistLayers": []}, ["mako"]) == "")
check("and an absent key restores the defaults",
      hs.blocked_layer({}, ["mako"]) == "mako")

# the whole gate, with an overlay rather than a window
overlay_seen = []
real_capture2 = hs.capture
hs.capture = lambda m: (overlay_seen.append(m), (None, "must not run"))[1]
g3 = hs.Recorder.__new__(hs.Recorder)
g3.cfg = dict(hs.DEFAULTS)
# tick() reloads config when the stamp differs, which would discard the dict
# set above and test whatever is on disk instead.
g3.cfg_stamp = hs.config_stamp()
g3.conn = hs.connect()
g3.emitted = None
g3.dropped_ocr = 0
g3.last_hash = None
g3.coverage_cache = {"coverageDays": 0, "coverageText": "", "coverageBasis": "default"}
g3.coverage_at = time.time()
saved = (hs.session_locked, hs.focused_monitor, hs.active_window,
         hs.visible_windows, hs.visible_layers)
hs.session_locked = lambda: False
hs.focused_monitor = lambda: (True, "DP-1", True)
hs.active_window = lambda: (True, "com.mitchellh.ghostty", "work")
hs.visible_windows = lambda m: (True, [("com.mitchellh.ghostty", "work")])
hs.visible_layers = lambda m: (True, ["mako"])
try:
    g3.tick()
finally:
    (hs.session_locked, hs.focused_monitor, hs.active_window,
     hs.visible_windows, hs.visible_layers) = saved
    hs.capture = real_capture2
check("a notification on screen stops the recorder reaching capture",
      not overlay_seen, overlay_seen)

print("\n-- the rules themselves, so a dropped token cannot pass unnoticed --")
for daemon in ("mako", "swaync", "dunst", "fnott", "notifications",
               "swaylock", "hyprlock", "wlogout", "rofi", "fuzzel"):
    check("%s is in the shipped layer rules" % daemon,
          daemon in hs.DEFAULTS["blocklistLayers"])

# Namespaces name themselves at one end or the other; a widget that merely
# mentions the word is neither.
for namespace, want in (
        ("mako", True), ("notifications", True),
        ("swaync-control-center", True), ("swaync_control_center", True),
        ("dunst_popup", True), ("mako.surface", True),
        ("org.freedesktop.Notifications", True), ("ags-notifications", True),
        ("eww-notifications-bar", False),
    ("quickshell:notifications", True),
    ("shell/notifications", True),
    ("notification-popups", True),
    ("notification-center", True),
    ("launcher", True),
    ("lockscreen", True),
    # Accepted, and documented rather than wished away: a bar that names
    # itself after what it holds is indistinguishable from a daemon.
    ("notifications-bar", True), ("my-notifications-widget", False),
        ("omarchy-bar", False), ("omarchy-background", False)):
    got = bool(hs.blocked_layer(hs.DEFAULTS, [namespace]))
    check("%-32s %s" % (namespace, "blocks" if want else "does not block"),
          got == want, "blocked=%s" % got)

print("\n-- a frame is re-checked after the shutter, not only before it --")
late_seen = []
real_capture3 = hs.capture
hs.capture = lambda m: (ppm_frame, None)
ppm_frame = ppm(8, 8, lambda x, y: (x * 9, y * 9, 0))
g4 = hs.Recorder.__new__(hs.Recorder)
g4.cfg = dict(hs.DEFAULTS)
g4.cfg_stamp = hs.config_stamp()
g4.conn = hs.connect()
g4.emitted = None
g4.dropped_ocr = 0
g4.last_hash = None
g4.coverage_cache = {"coverageDays": 0, "coverageText": "", "coverageBasis": "default"}
g4.coverage_at = time.time()
g4.jobs = __import__("queue").Queue(maxsize=4)

saved2 = (hs.session_locked, hs.focused_monitor, hs.active_window,
          hs.visible_windows, hs.visible_layers, hs.encode_webp)
calls = {"lock": 0}
def locks_late():
    calls["lock"] += 1
    return calls["lock"] > 1      # clear before the shutter, locked after
hs.session_locked = locks_late
hs.focused_monitor = lambda: (True, "DP-1", True)
hs.active_window = lambda: (True, "ghostty", "work")
hs.visible_windows = lambda m: (True, [("ghostty", "work")])
hs.visible_layers = lambda m: (True, [])
hs.encode_webp = lambda p, q: (late_seen.append("encoded"), (b"x", None))[1]
frames_before = hs.usage(g4.conn)[0]
try:
    g4.tick()
finally:
    (hs.session_locked, hs.focused_monitor, hs.active_window,
     hs.visible_windows, hs.visible_layers, hs.encode_webp) = saved2
    hs.capture = real_capture3
check("a screen locked between the gate and the shutter drops the frame",
      not late_seen and hs.usage(g4.conn)[0] == frames_before,
      "encoded=%s" % late_seen)
check("and the lock was asked again after the capture", calls["lock"] == 2,
      calls["lock"])


print("\n-- one gate, and every probe that cannot answer refuses --")
# Each row silences one probe and leaves the rest clear. The frame must be
# refused for every one of them: a probe that cannot answer is not a probe
# that said yes. Table-driven so a probe added to capture_gate without a
# fail-closed branch fails here rather than in the field.
CLEAR = {
    "active_window": lambda: (True, "ghostty", "work"),
    "visible_windows": lambda m: (True, [("ghostty", "work")]),
    "visible_layers": lambda m: (True, []),
    "session_locked": lambda: False,
}
MUTE = {
    "active_window": ((lambda: (False, "", "")), "focused window unknown"),
    "visible_windows": ((lambda m: (False, [])), "window list unknown"),
    "visible_layers": ((lambda m: (False, [])), "overlay list unknown"),
    "session_locked": ((lambda: None), "lock state unknown"),
}
saved3 = {k: getattr(hs, k) for k in CLEAR}
try:
    for probe, (mute, want) in sorted(MUTE.items()):
        for name, fn in CLEAR.items():
            setattr(hs, name, fn)
        setattr(hs, probe, mute)
        reason, rule, _, _ = hs.capture_gate(dict(hs.DEFAULTS), "DP-1")
        check("a silent %s refuses the frame" % probe, reason == want,
              "reason=%r rule=%r" % (reason, rule))
    for name, fn in CLEAR.items():
        setattr(hs, name, fn)
    reason, rule, app, _ = hs.capture_gate(dict(hs.DEFAULTS), "DP-1")
    check("and an answered, clear screen is captured",
          reason == "" and rule == "" and app == "ghostty",
          "reason=%r rule=%r app=%r" % (reason, rule, app))
    # A blocked overlay must be named, not merely refused: the panel shows
    # the rule so the user can see why the recorder went quiet.
    hs.visible_layers = lambda m: (True, ["swaync-control-center"])
    reason, rule, _, _ = hs.capture_gate(dict(hs.DEFAULTS), "DP-1")
    check("a blocked overlay is refused and named",
          reason == "blocked" and rule == "swaync", "%r %r" % (reason, rule))
finally:
    for name, fn in saved3.items():
        setattr(hs, name, fn)


print("\n-- doctor refuses on the same terms the recorder does --")
# doctor takes a real screenshot, so it needs the recorder's gate, not a
# copy of it. It had one: the copy treated a probe that could not answer as
# a clear screen, and photographed whatever was on it.
shots = []
saved4 = {k: getattr(hs, k) for k in
          ("active_window", "visible_windows", "visible_layers",
           "session_locked", "focused_monitor", "capture")}
try:
    hs.capture = lambda m: (shots.append(m), (ppm(4, 4, lambda x, y: (0, 0, 0)), ""))[1]
    hs.focused_monitor = lambda: (True, "DP-1", True)
    for probe, (mute, _want) in sorted(MUTE.items()):
        for name, fn in CLEAR.items():
            setattr(hs, name, fn)
        setattr(hs, probe, mute)
        shots[:] = []
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            hs.cmd_doctor(None)
        report = _json.loads(buf.getvalue())
        detail = [c for c in report["checks"] if c["name"] == "capture"][0]
        check("doctor takes no picture when %s is silent" % probe,
              not shots and detail["detail"].startswith("not attempted"),
              "shots=%s detail=%r" % (shots, detail["detail"]))
    # And a blocked window, which is the case doctor already handled.
    for name, fn in CLEAR.items():
        setattr(hs, name, fn)
    hs.visible_layers = lambda m: (True, ["mako"])
    shots[:] = []
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        hs.cmd_doctor(None)
    check("doctor takes no picture while a notification is up", not shots,
          shots)
finally:
    for name, fn in saved4.items():
        setattr(hs, name, fn)

# The point of the refactor: both callers ask the same function, so a gate
# cannot be added to one and forgotten in the other.
gate_calls = []
real_gate = hs.capture_gate
hs.capture_gate = lambda cfg, m: (gate_calls.append(m), ("paused", "", "", ""))[1]
saved5 = (hs.focused_monitor, hs.capture)
try:
    hs.focused_monitor = lambda: (True, "DP-1", True)
    hs.capture = lambda m: (None, "should not be reached")
    g5 = hs.Recorder.__new__(hs.Recorder)
    g5.cfg = dict(hs.DEFAULTS)
    g5.cfg_stamp = hs.config_stamp()
    g5.conn = hs.connect()
    g5.emitted = None
    g5.dropped_ocr = 0
    g5.last_hash = None
    g5.coverage_cache = {"coverageDays": 0, "coverageText": "",
                         "coverageBasis": "default"}
    g5.coverage_at = time.time()
    g5.jobs = __import__("queue").Queue(maxsize=4)
    g5.tick()
    tick_calls = len(gate_calls)
    with contextlib.redirect_stdout(io.StringIO()):
        hs.cmd_doctor(None)
    check("the recorder and doctor ask the one gate",
          tick_calls >= 1 and len(gate_calls) > tick_calls,
          "tick=%d doctor=%d" % (tick_calls, len(gate_calls) - tick_calls))
finally:
    hs.capture_gate = real_gate
    (hs.focused_monitor, hs.capture) = saved5



print("\n-- a clear screen goes all the way into the index --")
# Every other tick test asserts that some gate refused. None of them ran a
# frame through to the end, so when capture_gate stopped handing back the
# window title, store() raised NameError, the loop logged "tick failed", and
# 71 frames sat on disk with no row pointing at them - invisible to search
# and, because forget works off rows, untouched by "forget all".
saved6 = {k: getattr(hs, k) for k in
          ("active_window", "visible_windows", "visible_layers",
           "session_locked", "focused_monitor", "capture", "encode_webp")}
g6 = hs.Recorder.__new__(hs.Recorder)
g6.cfg = dict(hs.DEFAULTS)
g6.cfg_stamp = hs.config_stamp()
g6.conn = hs.connect()
g6.emitted = None
g6.dropped_ocr = 0
g6.last_hash = None
g6.coverage_cache = {"coverageDays": 0, "coverageText": "",
                     "coverageBasis": "default"}
g6.coverage_at = time.time()
g6.jobs = __import__("queue").Queue(maxsize=8)
try:
    hs.focused_monitor = lambda: (True, "DP-1", True)
    hs.active_window = lambda: (True, "ghostty", "a distinctive title")
    hs.visible_windows = lambda m: (True, [("ghostty", "a distinctive title")])
    hs.visible_layers = lambda m: (True, [])
    hs.session_locked = lambda: False
    hs.capture = lambda m: (ppm(8, 8, lambda x, y: ((x * 31) % 256, y * 7, 9)), "")
    hs.encode_webp = lambda p, q: (b"RIFFfake", "")
    before = hs.usage(g6.conn)[0]
    g6.tick()
    after = hs.usage(g6.conn)[0]
    check("an unblocked tick stores exactly one frame", after == before + 1,
          "%d -> %d" % (before, after))
    row = g6.conn.execute(
        "SELECT app, title, path FROM frames ORDER BY id DESC LIMIT 1").fetchone()
    check("and records the app and title the gate saw",
          row and row[0] == "ghostty" and row[1] == "a distinctive title", row)
    check("and the file it wrote is the file the row points at",
          row and os.path.exists(row[2]), row and row[2])
    # The orphan is the thing that actually hurt: a frame on disk that no row
    # names is a frame "forget all" cannot reach.
    known = {r[0] for r in g6.conn.execute("SELECT path FROM frames")}
    orphans = []
    # Today's directory only: other tests plant frames on other days on
    # purpose, and one of them is a deliberate orphan.
    for base, _dirs, files in os.walk(
            os.path.join(hs.DATA, "frames", time.strftime("%Y-%m-%d"))):
        for f in files:
            if f.endswith(".webp") and os.path.join(base, f) not in known:
                orphans.append(os.path.join(base, f))
    check("no frame is left on disk without a row naming it", not orphans,
          orphans[:3])
finally:
    for name, fn in saved6.items():
        setattr(hs, name, fn)


print("\n-- an orphaned frame is reclaimed, a fresh one is left alone --")
# forget walks the index, so a frame no row names survives "forget all".
orphan_day = os.path.join(hs.DATA, "frames", "2021-06-01")
os.makedirs(orphan_day, exist_ok=True)
stale = os.path.join(orphan_day, "120000-000.webp")
fresh = os.path.join(orphan_day, "120004-000.webp")
for f in (stale, fresh):
    io.open(f, "wb").write(b"RIFForphan")
old_time = time.time() - 3600
os.utime(stale, (old_time, old_time))
conn_o = hs.connect()
named = os.path.join(orphan_day, "120008-000.webp")
io.open(named, "wb").write(b"RIFFkept")
os.utime(named, (old_time, old_time))
hs.store(conn_o, old_time, named, "ghostty", "kept", "DP-1", 1, 8)
swept = hs.sweep_orphans(conn_o)
check("the orphan past the grace window is removed",
      swept >= 1 and not os.path.exists(stale), "swept=%d" % swept)
check("a frame written moments ago is not mistaken for an orphan",
      os.path.exists(fresh))
check("and a frame the index does name is untouched", os.path.exists(named))

# The guard that matters: a planted symlink must not turn the sweep into a
# way to delete something outside the archive.
outside = os.path.join(TMP, "not-a-frame.webp")
io.open(outside, "wb").write(b"keep me")
link = os.path.join(orphan_day, "130000-000.webp")
os.symlink(outside, link)
os.utime(link, (old_time, old_time), follow_symlinks=False)
swept_link = hs.sweep_orphans(conn_o)
check("the sweep does not follow a symlink out of the archive",
      os.path.exists(outside), outside)
# It also has to admit it removed nothing. Counting the refusal made prune
# call empty_dirs() and drop the cached archive size on every sweep, for as
# long as the symlink sat there.
check("and reports no removal for the file it refused", swept_link == 0,
      swept_link)
check("so a refused file does not keep re-triggering the sweep",
      hs.sweep_orphans(conn_o) == 0 and os.path.lexists(link))

print("\n-- what a helper hands back is bounded --")
import resource

# These run real processes. The whole point is that a fake subprocess cannot
# show a deadlock, a leaked descriptor or a surviving grandchild.

check("returns a helper's output and its exit code",
      hs.run(["sh", "-c", "printf hello"]) == (0, b"hello", b""))
check("passes a failure through with its stderr",
      hs.run(["sh", "-c", "printf oops >&2; exit 3"]) == (3, b"", b"oops"))
check("reports a helper that is not installed",
      hs.run(["hindsight-no-such-binary"])[0] == hs.RUN_MISSING)

# A pipe holds 64 KB. Writing 8 MB into one while the child writes 8 MB back
# deadlocks anything that does not drain both ends at once - which is the
# shape of every frame this program pipes through magick and tesseract.
big = b"x" * (8 * 1024 * 1024)
code, out, _ = hs.run(["cat"], timeout=30, stdin_bytes=big, limit=16 * 1024 * 1024)
check("pushes more into a helper than a pipe buffer holds", code == 0, code)
check("and gets every byte back", out == big, len(out))

started = time.time()
code, out, err = hs.run(["cat", "/dev/zero"], timeout=30, limit=1024 * 1024)
elapsed = time.time() - started
check("stops a helper that writes past its ceiling", code == hs.RUN_OVERFLOW, code)
check("and keeps none of what it wrote", out == b"", len(out))
check("and says so in stderr", b"over" in err, err)
# The timeout is not the bound. A 4K frame grabber can produce gigabytes in
# well under ten seconds, and by then the memory is already gone.
check("and does not wait out the timeout to do it", elapsed < 10, elapsed)

before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
hs.run(["cat", "/dev/zero"], timeout=30, limit=4 * 1024 * 1024)
grew = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before
check("and never holds more than the ceiling while draining",
      grew < 32 * 1024, "%d KB" % grew)

code, out, err = hs.run(
    ["sh", "-c", "head -c 4000000 /dev/zero >&2; printf done"], timeout=30)
check("caps stderr too", len(err) <= hs.CAP_STDERR, len(err))
check("without losing stdout", out == b"done", out)

code, _, _ = hs.run(["sleep", "30"], timeout=0.5)
check("stops a helper that runs past its timeout", code == hs.RUN_TIMEOUT, code)

# magick forks delegates and tesseract forks workers. Signalling only the
# child leaves those holding the pipe open and still writing.
pidfile = os.path.join(TMP, "grandchild.pid")
hs.run(["sh", "-c", "sleep 30 & echo $! > %s; wait" % pidfile], timeout=1)
gpid = int(io.open(pidfile).read().strip())
alive = True
for _ in range(60):
    try:
        os.kill(gpid, 0)
    except OSError:
        alive = False
        break
    time.sleep(0.05)
check("kills the whole process group, not just the child it started",
      not alive, gpid)

fds = os.path.join("/proc", str(os.getpid()), "fd")
open_before = len(os.listdir(fds))
for _ in range(15):
    hs.run(["sh", "-c", "printf hi"])
    hs.run(["sleep", "30"], timeout=0.2)
    hs.run(["cat", "/dev/zero"], timeout=30, limit=64 * 1024)
open_after = len(os.listdir(fds))
check("leaks no descriptors across kills and timeouts",
      open_after <= open_before + 2, (open_before, open_after))

print("\n-- a frame is whole or it is not stored --")

whole = ppm(8, 4, lambda x, y: (1, 2, 3))
check("accepts a complete P6 frame", hs.ppm_complete(whole))
check("rejects one cut short", not hs.ppm_complete(whole[:-1]))
check("keeps one with a byte trailing the pixels", hs.ppm_complete(whole + b"\x00"))
check("rejects a buffer that is not a PPM", not hs.ppm_complete(b"\x89PNG\r\n\x1a\n"))
check("rejects a header with no pixels behind it",
      not hs.ppm_complete(b"P6\n8 4\n255\n"))
check("skips a comment in the header",
      hs.ppm_complete(b"P6\n# grim wrote this\n8 4\n255\n" + b"\x00" * 96))
check("counts two bytes a channel when maxval needs them",
      hs.ppm_complete(b"P6\n2 2\n65535\n" + b"\x00" * 24))
check("a ceiling large enough for an 8K frame",
      hs.CAP_PPM > 7680 * 4320 * 3, hs.CAP_PPM)
check("rejects a dimension too long to be real",
      not hs.ppm_complete(b"P6\n999999999 1\n255\n"))

# A killed grim still returns everything it managed to write. Half a frame
# decodes cleanly into a picture of the top of the screen, and would be
# stored, OCR'd and searchable like any other.
half = whole[:len(whole) // 2]
check("capture refuses a truncated frame",
      with_run(lambda *a, **k: (0, half, b""), lambda: hs.capture(None))
      == (None, "incomplete frame from grim"))
check("and accepts a whole one",
      with_run(lambda *a, **k: (0, whole, b""), lambda: hs.capture(None))
      == (whole, ""))

check("accepts a whole RIFF/WEBP file",
      hs.webp_complete(b"RIFF" + (12).to_bytes(4, "little") + b"WEBPVP8 abcd"))
check("rejects one cut short",
      not hs.webp_complete(b"RIFF" + (12).to_bytes(4, "little") + b"WEBPVP8 abc"))
check("keeps one with a byte trailing the payload",
      hs.webp_complete(b"RIFF" + (12).to_bytes(4, "little") + b"WEBPVP8 abcde"))
check("rejects a buffer that is not a RIFF container",
      not hs.webp_complete(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8))
check("the encoder refuses a truncated WebP",
      with_run(lambda *a, **k: (0, b"RIFF" + (99).to_bytes(4, "little") + b"WEBPxx", b""),
               lambda: hs.encode_webp(whole, 60))
      == (None, "incomplete frame from magick"))

real_read = hs.read_frame_bytes
hs.read_frame_bytes = lambda path: b"webp-bytes"
try:
    decode = lambda body: (lambda cmd, **k:
                           (0, body, b"") if cmd[0] == "magick" else (0, b"words", b""))
    check("OCR refuses a half-decoded frame",
          with_run(decode(half), lambda: hs.ocr_frame_file("x", 30)) is None)
    check("and reads a whole one",
          with_run(decode(whole), lambda: hs.ocr_frame_file("x", 30)) == "words")
finally:
    hs.read_frame_bytes = real_read

caps = {}

def record_cap(cmd, **kwargs):
    # A probe takes the default, so read it the way run() would.
    caps[cmd[0]] = kwargs.get("limit", hs.CAP_PROBE)
    return 0, whole, b""

with_run(record_cap, lambda: hs.capture(None))
with_run(record_cap, lambda: hs.encode_webp(whole, 60))
with_run(record_cap, lambda: hs.ocr_text(whole, 30))
with_run(record_cap, hs.active_window)
import inspect
check("an unlabelled call still gets the probe ceiling",
      inspect.signature(real_run).parameters["limit"].default == hs.CAP_PROBE)
check("every stage names its own ceiling",
      (caps.get("grim"), caps.get("magick"), caps.get("nice"), caps.get("hyprctl"))
      == (hs.CAP_PPM, hs.CAP_WEBP, hs.CAP_OCR, hs.CAP_PROBE), caps)

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
if FAIL:
    print("failed: " + ", ".join(FAIL))
sys.exit(1 if FAIL else 0)
