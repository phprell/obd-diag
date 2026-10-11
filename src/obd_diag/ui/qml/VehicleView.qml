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
        title: pane.vm.busy ? qsTr("Lese Fahrzeug-Identifizierungsnummer …")
             : !pane.vm.hasResult ? qsTr("Noch nicht verbunden")
             : qsTr("Nicht verfügbar – Steuergerät hat nicht geantwortet")
        text: pane.vm.busy ? ""
            : !pane.vm.hasResult
              ? qsTr("Nach „Verbinden & Scannen“ stehen hier die FIN und was sich daraus ablesen lässt: Hersteller, Land, Modelljahr.")
              : qsTr("Das Fahrzeug hat keine FIN gemeldet (Mode 09). Viele Fahrzeuge vor etwa 2005 unterstützen das nicht; die FIN steht dann im Fahrzeugschein (Feld E).")
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
                    text: qsTr("Fahrzeug-Identifizierungsnummer (FIN)")
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
                        text: pane.info.valid ? qsTr("gültig") : qsTr("ungültig")
                        textColor: pane.info.valid ? Theme.okText : Theme.errorText
                        fill: pane.info.valid ? Theme.okBg : Theme.errorBg
                    }
                    Chip {
                        Layout.alignment: Qt.AlignVCenter
                        visible: pane.info.checksumOk === false
                        text: qsTr("Prüfziffer stimmt nicht")
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
                    text: qsTr("Hersteller und Land stammen aus den ersten drei Stellen (WMI), das Modelljahr aus Stelle 10. Der Code wiederholt sich alle 30 Jahre; außerhalb Nordamerikas ist er nicht eindeutig, weitere mögliche Jahre stehen mit „oder“ dabei.")
                    color: Theme.muted
                    font.pixelSize: 12
                    wrapMode: Text.Wrap
                }
            }

            Card {
                Layout.fillWidth: true

                SectionTitle {
                    text: qsTr("Online-Angaben (NHTSA vPIC)")
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
                          ? qsTr("Keine Online-Angaben zu dieser FIN (kein Netz, oder die Datenbank kennt das Fahrzeug nicht). Die Datenbank deckt vor allem Fahrzeuge für den US-Markt ab.")
                          : qsTr("Modell, Motor und weitere Angaben lassen sich bei der US-Behörde NHTSA nachschlagen. Dabei wird nur die FIN übertragen.")
                    color: Theme.muted
                    wrapMode: Text.Wrap
                }
                CheckBox {
                    objectName: "onlineVinCheck"
                    Layout.topMargin: 2
                    text: qsTr("FIN online nachschlagen (NHTSA)")
                    checked: pane.vm.onlineVinLookup
                    onToggled: pane.vm.onlineVinLookup = checked
                    ToolTip.visible: hovered
                    ToolTip.delay: 600
                    ToolTip.text: qsTr("Gilt ab dem nächsten Scan; die Antwort wird je FIN lokal gespeichert.")
                }
            }

            Item {
                implicitHeight: Theme.gap
            }
        }
    }
}
