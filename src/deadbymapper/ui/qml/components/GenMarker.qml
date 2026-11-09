import QtQuick
import QtQuick.Shapes

// Generator: DIAMOND (shape-coded). candidate = hollow, spawned = solid orange,
// completed = solid green + tick, regressing = vermilion + outward pulse.
// rank 1 = recommended next gen -> slow breathing white ring (pre-attentive, no text).
Item {
    id: g
    property string genState: "candidate"
    property int rank: 0
    property real size: 12
    readonly property bool emphasize: rank === 1 && genState !== "completed"
    readonly property color fill: genState === "completed" ? theme.genDone
                                : genState === "regressing" ? theme.genRegress : theme.gen
    width: size
    height: size

    Rectangle {
        visible: g.emphasize
        anchors.centerIn: parent
        width: g.size + 12; height: width; radius: width / 2
        color: "transparent"
        border.color: theme.player
        border.width: 1.5
        SequentialAnimation on opacity {
            running: g.emphasize
            loops: Animation.Infinite
            NumberAnimation { to: 0.28; duration: 1400; easing.type: Easing.InOutSine }
            NumberAnimation { to: 0.90; duration: 1400; easing.type: Easing.InOutSine }
        }
    }

    Rectangle {
        id: pulse
        visible: g.genState === "regressing"
        anchors.centerIn: parent
        width: g.size; height: width; radius: width / 2
        color: "transparent"
        border.color: theme.genRegress
        border.width: 2
        SequentialAnimation {
            running: pulse.visible
            loops: Animation.Infinite
            ParallelAnimation {
                NumberAnimation { target: pulse; property: "scale"; from: 1.0; to: 2.6; duration: 1100; easing.type: Easing.OutCubic }
                NumberAnimation { target: pulse; property: "opacity"; from: 0.9; to: 0.0; duration: 1100 }
            }
        }
    }

    Shape {
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeWidth: g.genState === "candidate" ? 1.5 : 1
            strokeColor: g.genState === "candidate" ? Qt.rgba(g.fill.r, g.fill.g, g.fill.b, 0.80) : Qt.darker(g.fill, 1.7)
            fillColor: g.genState === "candidate" ? Qt.rgba(g.fill.r, g.fill.g, g.fill.b, 0.12) : g.fill
            joinStyle: ShapePath.MiterJoin
            startX: g.size / 2; startY: 0.75
            PathLine { x: g.size - 0.75; y: g.size / 2 }
            PathLine { x: g.size / 2; y: g.size - 0.75 }
            PathLine { x: 0.75; y: g.size / 2 }
            PathLine { x: g.size / 2; y: 0.75 }
        }
        ShapePath {
            strokeColor: g.genState === "completed" ? theme.white : "transparent"
            strokeWidth: 1.6
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            joinStyle: ShapePath.RoundJoin
            startX: g.size * 0.31; startY: g.size * 0.52
            PathLine { x: g.size * 0.45; y: g.size * 0.66 }
            PathLine { x: g.size * 0.71; y: g.size * 0.37 }
        }
    }
}
