import QtQuick
import QtQuick.Controls.Basic

// Sensor tile: status dot + label on the first line, the reason on the second (elided; the full
// text and the status word on hover). Status is coded by shape AND colour:
//   solid dot     = running   (green ok / amber degraded / red down; failing tiles also get a tinted edge)
//   hollow ring   = off or disabled (quiet: no fill, muted text - nothing to fix)
Rectangle {
    id: chip
    property string label
    property string status: "off"   // ok | degraded | down | disabled | off
    property string detail
    readonly property bool quiet: status === "off" || status === "disabled"
    readonly property color dotColor: status === "ok" ? theme.ok
                                     : status === "degraded" ? theme.warn
                                     : status === "down" ? theme.down : theme.off
    readonly property string statusWord: status === "ok" ? "Running"
                                        : status === "degraded" ? "Degraded"
                                        : status === "down" ? "Down"
                                        : status === "disabled" ? "Disabled" : "Off"

    implicitWidth: 200
    implicitHeight: 44
    radius: theme.radiusSmall
    color: quiet ? "transparent" : (hover.hovered ? theme.surfaceHover : theme.surface2)
    border.width: 1
    border.color: status === "down" ? theme.downEdge : status === "degraded" ? theme.warnEdge : theme.hairline

    Rectangle {
        id: dot
        x: 12; y: 10
        width: 8; height: 8; radius: 4
        color: chip.quiet ? "transparent" : chip.dotColor
        border.width: chip.quiet ? 1.5 : 0
        border.color: chip.dotColor
    }
    Text {
        id: name
        x: 28; y: 5
        width: chip.width - x - 12
        height: 18
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
        text: chip.label
        color: chip.quiet ? theme.textDim : theme.text
        font.family: theme.font
        font.pixelSize: 13
        font.weight: Font.DemiBold
    }
    Text {
        id: reason
        x: 28; y: 23
        width: chip.width - x - 12
        height: 16
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
        text: chip.detail
        color: chip.quiet ? theme.textMute : theme.textDim
        font.family: theme.font
        font.pixelSize: 11
    }

    HoverHandler { id: hover }
    ToolTip {
        id: tip
        visible: hover.hovered
        delay: 300
        padding: 8
        text: chip.statusWord + (chip.detail ? " — " + chip.detail : "")
        contentItem: Text {
            text: tip.text
            color: theme.text
            font.family: theme.font
            font.pixelSize: 13
            wrapMode: Text.WordWrap
        }
        background: Rectangle {
            color: theme.surface2
            border.color: theme.hairlineStrong
            border.width: 1
            radius: theme.radiusTiny
        }
    }
}
