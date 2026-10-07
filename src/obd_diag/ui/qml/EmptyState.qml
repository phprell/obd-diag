import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Leerer Zustand einer Ansicht: fette Zeile, darunter eine Erklärung.
ColumnLayout {
    id: empty

    property string title: ""
    property string text: ""
    property color titleColor: palette.text

    spacing: Theme.gap

    Label {
        Layout.fillWidth: true
        horizontalAlignment: Text.AlignHCenter
        text: empty.title
        font.pixelSize: 16
        font.bold: true
        color: empty.titleColor
        wrapMode: Text.Wrap
    }
    Label {
        Layout.fillWidth: true
        horizontalAlignment: Text.AlignHCenter
        visible: text !== ""
        text: empty.text
        color: Theme.muted
        wrapMode: Text.Wrap
    }
}
