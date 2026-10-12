import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Testergebnisse (Mode 06): je Steuergerät Monitor, Test, Wert, Grenzen und Urteil.
Rectangle {
    id: pane

    required property var vm
    readonly property var info: vm.testResults
    readonly property bool available: info.available === true

    color: Theme.background

    EmptyState {
        objectName: "testsEmpty"
        anchors.centerIn: parent
        width: Math.min(parent.width - 2 * Theme.pad, 520)
        visible: !pane.available
        title: pane.vm.busy ? qsTr("Lese Testergebnisse …")
             : !pane.vm.hasResult ? qsTr("Noch nicht verbunden")
             : qsTr("Nicht verfügbar – Steuergerät hat nicht geantwortet")
        text: pane.vm.busy ? ""
            : !pane.vm.hasResult
              ? qsTr("Nach „Verbinden & Scannen“ stehen hier die Messwerte der Eigendiagnosen (Mode 06) mit ihren Grenzwerten.")
              : qsTr("Das Fahrzeug hat keine Testergebnisse (Mode 06) gemeldet. obd-diag liest sie nur bei CAN-Fahrzeugen, etwa ab 2008.")
    }

    ScrollView {
        id: scroll
        anchors.fill: parent
        visible: pane.available
        contentWidth: availableWidth
        clip: true

        ColumnLayout {
            x: Math.max(Theme.pad, (scroll.availableWidth - width) / 2)
            width: Math.min(scroll.availableWidth - 2 * Theme.pad, 980)
            spacing: Theme.pad

            Item {
                implicitHeight: 1
            }

            Card {
                Layout.fillWidth: true
                Label {
                    objectName: "testsSummary"
                    Layout.fillWidth: true
                    text: pane.info.summary ?? ""
                    font.pixelSize: 18
                    font.bold: true
                    wrapMode: Text.Wrap
                    color: (pane.info.failedCount ?? 0) > 0 ? Theme.errorText : palette.text
                }
                Label {
                    Layout.fillWidth: true
                    text: qsTr("Jeder Test meldet einen Messwert und die Grenzen, innerhalb derer das Steuergerät ihn als in Ordnung bewertet. „Kein Ergebnis“ heißt meist: Der Test ist seit dem letzten Löschen noch nicht gelaufen. Tests ab Nummer 80 legt der Hersteller fest, ihre Bedeutung ist nicht genormt.")
                    color: Theme.muted
                    font.pixelSize: 12
                    wrapMode: Text.Wrap
                }
            }

            Repeater {
                id: groups
                objectName: "testGroups"
                model: pane.info.groups ?? []
                delegate: Card {
                    id: group
                    required property var modelData
                    Layout.fillWidth: true

                    SectionTitle {
                        text: group.modelData.title
                        topPadding: 0
                    }
                    Repeater {
                        model: group.modelData.rows
                        delegate: RowLayout {
                            id: row
                            required property var modelData
                            Layout.fillWidth: true
                            spacing: Theme.pad

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 1
                                Label {
                                    Layout.fillWidth: true
                                    text: row.modelData.monitor
                                    font.bold: true
                                    elide: Text.ElideRight
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: row.modelData.test
                                    color: Theme.muted
                                    font.pixelSize: 12
                                    elide: Text.ElideRight
                                }
                            }
                            Label {
                                Layout.preferredWidth: 220
                                horizontalAlignment: Text.AlignRight
                                visible: row.modelData.value !== ""
                                text: row.modelData.value + "\n" + row.modelData.limits
                            }
                            Chip {
                                Layout.alignment: Qt.AlignVCenter
                                text: row.modelData.statusLabel
                                textColor: row.modelData.status === "passed" ? Theme.okText
                                         : row.modelData.status === "failed" ? Theme.errorText
                                         : Theme.muted
                                fill: row.modelData.status === "passed" ? Theme.okBg
                                    : row.modelData.status === "failed" ? Theme.errorBg
                                    : Theme.background
                            }
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
