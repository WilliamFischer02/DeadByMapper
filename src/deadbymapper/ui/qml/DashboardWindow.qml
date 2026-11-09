import QtQuick
import QtQuick.Window
import QtQuick.Controls.Basic
import "components"

// Out-of-trial home: is the game linked, are the sensors healthy, is calibration done, how much
// map data exists. Everything here answers "will the minimap work when I queue up?"
Window {
    id: dash
    width: 1080
    height: 720
    minimumWidth: 920
    minimumHeight: 660
    color: theme.bg
    title: "DeadByMapper"

    readonly property var link: bridge.link
    readonly property var lib: bridge.library
    readonly property var counts: lib.counts || ({})

    readonly property bool inTrial: bridge.mode === "match"
    readonly property string mapStatus: bridge.map.status || ""

    function heroTitle() {
        if (dash.inTrial) return "In a trial · " + (bridge.map.name || "map not picked yet")
        if (bridge.mode === "review") return "Trial finished"
        if (!link.running) return "Waiting for Dead by Daylight"
        return ["Linked", link.platformLabel, link.resolution, link.displayLabel].filter(function (s) { return !!s }).join(" · ")
    }
    // ONE line: what the player should do next. Must fit beside the action area (~75 characters).
    function heroSub() {
        if (bridge.isDemo)
            return dash.inTrial ? "A simulated survivor is playing. Stop trial to see the idle dashboard."
                                : "Demo paused. Start trial brings the pane back."
        if (bridge.mode === "review") return "Trial over: the pane shows your route. Start trial when the next one loads."
        if (link.running && link.display === "fullscreen")
            return "Exclusive fullscreen hides overlays. Switch to Windowed Fullscreen."
        if (dash.inTrial && !bridge.hud.trialManual) return "Trial detected and the pane is up. Stop trial if that is wrong."
        if (dash.inTrial && !bridge.map.id) return "Pick the map you loaded into so the pane can place you."
        if (dash.inTrial && dash.mapStatus === "stub") return "No layout for this map yet: the pane follows your breadcrumb trail."
        if (dash.inTrial) return "The pane is up. Press Stop trial when you are back in the lobby."
        if (!link.running) return "Start Dead by Daylight, then press Start trial when the match loads."
        return "When the match loads, pick the map and press Start trial."
    }

    component NavItem: Rectangle {
        id: nav
        property string label
        property bool current: false
        width: 60; height: 52; radius: 10
        color: current ? theme.surface2 : (h.hovered ? theme.surface : "transparent")
        Column {
            anchors.centerIn: parent
            spacing: 5
            Rectangle {
                anchors.horizontalCenter: parent.horizontalCenter
                width: 18; height: 3; radius: 1.5
                color: nav.current ? theme.player : theme.textMute
            }
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: nav.label
                color: nav.current ? theme.text : theme.textDim
                font.family: theme.font
                font.pixelSize: 11
                font.weight: nav.current ? Font.DemiBold : Font.Normal
            }
        }
        HoverHandler { id: h }
    }

    component PrimaryButton: Rectangle {
        id: btn
        property string text
        property bool enabledLook: true
        signal clicked()
        width: Math.ceil(lbl.implicitWidth) + 28; height: 32; radius: theme.radiusSmall
        color: !enabledLook ? theme.surface2 : (ma.containsMouse ? theme.white : theme.player)
        Text {
            id: lbl
            anchors.centerIn: parent
            text: btn.text
            color: btn.enabledLook ? theme.onLight : theme.textMute
            font.family: theme.font
            font.pixelSize: 13
            font.weight: Font.DemiBold
        }
        MouseArea { id: ma; anchors.fill: parent; hoverEnabled: true; onClicked: btn.clicked() }
    }

    // ------------------------------------------------------------------ rail
    Rectangle {
        id: rail
        width: 80
        anchors { top: parent.top; bottom: parent.bottom; left: parent.left }
        color: theme.rail
        Rectangle { width: 1; anchors { top: parent.top; bottom: parent.bottom; right: parent.right } color: theme.hairline }
        BrandMark { size: 38; anchors.horizontalCenter: parent.horizontalCenter; y: 22 }
        Column {
            y: 92
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: 6
            NavItem { label: "Live"; current: true }
            NavItem { label: "Maps" }
            NavItem { label: "Calibrate" }
            NavItem { label: "Sessions" }
            NavItem { label: "Settings" }
        }
    }

    // ------------------------------------------------------------------ content
    Item {
        id: content
        anchors { left: rail.right; right: parent.right; top: parent.top; bottom: parent.bottom; margins: 28 }

        Column {
            id: head
            spacing: 4
            Text {
                text: "DeadByMapper"
                color: theme.text
                font.family: theme.fontDisplay
                font.pixelSize: 22
                font.weight: Font.DemiBold
            }
            Text {
                text: "Glanceable minimap companion for Dead by Daylight · v" + (link.version || "")
                color: theme.textMute
                font.family: theme.font
                font.pixelSize: 13
            }
        }
        Rectangle {
            visible: bridge.isDemo
            anchors { right: parent.right; top: parent.top; topMargin: 6 }
            width: demoTxt.implicitWidth + 20; height: 26; radius: 13
            color: theme.demoFill
            border.color: theme.demoStroke
            Text { id: demoTxt; anchors.centerIn: parent; text: "DEMO · simulated trial"; color: theme.gate; font.family: theme.font; font.pixelSize: 11; font.weight: Font.DemiBold; font.letterSpacing: 0.8 }
        }

        // ---- hero: game link + sensors. The action area (map + trial) sits on the eyebrow line.
        Card {
            id: hero
            anchors { left: parent.left; right: parent.right; top: head.bottom; topMargin: 20 }
            height: 122 + sensors.height
            eyebrow: "Game link"
            title: dash.heroTitle()
            pipColor: link.running ? theme.ok : theme.warn
            pipPulse: !link.running
            actions: [
                MapPicker {},
                TrialControl {}
            ]

            Text {
                id: heroLine
                width: parent.width - hero.actionsWidth
                height: 18
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
                text: dash.heroSub()
                color: theme.textDim
                font.family: theme.font
                font.pixelSize: 13
            }
            // Sensors: dot + name + the reason, so a failure is readable without hovering.
            Grid {
                id: sensors
                y: 34
                width: parent.width
                columns: 4
                rowSpacing: 8
                columnSpacing: 8
                readonly property int cellW: Math.floor((width - (columns - 1) * columnSpacing) / columns)
                Repeater {
                    model: bridge.chips
                    StatusChip {
                        width: sensors.cellW
                        label: modelData.label
                        status: modelData.status
                        detail: modelData.detail
                    }
                }
            }
        }

        // ---- row 2
        Item {
            id: row2
            anchors { left: parent.left; right: parent.right; top: hero.bottom; bottom: foot.top; topMargin: 16; bottomMargin: 16 }
            readonly property real gap: 16
            readonly property real colW: Math.floor((width - 2 * gap) / 3)

            Card {
                id: calib
                x: 0; width: row2.colW; height: parent.height
                eyebrow: "Calibration"
                title: bridge.isDemo ? "Using the demo's mouse value"
                     : bridge.hud.calibrated ? "Mouse yaw calibrated" : "Mouse not calibrated"
                Column {
                    anchors.fill: parent
                    spacing: 12
                    Text {
                        width: parent.width
                        wrapMode: Text.WordWrap
                        text: "One slow 360° spin in a custom game teaches DeadByMapper how far your camera turns per mouse count. Redo it after changing DPI or sensitivity."
                        color: theme.textDim
                        font.family: theme.font
                        font.pixelSize: 13
                        lineHeight: 1.2
                    }
                    PrimaryButton { text: "Calibrate mouse"; enabledLook: false }
                    Text {
                        width: parent.width
                        wrapMode: Text.WordWrap
                        text: bridge.isDemo ? "Demo mode runs on the simulator's value, not your mouse. The wizard arrives in M2."
                                            : "The wizard arrives in milestone M2."
                        color: theme.textMute
                        font.family: theme.font
                        font.pixelSize: 11
                        lineHeight: 1.2
                    }
                }
            }

            Card {
                id: library
                x: row2.colW + row2.gap; width: row2.colW; height: parent.height
                eyebrow: "Map library"
                title: (dash.counts.total || 0) + " live maps · " + (dash.counts.verified || 0) + " mapped"
                Column {
                    anchors.fill: parent
                    spacing: 10
                    Rectangle {
                        width: parent.width; height: 6; radius: 3
                        color: theme.surface2
                        Rectangle {
                            height: parent.height; radius: 3
                            width: parent.width * ((dash.counts.verified || 0) + 0.5 * (dash.counts.partial || 0)) / Math.max(1, dash.counts.total || 1)
                            color: theme.genDone
                        }
                    }
                    Item {
                        width: parent.width
                        height: parent.height - 16
                        ListView {
                            id: libList
                            // Gutter: the scrollbar sits beside the rows, never over the badges.
                            width: parent.width - theme.scrollBar - 8
                            height: parent.height
                            clip: true
                            model: dash.lib.maps || []
                            boundsBehavior: Flickable.StopAtBounds
                            ScrollBar.vertical: libBar
                            delegate: Item {
                                width: ListView.view.width
                                height: 28
                                Text {
                                    width: parent.width - libBadge.width - 8
                                    height: parent.height
                                    verticalAlignment: Text.AlignVCenter
                                    elide: Text.ElideRight
                                    text: modelData.name
                                    color: theme.text
                                    font.family: theme.font
                                    font.pixelSize: 13
                                }
                                StatusBadge { id: libBadge; x: parent.width - width; y: 4; status: modelData.status }
                            }
                        }
                        ListFade { list: libList; width: libList.width; y: libList.height - height }
                        SlimScrollBar { id: libBar; x: parent.width - width; height: parent.height }
                    }
                }
            }

            Card {
                id: plan
                x: 2 * (row2.colW + row2.gap); width: row2.width - x; height: parent.height
                eyebrow: bridge.mode === "match" ? "Generator plan" : "Last trial"
                title: bridge.mode === "match"
                       ? (bridge.hud.gensRemaining >= 0 ? bridge.hud.gensRemaining + " generators to go" : "Reading the HUD…")
                       : "No trials recorded yet"
                Column {
                    anchors.fill: parent
                    spacing: 12
                    Text {
                        width: parent.width
                        wrapMode: Text.WordWrap
                        text: bridge.mode === "match"
                              ? (bridge.hud.next ? "Ringed on the pane: the gen that " + bridge.hud.nextReason.replace(/3-gen/g, "3‑gen") + ". Clear the tight cluster early so the last gens are far apart — never leave a 3‑gen." : "Waiting for a position lock to rank generators.")
                              : "After each trial you get your route, the gens you touched and where the heartbeat started. Turn on Record sessions to keep them."
                        color: theme.textDim
                        font.family: theme.font
                        font.pixelSize: 13
                        lineHeight: 1.2
                    }
                    Row {
                        visible: bridge.mode === "match"
                        spacing: 18
                        Column {
                            spacing: 2
                            Text { text: "POSITION"; color: theme.textMute; font.family: theme.font; font.pixelSize: 11; font.letterSpacing: 1.3; font.weight: Font.DemiBold }
                            Text { text: (bridge.player.status || "").replace("_", " "); color: theme.text; font.family: theme.font; font.pixelSize: 15; font.weight: Font.DemiBold }
                        }
                        Column {
                            spacing: 2
                            Text { text: "HEARTBEAT"; color: theme.textMute; font.family: theme.font; font.pixelSize: 11; font.letterSpacing: 1.3; font.weight: Font.DemiBold }
                            Text {
                                text: bridge.terror.active ? Math.round(bridge.terror.inner) + "–" + Math.round(bridge.terror.outer) + " m" : "silent"
                                color: bridge.terror.active ? theme.danger : theme.text
                                font.family: theme.font; font.pixelSize: 15; font.weight: Font.DemiBold
                            }
                        }
                        Column {
                            spacing: 2
                            Text { text: "TRIAL"; color: theme.textMute; font.family: theme.font; font.pixelSize: 11; font.letterSpacing: 1.3; font.weight: Font.DemiBold }
                            Text {
                                text: Math.floor((bridge.hud.elapsed || 0) / 60) + ":" + ("0" + ((bridge.hud.elapsed || 0) % 60)).slice(-2)
                                color: theme.text; font.family: theme.font; font.pixelSize: 15; font.weight: Font.DemiBold
                            }
                        }
                    }
                }
            }
        }

        Text {
            id: foot
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
            text: "Read-only by design: your screen, the audio you hear, your own keys/mouse and the game's log files. No memory reading, no injection, no automation."
            color: theme.textMute
            font.family: theme.font
            font.pixelSize: 11
            elide: Text.ElideRight
        }
    }
}
