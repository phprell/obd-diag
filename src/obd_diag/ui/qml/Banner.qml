import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Hinweisleiste über der Liste: info, warn, error oder ok.
Rectangle {
    id: banner

    property string kind: "info"
    property string text: ""
    property bool closable: false
    signal closed

    visible: text !== ""
    implicitHeight: Math.max(row.implicitHeight, 20) + 2 * 9
    color: kind === "error" ? Theme.errorBg : kind === "warn" ? Theme.warnBg
         : kind === "ok" ? Theme.okBg : Theme.infoBg
    border.color: kind === "error" ? Theme.errorBorder : kind === "warn" ? Theme.warnBorder
                : kind === "ok" ? Theme.okBorder : Theme.infoBorder
    radius: 4

    readonly property color textColor: kind === "error" ? Theme.errorText
                                     : kind === "warn" ? Theme.warnText
                                     : kind === "ok" ? Theme.okText : Theme.infoText

    RowLayout {
        id: row
        anchors.fill: parent
        anchors.leftMargin: 12
        anchors.rightMargin: 8
        spacing: Theme.gap

        Label {
            Layout.fillWidth: true
            text: banner.text
            color: banner.textColor
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
        }
        ToolButton {
            visible: banner.closable
            implicitWidth: 24
            implicitHeight: 24
            padding: 0
            text: "✕"
            Accessible.name: "Hinweis schließen"
            onClicked: banner.closed()
        }
    }
}
