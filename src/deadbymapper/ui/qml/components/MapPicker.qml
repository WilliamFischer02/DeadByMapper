import QtQuick
import QtQuick.Shapes
import QtQuick.Controls.Basic

// Pick the current map by hand: today the only way to tell the app which map you loaded into
// (automatic identification is M3), and the override afterwards. Field + filterable dropdown,
// laid out like TrialControl (label / control / caption) so the two read as one action area.
// Keyboard: type to filter, Up/Down to move, Enter to pick, Esc to close.
Column {
    id: picker
    property int fieldWidth: 248
    property string filter: ""
    readonly property var current: bridge.map
    readonly property bool hasMap: (current.id || "") !== ""
    readonly property var rows: build(bridge.pickableMaps, filter)
    spacing: 4

    // Flatten the pickable maps into group headers + rows (Test Arena first, then realms).
    function build(maps, q) {
        var out = [], last = null, needle = q.trim().toLowerCase()
        for (var i = 0; i < maps.length; ++i) {
            var m = maps[i]
            if (needle !== "" && m.name.toLowerCase().indexOf(needle) < 0 && m.realm.toLowerCase().indexOf(needle) < 0)
                continue
            if (m.group !== last) {
                out.push({ header: true, label: m.group, id: "", name: "", status: "" })
                last = m.group
            }
            out.push({ header: false, label: "", id: m.id, name: m.name, status: m.status })
        }
        return out
    }
    function firstMapRow() {
        for (var i = 0; i < rows.length; ++i)
            if (!rows[i].header) return i
        return -1
    }
    function rowOf(mapId) {
        for (var i = 0; i < rows.length; ++i)
            if (rows[i].id === mapId) return i
        return firstMapRow()
    }
    function step(d) {
        var i = list.currentIndex
        do { i += d } while (i >= 0 && i < rows.length && rows[i].header)
        if (i >= 0 && i < rows.length) {
            list.currentIndex = i
            list.positionViewAtIndex(i, ListView.Contain)
        }
    }
    function pick(mapId) {
        bridge.confirmMap(mapId)
        popup.close()
    }
    function pickCurrent() {
        var i = list.currentIndex
        if (i >= 0 && i < rows.length && !rows[i].header) pick(rows[i].id)
    }

    Text {
        height: 16
        verticalAlignment: Text.AlignVCenter
        text: "MAP"
        color: theme.textMute
        font.family: theme.font
        font.pixelSize: 11
        font.weight: Font.DemiBold
        font.letterSpacing: 1.3
    }

    Rectangle {
        id: field
        width: picker.fieldWidth
        height: 32
        radius: theme.radiusSmall
        color: fieldMa.containsMouse || popup.opened ? theme.surfaceHover : theme.surface2
        border.color: popup.opened ? theme.textMute : theme.hairlineStrong
        border.width: 1

        Text {
            x: 12
            width: (badge.visible ? badge.x - 8 : chev.x - 8) - x
            height: field.height
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
            text: picker.hasMap ? picker.current.name : "Pick a map"
            color: picker.hasMap ? theme.text : theme.textDim
            font.family: theme.font
            font.pixelSize: 13
            font.weight: picker.hasMap ? Font.DemiBold : Font.Normal
        }
        StatusBadge {
            id: badge
            visible: picker.hasMap
            status: picker.current.status || ""
            x: chev.x - 8 - width
            y: 6
        }
        Shape {
            id: chev
            x: field.width - 12 - width
            y: 13
            width: 10; height: 6
            preferredRendererType: Shape.CurveRenderer
            rotation: popup.opened ? 180 : 0
            ShapePath {
                strokeColor: theme.textDim
                strokeWidth: 1.5
                fillColor: "transparent"
                capStyle: ShapePath.RoundCap
                joinStyle: ShapePath.RoundJoin
                startX: 1; startY: 1
                PathLine { x: 5; y: 5 }
                PathLine { x: 9; y: 1 }
            }
        }
        MouseArea {
            id: fieldMa
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: popup.opened ? popup.close() : popup.open()
        }
    }

    Text {
        width: picker.fieldWidth
        height: 16
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
        text: !picker.hasMap ? "Pick the map you loaded into"
            : picker.current.status === "stub" ? "No layout yet — breadcrumb only"
            : picker.current.status === "partial" ? "Partial layout — some structures missing"
            : picker.current.status === "synthetic" ? "Test map — full layout"
            : "Full layout"
        color: picker.hasMap && picker.current.status === "stub" ? theme.warn : theme.textMute
        font.family: theme.font
        font.pixelSize: 11
    }

    Popup {
        id: popup
        objectName: "mapPickerPopup"
        parent: field
        x: field.width - width
        y: field.height + 4
        width: 320
        height: 400
        padding: 8
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent
        onOpened: {
            query.text = ""
            picker.filter = ""
            list.currentIndex = picker.rowOf(picker.current.id || "")
            list.positionViewAtIndex(list.currentIndex, ListView.Center)
            query.forceActiveFocus()
        }
        background: Rectangle {
            color: theme.surface2
            border.color: theme.hairlineStrong
            border.width: 1
            radius: theme.radiusSmall
        }
        contentItem: Item {
            id: pop

            Rectangle {
                id: search
                width: pop.width
                height: 32
                radius: theme.radiusTiny
                color: theme.field
                border.color: query.activeFocus ? theme.hairlineStrong : theme.hairline
                border.width: 1
                TextInput {
                    id: query
                    objectName: "mapPickerQuery"
                    x: 10
                    width: search.width - 20
                    height: search.height
                    verticalAlignment: TextInput.AlignVCenter
                    clip: true
                    color: theme.text
                    selectionColor: theme.selection
                    selectedTextColor: theme.text
                    selectByMouse: true
                    font.family: theme.font
                    font.pixelSize: 13
                    onTextChanged: {
                        picker.filter = text
                        list.currentIndex = picker.firstMapRow()
                        list.positionViewAtBeginning()
                    }
                    Keys.onDownPressed: picker.step(1)
                    Keys.onUpPressed: picker.step(-1)
                    Keys.onReturnPressed: picker.pickCurrent()
                    Keys.onEnterPressed: picker.pickCurrent()
                }
                Text {
                    x: 10
                    height: search.height
                    verticalAlignment: Text.AlignVCenter
                    visible: query.text === ""
                    text: "Type to filter maps or realms"
                    color: theme.textMute
                    font.family: theme.font
                    font.pixelSize: 13
                }
            }

            ListView {
                id: list
                y: search.height + 8
                width: pop.width - theme.scrollBar - 8
                height: note.y - 8 - y
                clip: true
                model: picker.rows
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: sb
                delegate: Item {
                    id: row
                    required property var modelData
                    required property int index
                    width: ListView.view.width
                    height: modelData.header ? 28 : 32

                    Text {
                        visible: row.modelData.header
                        x: 10; y: 8
                        width: row.width - 20
                        height: 16
                        verticalAlignment: Text.AlignVCenter
                        elide: Text.ElideRight
                        text: row.modelData.label.toUpperCase()
                        color: theme.textMute
                        font.family: theme.font
                        font.pixelSize: 11
                        font.weight: Font.DemiBold
                        font.letterSpacing: 1.3
                    }
                    Rectangle {
                        visible: !row.modelData.header
                        anchors.fill: parent
                        radius: theme.radiusTiny
                        readonly property bool isCurrent: row.modelData.id === (picker.current.id || "")
                        color: row.index === list.currentIndex || rowMa.containsMouse ? theme.surfaceHover : "transparent"

                        Shape {
                            id: tick
                            visible: parent.isCurrent
                            x: 10; y: 12
                            width: 10; height: 8
                            preferredRendererType: Shape.CurveRenderer
                            ShapePath {
                                strokeColor: theme.ok
                                strokeWidth: 1.5
                                fillColor: "transparent"
                                capStyle: ShapePath.RoundCap
                                joinStyle: ShapePath.RoundJoin
                                startX: 1; startY: 4
                                PathLine { x: 4; y: 7 }
                                PathLine { x: 9; y: 1 }
                            }
                        }
                        Text {
                            x: 28
                            width: rowBadge.x - 8 - x
                            height: parent.height
                            verticalAlignment: Text.AlignVCenter
                            elide: Text.ElideRight
                            text: row.modelData.name
                            color: theme.text
                            font.family: theme.font
                            font.pixelSize: 13
                            font.weight: parent.isCurrent ? Font.DemiBold : Font.Normal
                        }
                        StatusBadge {
                            id: rowBadge
                            status: row.modelData.status
                            x: parent.width - 8 - width
                            y: 6
                        }
                        MouseArea {
                            id: rowMa
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: picker.pick(row.modelData.id)
                        }
                    }
                }
            }
            ListFade {
                list: list
                width: list.width
                y: list.y + list.height - height
                base: theme.surface2
                clear: theme.surface2Clear
            }
            SlimScrollBar {
                id: sb
                x: pop.width - width
                y: list.y
                height: list.height
            }
            Text {
                visible: picker.rows.length === 0
                y: list.y + 8
                x: 10
                text: "No map matches “" + query.text + "”"
                color: theme.textDim
                font.family: theme.font
                font.pixelSize: 13
            }
            Text {
                id: note
                width: pop.width
                y: Math.round(pop.height - height)
                wrapMode: Text.WordWrap
                text: "Stub maps have no layout yet: on them the pane shows your breadcrumb trail only."
                color: theme.textMute
                font.family: theme.font
                font.pixelSize: 11
                lineHeight: 1.2
            }
        }
    }
}
