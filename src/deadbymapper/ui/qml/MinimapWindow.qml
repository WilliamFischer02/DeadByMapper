import QtQuick
import QtQuick.Window
import "components"

// The in-trial pane. Glance budget: < 1 s to answer "where am I / where's the next gen / how close
// is the killer". One number (gens left), one line of guidance, zero dialogs. Never takes focus.
Window {
    id: pane
    width: theme.paneW
    height: theme.paneH
    x: Screen.width - width - 24
    y: 72
    color: "transparent"
    title: "DeadByMapper - Minimap"
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus
           | (bridge.clickThrough ? Qt.WindowTransparentForInput : 0)

    readonly property var pl: bridge.player
    readonly property string st: pl.status || "no_map"
    readonly property bool busy: st === "no_fix" || st === "searching" || st === "converging" || st === "lost"

    function statusText(s) {
        switch (s) {
        case "locked": return "LOCKED"
        case "converging": return "LOCATING"
        case "searching": return "LOCATING"
        case "no_fix": return "NO FIX YET"
        case "lost": return "RE-LOCATING"
        default: return "BREADCRUMB"
        }
    }
    function hint() {
        if (bridge.mode === "review") return "Trial over — this was your route"
        if (!bridge.map.id) return "Map not identified — open the pause menu to show its name"
        if (bridge.map.status === "stub") return "No layout data for this map yet — breadcrumb only"
        if (bridge.terror.active && bridge.terror.chase) return "Chase — killer inside " + Math.round(bridge.terror.outer) + " m"
        if (st === "no_fix") return bridge.isDemo || !bridge.genHotkey
                                   ? "Locating — repair a generator or tap your spot"
                                   : "Locating — " + bridge.genHotkey + " at a generator, or tap your spot"
        if ((st === "searching" || st === "converging") && bridge.hypotheses.length > 0) return "Tap the ring that matches where you are"
        if (st === "searching" || st === "converging") return "Narrowing down — the next generator locks it"
        if (st === "lost") return "Uncertain again — next generator or tap re-locks"
        if (bridge.hud.next) return "Next gen ringed — " + bridge.hud.nextReason
        return ""
    }

    Rectangle {
        id: card
        anchors.fill: parent
        radius: theme.radius
        color: theme.bgPane
        border.color: theme.hairline
        border.width: 1

        Item {
            id: header
            anchors { left: parent.left; right: parent.right; top: parent.top; leftMargin: 16; rightMargin: 16; topMargin: 12 }
            height: 38

            Column {
                anchors.verticalCenter: parent.verticalCenter
                spacing: 3
                Text {
                    width: header.width - 70
                    elide: Text.ElideRight
                    text: (bridge.map.name || "Map unknown").toUpperCase()
                    color: theme.text
                    font.family: theme.font
                    font.pixelSize: 12
                    font.weight: Font.DemiBold
                    font.letterSpacing: 1.1
                }
                Row {
                    spacing: 6
                    Rectangle {
                        width: 7; height: 7; radius: 3.5
                        anchors.verticalCenter: parent.verticalCenter
                        color: pane.st === "locked" ? theme.player : "transparent"
                        border.color: pane.st === "locked" ? theme.player : theme.textDim
                        border.width: 1.5
                        SequentialAnimation on opacity {
                            running: pane.busy
                            loops: Animation.Infinite
                            NumberAnimation { to: 0.25; duration: 700; easing.type: Easing.InOutSine }
                            NumberAnimation { to: 1.0; duration: 700; easing.type: Easing.InOutSine }
                        }
                    }
                    Text {
                        text: pane.statusText(pane.st)
                        color: theme.textDim
                        font.family: theme.font
                        font.pixelSize: 11
                        font.letterSpacing: 0.8
                    }
                }
            }
            GenCounter {
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                count: bridge.hud.gensRemaining
            }
            DragHandler {
                target: null
                onActiveChanged: if (active) pane.startSystemMove()
            }
        }

        Rectangle {
            id: sep
            anchors { left: parent.left; right: parent.right; top: header.bottom; topMargin: 10 }
            height: 1
            color: theme.hairline
        }

        MapCanvas {
            id: canvas
            anchors { left: parent.left; right: parent.right; top: sep.bottom; bottom: footer.top; margins: 6 }
        }

        Text {
            id: footer
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom; leftMargin: 16; rightMargin: 16; bottomMargin: 12 }
            height: 16
            text: pane.hint()
            color: theme.textDim
            font.family: theme.font
            font.pixelSize: 11
            elide: Text.ElideRight
        }
    }
}
