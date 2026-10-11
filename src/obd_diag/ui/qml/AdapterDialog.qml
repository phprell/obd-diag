import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Ergebnis des Adapter-Checks (Optionen → Adapter prüfen …): Version, Urteil, Spannung,
// UDS-Eignung und die einzelnen geprüften Befehle.
Dialog {
    id: dialog

    required property var vm
    readonly property var check: vm.adapterCheck

    title: qsTr("Adapter-Check")
    modal: true
    anchors.centerIn: Overlay.overlay
    width: Math.min(640, parent ? parent.width - 48 : 640)
    height: Math.min(implicitHeight, parent ? parent.height - 48 : implicitHeight)
    padding: 20
    topPadding: 12
    standardButtons: Dialog.Close

    header: Label {
        text: dialog.title
        font.pixelSize: 17
        font.bold: true
        padding: 20
        bottomPadding: 0
    }

    ScrollView {
        anchors.fill: parent
        contentWidth: availableWidth
        clip: true

        ColumnLayout {
            width: parent.width
            spacing: 12

            GridLayout {
                objectName: "adapterRows"
                Layout.fillWidth: true
                columns: 2
                columnSpacing: Theme.pad
                rowSpacing: 6

                Repeater {
                    model: dialog.check.rows ?? []
                    delegate: Label {
                        required property var modelData
                        required property int index
                        Layout.row: index
                        Layout.column: 0
                        Layout.alignment: Qt.AlignTop
                        text: modelData.label
                        color: Theme.muted
                    }
                }
                Repeater {
                    model: dialog.check.rows ?? []
                    delegate: Label {
                        required property var modelData
                        required property int index
                        Layout.row: index
                        Layout.column: 1
                        Layout.fillWidth: true
                        text: modelData.value
                        font.bold: index === 3 || index === 6  // Urteil und UDS
                        wrapMode: Text.Wrap
                    }
                }
            }

            SectionTitle {
                text: qsTr("Geprüfte Befehle")
            }
            GridLayout {
                objectName: "adapterCommands"
                Layout.fillWidth: true
                columns: 2
                columnSpacing: Theme.pad
                rowSpacing: 4

                Repeater {
                    model: dialog.check.commands ?? []
                    delegate: Label {
                        required property var modelData
                        required property int index
                        Layout.row: index
                        Layout.column: 0
                        text: modelData.command
                        font.family: "monospace"
                    }
                }
                Repeater {
                    model: dialog.check.commands ?? []
                    delegate: Label {
                        required property var modelData
                        required property int index
                        Layout.row: index
                        Layout.column: 1
                        Layout.fillWidth: true
                        text: modelData.text
                        color: modelData.supported ? palette.text : Theme.warnText
                        wrapMode: Text.Wrap
                    }
                }
            }

            Label {
                Layout.fillWidth: true
                text: dialog.check.note ?? ""
                color: Theme.muted
                wrapMode: Text.Wrap
            }
        }
    }
}
