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
check("survives an invalid regex instead of crashing",
      hs.blocked_by(broken, "ghostty", "anything") == "")

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
for n in range(40):
    path = os.path.join(hs.FRAMES, "bulk%d.webp" % n)
    open(path, "wb").write(b"y" * 1000)
    hs.store(big, now - (100 - n) * 60, path, "app", "t", "DP-1", n, 1024 * 1024)
before, bytes_before = hs.usage(big)
cfg_small = dict(hs.DEFAULTS)
cfg_small["budgetMB"] = 10
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
for index, text in enumerate(variants):
    fid = hs.store(dedup, now + index,
                   os.path.join(hs.FRAMES, "2021-05-05", "dup%d.webp" % index),
                   "app", "t", "DP-1", random.getrandbits(hs.HASH_BITS), 1000)
    hs.attach_text(dedup, fid, text, "app", "t")
fid = hs.store(dedup, now + 9,
               os.path.join(hs.FRAMES, "2021-05-05", "other.webp"),
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
check("a locker that is definitely absent counts as unlocked",
      with_run(answered_no, hs.session_locked) is False,
      with_run(answered_no, hs.session_locked))

def says_locked(cmd, **kwargs):
    if cmd[0] == "pgrep":
        return 1, b"", b""
    return 0, b"yes", b""
check("loginctl reporting a locked session is believed",
      with_run(says_locked, hs.session_locked) is True)

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
check("a symlinked config is ignored", hs.read_private(cfg_link) is None)

huge = os.path.join(TMP, "huge.json")
with open(huge, "w") as fh:
    fh.write("{}" + " " * (hs.MAX_TEXT_BYTES + 10))
check("an oversized config is ignored", hs.read_private(huge) is None)

small = os.path.join(TMP, "small.json")
open(small, "w").write('{"ok": true}')
check("a normal file still reads", hs.read_private(small) == '{"ok": true}')

check("writing refuses to go through a symlink",
      hs.write_private(cfg_link, "overwritten") is False)
check("and the link target is untouched",
      open(bait).read() == "not ours")
os.remove(cfg_link)

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
if FAIL:
    print("failed: " + ", ".join(FAIL))
sys.exit(1 if FAIL else 0)
