import QtQuick

// Manual trial start/stop. Until automatic match detection ships (M3) this is how the pane
// appears; afterwards it stays as the override. One button; state carried by label AND colour.
Item {
    id: ctl
    readonly property bool inTrial: bridge.hud.inTrial === true
    readonly property bool manual: bridge.hud.trialManual === true
    implicitWidth: row.implicitWidth
    implicitHeight: 32

    Row {
        id: row
        spacing: 10
        anchors.verticalCenter: parent.verticalCenter
        Text {
            anchors.verticalCenter: parent.verticalCenter
            visible: ctl.inTrial
            text: ctl.manual ? "MANUAL TRIAL" : "TRIAL DETECTED"
            color: theme.textMute
            font.family: theme.font
            font.pixelSize: 10
            font.weight: Font.DemiBold
            font.letterSpacing: 1.1
        }
        Rectangle {
            id: btn
            width: lbl.implicitWidth + 28; height: 32; radius: 8
            anchors.verticalCenter: parent.verticalCenter
            color: ctl.inTrial ? (ma.containsMouse ? theme.surfaceHover : theme.surface2)
                               : (ma.containsMouse ? theme.white : theme.player)
            border.color: ctl.inTrial ? theme.hairlineStrong : theme.player
            border.width: 1
            Text {
                id: lbl
                anchors.centerIn: parent
                text: ctl.inTrial ? "Stop trial" : "Start trial"
                color: ctl.inTrial ? theme.text : theme.onLight
                font.family: theme.font
                font.pixelSize: 12
                font.weight: Font.DemiBold
            }
            MouseArea {
                id: ma
                anchors.fill: parent
                hoverEnabled: true
                onClicked: ctl.inTrial ? bridge.stopTrial() : bridge.startTrial()
            }
        }
    }
}
