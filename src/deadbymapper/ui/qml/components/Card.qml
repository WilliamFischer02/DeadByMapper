import QtQuick

// Dashboard card: eyebrow (caps caption), title (card title, 15 px), body.
// Optional: a status pip that leads the title (the title is the pip's label), and a header action
// area (`actions`) right-aligned on the eyebrow line; the title elides before it.
Rectangle {
    id: card
    property string eyebrow
    property string title
    property color pipColor: "transparent"
    property bool pipPulse: false
    default property alias content: body.data
    property alias actions: act.data
    readonly property real actionsWidth: act.width > 0 ? act.width + 24 : 0

    radius: theme.radius
    color: theme.surface
    border.color: theme.hairline
    border.width: 1

    Text {
        id: eb
        x: 20; y: 16
        height: 16
        verticalAlignment: Text.AlignVCenter
        visible: card.eyebrow !== ""
        text: card.eyebrow.toUpperCase()
        color: theme.textMute
        font.family: theme.font
        font.pixelSize: 11
        font.weight: Font.DemiBold
        font.letterSpacing: 1.3
    }
    Rectangle {
        id: pip
        visible: card.pipColor.a > 0
        x: 20
        y: tt.y + 6
        width: 8; height: 8; radius: 4
        color: card.pipColor
        SequentialAnimation on opacity {
            running: card.pipPulse && pip.visible
            loops: Animation.Infinite
            NumberAnimation { to: 0.3; duration: 900; easing.type: Easing.InOutSine }
            NumberAnimation { to: 1.0; duration: 900; easing.type: Easing.InOutSine }
            onStopped: pip.opacity = 1
        }
    }
    Text {
        id: tt
        x: pip.visible ? 36 : 20
        y: eb.visible ? 36 : 16
        height: 20
        verticalAlignment: Text.AlignVCenter
        width: card.width - x - 20 - card.actionsWidth
        elide: Text.ElideRight
        visible: card.title !== ""
        text: card.title
        color: theme.text
        font.family: theme.fontDisplay
        font.pixelSize: 15
        font.weight: Font.DemiBold
    }
    Row {
        id: act
        anchors.right: card.right
        anchors.rightMargin: 20
        y: 16
        spacing: 12
    }
    Item {
        id: body
        anchors {
            left: card.left; right: card.right; bottom: card.bottom
            top: tt.visible ? tt.bottom : (eb.visible ? eb.bottom : card.top)
            leftMargin: 20; rightMargin: 20; bottomMargin: 20; topMargin: 12
        }
    }
}
