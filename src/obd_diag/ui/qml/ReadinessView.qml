import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Readiness (Mode 01, PID 01): alle Tests abgeschlossen ja/nein und Status der Monitore.
Rectangle {
    id: pane

    required property var vm
    readonly property var info: vm.readiness
    readonly property bool available: info.available === true

    color: Theme.background

    EmptyState {
        objectName: "readinessEmpty"
        anchors.centerIn: parent
        width: Math.min(parent.width - 2 * Theme.pad, 520)
        visible: !pane.available
        title: pane.vm.busy ? qsTr("Lese Readiness …")
             : !pane.vm.hasResult ? qsTr("Noch nicht verbunden")
             : qsTr("Nicht verfügbar – Steuergerät hat nicht geantwortet")
        text: pane.vm.busy ? ""
            : !pane.vm.hasResult
              ? qsTr("Nach „Verbinden & Scannen“ steht hier, ob alle Eigendiagnosen für das Abgassystem (Readiness) abgeschlossen sind.")
              : qsTr("Das Steuergerät hat den Readiness-Status (Mode 01, PID 01) nicht gemeldet.")
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

            // Ergebnis: alle unterstützten Tests abgeschlossen ja/nein (kein AU-Urteil)
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
                        // offene Tests sind kein Fehler, nur „noch nicht fertig“: Warnfarbe
                        color: pane.info.ready ? Theme.okBg : Theme.warnBg
                        border.color: pane.info.ready ? Theme.okBorder : Theme.warnBorder
                        Label {
                            anchors.centerIn: parent
                            text: pane.info.ready ? "✓" : "!"
                            font.pixelSize: 22
                            font.bold: true
                            color: pane.info.ready ? Theme.okText : Theme.warnText
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 4
                        Label {
                            objectName: "readinessHeadline"
                            text: pane.info.readyLabel ?? ""
                            font.pixelSize: 22
                            font.bold: true
                            color: pane.info.ready ? Theme.okText : Theme.warnText
                        }
                        Label {
                            objectName: "readinessSummary"
                            Layout.fillWidth: true
                            text: pane.info.summary ?? ""
                            wrapMode: Text.Wrap
                        }
                    }
                }

                Flow {
                    Layout.fillWidth: true
                    Layout.topMargin: 4
                    spacing: 6
                    Chip {
                        objectName: "readinessMil"
                        text: qsTr("Motorkontrollleuchte (MIL) %1").arg(pane.info.milLabel ?? "")
                        textColor: pane.info.milOn ? Theme.warnText : Theme.muted
                        fill: pane.info.milOn ? Theme.warnBg : Theme.background
                    }
                    Chip {
                        text: qsTr("Gemeldete Fehlercodes: %1").arg(pane.info.dtcCount ?? 0)
                    }
                    Chip {
                        text: qsTr("Motor: %1").arg(pane.info.engineLabel ?? "")
                    }
                }

                Label {
                    Layout.fillWidth: true
                    Layout.topMargin: 2
                    objectName: "readinessNote"
                    text: qsTr("Abgeschlossen heißt: alle Eigendiagnosen, die das Fahrzeug unterstützt, sind gelaufen. Offene Tests laufen bei normaler Fahrt von selbst; nach dem Löschen von Fehlercodes dauert das einige Fahrzyklen.")
                          + " " + (pane.info.auNote ?? "")
                    color: Theme.muted
                    font.pixelSize: 12
                    wrapMode: Text.Wrap
                }
            }

            // Monitore
            Card {
                Layout.fillWidth: true
                spacing: 0

                RowLayout {
                    Layout.fillWidth: true
                    Layout.bottomMargin: 4
                    SectionTitle {
                        Layout.fillWidth: true
                        text: qsTr("Monitore")
                        topPadding: 0
                    }
                    Label {
                        text: qsTr("%1 von %2 abgeschlossen").arg(pane.info.completeCount ?? 0)
                              .arg(pane.info.supportedCount ?? 0)
                        color: Theme.muted
                        font.pixelSize: 12
                    }
                }

                Repeater {
                    id: monitors
                    objectName: "monitorRepeater"
                    model: pane.vm.monitors
                    delegate: Item {
                        id: monitorRow
                        required property int index
                        required property string name
                        required property string state
                        required property string stateLabel

                        Layout.fillWidth: true
                        implicitHeight: 40

                        RowLayout {
                            anchors.fill: parent
                            spacing: Theme.pad
                            Label {
                                Layout.fillWidth: true
                                text: monitorRow.name
                                color: monitorRow.state === "not_supported" ? Theme.muted
                                                                             : palette.text
                                elide: Text.ElideRight
                            }
                            Chip {
                                Layout.preferredWidth: 150
                                text: monitorRow.stateLabel
                                textColor: Theme.monitorText(monitorRow.state)
                                fill: Theme.monitorBg(monitorRow.state)
                            }
                        }
                        Rectangle {
                            visible: monitorRow.index < monitors.count - 1
                            anchors.bottom: parent.bottom
                            width: parent.width
                            height: 1
                            color: Theme.divider
                        }
                    }
                }
            }

            Item {
                implicitHeight: Theme.gap
            }
        }
    }
}
