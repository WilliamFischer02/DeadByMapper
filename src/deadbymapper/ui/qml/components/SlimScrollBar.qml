import QtQuick
import QtQuick.Controls.Basic

// 4 px scrollbar for dashboard lists. Place it BESIDE the list (the list reserves a gutter of
// scrollBar + 8 px), never on top of the content. Thumb only, no track; hidden when nothing scrolls.
ScrollBar {
    id: sb
    width: theme.scrollBar
    padding: 0
    minimumSize: 0.12
    policy: ScrollBar.AlwaysOn
    visible: size < 1.0
    contentItem: Rectangle {
        implicitWidth: theme.scrollBar
        radius: theme.scrollBar / 2
        color: sb.pressed || sb.hovered ? theme.textMute : theme.hairlineStrong
    }
    background: Item {}
}
