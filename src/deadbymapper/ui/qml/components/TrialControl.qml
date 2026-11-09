import QtQuick

// Manual trial start/stop - the Game link card's primary action until automatic match detection
// ships (M3), and the override afterwards.
//   idle      -> PRIMARY "Start trial" (filled, the brightest thing on the card)
//   in trial  -> SECONDARY "Stop trial" (outlined), caption says how the trial began:
//                MANUAL (you pressed Start) or DETECTED (the app saw the match start).
// Laid out like a form field (label / control / caption) so it lines up with MapPicker.
Column {
    id: ctl
    readonly property bool inTrial: bridge.hud.inTrial === true
    readonly property bool manual: bridge.hud.trialManual === true
    spacing: 4

    Text {
        height: 16
        verticalAlignment: Text.AlignVCenter
        text: "TRIAL"
        color: theme.textMute
        font.family: theme.font
        font.pixelSize: 11
        font.weight: Font.DemiBold
        font.letterSpacing: 1.3
    }
    Rectangle {
        id: btn
        // Fixed width: the button does not jump when its label flips between Start and Stop.
        width: 112; height: 32; radius: theme.radiusSmall
        color: ctl.inTrial ? (ma.containsMouse ? theme.surfaceHover : "transparent")
                           : (ma.containsMouse ? theme.white : theme.player)
        border.color: ctl.inTrial ? theme.hairlineStrong : "transparent"
        border.width: 1
        Text {
            anchors.fill: parent
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            text: ctl.inTrial ? "Stop trial" : "Start trial"
            color: ctl.inTrial ? theme.text : theme.onLight
            font.family: theme.font
            font.pixelSize: 13
            font.weight: Font.DemiBold
        }
        MouseArea {
            id: ma
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: ctl.inTrial ? bridge.stopTrial() : bridge.startTrial()
        }
    }
    Text {
        height: 16
        verticalAlignment: Text.AlignVCenter
        text: !ctl.inTrial ? "" : ctl.manual ? "MANUAL" : "DETECTED"
        color: ctl.manual ? theme.textDim : theme.textMute
        font.family: theme.font
        font.pixelSize: 11
        font.weight: Font.DemiBold
        font.letterSpacing: 1.3
    }
}
