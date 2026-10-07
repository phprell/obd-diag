import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: window

    // DiagnosisViewModel, von Python als Kontext-Eigenschaft gesetzt (ui/window.py)
    readonly property var vm: diagnosis

    width: 1000
    height: 700
    minimumWidth: 760
    minimumHeight: 480
    visible: true
    title: "OBD-Diagnose"
    color: Theme.background

    header: ConnectionBar {
        vm: window.vm
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // Hinweise über der Liste
        ColumnLayout {
            id: banners
            Layout.fillHeight: false
            readonly property bool shown: errorBanner.visible || noticeBanner.visible
                                          || voltageBanner.visible || catalogBanner.visible
            Layout.fillWidth: true
            Layout.leftMargin: Theme.gap
            Layout.rightMargin: Theme.gap
            Layout.topMargin: shown ? Theme.gap : 0
            Layout.bottomMargin: shown ? Theme.gap : 0
            spacing: 6

            Banner {
                id: errorBanner
                objectName: "errorBanner"
                Layout.fillWidth: true
                kind: "error"
                text: window.vm.errorMessage
                closable: true
                onClosed: window.vm.dismissError()
            }
            Banner {
                id: noticeBanner
                objectName: "noticeBanner"
                Layout.fillWidth: true
                kind: "ok"
                text: window.vm.notice
                closable: true
                onClosed: window.vm.dismissNotice()
            }
            Banner {
                Layout.fillWidth: true
                id: voltageBanner
                kind: "warn"
                text: window.vm.lowVoltage ? window.vm.lowVoltageWarning : ""
            }
            Banner {
                id: catalogBanner
                objectName: "catalogBanner"
                Layout.fillWidth: true
                kind: "info"
                text: window.vm.catalogMissing
                      ? "Der Fehlercode-Katalog fehlt, Codes erscheinen ohne Beschreibung. "
                        + "Erzeugen mit: uv run python tools/build_dtc_db.py"
                      : ""
            }
        }

        SplitView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            orientation: Qt.Horizontal

            handle: Rectangle {
                implicitWidth: 1
                color: Theme.border
            }

            CodeList {
                vm: window.vm
                SplitView.preferredWidth: 360
                SplitView.minimumWidth: 260
            }
            CodeDetail {
                vm: window.vm
                SplitView.fillWidth: true
                SplitView.minimumWidth: 360
            }
        }
    }

    footer: ToolBar {
        topPadding: 7
        bottomPadding: 6
        leftPadding: Theme.pad
        rightPadding: Theme.pad
        background: Rectangle {
            color: Theme.surface
            Rectangle {
                width: parent.width
                height: 1
                color: Theme.border
            }
        }

        RowLayout {
            width: parent.width
            spacing: Theme.pad

            Label {
                objectName: "statusLine"
                Layout.fillWidth: true
                elide: Text.ElideRight
                textFormat: Text.StyledText
                color: Theme.muted
                text: {
                    if (window.vm.busy)
                        return window.vm.busyText
                    if (!window.vm.hasResult)
                        return "Nicht verbunden"
                    const voltage = window.vm.lowVoltage
                        ? "<font color=\"" + Theme.errorText + "\"><b>" + window.vm.voltageText
                          + " (niedrig)</b></font>"
                        : window.vm.voltageText
                    return "Adapter: " + window.vm.adapter
                         + "  ·  Protokoll: " + window.vm.protocol
                         + "  ·  Bordspannung: " + voltage
                }
            }
            Label {
                visible: window.vm.hasResult && !window.vm.busy
                text: window.vm.codeCount === 1 ? "1 Code" : window.vm.codeCount + " Codes"
                color: Theme.muted
            }
            Button {
                objectName: "clearButton"
                text: "Fehlercodes löschen …"
                enabled: window.vm.canClear
                onClicked: clearDialog.open()
            }
        }
    }

    ClearDialog {
        id: clearDialog
        objectName: "clearDialog"
        vm: window.vm
    }

    Dialog {
        id: refusedDialog
        objectName: "refusedDialog"
        property alias message: refusedLabel.text
        title: "Löschen nicht möglich"
        modal: true
        anchors.centerIn: Overlay.overlay
        width: Math.min(480, window.width - 48)
        standardButtons: Dialog.Ok
        padding: 20

        Label {
            id: refusedLabel
            width: parent.width
            wrapMode: Text.Wrap
        }
    }

    Connections {
        target: window.vm
        function onClearRefused(message) {
            refusedDialog.message = message
            refusedDialog.open()
        }
    }
}
