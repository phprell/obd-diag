import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Kopfzeile: Port und Baudrate wählen, verbinden und scannen.
ToolBar {
    id: bar

    required property var vm
    readonly property alias portText: portBox.editText

    // Fusion setzt die Abstände einzeln (SafeArea), daher alle vier angeben.
    topPadding: Theme.gap
    bottomPadding: Theme.gap + 1
    leftPadding: Theme.pad
    rightPadding: Theme.pad

    background: Rectangle {
        color: Theme.surface
        Rectangle {
            anchors.bottom: parent.bottom
            width: parent.width
            height: 1
            color: Theme.border
        }
    }

    RowLayout {
        width: parent.width
        spacing: Theme.gap

        Label {
            text: "Port"
        }
        ComboBox {
            id: portBox
            objectName: "portBox"
            Layout.preferredWidth: 300
            editable: true
            enabled: !bar.vm.busy
            model: bar.vm.ports
            textRole: "device"
            Accessible.name: "Serieller Port"
            ToolTip.visible: hovered && !popup.visible
            ToolTip.delay: 600
            ToolTip.text: "Adapter wählen oder Pfad eingeben, z. B. /dev/ttyUSB0 oder /dev/pts/5"

            delegate: ItemDelegate {
                required property var modelData
                required property int index
                width: ListView.view.width
                highlighted: portBox.highlightedIndex === index
                contentItem: Column {
                    Label {
                        text: modelData.device
                        font.family: "monospace"
                    }
                    Label {
                        text: modelData.description
                        color: Theme.muted
                        font.pixelSize: 12
                        visible: text !== ""
                    }
                }
            }

            function resetText() {
                if (count === 0)
                    editText = bar.vm.defaultPort
            }
            Component.onCompleted: resetText()
            onCountChanged: resetText()
            onAccepted: bar.scan()
        }
        Button {
            text: "Suchen"
            enabled: !bar.vm.busy
            ToolTip.visible: hovered
            ToolTip.delay: 600
            ToolTip.text: "Angeschlossene Adapter neu suchen"
            onClicked: bar.vm.refreshPorts()
        }

        Item {
            Layout.preferredWidth: Theme.gap
        }

        Label {
            text: "Baud"
        }
        ComboBox {
            id: baudBox
            objectName: "baudBox"
            Layout.preferredWidth: 110
            enabled: !bar.vm.busy
            model: [38400, 9600, 115200, 230400, 500000]
            Accessible.name: "Baudrate"
        }

        Item {
            Layout.preferredWidth: Theme.gap
        }

        Button {
            objectName: "scanButton"
            text: bar.vm.hasResult ? "Erneut scannen" : "Verbinden && Scannen"
            enabled: !bar.vm.busy
            palette.button: Qt.darker(Theme.accent, 1.35)
            palette.buttonText: "white"
            font.bold: true
            opacity: enabled ? 1 : 0.5
            onClicked: bar.scan()
        }
        BusyIndicator {
            // Platz immer freihalten, damit die Leiste beim Start nicht springt
            objectName: "busyIndicator"
            Layout.preferredWidth: 24
            Layout.preferredHeight: 24
            running: bar.vm.busy
        }

        Item {
            Layout.fillWidth: true
        }
    }

    function scan() {
        vm.connectAndScan(portBox.editText, baudBox.currentValue)
    }
}
