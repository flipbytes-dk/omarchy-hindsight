import QtQuick
import Quickshell
import Quickshell.Io

// Supervises the recorder. Capture, change detection, the OCR queue, the
// blocklist and pruning all live in bin/hindsight, which runs and is tested
// without the shell. This side keeps it alive, mirrors what it says, and
// forwards searches.
Item {
  id: root

  property bool paused: false
  property bool capturing: false
  property int frames: 0
  property int today: 0
  property real bytes: 0
  property real budget: 0
  property int pendingOcr: 0
  property string reason: ""
  property string blockedBy: ""
  property string lastApp: ""
  property bool recorderRunning: false

  // Disk is spent in gigabytes but felt in days, so the recorder reports both
  // and the panel offers the choice in the unit people actually think in.
  property string coverageText: ""
  property int coverageDays: 0
  property string coverageBasis: ""
  property var budgetOptions: []

  // Search results are pulled on demand rather than streamed: the recorder
  // never puts screen text on the state channel, so nothing readable sits in
  // the shell unless somebody actually asked for it.
  property var results: []
  property string query: ""
  property bool searching: false

  readonly property string helperPath: {
    var url = Qt.resolvedUrl("bin/hindsight").toString()
    return url.indexOf("file://") === 0 ? url.substring(7) : url
  }

  readonly property real fillFraction: budget > 0 ? Math.min(1, bytes / budget) : 0

  function humanBytes(value) {
    if (!value || value < 1024) return (value || 0) + " B"
    if (value < 1048576) return (value / 1024).toFixed(0) + " KB"
    if (value < 1073741824) return (value / 1048576).toFixed(0) + " MB"
    return (value / 1073741824).toFixed(1) + " GB"
  }

  function search(text) {
    root.query = text
    if (!text || text.trim() === "") {
      root.results = []
      root.searching = false
      searcher.running = false
      return
    }
    root.searching = true
    searcher.running = false
    searcher.command = [root.helperPath, "search", text]
    searcher.running = true
  }

  // Pause gets its own Process. It used to share one with forget(), and a
  // command assigned to a Process that is already running is ignored - so a
  // pause pressed during a "forget everything" never ran, while the bar had
  // already switched to the paused glyph and the panel said nothing was
  // being captured.
  function setPaused(value) {
    pauser.running = false
    pauser.answered = false
    pauser.command = [root.helperPath, value ? "pause" : "resume"]
    pauser.running = true
  }

  // The options carry their own "about N weeks" text, computed from this
  // machine's measured capture rate rather than from a table of guesses.
  function refreshBudget() {
    budgeter.running = false
    budgeter.command = [root.helperPath, "budget", "--json"]
    budgeter.running = true
  }

  function setBudget(megabytes) {
    budgeter.running = false
    budgeter.command = [root.helperPath, "budget", String(megabytes), "--json"]
    budgeter.running = true
  }

  function forget(target) {
    control.running = false
    control.command = [root.helperPath, "forget", target]
    control.running = true
    root.results = []
    root.query = ""
  }

  Process {
    id: recorder
    running: true
    command: [root.helperPath, "watch"]

    stdout: SplitParser {
      onRead: function (line) {
        var text = String(line).trim()
        if (text === "") return
        try {
          var data = JSON.parse(text)
          root.paused = data.paused === true
          root.capturing = data.capturing === true
          root.frames = data.frames || 0
          root.today = data.today || 0
          root.bytes = data.bytes || 0
          root.budget = data.budget || 0
          root.pendingOcr = data.pendingOcr || 0
          root.reason = data.reason || ""
          root.blockedBy = data.blockedBy || ""
          root.lastApp = data.app || ""
          if (data.coverageText !== undefined) {
            root.coverageText = data.coverageText || ""
            root.coverageDays = data.coverageDays || 0
            root.coverageBasis = data.coverageBasis || ""
          }
        } catch (e) {
          // Diagnostic noise on stdout is not state.
        }
      }
    }

    // Without this the recorder's diagnostics go into a pipe nobody reads:
    // every "config unreadable", "refusing to delete", "tightened
    // permissions" line was invisible, including while debugging this.
    stderr: SplitParser {
      onRead: function (line) {
        var text = String(line).trim()
        if (text !== "") console.warn("hindsight: " + text)
      }
    }

    onRunningChanged: root.recorderRunning = recorder.running

    onExited: function (exitCode) {
      root.recorderRunning = false
      console.warn("hindsight: recorder exited (" + exitCode + "), restarting")
      restart.start()
    }
  }

  Process {
    id: searcher
    running: false
    stdout: StdioCollector {
      onStreamFinished: {
        root.searching = false
        try {
          var data = JSON.parse(String(this.text))
          if (data.query === root.query || !root.query) root.results = data.results || []
        } catch (e) {
          root.results = []
        }
      }
    }
  }

  Process {
    id: budgeter
    running: false
    stdout: StdioCollector {
      onStreamFinished: {
        try {
          var data = JSON.parse(String(this.text))
          root.budgetOptions = data.options || []
          root.budget = data.budgetBytes || root.budget
          root.coverageText = data.text || root.coverageText
          root.coverageDays = Math.round(data.coverageDays || 0)
          root.coverageBasis = data.basis || root.coverageBasis
          root.bytes = data.usedBytes !== undefined ? data.usedBytes : root.bytes
          root.frames = data.frames !== undefined ? data.frames : root.frames
        } catch (e) {
          // Leave the last good numbers on screen rather than blanking them.
        }
      }
    }
  }

  Process {
    id: control
    running: false
  }

  // The recorder is the authority on whether it is paused: reporting it
  // optimistically here is what let the bar claim "paused" while capture
  // carried on. root.paused now changes only when the recorder says so.
  Process {
    id: pauser
    running: false
    property bool answered: false

    stdout: StdioCollector {
      onStreamFinished: {
        try {
          var data = JSON.parse(String(this.text))
          if (data.paused !== undefined) {
            root.paused = data.paused === true
            pauser.answered = true
          }
        } catch (e) {
          // Left to onExited: this is the privacy control, so a pause that
          // said nothing must not pass silently.
        }
      }
    }

    // The helper exits non-zero when the marker could not be written, and it
    // can die before printing anything at all. Either way the user pressed
    // pause and needs to know it did not take.
    onExited: function (exitCode) {
      // Reset on start, not here: exit and stdout drain are separate events,
      // and clearing the flag on exit made the warning off by one run.
      if (exitCode !== 0 || !pauser.answered)
        console.warn("hindsight: pause/resume did not take (exit " + exitCode + ")")
    }
  }

  Timer {
    id: restart
    interval: 2000
    repeat: false
    onTriggered: recorder.running = true
  }
}
