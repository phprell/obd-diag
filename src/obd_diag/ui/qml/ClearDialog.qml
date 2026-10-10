import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Bestätigung vor dem Löschen (Mode 04): listet die Codes und die Regeln auf.
Dialog {
    id: dialog

    required property var vm

    title: qsTr("Fehlercodes löschen?")
    modal: true
    anchors.centerIn: Overlay.overlay
    width: Math.min(560, parent ? parent.width - 48 : 560)
    padding: 20
    topPadding: 12
    bottomPadding: 4
    closePolicy: Popup.CloseOnEscape

    onAboutToShow: confirm.checked = false

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
            Layout.fillWidth: true
            text: dialog.vm.uniqueCodes.length === 1
                  ? qsTr("Dieser Code wird im Steuergerät gelöscht:")
                  : qsTr("Diese %1 Codes werden im Steuergerät gelöscht:").arg(dialog.vm.uniqueCodes.length)
            wrapMode: Text.Wrap
        }
        Flow {
            Layout.fillWidth: true
            spacing: 6
            Repeater {
                model: dialog.vm.uniqueCodes
                delegate: Chip {
                    required property string modelData
                    text: modelData
                    mono: true
                    textColor: palette.text
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.topMargin: 4
            implicitHeight: rules.implicitHeight + 24
            color: Theme.warnBg
            border.color: Theme.warnBorder
            radius: 4

            ColumnLayout {
                id: rules
                anchors.fill: parent
                anchors.margins: 12
                spacing: 6

                Repeater {
                    model: [
                        qsTr("Zündung an, Motor aus, Bordspannung mindestens 11,8 V. Sonst wird nicht gelöscht."),
                        qsTr("Codes und Freeze Frame werden vorher gesichert."),
                        qsTr("Die Bereitschaftstests (Readiness) werden zurückgesetzt: Die Abgasuntersuchung ist erst nach einigen Fahrzyklen wieder möglich."),
                        dialog.vm.permanentCount > 0
                            ? qsTr("Permanente Codes löscht das Steuergerät erst selbst, wenn der Fehler behoben ist (hier: %1).").arg(dialog.vm.permanentCount)
                            : qsTr("Permanente Codes löscht das Steuergerät erst selbst, wenn der Fehler behoben ist."),
                        qsTr("Löschen behebt keinen Defekt. Kehrt ein Code zurück, besteht die Ursache weiter.")
                    ]
                    delegate: Label {
                        required property string modelData
                        Layout.fillWidth: true
                        text: "•  " + modelData
                        color: Theme.warnText
                        wrapMode: Text.Wrap
                    }
                }
            }
        }

        CheckBox {
            id: confirm
            objectName: "clearConfirm"
            Layout.fillWidth: true
            text: qsTr("Zündung ist an, der Motor ist aus. Ich möchte die Codes löschen.")
        }
    }

    footer: DialogButtonBox {
        padding: 16
        topPadding: 8
        Button {
            text: qsTr("Abbrechen")
            DialogButtonBox.buttonRole: DialogButtonBox.RejectRole
        }
        Button {
            objectName: "clearAccept"
            text: qsTr("Fehlercodes löschen")
            enabled: confirm.checked
            DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole
        }
    }

    onAccepted: vm.clearCodes()
}
