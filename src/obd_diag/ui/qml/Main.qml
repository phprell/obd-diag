import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

ApplicationWindow {
    id: window

    // DiagnosisViewModel, von Python als Kontext-Eigenschaft gesetzt (ui/window.py)
    readonly property var vm: diagnosis
    // LiveViewModel (Reiter „Live-Daten“), ebenfalls aus ui/window.py
    readonly property var liveVm: live
    // DesignController (ui/theme.py): hell, dunkel oder wie das System
    readonly property var design: designController
    // LanguageController (ui/window.py): Deutsch oder Englisch
    readonly property var language: languageController

    width: 1000
    height: 700
    minimumWidth: 760
    minimumHeight: 480
    visible: true
    title: qsTr("OBD-Diagnose")
    color: Theme.background

    // Fusion-Palette passend zum Design (Theme.qml); Menüs und Dialoge erben sie.
    // Rollen mit eigener Farbe für deaktivierte Elemente je Gruppe setzen: die
    // Kurzform (palette.text) gilt für alle Gruppen und überschriebe sonst je nach
    // Reihenfolge der Bindungen die deaktivierte Farbe.
    palette.window: Theme.fusion.window
    palette.alternateBase: Theme.fusion.alternateBase
    palette.button: Theme.fusion.button
    palette.brightText: "#ffffff"
    palette.highlightedText: "#ffffff"
    palette.toolTipBase: Theme.fusion.toolTipBase
    palette.toolTipText: Theme.fusion.toolTipText
    palette.light: Theme.fusion.light
    palette.midlight: Theme.fusion.midlight
    palette.mid: Theme.fusion.mid
    palette.dark: Theme.fusion.shade
    palette.shadow: Theme.fusion.shadow
    palette.link: Theme.fusion.link
    palette.linkVisited: Theme.fusion.linkVisited
    palette.placeholderText: Theme.fusion.placeholderText
    palette.active.windowText: Theme.fusion.windowText
    palette.inactive.windowText: Theme.fusion.windowText
    palette.disabled.windowText: Theme.fusion.disabledText
    palette.active.text: Theme.fusion.text
    palette.inactive.text: Theme.fusion.text
    palette.disabled.text: Theme.fusion.disabledText
    palette.active.buttonText: Theme.fusion.buttonText
    palette.inactive.buttonText: Theme.fusion.buttonText
    palette.disabled.buttonText: Theme.fusion.disabledText
    palette.active.base: Theme.fusion.base
    palette.inactive.base: Theme.fusion.base
    palette.disabled.base: Theme.fusion.disabledBase
    palette.active.highlight: Theme.fusion.highlight
    palette.inactive.highlight: Theme.fusion.highlight
    palette.disabled.highlight: Theme.fusion.disabledHighlight
    palette.active.accent: Theme.fusion.highlight
    palette.inactive.accent: Theme.fusion.highlight
    palette.disabled.accent: Theme.fusion.disabledHighlight

    // Eintrag unter Optionen → Design
    component DesignItem: MenuItem {
        required property string key
        objectName: "design_" + key
        checkable: true
        checked: window.design.mode === key
        ButtonGroup.group: designGroup
        onTriggered: window.design.mode = key
    }

    // Eintrag unter Optionen → Sprache; die Namen stehen in ihrer eigenen Sprache
    component LanguageItem: MenuItem {
        required property string code
        objectName: "language_" + code
        checkable: true
        checked: window.language.language === code
        ButtonGroup.group: languageGroup
        onTriggered: window.language.language = code
    }

    // --- Aktionen (Menü, Tastenkürzel und Schaltflächen teilen sie sich) ---

    Action {
        id: openAction
        text: qsTr("Sitzung öffnen …")
        shortcut: StandardKey.Open
        enabled: !window.vm.busy && !window.liveVm.running
        onTriggered: openDialog.open()
    }
    Action {
        id: saveAction
        text: qsTr("Sitzung speichern")
        shortcut: StandardKey.Save
        enabled: window.vm.canSave
        onTriggered: window.vm.saveSession()
    }
    Action {
        id: pdfAction
        text: qsTr("Bericht als PDF …")
        shortcut: "Ctrl+P"
        enabled: window.vm.canExport
        onTriggered: exportDialog.start("pdf")
    }
    Action {
        id: csvAction
        text: qsTr("CSV exportieren …")
        enabled: window.vm.canExport
        onTriggered: exportDialog.start("csv")
    }

    menuBar: MenuBar {
        objectName: "menuBar"
        Menu {
            objectName: "fileMenu"
            title: qsTr("&Datei")
            MenuItem {
                action: openAction
            }
            MenuItem {
                objectName: "saveMenuItem"
                action: saveAction
            }
            MenuSeparator {}
            MenuItem {
                objectName: "pdfMenuItem"
                action: pdfAction
            }
            MenuItem {
                action: csvAction
            }
            MenuSeparator {}
            MenuItem {
                text: qsTr("Beenden")
                onTriggered: Qt.quit()
            }
        }
        Menu {
            objectName: "optionsMenu"
            width: 320  // sonst werden die langen Einträge gekürzt
            title: qsTr("&Optionen")
            MenuItem {
                objectName: "onlineVinMenuItem"
                text: qsTr("FIN online nachschlagen (NHTSA)")
                checkable: true
                checked: window.vm.onlineVinLookup
                onToggled: window.vm.onlineVinLookup = checked
            }
            MenuItem {
                objectName: "onlineCodesMenuItem"
                text: qsTr("Fehlercodes online erklären")
                checkable: true
                checked: window.vm.onlineCodeLookup
                onToggled: window.vm.onlineCodeLookup = checked
            }
            MenuSeparator {}
            MenuItem {
                objectName: "traceMenuItem"
                text: qsTr("Adapter-Mitschnitt aufzeichnen")
                checkable: true
                checked: window.vm.traceAdapter
                onToggled: window.vm.traceAdapter = checked
            }
            MenuItem {
                text: qsTr("Mitschnitt-Ordner öffnen")
                onTriggered: window.vm.openTraceFolder()
            }
            MenuSeparator {}
            Menu {
                objectName: "designMenu"
                title: qsTr("Design")

                ButtonGroup {
                    id: designGroup
                }
                DesignItem {
                    key: "system"
                    text: qsTr("Wie das System")
                }
                DesignItem {
                    key: "light"
                    text: qsTr("Hell")
                }
                DesignItem {
                    key: "dark"
                    text: qsTr("Dunkel")
                }
            }
            Menu {
                objectName: "languageMenu"
                // zweisprachig, damit man es in jeder Sprache findet
                title: "Sprache / Language"

                ButtonGroup {
                    id: languageGroup
                }
                LanguageItem {
                    code: "de"
                    text: "Deutsch"
                }
                LanguageItem {
                    code: "en"
                    text: "English"
                }
            }
        }
    }

    Binding {
        target: Theme
        property: "dark"
        value: window.design.dark
    }

    header: ConnectionBar {
        id: connectionBar
        vm: window.vm
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // Reiter und, sobald bekannt, das Fahrzeug
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: tabs.implicitHeight + 1
            color: Theme.surface

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: Theme.gap
                anchors.rightMargin: Theme.pad
                anchors.bottomMargin: 1
                spacing: Theme.pad

                ViewTabs {
                    id: tabs
                    objectName: "viewTabs"
                    vm: window.vm
                    live: window.liveVm
                    Layout.alignment: Qt.AlignBottom
                }
                Item {
                    Layout.fillWidth: true
                }
                Chip {
                    visible: window.vm.viewOnly
                    text: qsTr("Gespeicherte Sitzung · nur ansehen")
                    textColor: Theme.infoText
                    fill: Theme.infoBg
                }
                Label {
                    objectName: "vehicleHeader"
                    Layout.maximumWidth: 360
                    visible: text !== ""
                    text: window.vm.vehicleText
                    elide: Text.ElideMiddle
                    font.bold: true
                    color: palette.text
                }
            }
            Rectangle {
                anchors.bottom: parent.bottom
                width: parent.width
                height: 1
                color: Theme.border
            }
        }

        // Hinweise über dem Inhalt
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
                      ? qsTr("Der Fehlercode-Katalog fehlt, Codes erscheinen ohne Beschreibung. Erzeugen mit: uv run python tools/build_dtc_db.py")
                      : ""
            }
        }

        // Rahmenlinie über dem Inhalt, wenn Hinweise darüber stehen
        Rectangle {
            visible: banners.shown
            Layout.fillWidth: true
            implicitHeight: 1
            color: Theme.border
        }

        StackLayout {
            objectName: "viewStack"
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: tabs.currentIndex

            SplitView {
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
            ReadinessView {
                vm: window.vm
            }
            FreezeFrameView {
                vm: window.vm
            }
            VehicleView {
                vm: window.vm
            }
            LiveView {
                live: window.liveVm
                port: connectionBar.portText
                baud: connectionBar.baud
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
            spacing: Theme.gap

            Label {
                objectName: "statusLine"
                Layout.fillWidth: true
                elide: Text.ElideRight
                textFormat: Text.StyledText
                color: Theme.muted
                text: {
                    if (window.liveVm.running)
                        return window.liveVm.stopping ? qsTr("Live-Daten: stoppe …")
                             : qsTr("Live-Daten laufen") + (window.liveVm.connectionText
                                                            ? "  ·  " + window.liveVm.connectionText : "")
                    if (window.vm.busy)
                        return window.vm.busyText
                    if (!window.vm.hasResult)
                        return qsTr("Nicht verbunden")
                    const voltage = window.vm.lowVoltage
                        ? "<font color=\"" + Theme.errorText + "\"><b>"
                          + qsTr("%1 (niedrig)").arg(window.vm.voltageText) + "</b></font>"
                        : window.vm.voltageText
                    const prefix = window.vm.viewOnly
                        ? qsTr("Sitzung vom %1").arg(window.vm.createdText) + "  ·  "
                        : ""
                    return prefix + qsTr("Adapter: %1").arg(window.vm.adapter)
                         + "  ·  " + qsTr("Protokoll: %1").arg(window.vm.protocol)
                         + "  ·  " + qsTr("Bordspannung: %1").arg(voltage)
                }
            }
            Button {
                objectName: "saveButton"
                action: saveAction
                ToolTip.visible: hovered && window.vm.sessionPath !== ""
                ToolTip.delay: 400
                ToolTip.text: qsTr("Gespeichert: %1").arg(window.vm.sessionPath)
            }
            Button {
                objectName: "pdfButton"
                action: pdfAction
            }

            // Ein deaktivierter Button meldet kein hovered; den Tooltip trägt daher die Hülle.
            Item {
                implicitWidth: clearButton.implicitWidth
                implicitHeight: clearButton.implicitHeight
                Layout.leftMargin: Theme.gap

                Button {
                    id: clearButton
                    objectName: "clearButton"
                    anchors.fill: parent
                    text: qsTr("Fehlercodes löschen …")
                    enabled: window.vm.canClear
                    onClicked: clearDialog.open()
                }
                HoverHandler {
                    id: clearHover
                }
                ToolTip {
                    objectName: "clearTooltip"
                    visible: clearHover.hovered
                             && (window.vm.clearDisabledReason !== "" || window.vm.viewOnly)
                    delay: 400
                    text: window.vm.clearDisabledReason !== "" ? window.vm.clearDisabledReason
                                                               : qsTr("nur bei verbundenem Fahrzeug")
                }
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
        title: qsTr("Löschen nicht möglich")
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

    FileDialog {
        id: openDialog
        objectName: "openDialog"
        title: qsTr("Diagnosesitzung öffnen")
        fileMode: FileDialog.OpenFile
        nameFilters: [qsTr("Diagnosesitzungen (*.json)"), qsTr("Alle Dateien (*)")]
        currentFolder: window.vm.sessionFolder
        onAccepted: window.vm.openSession(selectedFile.toString())
    }

    FileDialog {
        id: exportDialog
        objectName: "exportDialog"

        property string kind: "pdf"

        function start(what) {
            kind = what
            const name = window.vm.reportBaseName + "." + what
            currentFolder = window.vm.reportFolder
            selectedFile = window.vm.reportFolder + "/" + name
            open()
            // Qt-eigener Ersatzdialog: Namensfeld füllen (siehe DialogHelper in window.py)
            Qt.callLater(dialogHelper.prefillFileName, name)
        }

        title: kind === "pdf" ? qsTr("Bericht als PDF speichern") : qsTr("Fehlercodes als CSV speichern")
        fileMode: FileDialog.SaveFile
        defaultSuffix: kind
        nameFilters: kind === "pdf" ? [qsTr("PDF-Dokumente (*.pdf)")] : [qsTr("CSV-Dateien (*.csv)")]
        onAccepted: {
            if (kind === "pdf")
                window.vm.exportPdf(selectedFile.toString())
            else
                window.vm.exportCsv(selectedFile.toString())
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
