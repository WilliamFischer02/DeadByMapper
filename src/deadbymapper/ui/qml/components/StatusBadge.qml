import QtQuick

// Map-data status pill: stub / partial / verified / synthetic. Word AND tint carry the meaning.
Rectangle {
    id: badge
    property string status
    readonly property color ink: status === "verified" ? theme.genDone
                               : status === "partial" ? theme.warn
                               : status === "synthetic" ? theme.gate : theme.textMute
    width: Math.ceil(t.implicitWidth) + 16
    height: 20
    radius: 10
    color: status === "verified" ? theme.badgeVerified
         : status === "partial" ? theme.badgePartial
         : status === "synthetic" ? theme.badgeSynthetic : theme.badgeStub
    Text {
        id: t
        x: 8
        height: parent.height
        verticalAlignment: Text.AlignVCenter
        text: badge.status
        color: badge.ink
        font.family: theme.font
        font.pixelSize: 11
        font.weight: Font.DemiBold
    }
}
