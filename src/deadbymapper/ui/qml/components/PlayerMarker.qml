import QtQuick
import QtQuick.Shapes

// "Where am I?" - the highest-contrast element, always on top.
// Uncertainty is shown SPATIALLY (halo radius = sigma), never as a number.
Item {
    id: p
    property real heading: -1      // deg, 0 = map up, clockwise; -1 = unknown (no cone)
    property real sigmaPx: 0
    property string poseStatus: "no_map"
    readonly property bool locked: poseStatus === "locked"
    readonly property real haloR: Math.max(10, Math.min(sigmaPx * 1.4, 90))
    width: 0
    height: 0

    // uncertainty halo (radial gradient - a soft *field*, edges stay crisp because the marker is crisp)
    Shape {
        x: -p.haloR; y: -p.haloR
        width: 2 * p.haloR; height: 2 * p.haloR
        visible: !p.locked || p.sigmaPx > 6
        ShapePath {
            strokeColor: "transparent"
            fillGradient: RadialGradient {
                centerX: p.haloR; centerY: p.haloR; centerRadius: p.haloR
                focalX: p.haloR; focalY: p.haloR
                GradientStop { position: 0.0; color: theme.playerHalo }
                GradientStop { position: 1.0; color: theme.playerClear }
            }
            PathAngleArc {
                centerX: p.haloR; centerY: p.haloR
                radiusX: p.haloR; radiusY: p.haloR
                startAngle: 0; sweepAngle: 360
            }
        }
    }

    // heading cone (where the camera looks)
    Item {
        visible: p.heading >= 0
        rotation: p.heading
        Behavior on rotation { RotationAnimation { duration: 90; direction: RotationAnimation.Shortest } }
        Shape {
            x: -26; y: -30
            width: 52; height: 30
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeColor: "transparent"
                fillGradient: LinearGradient {
                    x1: 26; y1: 30; x2: 26; y2: 0
                    GradientStop { position: 0.0; color: theme.playerCone }
                    GradientStop { position: 1.0; color: theme.playerClear }
                }
                startX: 26; startY: 30
                PathLine { x: 26 - 30 * Math.sin(Math.PI * 30 / 180); y: 30 - 30 * Math.cos(Math.PI * 30 / 180) }
                PathArc {
                    x: 26 + 30 * Math.sin(Math.PI * 30 / 180); y: 30 - 30 * Math.cos(Math.PI * 30 / 180)
                    radiusX: 30; radiusY: 30
                }
                PathLine { x: 26; y: 30 }
            }
        }
    }

    // the dot
    Rectangle {
        width: 12; height: 12; radius: 6
        x: -6; y: -6
        color: theme.player
        border.color: theme.onLight
        border.width: 2
    }
}
