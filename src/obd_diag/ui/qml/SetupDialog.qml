import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Einrichtung prüfen (Optionen-Menü, beim ersten Start von selbst): Adapter,
// Zugriffsrechte, Baudrate. „Übernehmen“ trägt Port und Baudrate in die Kopfzeile ein.
Dialog {
    id: dialog

    required property var vm
    signal apply(string port, int baud)

    title: qsTr("Einrichtung prüfen")
    modal: true
    anchors.centerIn: Overlay.overlay
    width: Math.min(640, parent ? parent.width - 48 : 640)
    height: Math.min(implicitHeight, parent ? parent.height - 48 : implicitHeight)
    padding: 20
    topPadding: 12
    bottomPadding: 4
    closePolicy: Popup.CloseOnEscape

    onClosed: vm.markSeen()

    function statusColor(status) {
        if (status === "ok")
            return Theme.okDot
        if (status === "warning")
            return Theme.warnDot
        if (status === "error")
            return Theme.errorDot
        return Theme.accent
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
            objectName: "setupSummary"
            Layout.fillWidth: true
            text: dialog.vm.summary !== "" ? dialog.vm.summary
                                           : qsTr("Prüft, ob der Adapter gefunden wird, geöffnet werden darf und mit welcher Baudrate er antwortet. Gesendet werden nur Adapter-Befehle, nichts ans Fahrzeug.")
            wrapMode: Text.Wrap
            font.bold: dialog.vm.done
        }

        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.preferredHeight: Math.min(itemList.implicitHeight, 360)
            contentWidth: availableWidth
            visible: dialog.vm.items.length > 0

            ColumnLayout {
                id: itemList
                width: parent.width
                spacing: 10

                Repeater {
                    objectName: "setupItems"
                    model: dialog.vm.items
                    delegate: RowLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        spacing: 10

                        Rectangle {
                            Layout.alignment: Qt.AlignTop
                            Layout.topMargin: 5
                            width: 10
                            height: 10
                            radius: 5
                            color: dialog.statusColor(modelData.status)
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            Label {
                                Layout.fillWidth: true
                                text: modelData.title
                                wrapMode: Text.Wrap
                            }
                            Label {
                                Layout.fillWidth: true
                                text: modelData.detail
                                visible: text !== ""
                                color: Theme.muted
                                wrapMode: Text.Wrap
                            }
                            // Befehl zum Kopieren (markierbar)
                            TextEdit {
                                Layout.fillWidth: true
                                text: modelData.fix
                                visible: text !== ""
                                readOnly: true
                                selectByMouse: true
                                font.family: "monospace"
                                color: palette.text
                                wrapMode: Text.Wrap
                            }
                        }
                    }
                }
            }
        }
    }

    footer: DialogButtonBox {
        padding: 16
        topPadding: 8
        Button {
            objectName: "setupRun"
            text: dialog.vm.done ? qsTr("Erneut prüfen") : qsTr("Prüfen")
            enabled: !dialog.vm.running
            DialogButtonBox.buttonRole: DialogButtonBox.ActionRole
            onClicked: dialog.vm.start()
        }
        Button {
            objectName: "setupApply"
            text: qsTr("Übernehmen")
            visible: dialog.vm.port !== ""
            enabled: !dialog.vm.running
            DialogButtonBox.buttonRole: DialogButtonBox.ActionRole
            onClicked: {
                dialog.apply(dialog.vm.port, dialog.vm.baud)
                dialog.close()
            }
        }
        Button {
            text: qsTr("Schließen")
            DialogButtonBox.buttonRole: DialogButtonBox.RejectRole
        }
    }
}
