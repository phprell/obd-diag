import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Kopfzeile: Port und Baudrate wählen, verbinden und scannen.
ToolBar {
    id: bar

    required property var vm
    readonly property alias portText: portBox.editText
    readonly property int baud: baudBox.currentValue ?? 38400
    // Live-Daten belegen den Adapter: dann nichts umstellen und nicht scannen
    readonly property bool locked: vm.busy || vm.blocked

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
            text: qsTr("Port")
        }
        ComboBox {
            id: portBox
            objectName: "portBox"
            Layout.preferredWidth: 300
            editable: true
            enabled: !bar.locked
            model: bar.vm.ports
            textRole: "device"
            Accessible.name: qsTr("Serieller Port")
            ToolTip.visible: hovered && !popup.visible
            ToolTip.delay: 600
            ToolTip.text: qsTr("Adapter wählen oder Pfad eingeben, z. B. /dev/ttyUSB0 oder /dev/pts/5")

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
            text: qsTr("Suchen")
            enabled: !bar.locked
            ToolTip.visible: hovered
            ToolTip.delay: 600
            ToolTip.text: qsTr("Angeschlossene Adapter neu suchen")
            onClicked: bar.vm.refreshPorts()
        }

        Item {
            Layout.preferredWidth: Theme.gap
        }

        Label {
            text: qsTr("Baud")
        }
        ComboBox {
            id: baudBox
            objectName: "baudBox"
            Layout.preferredWidth: 110
            enabled: !bar.locked
            model: [38400, 9600, 115200, 230400, 500000]
            Accessible.name: qsTr("Baudrate")
        }

        Item {
            Layout.preferredWidth: Theme.gap
        }

        Button {
            objectName: "scanButton"
            text: bar.vm.hasResult && !bar.vm.viewOnly ? qsTr("Erneut scannen") : qsTr("Verbinden && Scannen")
            enabled: !bar.locked
            palette.button: Theme.accentButton
            palette.buttonText: "white"
            font.bold: true
            opacity: enabled ? 1 : 0.5
            onClicked: bar.scan()
        }
        Item {
            // Platz immer freihalten, damit die Leiste beim Start nicht springt
            Layout.preferredWidth: 24
            Layout.preferredHeight: 24
            BusyIndicator {
                // visible statt nur running: der Stil blendet sonst langsam aus und
                // bleibt nach kurzen Jobs als blasser Kringel stehen.
                objectName: "busyIndicator"
                anchors.fill: parent
                running: bar.vm.busy
                visible: bar.vm.busy
            }
        }

        Item {
            Layout.fillWidth: true
        }
    }

    function scan() {
        if (locked)
            return
        vm.connectAndScan(portBox.editText, baudBox.currentValue)
    }
}
