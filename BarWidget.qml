import QtQuick
import Quickshell
import qs.Ui

BarWidget {
  id: root

  moduleName: "dhirajkhanna.hindsight"

  readonly property var service: bar && bar.shell ? bar.shell.serviceFor("dhirajkhanna.hindsight") : null
  readonly property bool paused: service ? service.paused === true : false
  readonly property bool capturing: service ? service.capturing === true : false
  readonly property int today: service ? service.today : 0
  readonly property int frames: service ? service.frames : 0
  readonly property string reason: service ? service.reason : ""
  readonly property string blockedBy: service ? service.blockedBy : ""
  readonly property bool recorderDown: service ? service.recorderRunning !== true : false

  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing === true : false

  function open() { if (panelLoader.item) panelLoader.item.open() }
  function close() { if (panelLoader.item) panelLoader.item.close() }
  function toggle() { if (panelLoader.item) panelLoader.item.toggle() }
  function closeForPopoutSwitch() { if (panelLoader.item) panelLoader.item.closeForPopoutSwitch() }

  function injectPanel() {
    if (!panelLoader.item) return
    panelLoader.item.bar = root.bar
    panelLoader.item.anchorItem = button
    panelLoader.item.hostWidget = root
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight
  onBarChanged: injectPanel()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar

    // The icon is deliberately never ambiguous. If this thing is recording the
    // screen, the bar says so - a quiet indicator would be the wrong kind of
    // quiet for something that remembers what you looked at.
    text: root.recorderDown ? "󰋙"
          : (root.paused ? "󰏤" : (root.blockedBy !== "" ? "󰈉" : "󰑊"))

    active: !root.paused && !root.recorderDown && root.blockedBy === ""
    dimmed: root.paused || root.recorderDown

    tooltipText: {
      if (root.recorderDown) return "Hindsight: recorder not running"
      if (root.paused) return "Hindsight is paused - nothing is being captured"
      if (root.blockedBy !== "") return "Hindsight is holding off: this window matches \"" + root.blockedBy + "\""
      if (root.reason !== "") return "Hindsight: " + root.reason
      return "Hindsight - " + root.today + " captured today. Click to search what you have seen."
    }

    onPressed: function (buttonCode) {
      if (buttonCode === Qt.LeftButton) root.toggle()
      else if (buttonCode === Qt.MiddleButton && root.service) root.service.setPaused(!root.paused)
    }
  }
}
