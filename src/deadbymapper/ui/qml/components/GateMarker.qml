import QtQuick

// Exit gate: BAR lying along the map edge (shape-coded). candidate = faint, found/powered = solid.
Item {
    id: gt
    property string gateState: "candidate"
    property real facing: -1
    property real len: 15
    readonly property bool vertical: facing === 90 || facing === 270
    readonly property bool known: gateState !== "candidate"
    width: 0
    height: 0

    Rectangle {
        width: gt.vertical ? 4 : gt.len
        height: gt.vertical ? gt.len : 4
        x: -Math.round(width / 2)
        y: -Math.round(height / 2)
        radius: 2
        color: gt.known ? theme.gate : theme.gateCandidate
        border.width: gt.known ? 1 : 0
        border.color: Qt.lighter(theme.gate, 1.35)
    }
    Rectangle {
        visible: gt.gateState === "powered" || gt.gateState === "open"
        anchors.centerIn: parent
        width: gt.len + 10; height: width; radius: width / 2
        color: "transparent"
        border.color: theme.gate
        border.width: 1
        opacity: 0.6
    }
}
