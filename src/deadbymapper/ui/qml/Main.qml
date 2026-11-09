import QtQuick
import QtQuick.Window

// Two windows, one app:
//   dashboard - game link, sensors, calibration, map library (shown when NOT in a trial)
//   pane      - the glanceable minimap (shown during a trial; frameless, always on top, never takes focus)
QtObject {
    id: root

    property Window dashboard: DashboardWindow {
        visible: bridge.mode !== "match" || bridge.showDashboardInMatch
    }

    property Window pane: MinimapWindow {
        visible: bridge.mode === "match" || bridge.mode === "review"
    }
}
