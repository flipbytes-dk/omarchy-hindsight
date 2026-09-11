import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

Panel {
  id: root

  moduleName: "dhirajkhanna.hindsight"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  property int cursor: 0

  readonly property var service: bar && bar.shell ? bar.shell.serviceFor("dhirajkhanna.hindsight") : null
  readonly property bool paused: service ? service.paused === true : false
  readonly property int frames: service ? service.frames : 0
  readonly property int today: service ? service.today : 0
  readonly property int pendingOcr: service ? service.pendingOcr : 0
  readonly property real bytes: service ? service.bytes : 0
  readonly property real budget: service ? service.budget : 0
  readonly property var results: service ? service.results : []
  readonly property bool searching: service ? service.searching === true : false
  readonly property string query: service ? service.query : ""
  readonly property string blockedBy: service ? service.blockedBy : ""
  readonly property string coverageText: service ? service.coverageText : ""
  readonly property string coverageBasis: service ? service.coverageBasis : ""
  readonly property var budgetOptions: service ? service.budgetOptions : []

  property bool showStorage: false

  function open() { root.controller.show() }
  function close() { root.controller.hide() }

  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.hostWidget || root, direction)
    return false
  }

  function sizeText(value) {
    return root.service ? root.service.humanBytes(value) : "0 B"
  }

  function moveCursor(delta) {
    var count = root.results ? root.results.length : 0
    if (count === 0) return
    root.cursor = Math.max(0, Math.min(count - 1, root.cursor + delta))
    list.positionViewAtIndex(root.cursor, ListView.Contain)
  }

  // Selecting a result copies its text rather than trying to reopen the app:
  // the words are what you came back for, and the picture is right there to
  // confirm you found the right moment.
  function copyCurrent() {
    if (!root.results || root.cursor >= root.results.length) return
    // The id and the path both arrive as JSON from the helper, so neither is
    // pasted into the command string. The id has to look like a plain number
    // before it is used at all, and it reaches the shell as an argument
    // rather than as text spliced into the script.
    var id = Number(root.results[root.cursor].id)
    if (!isFinite(id) || id <= 0 || Math.floor(id) !== id) return
    copier.command = ["sh", "-c",
      "\"$1\" \"$2\" text \"$3\" | wl-copy",
      "hindsight-copy", "python3", root.service.helperPath, String(id)]
    copier.running = true
  }

  Process { id: copier; running: false }

  onOpenedChanged: {
    if (root.opened) {
      root.cursor = 0
      if (root.service) {
        root.service.search(searchBox.text)
        root.service.refreshBudget()
      }
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.hostWidget || root
    bar: root.bar
    open: root.opened
    focusTarget: searchBox
    contentWidth: panel.fittedContentWidth(Style.space(420))
    contentHeight: Math.min(Style.space(520), column.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()

      Column {
        id: column
        width: parent.width
        spacing: Style.space(8)

        // -- what it is doing right now -------------------------------------
        Row {
          width: parent.width
          spacing: Style.space(8)

          Text {
            textFormat: Text.PlainText
            width: parent.width - pauseButton.width - Style.space(8)
            color: Color.foreground
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            wrapMode: Text.WordWrap
            text: {
              if (root.paused) return "Paused - nothing is being captured."
              if (root.blockedBy !== "") return "Holding off: this window matches \"" + root.blockedBy + "\"."
              var parts = [root.today + " captured today", root.sizeText(root.bytes) + " of " + root.sizeText(root.budget)]
              if (root.pendingOcr > 0) parts.push(root.pendingOcr + " still being read")
              return parts.join("  ·  ")
            }
          }

          PanelActionButton {
            id: pauseButton
            iconText: root.paused ? "󰐊" : "󰏤"
            tooltipText: root.paused ? "Resume capturing" : "Pause capturing"
            onClicked: if (root.service) root.service.setPaused(!root.paused)
          }
        }

        PanelSeparator { width: parent.width }

        // -- the search box -------------------------------------------------
        TextField {
          id: searchBox
          width: parent.width
          foreground: Color.foreground
          placeholderText: "Search what you have seen"
          onTextChanged: {
            root.cursor = 0
            debounce.restart()
          }
          onAccepted: root.copyCurrent()
          Keys.onPressed: function (event) {
            if (event.key === Qt.Key_Down) { root.moveCursor(1); event.accepted = true }
            else if (event.key === Qt.Key_Up) { root.moveCursor(-1); event.accepted = true }
            else if (event.key === Qt.Key_Escape) { root.close(); event.accepted = true }
          }
        }

        // OCR text is long; firing a query on every keystroke would spawn a
        // process per character.
        Timer {
          id: debounce
          interval: 180
          repeat: false
          onTriggered: if (root.service) root.service.search(searchBox.text)
        }

        // -- results --------------------------------------------------------
        Text {
          textFormat: Text.PlainText
          width: parent.width
          visible: searchBox.text.length > 0 && !root.searching && root.results.length === 0
          color: Color.foreground
          opacity: 0.7
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
          wrapMode: Text.WordWrap
          text: root.frames === 0
                ? "Nothing captured yet. Leave it running and come back."
                : "No screen you have seen contains that."
        }

        Text {
          textFormat: Text.PlainText
          width: parent.width
          visible: searchBox.text.length === 0
          color: Color.foreground
          opacity: 0.7
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
          wrapMode: Text.WordWrap
          text: root.frames === 0
                ? "Nothing captured yet."
                : "Type to search across " + root.frames + " remembered screens. Enter copies the text, and everything stays on this machine."
        }

        ListView {
          id: list
          width: parent.width
          height: Math.min(Style.space(360), contentHeight)
          visible: root.results.length > 0
          clip: true
          model: root.results
          spacing: Style.space(4)
          currentIndex: root.cursor

          delegate: Rectangle {
            width: list.width
            height: entry.implicitHeight + Style.space(10)
            radius: Style.cornerRadius
            color: index === root.cursor ? Style.selectedFill : "transparent"

            MouseArea {
              anchors.fill: parent
              hoverEnabled: true
              onEntered: root.cursor = index
              onClicked: { root.cursor = index; root.copyCurrent() }
            }

            Row {
              id: entry
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.leftMargin: Style.space(6)
              anchors.rightMargin: Style.space(6)
              spacing: Style.space(8)

              Image {
                id: thumb
                width: Style.space(64)
                height: Style.space(40)
                fillMode: Image.PreserveAspectCrop
                asynchronous: true
                cache: false
                // Built per path segment rather than concatenated: Qt
                // percent-decodes a file URL, so a raw "%2e%2e" in a stored
                // path would climb out of the archive, and an ordinary "#"
                // in a home directory would break every thumbnail.
                source: {
                  if (!modelData.path) return ""
                  var parts = String(modelData.path).split("/")
                  var encoded = []
                  for (var i = 0; i < parts.length; i++)
                    encoded.push(encodeURIComponent(parts[i]))
                  return "file://" + encoded.join("/")
                }
                sourceSize.width: Style.space(128)
              }

              Column {
                width: entry.width - thumb.width - Style.space(8)
                spacing: Style.space(2)

                Text {
                  textFormat: Text.PlainText
                  width: parent.width
                  color: Color.foreground
                  opacity: 0.65
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                  elide: Text.ElideRight
                  text: modelData.time + "  ·  " + (modelData.app || "unknown")
                }

                Text {
                  textFormat: Text.PlainText
                  width: parent.width
                  color: Color.foreground
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                  wrapMode: Text.WordWrap
                  maximumLineCount: 2
                  elide: Text.ElideRight
                  text: modelData.snippet
                }
              }
            }
          }
        }

        PanelSeparator { width: parent.width }

        // -- how far back this reaches, and the one knob that changes it ----
        // Disk is the only limit that matters here, so it is stated in the
        // unit the limit is actually felt in: days, not gigabytes.
        Row {
          width: parent.width
          spacing: Style.space(8)

          Text {
            textFormat: Text.PlainText
            id: coverageLine
            width: parent.width - storageButton.width - Style.space(8)
            color: Color.foreground
            opacity: 0.75
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            elide: Text.ElideRight
            text: {
              var size = root.sizeText(root.budget)
              if (root.coverageText === "") return "Storage: " + size
              var line = size + "  ·  holds " + root.coverageText
              if (root.coverageBasis === "default") line += " (estimate)"
              return line
            }
          }

          PanelActionButton {
            id: storageButton
            iconText: "󰋊"
            tooltipText: "Choose how much disk to use"
            onClicked: root.showStorage = !root.showStorage
          }
        }

        Column {
          width: parent.width
          spacing: Style.space(6)
          visible: root.showStorage

          Text {
            textFormat: Text.PlainText
            width: parent.width
            color: Color.foreground
            opacity: 0.6
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            wrapMode: Text.WordWrap
            text: root.coverageBasis === "measured"
              ? "Estimated from your own capture rate."
              : "Estimated from this machine; it sharpens after a few days."
          }

          Flow {
            width: parent.width
            spacing: Style.space(6)

            Repeater {
              model: root.budgetOptions

              Rectangle {
                id: chip
                readonly property bool current: root.budget > 0
                  && Math.abs(root.budget - modelData.mb * 1048576) < 1048576
                width: chipLabel.implicitWidth + Style.space(16)
                height: chipLabel.implicitHeight + Style.space(10)
                radius: Style.space(4)
                color: chip.current ? Color.foreground : "transparent"
                border.width: 1
                border.color: Color.foreground
                opacity: chip.current ? 1.0 : 0.45

                Text {
                  textFormat: Text.PlainText
                  id: chipLabel
                  anchors.centerIn: parent
                  color: chip.current ? Color.background : Color.foreground
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                  text: modelData.label
                }

                MouseArea {
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: if (root.service) root.service.setBudget(modelData.mb)
                  onEntered: hint.text = modelData.label + " holds " + modelData.text
                }
              }
            }
          }

          Text {
            textFormat: Text.PlainText
            id: hint
            width: parent.width
            color: Color.foreground
            opacity: 0.6
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            wrapMode: Text.WordWrap
            text: "Lowering this deletes the oldest frames straight away."
          }
        }

        PanelSeparator { width: parent.width; visible: root.frames > 0 }

        Row {
          width: parent.width
          spacing: Style.space(8)
          visible: root.frames > 0

          Text {
            textFormat: Text.PlainText
            color: Color.foreground
            opacity: 0.6
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            text: "Local only. Never leaves this machine."
          }
        }
      }
    }
  }
}
