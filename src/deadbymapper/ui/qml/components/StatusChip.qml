import QtQuick
import QtQuick.Controls

// Sensor status pill. Colour AND label carry meaning; detail on hover.
Rectangle {
    id: chip
    property string label
    property string status: "off"   // ok | degraded | down | disabled | off
    property string detail
    readonly property color dotColor: status === "ok" ? theme.ok
                                     : status === "degraded" ? theme.warn
                                     : status === "down" ? theme.down : theme.off

    implicitHeight: 28
    implicitWidth: row.implicitWidth + 24
    radius: 14
    color: hover.hovered ? theme.surfaceHover : theme.surface2
    border.color: theme.hairline
    border.width: 1

    Row {
        id: row
        anchors.centerIn: parent
        spacing: 8
        Rectangle {
            width: 8; height: 8; radius: 4
            color: chip.dotColor
            anchors.verticalCenter: parent.verticalCenter
        }
        Text {
            text: chip.label
            color: chip.status === "off" || chip.status === "disabled" ? theme.textMute : theme.text
            font.family: theme.font
            font.pixelSize: 12
            font.weight: Font.Medium
            anchors.verticalCenter: parent.verticalCenter
        }
    }

    HoverHandler { id: hover }
    ToolTip.visible: hover.hovered && chip.detail !== ""
    ToolTip.delay: 350
    ToolTip.text: chip.detail
}
