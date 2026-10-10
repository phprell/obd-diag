import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Freeze Frame (Mode 02): Messwerte in dem Moment, als ein Fehlercode gespeichert wurde.
Rectangle {
    id: pane

    required property var vm
    readonly property var frame: vm.freezeFrame
    readonly property bool shown: frame.available === true && frame.empty !== true

    color: Theme.background

    EmptyState {
        objectName: "freezeEmpty"
        anchors.centerIn: parent
        width: Math.min(parent.width - 2 * Theme.pad, 520)
        visible: !pane.shown
        title: pane.vm.busy ? qsTr("Lese Freeze Frame …")
             : !pane.vm.hasResult ? qsTr("Noch nicht verbunden")
             : pane.frame.available ? qsTr("Kein Freeze Frame gespeichert")
             : qsTr("Nicht verfügbar – Steuergerät hat nicht geantwortet")
        text: pane.vm.busy ? ""
            : !pane.vm.hasResult
              ? qsTr("Nach „Verbinden & Scannen“ stehen hier die Messwerte, die das Steuergerät beim Speichern eines Fehlercodes festgehalten hat.")
            : pane.frame.available
              ? qsTr("Das Steuergerät hat keine Momentaufnahme abgelegt, etwa weil kein Fehlercode gesetzt ist oder die Codes gelöscht wurden.")
              : qsTr("Das Steuergerät hat Mode 02 (Freeze Frame) nicht beantwortet.")
    }

    ScrollView {
        id: scroll
        anchors.fill: parent
        visible: pane.shown
        contentWidth: availableWidth
        clip: true

        ColumnLayout {
            x: Math.max(Theme.pad, (scroll.availableWidth - width) / 2)
            width: Math.min(scroll.availableWidth - 2 * Theme.pad, 820)
            spacing: Theme.pad

            Item {
                implicitHeight: 1
            }

            Card {
                Layout.fillWidth: true

                SectionTitle {
                    text: qsTr("Auslösender Code")
                    topPadding: 0
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 12
                    Label {
                        objectName: "freezeDtc"
                        text: pane.frame.dtc !== "" ? pane.frame.dtc : qsTr("nicht gemeldet")
                        font.family: pane.frame.dtc !== "" ? "monospace" : Qt.application.font.family
                        font.bold: pane.frame.dtc !== ""
                        font.pixelSize: pane.frame.dtc !== "" ? 22 : 14
                        color: pane.frame.dtc !== "" ? palette.text : Theme.muted
                    }
                    Label {
                        Layout.fillWidth: true
                        text: pane.frame.dtcTitle ?? ""
                        wrapMode: Text.Wrap
                        font.pixelSize: 15
                    }
                }

                SectionTitle {
                    visible: rows.count > 0
                    Layout.topMargin: 8
                    text: qsTr("Messwerte beim Speichern des Codes")
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 0
                    Repeater {
                        id: rows
                        objectName: "freezeRows"
                        model: pane.frame.rows ?? []
                        delegate: FactRow {
                            required property var modelData
                            required property int index
                            label: modelData.label
                            value: modelData.unit !== "" ? modelData.value + " " + modelData.unit
                                                         : modelData.value
                            last: index === rows.count - 1
                        }
                    }
                }
                Label {
                    visible: rows.count === 0
                    text: qsTr("Keine Messwerte gespeichert.")
                    color: Theme.muted
                }

                Label {
                    Layout.fillWidth: true
                    Layout.topMargin: 4
                    text: qsTr("Momentaufnahme (Frame 0) von dem Augenblick, in dem das Steuergerät den Code gespeichert hat. Hilft einzugrenzen, unter welchen Bedingungen der Fehler auftritt. Wird beim Löschen der Codes mit gelöscht.")
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
