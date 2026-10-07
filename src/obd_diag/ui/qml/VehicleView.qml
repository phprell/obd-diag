import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Fahrzeug: FIN (Mode 09, PID 02), daraus Hersteller, Land, Modelljahr; optional NHTSA vPIC.
Rectangle {
    id: pane

    required property var vm
    readonly property var info: vm.vehicle
    readonly property bool available: info.available === true

    color: Theme.background

    EmptyState {
        objectName: "vehicleEmpty"
        anchors.centerIn: parent
        width: Math.min(parent.width - 2 * Theme.pad, 520)
        visible: !pane.available
        title: pane.vm.busy ? "Lese Fahrzeug-Identifizierungsnummer …"
             : !pane.vm.hasResult ? "Noch nicht verbunden"
             : "Nicht verfügbar – Steuergerät hat nicht geantwortet"
        text: pane.vm.busy ? ""
            : !pane.vm.hasResult
              ? "Nach „Verbinden & Scannen“ stehen hier die FIN und was sich daraus ablesen "
                + "lässt: Hersteller, Land, Modelljahr."
              : "Das Fahrzeug hat keine FIN gemeldet (Mode 09). Viele Fahrzeuge vor etwa "
                + "2005 unterstützen das nicht; die FIN steht dann im Fahrzeugschein (Feld E)."
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

            Card {
                Layout.fillWidth: true

                SectionTitle {
                    text: "Fahrzeug-Identifizierungsnummer (FIN)"
                    topPadding: 0
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 12
                    Label {
                        objectName: "vehicleVin"
                        text: pane.info.vin ?? ""
                        font.family: "monospace"
                        font.bold: true
                        font.pixelSize: 24
                        font.letterSpacing: 1
                    }
                    Chip {
                        Layout.alignment: Qt.AlignVCenter
                        text: pane.info.valid ? "gültig" : "ungültig"
                        textColor: pane.info.valid ? Theme.okText : Theme.errorText
                        fill: pane.info.valid ? Theme.okBg : Theme.errorBg
                    }
                    Chip {
                        Layout.alignment: Qt.AlignVCenter
                        visible: pane.info.checksumOk === false
                        text: "Prüfziffer stimmt nicht"
                        textColor: Theme.warnText
                        fill: Theme.warnBg
                    }
                    Item {
                        Layout.fillWidth: true
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.topMargin: 4
                    spacing: 0
                    Repeater {
                        id: facts
                        objectName: "vehicleFacts"
                        model: pane.info.facts ?? []
                        delegate: FactRow {
                            required property var modelData
                            required property int index
                            label: modelData.label
                            value: modelData.value
                            last: index === facts.count - 1
                        }
                    }
                }
                Label {
                    Layout.fillWidth: true
                    text: "Hersteller und Land stammen aus den ersten drei Stellen (WMI), das "
                          + "Modelljahr aus Stelle 10; außerhalb Nordamerikas ist es nicht eindeutig."
                    color: Theme.muted
                    font.pixelSize: 12
                    wrapMode: Text.Wrap
                }
            }

            Card {
                Layout.fillWidth: true

                SectionTitle {
                    text: "Online-Angaben (NHTSA vPIC)"
                    topPadding: 0
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 0
                    visible: online.count > 0
                    Repeater {
                        id: online
                        objectName: "vehicleOnline"
                        model: pane.info.online ?? []
                        delegate: FactRow {
                            required property var modelData
                            required property int index
                            label: modelData.label
                            value: modelData.value
                            last: index === online.count - 1
                        }
                    }
                }
                Label {
                    Layout.fillWidth: true
                    visible: online.count === 0
                    text: pane.vm.onlineVinLookup
                          ? "Keine Online-Angaben zu dieser FIN (kein Netz, oder die Datenbank "
                            + "kennt das Fahrzeug nicht). Die Datenbank deckt vor allem Fahrzeuge "
                            + "für den US-Markt ab."
                          : "Modell, Motor und weitere Angaben lassen sich bei der US-Behörde NHTSA "
                            + "nachschlagen. Dabei wird nur die FIN übertragen."
                    color: Theme.muted
                    wrapMode: Text.Wrap
                }
                CheckBox {
                    objectName: "onlineVinCheck"
                    Layout.topMargin: 2
                    text: "FIN online nachschlagen (NHTSA)"
                    checked: pane.vm.onlineVinLookup
                    onToggled: pane.vm.onlineVinLookup = checked
                    ToolTip.visible: hovered
                    ToolTip.delay: 600
                    ToolTip.text: "Gilt ab dem nächsten Scan; die Antwort wird je FIN lokal gespeichert."
                }
            }

            Item {
                implicitHeight: Theme.gap
            }
        }
    }
}
