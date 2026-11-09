import QtQuick
import QtQuick.Window
import QtQuick.Controls
import "components"

// Out-of-trial home: is the game linked, are the sensors healthy, is calibration done, how much
// map data exists. Everything here answers "will the minimap work when I queue up?"
Window {
    id: dash
    width: 1080
    height: 680
    minimumWidth: 920
    minimumHeight: 600
    color: theme.bg
    title: "DeadByMapper"

    readonly property var link: bridge.link
    readonly property var lib: bridge.library
    readonly property var counts: lib.counts || ({})

    function heroTitle() {
        if (!link.running) return "Waiting for Dead by Daylight"
        if (bridge.mode === "match") return "In a trial · " + (bridge.map.name || "map not identified yet")
        if (bridge.mode === "review") return "Trial finished"
        return "Linked · " + [link.platform, link.resolution, link.display].filter(function (s) { return s && s !== "unknown" }).join(" · ")
    }
    function heroSub() {
        if (bridge.isDemo) return "Demo mode: a simulated survivor is playing the Test Arena so you can see every layer of the pane."
        if (!link.running) return "Launch the game. Press Start trial when you load into a match — automatic detection arrives in M3."
        if (link.display === "fullscreen") return "Exclusive fullscreen hides overlays — use Windowed Fullscreen, or put the pane on a second monitor."
        if (bridge.hud.trialManual) return "Manual trial running — the pane is up. Press Stop trial when the match ends."
        return "Ready. Press Start trial when you load into a match — automatic detection arrives in M3."
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

    component Badge: Rectangle {
        property string status
        width: t.implicitWidth + 14; height: 20; radius: 10
        color: status === "verified" ? theme.badgeVerified
             : status === "partial" ? theme.badgePartial
             : status === "synthetic" ? theme.badgeSynthetic : theme.badgeStub
        Text {
            id: t
            anchors.centerIn: parent
            text: parent.status
            color: parent.status === "verified" ? theme.genDone : parent.status === "partial" ? theme.warn
                 : parent.status === "synthetic" ? theme.gate : theme.textMute
            font.family: theme.font
            font.pixelSize: 10
            font.weight: Font.DemiBold
        }
    }

    component PrimaryButton: Rectangle {
        id: btn
        property string text
        property bool enabledLook: true
        signal clicked()
        width: lbl.implicitWidth + 28; height: 32; radius: 8
        color: !enabledLook ? theme.surface2 : (ma.containsMouse ? theme.white : theme.player)
        Text {
            id: lbl
            anchors.centerIn: parent
            text: btn.text
            color: btn.enabledLook ? theme.onLight : theme.textMute
            font.family: theme.font
            font.pixelSize: 12
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
                font.pixelSize: 26
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

        // ---- hero: game link + sensors
        Card {
            id: hero
            anchors { left: parent.left; right: parent.right; top: head.bottom; topMargin: 22 }
            height: 150
            eyebrow: "Game link"
            title: dash.heroTitle()

            Column {
                anchors.fill: parent
                spacing: 16
                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    text: dash.heroSub()
                    color: theme.textDim
                    font.family: theme.font
                    font.pixelSize: 13
                }
                Flow {
                    width: parent.width
                    spacing: 8
                    Repeater {
                        model: bridge.chips
                        StatusChip { label: modelData.label; status: modelData.status; detail: modelData.detail }
                    }
                }
            }
            TrialControl {
                anchors { right: parent.right; top: parent.top; rightMargin: 20; topMargin: 40 }
            }
            Rectangle {
                anchors { right: parent.right; top: parent.top; rightMargin: 20; topMargin: 22 }
                width: 10; height: 10; radius: 5
                color: link.running ? theme.ok : theme.warn
                SequentialAnimation on opacity {
                    running: !link.running
                    loops: Animation.Infinite
                    NumberAnimation { to: 0.3; duration: 900; easing.type: Easing.InOutSine }
                    NumberAnimation { to: 1.0; duration: 900; easing.type: Easing.InOutSine }
                }
            }
        }

        // ---- row 2
        Item {
            id: row2
            anchors { left: parent.left; right: parent.right; top: hero.bottom; bottom: foot.top; topMargin: 16; bottomMargin: 14 }
            readonly property real gap: 16
            readonly property real colW: (width - 2 * gap) / 3

            Card {
                id: calib
                x: 0; width: row2.colW; height: parent.height
                eyebrow: "Calibration"
                title: bridge.hud.calibrated ? "Mouse yaw calibrated" : "Mouse not calibrated"
                Column {
                    anchors.fill: parent
                    spacing: 14
                    Text {
                        width: parent.width
                        wrapMode: Text.WordWrap
                        text: "One slow 360° spin in a custom game teaches DeadByMapper how far your camera turns per mouse count. Takes about 20 seconds; redo it if you change DPI or in-game sensitivity."
                        color: theme.textDim
                        font.family: theme.font
                        font.pixelSize: 12
                        lineHeight: 1.25
                    }
                    PrimaryButton { text: bridge.hud.calibrated ? "Recalibrate" : "Calibrate mouse"; enabledLook: false }
                    Text { text: "Wizard arrives in milestone M2"; color: theme.textMute; font.family: theme.font; font.pixelSize: 11 }
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
                    ListView {
                        width: parent.width
                        height: parent.height - 16
                        clip: true
                        spacing: 2
                        model: dash.lib.maps || []
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                        delegate: Item {
                            width: ListView.view.width - 8
                            height: 28
                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                width: parent.width - 80
                                elide: Text.ElideRight
                                text: modelData.name
                                color: theme.text
                                font.family: theme.font
                                font.pixelSize: 12
                            }
                            Badge { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; status: modelData.status }
                        }
                    }
                }
            }

            Card {
                id: plan
                x: 2 * (row2.colW + row2.gap); width: row2.colW; height: parent.height
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
                              ? (bridge.hud.next ? "Ringed on the pane: the gen that " + bridge.hud.nextReason + ". Clear the tight cluster early so the last gens are far apart — never leave a 3-gen." : "Waiting for a position lock to rank generators.")
                              : "After each trial you get your route, the gens you touched and where the heartbeat started. Turn on Record sessions to keep them."
                        color: theme.textDim
                        font.family: theme.font
                        font.pixelSize: 12
                        lineHeight: 1.25
                    }
                    Row {
                        visible: bridge.mode === "match"
                        spacing: 18
                        Column {
                            spacing: 2
                            Text { text: "POSITION"; color: theme.textMute; font.family: theme.font; font.pixelSize: 10; font.letterSpacing: 1.1; font.weight: Font.DemiBold }
                            Text { text: (bridge.player.status || "").replace("_", " "); color: theme.text; font.family: theme.font; font.pixelSize: 14; font.weight: Font.DemiBold }
                        }
                        Column {
                            spacing: 2
                            Text { text: "HEARTBEAT"; color: theme.textMute; font.family: theme.font; font.pixelSize: 10; font.letterSpacing: 1.1; font.weight: Font.DemiBold }
                            Text {
                                text: bridge.terror.active ? Math.round(bridge.terror.inner) + "–" + Math.round(bridge.terror.outer) + " m" : "silent"
                                color: bridge.terror.active ? theme.danger : theme.text
                                font.family: theme.font; font.pixelSize: 14; font.weight: Font.DemiBold
                            }
                        }
                        Column {
                            spacing: 2
                            Text { text: "TRIAL"; color: theme.textMute; font.family: theme.font; font.pixelSize: 10; font.letterSpacing: 1.1; font.weight: Font.DemiBold }
                            Text {
                                text: Math.floor((bridge.hud.elapsed || 0) / 60) + ":" + ("0" + ((bridge.hud.elapsed || 0) % 60)).slice(-2)
                                color: theme.text; font.family: theme.font; font.pixelSize: 14; font.weight: Font.DemiBold
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
