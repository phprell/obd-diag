import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Gebrauchtwagen-Check: Zähler seit dem Löschen (PID 30, 31, 4E, 21, 4D), Warnleuchte,
// permanente Codes (Mode 0A) und ein Urteil, ob kürzlich gelöscht wurde.
Rectangle {
    id: pane

    required property var vm
    readonly property var info: vm.usedCar
    readonly property bool available: info.available === true
    readonly property string verdict: info.verdict ?? ""

    function levelText(level) {
        return level === "warning" ? Theme.warnText : level === "ok" ? Theme.okText : palette.text
    }

    color: Theme.background

    EmptyState {
        objectName: "usedCarEmpty"
        anchors.centerIn: parent
        width: Math.min(parent.width - 2 * Theme.pad, 520)
        visible: !pane.available
        title: pane.vm.busy ? qsTr("Lese Zähler seit dem Löschen …") : qsTr("Noch nicht verbunden")
        text: pane.vm.busy ? ""
            : qsTr("Nach „Verbinden & Scannen“ steht hier, ob die Fehlercodes vor kurzem gelöscht wurden: Warmläufe, Strecke und Zeit seit dem Löschen, Warnleuchte und permanente Codes.")
    }

    ScrollView {
        id: scroll
        anchors.fill: parent
        visible: pane.available
        contentWidth: availableWidth
        clip: true

        ColumnLayout {
            x: Math.max(Theme.pad, (scroll.availableWidth - width) / 2)
            width: Math.min(scroll.availableWidth - 2 * Theme.pad, 820)
            spacing: Theme.pad

            Item {
                implicitHeight: 1
            }

            // Urteil und Befunde
            Card {
                Layout.fillWidth: true

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.pad

                    Rectangle {
                        Layout.alignment: Qt.AlignTop
                        implicitWidth: 44
                        implicitHeight: 44
                        radius: 22
                        color: pane.verdict === "inconspicuous" ? Theme.okBg
                             : pane.verdict === "suspicious" ? Theme.warnBg : Theme.background
                        border.color: pane.verdict === "inconspicuous" ? Theme.okBorder
                                    : pane.verdict === "suspicious" ? Theme.warnBorder : Theme.border
                        Label {
                            anchors.centerIn: parent
                            text: pane.verdict === "inconspicuous" ? "✓"
                                : pane.verdict === "suspicious" ? "!" : "?"
                            font.pixelSize: 22
                            font.bold: true
                            color: pane.verdict === "inconspicuous" ? Theme.okText
                                 : pane.verdict === "suspicious" ? Theme.warnText : Theme.muted
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 6
                        Label {
                            objectName: "usedCarVerdict"
                            text: pane.info.verdictLabel ?? ""
                            font.pixelSize: 22
                            font.bold: true
                            color: pane.verdict === "inconspicuous" ? Theme.okText
                                 : pane.verdict === "suspicious" ? Theme.warnText : palette.text
                        }
                        Repeater {
                            id: findings
                            objectName: "usedCarFindings"
                            model: pane.info.findings ?? []
                            delegate: Label {
                                required property var modelData
                                Layout.fillWidth: true
                                text: (modelData.level === "warning" ? "! "
                                       : modelData.level === "ok" ? "✓ " : "• ") + modelData.text
                                color: pane.levelText(modelData.level)
                                wrapMode: Text.Wrap
                            }
                        }
                    }
                }

                Label {
                    Layout.fillWidth: true
                    Layout.topMargin: 2
                    objectName: "usedCarNote"
                    text: pane.info.note ?? ""
                    color: Theme.muted
                    font.pixelSize: 12
                    wrapMode: Text.Wrap
                }
            }

            // Zähler
            Card {
                Layout.fillWidth: true

                SectionTitle {
                    text: qsTr("Zähler seit dem letzten Löschen")
                    topPadding: 0
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 0
                    visible: counters.count > 0
                    Repeater {
                        id: counters
                        objectName: "usedCarCounters"
                        model: pane.info.counters ?? []
                        delegate: FactRow {
                            required property var modelData
                            required property int index
                            label: modelData.label
                            value: modelData.value
                            last: index === counters.count - 1
                        }
                    }
                }
                Label {
                    Layout.fillWidth: true
                    visible: pane.info.countersRead === false
                    text: qsTr("In dieser Sitzung nicht gelesen (mit einer älteren Version von obd-diag gespeichert).")
                    color: Theme.muted
                    wrapMode: Text.Wrap
                }
                Label {
                    Layout.fillWidth: true
                    visible: pane.info.countersRead === true
                    text: qsTr("Beim Löschen der Fehlercodes setzt das Steuergerät diese Zähler auf null (OBD Mode 01, PID 30, 31, 4E, 21, 4D). Antworten mehrere Steuergeräte, gilt bei den Zählern seit dem Löschen der kleinste Wert.")
                    color: Theme.muted
                    font.pixelSize: 12
                    wrapMode: Text.Wrap
                }
            }

            Item {
                implicitHeight: Theme.gap
            }
        }
    }
}
