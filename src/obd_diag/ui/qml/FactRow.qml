import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Zeile einer zweispaltigen Tabelle: Bezeichnung links, Wert rechts, Trennlinie darunter.
Item {
    id: fact

    property string label: ""
    property string value: ""
    property bool mono: false
    property bool last: false

    Layout.fillWidth: true
    implicitHeight: row.implicitHeight + 18

    RowLayout {
        id: row
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        spacing: Theme.pad

        Label {
            Layout.fillWidth: true
            text: fact.label
            color: Theme.muted
            elide: Text.ElideRight
        }
        Label {
            text: fact.value
            font.bold: true
            font.family: fact.mono ? "monospace" : Qt.application.font.family
            horizontalAlignment: Text.AlignRight
        }
    }
    Rectangle {
        visible: !fact.last
        anchors.bottom: parent.bottom
        width: parent.width
        height: 1
        color: Theme.divider
    }
}
