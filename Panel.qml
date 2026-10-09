import QtQuick
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Ui
import qs.Commons

// Omastart: a start-menu-ish popup for the bar. All data comes from
// backend/omastart.py; this file only renders it with the shell's theme tokens.
Panel {
  id: root
  moduleName: "mnx.omastart"
  ipcTarget: "mnx.omastart"
  manageIpc: true

  // ---- theme ---------------------------------------------------------------
  readonly property color fg: root.bar ? root.bar.foreground : Color.foreground
  readonly property color muted: Qt.darker(fg, 1.6)
  readonly property string fontFamily: root.bar ? root.bar.fontFamily : ""

  // ---- state ---------------------------------------------------------------
  readonly property string backend: decodeURIComponent(Qt.resolvedUrl("backend/omastart.py").toString().replace("file://", ""))
  property var apps: []
  property var rows: []
  property int selectedIndex: -1
  property string tab: setting("tab", "recent")
  property string kindFilter: "all"      // all | app | web | tui
  property string originFilter: "any"    // any | yours | omarchy
  property string query: ""

  readonly property var tabs: [
    { key: "recent", label: "Recent" },
    { key: "top",    label: "Top" },
    { key: "az",     label: "A–Z" },
    { key: "new",    label: "New" },
    { key: "size",   label: "Size" },
    { key: "groups", label: "Groups" },
    { key: "unused", label: "Unused" }
  ]
  readonly property var kinds: [
    { key: "all", label: "All" }, { key: "app", label: "Apps" },
    { key: "web", label: "Web" }, { key: "tui", label: "TUI" }
  ]
  readonly property var origins: [
    { key: "yours", label: "Mine" }, { key: "omarchy", label: "Omarchy" }
  ]

  readonly property var selected: selectedIndex >= 0 && selectedIndex < rows.length && rows[selectedIndex].app
    ? rows[selectedIndex].app : null

  // ---- formatting ----------------------------------------------------------
  readonly property var monthNames: ["January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"]

  function ago(t) {
    if (!t) return "never"
    var s = Math.max(0, Date.now() / 1000 - t)
    if (s < 90) return "just now"
    if (s < 3600) return Math.round(s / 60) + "m ago"
    if (s < 86400) return Math.round(s / 3600) + "h ago"
    if (s < 86400 * 14) return Math.round(s / 86400) + "d ago"
    if (s < 86400 * 60) return Math.round(s / 604800) + "w ago"
    return Math.round(s / 2592000) + "mo ago"
  }

  function dateText(t) {
    if (!t) return "—"
    var d = new Date(t * 1000)
    return d.getDate() + " " + monthNames[d.getMonth()].slice(0, 3) + " " + d.getFullYear()
  }

  function bytes(n) {
    if (!n) return "—"
    if (n >= 1073741824) return (n / 1073741824).toFixed(1) + " GB"
    if (n >= 1048576) return Math.round(n / 1048576) + " MB"
    return Math.max(1, Math.round(n / 1024)) + " KB"
  }

  function kindLabel(a) { return a.kind === "web" ? "WEB" : (a.kind === "tui" ? "TUI" : "APP") }

  function metaFor(a) {
    if (root.query !== "") return a.origin === "yours" ? "mine" : "omarchy"
    switch (root.tab) {
    case "recent": return a.last ? ago(a.last) : dateText(a.installed)
    case "top":    return a.count ? a.count + "×" : "—"
    case "new":    return dateText(a.installed)
    case "size":   return bytes(a.size)
    case "unused": return dateText(a.installed)
    default:       return a.source
    }
  }

  function subFor(a) {
    var parts = []
    if (a.kind === "web" && a.url) parts.push(a.url.replace(/^https?:\/\//, "").replace(/\/$/, ""))
    else if (a.sub) parts.push(a.sub)
    else parts.push(a.category)
    return parts.join(" · ")
  }

  // ---- row building --------------------------------------------------------
  function matches(a) {
    if (kindFilter !== "all" && a.kind !== kindFilter) return false
    if (originFilter !== "any" && a.origin !== originFilter) return false
    return true
  }

  function byName(x, y) { return x.name.toLowerCase() < y.name.toLowerCase() ? -1 : 1 }

  function pushGroup(out, title, items) {
    if (items.length === 0) return
    out.push({ header: title, count: items.length })
    for (var i = 0; i < items.length; i++) out.push({ app: items[i] })
  }

  function rebuild() {
    var list = apps.filter(matches)
    var out = []
    var q = query.trim().toLowerCase()

    if (q !== "") {
      var scored = []
      list.forEach(function(a) {
        var name = a.name.toLowerCase()
        var hay = [name, a.sub, a.source, a.category, a.url, a.exec, a.id].join(" ").toLowerCase()
        if (hay.indexOf(q) < 0) return
        var score = name === q ? 0 : (name.indexOf(q) === 0 ? 1 : (name.indexOf(q) > 0 ? 2 : 3))
        scored.push({ a: a, s: score })
      })
      scored.sort(function(x, y) { return x.s - y.s || y.a.last - x.a.last || byName(x.a, y.a) })
      scored.forEach(function(e) { out.push({ app: e.a }) })
      applyRows(out)
      return
    }

    var now = Date.now() / 1000
    var midnight = new Date(); midnight.setHours(0, 0, 0, 0)
    var today = midnight.getTime() / 1000

    if (tab === "recent") {
      var used = list.filter(function(a) { return a.last > 0 }).sort(function(x, y) { return y.last - x.last })
      var buckets = [["Today", []], ["Yesterday", []], ["This week", []], ["Earlier", []]]
      used.forEach(function(a) {
        var i = a.last >= today ? 0 : (a.last >= today - 86400 ? 1 : (a.last >= today - 6 * 86400 ? 2 : 3))
        buckets[i][1].push(a)
      })
      buckets.forEach(function(b) { pushGroup(out, b[0], b[1]) })
      pushGroup(out, "Not opened yet", list.filter(function(a) { return !a.last }).sort(function(x, y) { return y.installed - x.installed }))
    } else if (tab === "top") {
      pushGroup(out, "Most opened", list.filter(function(a) { return a.count > 0 }).sort(function(x, y) { return y.count - x.count || y.last - x.last }))
      pushGroup(out, "Not opened yet", list.filter(function(a) { return !a.count }).sort(byName))
    } else if (tab === "az") {
      var sorted = list.slice().sort(byName)
      var letter = "", bucket = []
      sorted.forEach(function(a) {
        var l = a.name.charAt(0).toUpperCase()
        if (!/[A-Z]/.test(l)) l = "#"
        if (l !== letter) { pushGroup(out, letter, bucket); letter = l; bucket = [] }
        bucket.push(a)
      })
      pushGroup(out, letter, bucket)
    } else if (tab === "new") {
      var fresh = list.slice().sort(function(x, y) { return y.installed - x.installed })
      var month = "", mb = []
      fresh.forEach(function(a) {
        var d = new Date(a.installed * 1000)
        var m = monthNames[d.getMonth()] + " " + d.getFullYear()
        if (m !== month) { pushGroup(out, month, mb); month = m; mb = [] }
        mb.push(a)
      })
      pushGroup(out, month, mb)
    } else if (tab === "size") {
      pushGroup(out, "Largest on disk", list.filter(function(a) { return a.size > 0 }).sort(function(x, y) { return y.size - x.size }))
      pushGroup(out, "No size (webapps, launchers, flatpaks)", list.filter(function(a) { return !a.size }).sort(byName))
    } else if (tab === "groups") {
      var cats = {}
      list.forEach(function(a) { (cats[a.category] = cats[a.category] || []).push(a) })
      Object.keys(cats).sort(function(x, y) {
        if (x === "Web apps") return -1
        if (y === "Web apps") return 1
        if (x === "Other") return 1
        if (y === "Other") return -1
        return x < y ? -1 : 1
      }).forEach(function(c) { pushGroup(out, c, cats[c].sort(byName)) })
    } else if (tab === "unused") {
      pushGroup(out, "Never opened since tracking began", list.filter(function(a) { return !a.count }).sort(function(x, y) { return y.installed - x.installed }))
    }
    applyRows(out)
  }

  function firstApp(from, step) {
    for (var i = from; i >= 0 && i < rows.length; i += step)
      if (rows[i].app) return i
    return -1
  }

  function applyRows(out) {
    rows = out
    selectedIndex = firstApp(0, 1)
    if (list.count >= 0) list.positionViewAtBeginning()
  }

  onAppsChanged: rebuild()
  onTabChanged: rebuild()
  onKindFilterChanged: rebuild()
  onOriginFilterChanged: rebuild()
  onQueryChanged: rebuild()

  function move(step) {
    var i = firstApp(selectedIndex + step, step)
    if (i < 0) return
    selectedIndex = i
    // Keep the section header visible when moving onto the first app of a group.
    list.positionViewAtIndex(i > 0 && rows[i - 1].header !== undefined ? i - 1 : i, ListView.Contain)
  }

  function cycleTab(dir) {
    var i = 0
    for (var k = 0; k < tabs.length; k++) if (tabs[k].key === tab) i = k
    tab = tabs[(i + dir + tabs.length) % tabs.length].key
  }

  function launch(a) {
    if (!a) return
    root.close()
    Quickshell.execDetached(["python3", root.backend, "launch", a.id])
  }

  onOpenedChanged: {
    if (opened) {
      query = ""
      kindFilter = "all"
      originFilter = "any"
      reload()
    }
  }

  // ---- backend -------------------------------------------------------------
  function reload() {
    if (!indexer.running) indexer.running = true
  }

  Process {
    id: indexer
    command: ["python3", root.backend, "index"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try { root.apps = JSON.parse(text) } catch (e) { console.warn("omastart: bad index output") }
      }
    }
  }

  // Logs every app window Hyprland opens (however it was started) so Recent and
  // Top work. Dies with the shell; a lock file keeps it to one instance.
  Process {
    id: tracker
    running: true
    command: ["python3", root.backend, "track"]
    onExited: trackerRestart.start()
  }
  Timer { id: trackerRestart; interval: 30000; onTriggered: tracker.running = true }

  Component.onCompleted: reload()

  // ---- bar button ----------------------------------------------------------
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: String.fromCodePoint(0xF003B)
    slotSize: Style.bar.statusSlot
    tooltipText: "Apps"
    onPressed: function(b) { root.toggle() }
  }

  // ---- reusable bits -------------------------------------------------------
  component Chip: CursorSurface {
    id: chip
    property string label: ""
    property bool active: false
    signal clicked()
    implicitWidth: chipText.implicitWidth + Style.space(16)
    implicitHeight: chipText.implicitHeight + Style.space(8)
    current: active
    hasCursor: chipMouse.containsMouse
    foreground: root.fg
    accent: Color.accent
    bordered: true

    Text {
      id: chipText
      anchors.centerIn: parent
      textFormat: Text.PlainText
      text: chip.label
      color: chip.active ? Color.accent : root.fg
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      font.bold: chip.active
    }
    MouseArea {
      id: chipMouse
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onClicked: chip.clicked()
    }
  }

  // ---- panel ---------------------------------------------------------------
  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: search
    contentWidth: panel.fittedContentWidth(Style.space(520))
    contentHeight: panel.fittedContentHeight(Style.space(580), Style.space(620))

    Item {
      id: keys
      anchors.fill: parent
      Keys.priority: Keys.BeforeItem
      Keys.onPressed: function(event) {
        var k = event.key
        if (k === Qt.Key_Escape) {
          if (root.query !== "") search.text = ""
          else root.close()
        } else if (k === Qt.Key_Down) root.move(1)
        else if (k === Qt.Key_Up) root.move(-1)
        else if (k === Qt.Key_PageDown) { for (var i = 0; i < 8; i++) root.move(1) }
        else if (k === Qt.Key_PageUp) { for (var j = 0; j < 8; j++) root.move(-1) }
        else if (k === Qt.Key_Return || k === Qt.Key_Enter) root.launch(root.selected)
        else if (k === Qt.Key_Tab) root.cycleTab(1)
        else if (k === Qt.Key_Backtab) root.cycleTab(-1)
        else if ((k === Qt.Key_Left || k === Qt.Key_Right) && search.text === "") root.cycleTab(k === Qt.Key_Right ? 1 : -1)
        else { event.accepted = false; return }
        event.accepted = true
      }

      ColumnLayout {
        anchors.fill: parent
        spacing: Style.space(8)

        // Search
        TextField {
          id: search
          Layout.fillWidth: true
          foreground: root.fg
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          placeholderText: "Search " + root.apps.length + " apps…   (Tab switches view)"
          onTextChanged: root.query = text
          Connections {
            target: root
            function onOpenedChanged() { if (root.opened) search.text = "" }
          }
        }

        // View tabs
        Row {
          Layout.fillWidth: true
          spacing: Style.space(4)
          Repeater {
            model: root.tabs
            Chip {
              required property var modelData
              label: modelData.label
              active: root.query === "" && root.tab === modelData.key
              width: (parent.width - Style.space(4) * (root.tabs.length - 1)) / root.tabs.length
              onClicked: { search.text = ""; root.tab = modelData.key; search.forceActiveFocus() }
            }
          }
        }

        // Filters: kind on the left, origin on the right
        RowLayout {
          Layout.fillWidth: true
          spacing: Style.space(4)
          Repeater {
            model: root.kinds
            Chip {
              required property var modelData
              label: modelData.label
              active: root.kindFilter === modelData.key
              onClicked: { root.kindFilter = modelData.key; search.forceActiveFocus() }
            }
          }
          Item { Layout.fillWidth: true }
          Repeater {
            model: root.origins
            Chip {
              required property var modelData
              label: modelData.label
              active: root.originFilter === modelData.key
              onClicked: {
                root.originFilter = root.originFilter === modelData.key ? "any" : modelData.key
                search.forceActiveFocus()
              }
            }
          }
        }

        PanelSeparator { foreground: root.fg; Layout.fillWidth: true }

        // App list
        ListView {
          id: list
          Layout.fillWidth: true
          Layout.fillHeight: true
          clip: true
          model: root.rows
          spacing: 1
          boundsBehavior: Flickable.StopAtBounds
          reuseItems: false

          delegate: Item {
            id: row
            required property var modelData
            required property int index
            readonly property bool isHeader: modelData.header !== undefined
            readonly property var app: modelData.app
            width: ListView.view.width
            height: isHeader ? Style.space(26) : Style.space(42)

            // Section header
            Text {
              visible: row.isHeader
              anchors.left: parent.left
              anchors.leftMargin: Style.space(8)
              anchors.bottom: parent.bottom
              anchors.bottomMargin: Style.space(3)
              textFormat: Text.PlainText
              text: row.isHeader ? (row.modelData.header + "  " + row.modelData.count).toUpperCase() : ""
              color: root.muted
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              font.bold: true
              font.letterSpacing: 1.2
            }

            // App row
            CursorSurface {
              visible: !row.isHeader
              anchors.fill: parent
              hasCursor: root.selectedIndex === row.index
              foreground: root.fg
              accent: Color.accent

              Item {
                id: iconBox
                anchors.left: parent.left
                anchors.leftMargin: Style.space(8)
                anchors.verticalCenter: parent.verticalCenter
                width: Style.space(28)
                height: width

                Image {
                  id: appIcon
                  anchors.fill: parent
                  visible: status === Image.Ready
                  source: row.app && row.app.icon ? Util.fileUrl(row.app.icon) : ""
                  sourceSize: Qt.size(width * 2, height * 2)
                  asynchronous: true
                  fillMode: Image.PreserveAspectFit
                }
                // No icon file (typical for webapps): letter tile
                Rectangle {
                  anchors.fill: parent
                  visible: appIcon.status !== Image.Ready
                  radius: Style.cornerRadius
                  color: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.1)
                  Text {
                    anchors.centerIn: parent
                    textFormat: Text.PlainText
                    text: row.app ? row.app.name.charAt(0).toUpperCase() : ""
                    color: root.fg
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.title
                    font.bold: true
                  }
                }
              }

              Column {
                anchors.left: iconBox.right
                anchors.leftMargin: Style.space(10)
                anchors.right: tag.left
                anchors.rightMargin: Style.space(8)
                anchors.verticalCenter: parent.verticalCenter
                spacing: 0
                Text {
                  width: parent.width
                  textFormat: Text.PlainText
                  text: row.app ? row.app.name : ""
                  elide: Text.ElideRight
                  color: root.selectedIndex === row.index ? Color.accent : root.fg
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                }
                Text {
                  width: parent.width
                  textFormat: Text.PlainText
                  text: row.app ? root.subFor(row.app) : ""
                  elide: Text.ElideRight
                  color: root.muted
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                }
              }

              Text {
                id: tag
                anchors.right: meta.left
                anchors.rightMargin: Style.space(10)
                anchors.verticalCenter: parent.verticalCenter
                textFormat: Text.PlainText
                text: row.app ? root.kindLabel(row.app) : ""
                color: root.muted
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                font.bold: true
                font.letterSpacing: 1
              }

              Text {
                id: meta
                anchors.right: parent.right
                anchors.rightMargin: Style.space(10)
                anchors.verticalCenter: parent.verticalCenter
                width: Style.space(78)
                horizontalAlignment: Text.AlignRight
                textFormat: Text.PlainText
                text: row.app ? root.metaFor(row.app) : ""
                elide: Text.ElideRight
                color: root.fg
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
              }

              MouseArea {
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onPositionChanged: root.selectedIndex = row.index
                onClicked: root.launch(row.app)
              }
            }
          }

          Text {
            anchors.centerIn: parent
            visible: root.rows.length === 0
            textFormat: Text.PlainText
            text: root.apps.length === 0 ? "Loading apps…" : "Nothing matches"
            color: root.muted
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
          }
        }

        PanelSeparator { foreground: root.fg; Layout.fillWidth: true }

        // Details of the highlighted app
        Column {
          Layout.fillWidth: true
          spacing: Style.space(2)
          Text {
            width: parent.width
            textFormat: Text.PlainText
            elide: Text.ElideRight
            text: root.selected
              ? root.selected.name + "  ·  " + (root.selected.origin === "yours" ? "installed by you" : "Omarchy / system")
              : " "
            color: root.fg
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            font.bold: true
          }
          Text {
            width: parent.width
            textFormat: Text.PlainText
            elide: Text.ElideRight
            text: root.selected
              ? root.selected.source + "  ·  added " + root.dateText(root.selected.installed)
                + (root.selected.size ? "  ·  " + root.bytes(root.selected.size) : "")
                + "  ·  " + (root.selected.count ? "opened " + root.selected.count + "× (last " + root.ago(root.selected.last) + ")" : "not opened since tracking began")
              : " "
            color: root.muted
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }
        }
      }
    }
  }
}
