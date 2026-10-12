import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Sitzungsvergleich (Datei → Mit Sitzung vergleichen …): je Abschnitt neue, geänderte,
// entfallene und weiterhin vorhandene Einträge; besser grün, schlechter rot.
Dialog {
    id: dialog

    required property var vm
    readonly property var comparison: vm.comparison

    title: qsTr("Sitzungsvergleich")
    modal: true
    anchors.centerIn: Overlay.overlay
    width: Math.min(720, parent ? parent.width - 48 : 720)
    height: Math.min(implicitHeight, parent ? parent.height - 48 : implicitHeight)
    padding: 20
    topPadding: 12
    standardButtons: Dialog.Close

    header: Label {
        text: dialog.title
        font.pixelSize: 17
        font.bold: true
        padding: 20
        bottomPadding: 0
    }

    function trendColor(trend) {
        if (trend === "better")
            return Theme.okText
        if (trend === "worse")
            return Theme.errorText
        return palette.text
    }

    function mark(kind) {
        return {"added": "+", "removed": "−", "changed": "~", "unchanged": "="}[kind] ?? ""
    }

    ScrollView {
        anchors.fill: parent
        contentWidth: availableWidth
        clip: true

        ColumnLayout {
            width: parent.width
            spacing: 12

            Banner {
                objectName: "compareError"
                Layout.fillWidth: true
                kind: "error"
                text: dialog.vm.errorMessage
            }
            Banner {
                objectName: "compareWarning"
                Layout.fillWidth: true
                kind: "warn"
                text: dialog.comparison.warning ?? ""
            }

            GridLayout {
                visible: dialog.vm.hasResult
                Layout.fillWidth: true
                columns: 2
                columnSpacing: Theme.pad
                rowSpacing: 4

                Label {
                    text: qsTr("Vorher")
                    color: Theme.muted
                }
                Label {
                    objectName: "compareBefore"
                    Layout.fillWidth: true
                    text: dialog.comparison.before ?? ""
                    elide: Text.ElideMiddle
                }
                Label {
                    text: qsTr("Nachher")
                    color: Theme.muted
                }
                Label {
                    objectName: "compareAfter"
                    Layout.fillWidth: true
                    text: dialog.comparison.after ?? ""
                    elide: Text.ElideMiddle
                }
            }

            Label {
                objectName: "compareSummary"
                visible: text !== ""
                Layout.fillWidth: true
                text: dialog.comparison.summary ?? ""
                font.bold: true
                wrapMode: Text.Wrap
            }

            Repeater {
                objectName: "compareSections"
                model: dialog.comparison.sections ?? []
                delegate: ColumnLayout {
                    id: section
                    required property var modelData
                    Layout.fillWidth: true
                    spacing: 4

                    SectionTitle {
                        text: section.modelData.title
                    }
                    Repeater {
                        model: section.modelData.changes
                        delegate: RowLayout {
                            id: row
                            required property var modelData
                            Layout.fillWidth: true
                            spacing: 8

                            Label {
                                Layout.alignment: Qt.AlignTop
                                Layout.preferredWidth: 14
                                text: dialog.mark(row.modelData.kind)
                                font.family: "monospace"
                                color: dialog.trendColor(row.modelData.trend)
                            }
                            Label {
                                Layout.alignment: Qt.AlignTop
                                Layout.preferredWidth: 260
                                text: row.modelData.subject
                                wrapMode: Text.Wrap
                                color: row.modelData.kind === "unchanged" ? Theme.muted : palette.text
                            }
                            Label {
                                Layout.fillWidth: true
                                Layout.alignment: Qt.AlignTop
                                text: row.modelData.text
                                wrapMode: Text.Wrap
                                color: row.modelData.kind === "unchanged"
                                       ? Theme.muted : dialog.trendColor(row.modelData.trend)
                            }
                        }
                    }
                }
            }
        }
    }
}
