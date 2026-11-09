import QtQuick

Rectangle {
    id: card
    property string eyebrow
    property string title
    default property alias content: body.data

    radius: theme.radius
    color: theme.surface
    border.color: theme.hairline
    border.width: 1

    Text {
        id: eb
        x: 20; y: 16
        visible: card.eyebrow !== ""
        text: card.eyebrow.toUpperCase()
        color: theme.textMute
        font.family: theme.font
        font.pixelSize: 11
        font.weight: Font.DemiBold
        font.letterSpacing: 1.3
    }
    Text {
        id: tt
        x: 20
        anchors.top: eb.visible ? eb.bottom : card.top
        anchors.topMargin: eb.visible ? 5 : 16
        width: card.width - 40
        elide: Text.ElideRight
        visible: card.title !== ""
        text: card.title
        color: theme.text
        font.family: theme.fontDisplay
        font.pixelSize: 17
        font.weight: Font.DemiBold
    }
    Item {
        id: body
        anchors {
            left: card.left; right: card.right; bottom: card.bottom
            top: tt.visible ? tt.bottom : (eb.visible ? eb.bottom : card.top)
            leftMargin: 20; rightMargin: 20; bottomMargin: 18; topMargin: 12
        }
    }
}
