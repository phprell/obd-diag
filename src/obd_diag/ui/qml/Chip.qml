import QtQuick
import QtQuick.Controls

// Kleines Etikett, z. B. für die Art des Codes oder die Wahrscheinlichkeit einer Ursache.
Rectangle {
    id: chip

    property string text: ""
    property color textColor: Theme.muted
    property color fill: Theme.background
    property bool mono: false

    implicitWidth: label.implicitWidth + 16
    implicitHeight: label.implicitHeight + 6
    radius: height / 2
    color: fill
    border.color: Theme.chipBorder(fill)

    Label {
        id: label
        anchors.centerIn: parent
        text: chip.text
        color: chip.textColor
        font.pixelSize: 12
        font.bold: true
        font.family: chip.mono ? "monospace" : Qt.application.font.family
    }
}
