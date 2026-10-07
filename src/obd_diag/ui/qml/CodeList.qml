import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Liste der Fehlercodes, nach Art gruppiert (gespeichert, ausstehend, permanent).
Rectangle {
    id: pane

    required property var vm

    color: Theme.surface

    ListView {
        id: list
        objectName: "codeList"
        anchors.fill: parent
        clip: true
        focus: true
        model: pane.vm.codes
        currentIndex: pane.vm.selectedIndex
        onCurrentIndexChanged: {
            if (pane.vm.selectedIndex !== currentIndex)
                pane.vm.selectedIndex = currentIndex
        }
        keyNavigationEnabled: true
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar {}

        section.property: "kindLabel"
        section.criteria: ViewSection.FullString
        section.delegate: Rectangle {
            required property string section
            width: ListView.view.width
            height: 34
            color: Theme.background

            Label {
                anchors.left: parent.left
                anchors.leftMargin: Theme.pad
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 7
                text: parent.section
                font.bold: true
                font.pixelSize: 12
                font.capitalization: Font.AllUppercase
                font.letterSpacing: 0.6
                color: Theme.muted
            }
            Rectangle {
                anchors.bottom: parent.bottom
                width: parent.width
                height: 1
                color: Theme.border
            }
        }

        delegate: ItemDelegate {
            id: row
            required property int index
            required property string code
            required property string kind
            required property string title
            required property bool hasInfo

            width: ListView.view.width
            highlighted: ListView.isCurrentItem
            leftPadding: Theme.pad
            rightPadding: Theme.pad
            topPadding: 10
            bottomPadding: 10
            onClicked: {
                pane.vm.selectedIndex = index
                list.forceActiveFocus()
            }

            background: Rectangle {
                color: row.highlighted ? Theme.selection
                     : row.hovered ? Theme.background : "transparent"
                Rectangle {
                    visible: row.highlighted
                    width: 3
                    height: parent.height
                    color: Theme.accent
                }
                Rectangle {
                    anchors.bottom: parent.bottom
                    x: Theme.pad
                    width: parent.width - Theme.pad
                    height: 1
                    color: Theme.divider
                }
            }

            contentItem: RowLayout {
                spacing: 12
                Rectangle {
                    Layout.alignment: Qt.AlignVCenter
                    width: 8
                    height: 8
                    radius: 4
                    color: Theme.kindColor(row.kind)
                }
                Label {
                    text: row.code
                    font.family: "monospace"
                    font.bold: true
                    font.pixelSize: 14
                    Layout.preferredWidth: 62
                }
                Label {
                    Layout.fillWidth: true
                    text: row.title
                    elide: Text.ElideRight
                    color: row.hasInfo ? palette.text : Theme.muted
                    font.italic: !row.hasInfo
                }
            }
        }
    }

    // Leere Zustände: noch kein Scan bzw. keine Codes
    ColumnLayout {
        anchors.centerIn: parent
        width: parent.width - 2 * Theme.pad
        visible: list.count === 0
        spacing: Theme.gap

        Label {
            Layout.fillWidth: true
            horizontalAlignment: Text.AlignHCenter
            text: pane.vm.busy ? "Lese Fehlercodes …"
                : pane.vm.hasResult ? "Keine Fehlercodes gespeichert."
                : "Noch nicht verbunden"
            font.pixelSize: 16
            font.bold: true
            color: pane.vm.hasResult && !pane.vm.busy ? Theme.okText : palette.text
            wrapMode: Text.Wrap
        }
        Label {
            Layout.fillWidth: true
            horizontalAlignment: Text.AlignHCenter
            visible: !pane.vm.busy
            text: pane.vm.hasResult
                  ? "Weder gespeicherte noch ausstehende oder permanente Codes."
                  : "Adapter einstecken, Zündung einschalten, Port wählen und "
                    + "„Verbinden & Scannen“ drücken."
            color: Theme.muted
            wrapMode: Text.Wrap
        }
    }
}
