import QtQuick

// Killer distance band from the terror radius: an annulus around YOU (the heartbeat is
// non-directional, so we show 'somewhere in this ring', never a fake direction).
// It contracts as the heartbeat swells and pulses in time with the detected bpm.
Item {
    id: tr
    property real innerPx: 0
    property real outerPx: 0
    property real bpm: 0
    property bool chase: false
    width: 0
    height: 0

    Behavior on innerPx { NumberAnimation { duration: 380; easing.type: Easing.OutCubic } }
    Behavior on outerPx { NumberAnimation { duration: 380; easing.type: Easing.OutCubic } }

    Rectangle {
        id: band
        width: 2 * tr.outerPx; height: width
        x: -tr.outerPx; y: -tr.outerPx
        radius: tr.outerPx
        color: tr.innerPx < 1 ? (tr.chase ? theme.dangerFillChase : theme.dangerFill) : "transparent"
        border.width: tr.innerPx < 1 ? 0 : Math.max(1, tr.outerPx - tr.innerPx)
        border.color: tr.chase ? theme.dangerFillChase : theme.dangerFill
    }
    Rectangle {
        width: 2 * tr.outerPx; height: width
        x: -tr.outerPx; y: -tr.outerPx
        radius: tr.outerPx
        color: "transparent"
        border.color: theme.dangerEdge
        border.width: 1.5
    }
    Rectangle {
        visible: tr.innerPx >= 1
        width: 2 * tr.innerPx; height: width
        x: -tr.innerPx; y: -tr.innerPx
        radius: tr.innerPx
        color: "transparent"
        border.color: theme.dangerInner
        border.width: 1
    }

    SequentialAnimation on opacity {
        running: tr.bpm > 0 && tr.visible
        loops: Animation.Infinite
        NumberAnimation { to: 1.0; duration: 110 }
        NumberAnimation { to: 0.55; duration: Math.max(220, 60000 / Math.max(tr.bpm, 30) - 110); easing.type: Easing.OutCubic }
    }
}
