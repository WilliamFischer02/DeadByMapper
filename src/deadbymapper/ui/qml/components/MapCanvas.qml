import QtQuick
import QtQuick.Shapes

// North-up map view in METRES. Layer order (bottom -> top):
//   tap area, map fill, 8 m grid, structures, scale bar, exit gates, generators, trail,
//   terror ring, alternative hypotheses, player.  The player is ALWAYS last (never occluded).
Item {
    id: cv
    clip: true

    property var mapData: bridge.map
    property var player: bridge.player
    property var terror: bridge.terror
    readonly property bool hasMap: (mapData.w || 0) > 0 && (mapData.h || 0) > 0
    readonly property real mapW: hasMap ? mapData.w : 96
    readonly property real mapH: hasMap ? mapData.h : 96
    readonly property real pad: 10
    readonly property real s: Math.max(0.01, Math.min((width - 2 * pad) / mapW, (height - 2 * pad) / mapH))
    readonly property real ox: Math.round((width - mapW * s) / 2)
    readonly property real oy: Math.round((height - mapH * s) / 2)
    readonly property bool relative: player.relative === true || !hasMap

    function px(xm) { return ox + xm * s }
    function py(ym) { return oy + ym * s }
    // 1 px strokes on half-pixel centres = crisp edges (no 2-px grey smear)
    function crisp(v) { return Math.round(v) + 0.5 }
    function poly(pts, closed) {
        var out = []
        for (var i = 0; i < pts.length; ++i)
            out.push(Qt.point(crisp(px(pts[i].x)), crisp(py(pts[i].y))))
        if (closed && pts.length > 0)
            out.push(Qt.point(crisp(px(pts[0].x)), crisp(py(pts[0].y))))
        return out
    }
    function line(pts) {
        var out = []
        for (var i = 0; i < pts.length; ++i)
            out.push(Qt.point(px(pts[i].x), py(pts[i].y)))
        return out
    }

    // ------------------------------------------------------------ tap-to-fix ("I am here")
    MouseArea {
        anchors.fill: parent
        enabled: !bridge.clickThrough && cv.hasMap
        onClicked: (mouse) => bridge.tapMap((mouse.x - cv.ox) / cv.s, (mouse.y - cv.oy) / cv.s)
    }

    // ------------------------------------------------------------ map layer
    Shape {
        anchors.fill: parent
        visible: cv.hasMap
        ShapePath {
            fillColor: theme.mapFill
            strokeColor: theme.mapStroke
            strokeWidth: 1
            PathPolyline { path: cv.poly(cv.mapData.bounds || [], true) }
        }
    }

    // one faint square = one 8 m border-wall segment (the game's own distance unit)
    Repeater {
        model: cv.hasMap ? Math.floor(cv.mapW / 8) + 1 : 0
        Rectangle {
            x: Math.round(cv.px(index * 8)); y: cv.oy
            width: 1; height: Math.round(cv.mapH * cv.s)
            color: index % 4 === 0 ? theme.gridMajor : theme.grid
        }
    }
    Repeater {
        model: cv.hasMap ? Math.floor(cv.mapH / 8) + 1 : 0
        Rectangle {
            x: cv.ox; y: Math.round(cv.py(index * 8))
            width: Math.round(cv.mapW * cv.s); height: 1
            color: index % 4 === 0 ? theme.gridMajor : theme.grid
        }
    }

    Repeater {
        model: cv.mapData.structures || []
        Shape {
            anchors.fill: parent
            ShapePath {
                fillColor: modelData.solid ? theme.structFill : "transparent"
                strokeColor: modelData.solid ? theme.structStroke : theme.tileStroke
                strokeWidth: 1
                strokeStyle: modelData.solid ? ShapePath.SolidLine : ShapePath.DashLine
                dashPattern: [3, 3]
                PathPolyline { path: cv.poly(modelData.points, true) }
            }
        }
    }

    Item {
        visible: cv.hasMap
        x: cv.ox + 8
        y: Math.round(cv.oy + cv.mapH * cv.s) - 18
        Rectangle { y: 8; width: Math.round(8 * cv.s); height: 2; color: theme.textMute }
        Text {
            x: Math.round(8 * cv.s) + 5; y: 1
            text: "8 m"
            color: theme.textMute
            font.family: theme.font
            font.pixelSize: 10
        }
    }

    // ------------------------------------------------------------ objectives
    Repeater {
        model: bridge.gates
        GateMarker {
            x: Math.round(cv.px(modelData.x)); y: Math.round(cv.py(modelData.y))
            gateState: modelData.state
            facing: modelData.facing
        }
    }
    Repeater {
        model: bridge.gens
        GenMarker {
            size: 12
            x: Math.round(cv.px(modelData.x) - size / 2)
            y: Math.round(cv.py(modelData.y) - size / 2)
            genState: modelData.state
            rank: modelData.rank
            visible: modelData.state !== "not_spawned"
        }
    }

    // ------------------------------------------------------------ trail (last ~40 s)
    Shape {
        anchors.fill: parent
        visible: !cv.relative && bridge.trail.length > 1
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeColor: theme.trail
            strokeWidth: 2
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            joinStyle: ShapePath.RoundJoin
            PathPolyline { path: cv.line(bridge.trail) }
        }
    }

    // ------------------------------------------------------------ killer distance band
    TerrorRing {
        visible: !cv.relative && cv.terror.active === true
        x: cv.px(cv.player.x); y: cv.py(cv.player.y)
        innerPx: (cv.terror.inner || 0) * cv.s
        outerPx: (cv.terror.outer || 0) * cv.s
        bpm: cv.terror.bpm || 0
        chase: cv.terror.chase === true
    }

    // ------------------------------------------------------------ alternative hypotheses (tap to confirm)
    Repeater {
        model: cv.relative ? [] : bridge.hypotheses
        Rectangle {
            width: 14; height: 14; radius: 7
            x: Math.round(cv.px(modelData.x) - 7); y: Math.round(cv.py(modelData.y) - 7)
            color: "transparent"
            border.color: theme.player
            border.width: 1.5
            opacity: 0.25 + 0.6 * Math.min(1, modelData.w * 2)
            MouseArea {
                anchors.fill: parent
                anchors.margins: -8
                enabled: !bridge.clickThrough
                onClicked: bridge.tapMap(modelData.x, modelData.y)
            }
        }
    }

    // ------------------------------------------------------------ you
    PlayerMarker {
        visible: !cv.relative
        x: cv.px(cv.player.x); y: cv.py(cv.player.y)
        Behavior on x { NumberAnimation { duration: 40 } }
        Behavior on y { NumberAnimation { duration: 40 } }
        heading: cv.player.heading
        sigmaPx: (cv.player.sigma > 0 ? cv.player.sigma : 0) * cv.s
        poseStatus: cv.player.status
    }

    // ------------------------------------------------------------ breadcrumb mode (no map data / no fix yet)
    Item {
        id: crumbs
        anchors.fill: parent
        visible: cv.relative
        readonly property real k: 2.4  // px per metre
        function toPath(pts) {
            var out = []
            for (var i = 0; i < pts.length; ++i)
                out.push(Qt.point(width / 2 + (pts[i].x - cv.player.x) * k, height / 2 + (pts[i].y - cv.player.y) * k))
            return out
        }
        Rectangle {
            anchors.centerIn: parent
            width: Math.min(parent.width, parent.height) - 24; height: width; radius: width / 2
            color: "transparent"
            border.color: theme.hairline
            border.width: 1
        }
        Shape {
            anchors.fill: parent
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeColor: theme.trail
                strokeWidth: 2
                fillColor: "transparent"
                capStyle: ShapePath.RoundCap
                joinStyle: ShapePath.RoundJoin
                PathPolyline { path: crumbs.toPath(bridge.crumbs) }
            }
        }
        PlayerMarker {
            x: crumbs.width / 2; y: crumbs.height / 2
            heading: cv.player.heading
            poseStatus: "locked"
        }
    }
}
