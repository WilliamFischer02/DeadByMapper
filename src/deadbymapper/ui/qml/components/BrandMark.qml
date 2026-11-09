import QtQuick
import QtQuick.Shapes

// Vector brand glyph: fog ring + heading chevron + generator diamond. Pure geometry = crisp at any DPI.
Item {
    id: m
    property real size: 28
    width: size
    height: size

    Rectangle {
        anchors.fill: parent
        radius: Math.round(m.size * 0.28)
        color: theme.brandTile
        border.color: theme.hairlineStrong
        border.width: 1
    }

    Shape {
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer

        ShapePath {
            strokeColor: theme.brandStroke
            strokeWidth: Math.max(1, m.size / 24)
            fillColor: "transparent"
            PathAngleArc {
                centerX: m.size / 2; centerY: m.size / 2
                radiusX: m.size * 0.30; radiusY: m.size * 0.30
                startAngle: 0; sweepAngle: 360
            }
        }
        ShapePath {
            strokeColor: "transparent"
            fillColor: theme.player
            startX: m.size * 0.50; startY: m.size * 0.22
            PathLine { x: m.size * 0.63; y: m.size * 0.52 }
            PathLine { x: m.size * 0.50; y: m.size * 0.45 }
            PathLine { x: m.size * 0.37; y: m.size * 0.52 }
            PathLine { x: m.size * 0.50; y: m.size * 0.22 }
        }
        ShapePath {
            strokeColor: "transparent"
            fillColor: theme.gen
            startX: m.size * 0.73; startY: m.size * 0.61
            PathLine { x: m.size * 0.81; y: m.size * 0.69 }
            PathLine { x: m.size * 0.73; y: m.size * 0.77 }
            PathLine { x: m.size * 0.65; y: m.size * 0.69 }
            PathLine { x: m.size * 0.73; y: m.size * 0.61 }
        }
    }
}
