import QtQuick

// Bottom-edge fade for a clipped list: says "more below" instead of cutting a row mid-text.
// An overlay on top of the list (the text underneath stays crisp); hidden at the end of the list.
Rectangle {
    id: fade
    property Flickable list: null
    property color base: theme.surface
    property color clear: theme.surfaceClear
    height: 32
    visible: list !== null && !list.atYEnd
    gradient: Gradient {
        GradientStop { position: 0.0; color: fade.clear }
        GradientStop { position: 1.0; color: fade.base }
    }
}
