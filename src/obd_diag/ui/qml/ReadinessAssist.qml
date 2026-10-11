import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Readiness-Assistent: offene Tests mit Hinweis, welche Fahrt sie abschließt; auf Knopfdruck
// zusätzlich der Stand dieser Fahrt (PID 41).
Card {
    id: assist

    required property var vm
    readonly property var info: vm.readinessAssist

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.pad
        SectionTitle {
            Layout.fillWidth: true
            text: qsTr("Readiness-Assistent")
            topPadding: 0
        }
        Button {
            objectName: "readinessCheckButton"
            text: qsTr("Stand dieser Fahrt lesen")
            enabled: assist.vm.canCheckReadiness
            onClicked: assist.vm.checkReadiness()
        }
    }

    Label {
        objectName: "readinessAssistIntro"
        Layout.fillWidth: true
        text: assist.info.checked
              ? (assist.info.checkedText ?? "")
              : qsTr("Welche Fahrt die offenen Tests abschließt. „Stand dieser Fahrt lesen“ fragt zusätzlich ab, was seit dem Einschalten der Zündung gelaufen ist (PID 41); am besten am Ende einer Fahrt bei laufendem Motor.")
        color: Theme.muted
        wrapMode: Text.Wrap
    }

    Label {
        objectName: "readinessAssistDone"
        Layout.fillWidth: true
        visible: assist.info.available === true && assist.info.allComplete === true
        text: qsTr("Alle unterstützten Tests sind abgeschlossen, eine besondere Fahrt ist nicht nötig.")
        color: Theme.okText
        wrapMode: Text.Wrap
    }

    Label {
        objectName: "readinessAssistMissing"
        Layout.fillWidth: true
        visible: assist.info.available !== true
        text: qsTr("Das Steuergerät hat den Readiness-Status (PID 01) diesmal nicht gemeldet.")
        color: Theme.warnText
        wrapMode: Text.Wrap
    }

    Repeater {
        objectName: "readinessAdvice"
        model: assist.info.items ?? []
        delegate: ColumnLayout {
            id: item
            required property var modelData
            Layout.fillWidth: true
            spacing: 4

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.pad
                Label {
                    Layout.fillWidth: true
                    text: item.modelData.name
                    font.bold: true
                    wrapMode: Text.Wrap
                }
                Chip {
                    visible: item.modelData.cycleState !== ""
                    text: qsTr("Diese Fahrt: %1").arg(item.modelData.cycleLabel)
                    textColor: item.modelData.cycleState === "complete" ? Theme.okText
                             : item.modelData.cycleState === "incomplete" ? Theme.warnText
                             : Theme.muted
                    fill: item.modelData.cycleState === "complete" ? Theme.okBg
                        : item.modelData.cycleState === "incomplete" ? Theme.warnBg
                        : Theme.background
                }
            }
            Label {
                Layout.fillWidth: true
                text: item.modelData.hint
                wrapMode: Text.Wrap
            }
            Label {
                Layout.fillWidth: true
                visible: item.modelData.cycleText !== ""
                text: item.modelData.cycleText
                color: Theme.muted
                wrapMode: Text.Wrap
            }
        }
    }

    Repeater {
        model: assist.info.notes ?? []
        delegate: Label {
            required property string modelData
            Layout.fillWidth: true
            text: modelData
            color: Theme.muted
            font.pixelSize: 12
            wrapMode: Text.Wrap
        }
    }
}
