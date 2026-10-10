import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Reiter über dem Hauptbereich: Fehlercodes, Readiness, Freeze Frame, Fahrzeug,
// Live-Daten.
TabBar {
    id: bar

    required property var vm
    required property var live  // LiveViewModel

    spacing: 0
    background: Item {}

    component ViewTab: TabButton {
        id: tab

        property string badge: ""
        property color dot: "transparent"

        width: implicitWidth
        leftPadding: 14
        rightPadding: 14
        topPadding: 11
        bottomPadding: 11

        contentItem: RowLayout {
            spacing: 7
            Rectangle {
                visible: tab.dot.a > 0
                Layout.alignment: Qt.AlignVCenter
                implicitWidth: 8
                implicitHeight: 8
                radius: 4
                color: tab.dot
            }
            Label {
                text: tab.text
                color: tab.checked ? palette.text : Theme.muted
                font.bold: true
            }
            Rectangle {
                visible: tab.badge !== ""
                Layout.alignment: Qt.AlignVCenter
                implicitWidth: Math.max(badgeLabel.implicitWidth + 10, implicitHeight)
                implicitHeight: badgeLabel.implicitHeight + 2
                radius: implicitHeight / 2
                color: tab.checked ? Theme.selection : Theme.background
                Label {
                    id: badgeLabel
                    anchors.centerIn: parent
                    text: tab.badge
                    font.pixelSize: 11
                    font.bold: true
                    color: tab.checked ? Theme.accent : Theme.muted
                }
            }
        }
        background: Rectangle {
            color: tab.hovered && !tab.checked ? Theme.background : "transparent"
            Rectangle {
                visible: tab.checked
                anchors.bottom: parent.bottom
                width: parent.width
                height: 2
                color: Theme.accent
            }
        }
    }

    ViewTab {
        objectName: "tabCodes"
        text: qsTr("Fehlercodes")
        badge: bar.vm.hasResult ? String(bar.vm.codeCount) : ""
    }
    ViewTab {
        objectName: "tabReadiness"
        text: qsTr("Readiness")
        dot: !bar.vm.readiness.available ? "transparent"
             : bar.vm.readiness.ready ? Theme.okDot : Theme.warnDot  // offen ist kein Fehler
    }
    ViewTab {
        objectName: "tabFreezeFrame"
        text: qsTr("Freeze Frame")
    }
    ViewTab {
        objectName: "tabVehicle"
        text: qsTr("Fahrzeug")
    }
    ViewTab {
        objectName: "tabLive"
        text: qsTr("Live-Daten")
        dot: bar.live.running ? Theme.okDot : "transparent"  // Abfrage läuft
    }
}
