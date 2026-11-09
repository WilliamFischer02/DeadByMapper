import QtQuick
import QtQuick.Shapes

// The ONE number on the pane: generators remaining.
Row {
    id: counter
    property int count: -1
    spacing: 7

    Shape {
        width: 11; height: 11
        anchors.verticalCenter: parent.verticalCenter
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeColor: "transparent"
            fillColor: theme.gen
            startX: 5.5; startY: 0
            PathLine { x: 11; y: 5.5 }
            PathLine { x: 5.5; y: 11 }
            PathLine { x: 0; y: 5.5 }
            PathLine { x: 5.5; y: 0 }
        }
    }
    Text {
        text: counter.count >= 0 ? counter.count : "–"
        color: counter.count === 0 ? theme.gate : theme.text
        font.family: theme.fontDisplay
        font.pixelSize: 24
        font.weight: Font.DemiBold
        anchors.verticalCenter: parent.verticalCenter
    }
}
