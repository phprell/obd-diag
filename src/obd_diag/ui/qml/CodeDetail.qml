import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Detailansicht des ausgewählten Codes: Beschreibung, Ursachen, Symptome, Kosten.
Rectangle {
    id: pane

    required property var vm
    readonly property var entry: vm.selected
    readonly property bool hasEntry: entry.code !== undefined
    readonly property bool hasInfo: hasEntry && entry.hasInfo

    color: Theme.background

    Label {
        anchors.centerIn: parent
        visible: !pane.hasEntry
        text: pane.vm.hasCodes ? "Code in der Liste auswählen" : ""
        color: Theme.muted
    }

    ScrollView {
        id: scroll
        anchors.fill: parent
        visible: pane.hasEntry
        contentWidth: availableWidth
        clip: true

        ColumnLayout {
            width: scroll.availableWidth
            spacing: 0

            // Karte mit dem eigentlichen Inhalt
            Rectangle {
                Layout.fillWidth: true
                Layout.margins: Theme.pad
                implicitHeight: content.implicitHeight + 2 * 20
                color: Theme.surface
                border.color: Theme.border
                radius: 6

                ColumnLayout {
                    id: content
                    anchors.fill: parent
                    anchors.margins: 20
                    spacing: 10

                    RowLayout {
                        spacing: 12
                        Label {
                            objectName: "detailCode"
                            text: pane.entry.code ?? ""
                            font.family: "monospace"
                            font.bold: true
                            font.pixelSize: 26
                        }
                        Chip {
                            Layout.alignment: Qt.AlignVCenter
                            text: pane.entry.kindLabel ?? ""
                            textColor: "white"
                            fill: Theme.kindColor(pane.entry.kind)
                        }
                        Item {
                            Layout.fillWidth: true
                        }
                    }

                    Label {
                        objectName: "detailTitle"
                        Layout.fillWidth: true
                        text: pane.entry.title ?? ""
                        font.pixelSize: 18
                        font.bold: pane.hasInfo
                        font.italic: !pane.hasInfo
                        color: pane.hasInfo ? palette.text : Theme.muted
                        wrapMode: Text.Wrap
                    }

                    Banner {
                        Layout.fillWidth: true
                        kind: "info"
                        text: !pane.hasInfo
                              ? "Für diesen Code gibt es keinen Eintrag im Offline-Katalog. "
                                + "Herstellerspezifische Codes (z. B. P1xxx, U3xxx) sind dort oft "
                                + "nicht enthalten; die Bedeutung steht in den Unterlagen des "
                                + "Herstellers."
                              : ""
                    }

                    Label {
                        Layout.fillWidth: true
                        visible: text !== ""
                        text: pane.entry.description ?? ""
                        wrapMode: Text.Wrap
                        lineHeight: 1.15
                    }

                    // Hinweise: Motorkontrollleuchte, Abgasrelevanz
                    Flow {
                        Layout.fillWidth: true
                        Layout.topMargin: 2
                        spacing: 6
                        visible: pane.entry.mil !== undefined && pane.entry.mil !== null
                                 || pane.entry.emissionsRelevant === true
                        Chip {
                            visible: pane.entry.mil === true
                            text: "Motorkontrollleuchte (MIL) an"
                            textColor: Theme.warnText
                            fill: Theme.warnBg
                        }
                        Chip {
                            visible: pane.entry.mil === false
                            text: "Keine Motorkontrollleuchte"
                        }
                        Chip {
                            visible: pane.entry.emissionsRelevant === true
                            text: "Abgasrelevant"
                            textColor: Theme.warnText
                            fill: Theme.warnBg
                        }
                    }

                    SectionTitle {
                        visible: causes.count > 0
                        text: "Mögliche Ursachen"
                        Layout.topMargin: 8
                    }
                    Repeater {
                        id: causes
                        model: pane.entry.causes ?? []
                        delegate: RowLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            spacing: 10
                            Chip {
                                Layout.preferredWidth: 72
                                Layout.alignment: Qt.AlignTop
                                text: modelData.likelihoodLabel
                                textColor: Theme.likelihoodColor(modelData.likelihood)
                                fill: Theme.likelihoodBg(modelData.likelihood)
                            }
                            Label {
                                Layout.fillWidth: true
                                text: modelData.label
                                wrapMode: Text.Wrap
                            }
                        }
                    }

                    SectionTitle {
                        visible: symptoms.count > 0
                        text: "Symptome"
                        Layout.topMargin: 8
                    }
                    Repeater {
                        id: symptoms
                        model: pane.entry.symptoms ?? []
                        delegate: Label {
                            required property string modelData
                            Layout.fillWidth: true
                            leftPadding: 4
                            text: "•  " + modelData
                            wrapMode: Text.Wrap
                        }
                    }

                    SectionTitle {
                        visible: repair.visible
                        text: "Reparatur"
                        Layout.topMargin: 8
                    }
                    GridLayout {
                        id: repair
                        visible: (pane.entry.costText ?? "") !== ""
                                 || (pane.entry.difficultyLabel ?? "") !== ""
                        columns: 2
                        columnSpacing: 16
                        rowSpacing: 4
                        Label {
                            visible: (pane.entry.costText ?? "") !== ""
                            text: "Kostenrahmen"
                            color: Theme.muted
                        }
                        Label {
                            objectName: "detailCost"
                            visible: (pane.entry.costText ?? "") !== ""
                            text: pane.entry.costText ?? ""
                            font.bold: true
                        }
                        Label {
                            visible: (pane.entry.difficultyLabel ?? "") !== ""
                            text: "Schwierigkeit"
                            color: Theme.muted
                        }
                        Label {
                            visible: (pane.entry.difficultyLabel ?? "") !== ""
                            text: pane.entry.difficultyLabel ?? ""
                        }
                    }
                    Label {
                        visible: repair.visible
                        Layout.fillWidth: true
                        text: "Richtwert für Teile und Arbeit, je nach Fahrzeug und Werkstatt."
                        color: Theme.muted
                        font.pixelSize: 12
                        wrapMode: Text.Wrap
                    }
                }
            }
        }
    }
}
