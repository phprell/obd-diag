import QtQuick
import QtQuick.Layouts

// Weiße Karte mit Rahmen, wie in der Detailansicht der Fehlercodes.
Rectangle {
    id: card

    default property alias content: column.data
    property int padding: 20
    property alias spacing: column.spacing

    implicitHeight: column.implicitHeight + 2 * padding
    color: Theme.surface
    border.color: Theme.border
    radius: 6

    ColumnLayout {
        id: column
        anchors.fill: parent
        anchors.margins: card.padding
        spacing: 10
    }
}
