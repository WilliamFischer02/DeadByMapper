import QtQuick
import QtQuick.Shapes

// The ONE number on the pane: generators remaining. 12 px diamond = the generator marker.
Row {
    id: counter
    property int count: -1
    spacing: 8
    height: 32

    Shape {
        width: 12; height: 12
        y: 10
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeColor: "transparent"
            fillColor: theme.gen
            startX: 6; startY: 0
            PathLine { x: 12; y: 6 }
            PathLine { x: 6; y: 12 }
            PathLine { x: 0; y: 6 }
            PathLine { x: 6; y: 0 }
        }
    }
    Text {
        height: 32
        verticalAlignment: Text.AlignVCenter
        text: counter.count >= 0 ? counter.count : "–"
        color: counter.count === 0 ? theme.gate : theme.text
        font.family: theme.fontDisplay
        font.pixelSize: 24
        font.weight: Font.DemiBold
    }
}
