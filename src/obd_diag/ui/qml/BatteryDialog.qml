import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Batterie- und Anlasstest (Optionen → Batterie- und Anlasstest …): Ruhespannung, Einbruch
// beim Anlassen, Ladespannung. Den Motor startet der Nutzer, wenn der Dialog dazu auffordert.
Dialog {
    id: dialog

    required property var vm
    property string port: ""
    property int baud: 38400

    title: qsTr("Batterie- und Anlasstest")
    modal: true
    anchors.centerIn: Overlay.overlay
    width: Math.min(600, parent ? parent.width - 48 : 600)
    height: Math.min(implicitHeight, parent ? parent.height - 48 : implicitHeight)
    padding: 20
    topPadding: 12
    bottomPadding: 4
    closePolicy: Popup.CloseOnEscape

    // Schließen bricht einen laufenden Test ab
    onClosed: vm.cancel()

    function ratingColor(rating) {
        if (rating === "good")
            return Theme.okDot
        if (rating === "weak")
            return Theme.warnDot
        if (rating === "bad")
            return Theme.errorDot
        return Theme.muted
    }

    header: Label {
        text: dialog.title
        font.pixelSize: 17
        font.bold: true
        padding: 20
        bottomPadding: 0
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 12

        Label {
            objectName: "batteryInstructions"
            Layout.fillWidth: true
            visible: !dialog.vm.running && !dialog.vm.done
            text: dialog.vm.instructions
            wrapMode: Text.Wrap
        }

        // Laufender Test: Phase groß, darunter Spannung und Restzeit
        ColumnLayout {
            Layout.fillWidth: true
            visible: dialog.vm.running
            spacing: 6

            Label {
                objectName: "batteryPhase"
                Layout.fillWidth: true
                text: dialog.vm.phaseText !== "" ? dialog.vm.phaseText : qsTr("Verbinde …")
                font.pixelSize: dialog.vm.phase === "start" ? 22 : 17
                font.bold: true
                color: dialog.vm.phase === "start" ? Theme.accent : palette.text
                wrapMode: Text.Wrap
            }
            RowLayout {
                spacing: Theme.pad
                Label {
                    objectName: "batteryVoltage"
                    text: dialog.vm.voltageText
                    visible: text !== ""
                    font.pixelSize: 28
                    font.bold: true
                }
                Label {
                    text: dialog.vm.remainingText
                    visible: text !== ""
                    color: Theme.muted
                }
            }
        }

        Label {
            objectName: "batteryError"
            Layout.fillWidth: true
            visible: text !== ""
            text: dialog.vm.error
            color: Theme.errorDot
            wrapMode: Text.Wrap
        }

        // Ergebnis
        ColumnLayout {
            Layout.fillWidth: true
            visible: dialog.vm.done && !dialog.vm.running
            spacing: 10

            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                Rectangle {
                    width: 12
                    height: 12
                    radius: 6
                    color: dialog.ratingColor(dialog.vm.overallRating)
                }
                Label {
                    objectName: "batteryOverall"
                    Layout.fillWidth: true
                    text: dialog.vm.overallText
                    font.bold: true
                    font.pixelSize: 15
                    wrapMode: Text.Wrap
                }
            }

            GridLayout {
                objectName: "batteryRows"
                Layout.fillWidth: true
                columns: 3
                columnSpacing: 10
                rowSpacing: 6

                Repeater {
                    model: dialog.vm.rows
                    delegate: Rectangle {
                        required property var modelData
                        required property int index
                        Layout.row: index
                        Layout.column: 0
                        Layout.alignment: Qt.AlignTop
                        Layout.topMargin: 4
                        width: 10
                        height: 10
                        radius: 5
                        color: dialog.ratingColor(modelData.rating)
                    }
                }
                Repeater {
                    model: dialog.vm.rows
                    delegate: Label {
                        required property var modelData
                        required property int index
                        Layout.row: index
                        Layout.column: 1
                        Layout.alignment: Qt.AlignTop
                        text: modelData.label
                        color: Theme.muted
                    }
                }
                Repeater {
                    model: dialog.vm.rows
                    delegate: Label {
                        required property var modelData
                        required property int index
                        Layout.row: index
                        Layout.column: 2
                        Layout.fillWidth: true
                        text: modelData.text
                        wrapMode: Text.Wrap
                    }
                }
            }

            Repeater {
                model: dialog.vm.notes
                delegate: Label {
                    required property var modelData
                    Layout.fillWidth: true
                    text: modelData
                    color: Theme.muted
                    wrapMode: Text.Wrap
                }
            }
        }
    }

    footer: DialogButtonBox {
        padding: 16
        topPadding: 8
        Button {
            objectName: "batteryStart"
            text: dialog.vm.done ? qsTr("Erneut testen") : qsTr("Test starten")
            visible: !dialog.vm.running
            enabled: dialog.vm.canStart
            DialogButtonBox.buttonRole: DialogButtonBox.ActionRole
            onClicked: dialog.vm.start(dialog.port, dialog.baud)
        }
        Button {
            objectName: "batteryCancel"
            text: qsTr("Abbrechen")
            visible: dialog.vm.running
            DialogButtonBox.buttonRole: DialogButtonBox.ActionRole
            onClicked: dialog.vm.cancel()
        }
        Button {
            text: qsTr("Schließen")
            DialogButtonBox.buttonRole: DialogButtonBox.RejectRole
        }
    }
}
