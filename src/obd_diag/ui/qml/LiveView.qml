import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Live-Daten (Mode 01): ausgewählte Werte fortlaufend lesen, als Kacheln mit Verlauf
// zeigen und auf Wunsch als CSV aufzeichnen. Port und Baudrate kommen aus der
// Kopfzeile (ConnectionBar).
Rectangle {
    id: pane

    required property var live  // LiveViewModel (ui/viewmodels/live.py)
    required property string port
    required property int baud

    readonly property bool running: live.running
    readonly property bool hasTiles: live.values.count > 0

    color: Theme.background

    // ohne Dezimalzeichen, damit es in jeder Sprache gleich aussieht
    function intervalText(seconds) {
        return seconds < 1 ? Math.round(seconds * 1000) + " ms" : seconds + " s"
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // --- Leiste: Start/Stopp, Intervall, Aufzeichnen, Laufzeit ---
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: bar.implicitHeight + 2 * Theme.gap + 1
            color: Theme.surface

            RowLayout {
                id: bar
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: Theme.pad
                anchors.rightMargin: Theme.pad
                spacing: Theme.gap

                Button {
                    objectName: "liveStartButton"
                    text: !pane.running ? qsTr("Start") : pane.live.stopping ? qsTr("Stoppe …") : qsTr("Stopp")
                    enabled: pane.running ? !pane.live.stopping : pane.live.canStart
                    palette.button: pane.running ? Theme.stopButton : Theme.accentButton
                    palette.buttonText: "white"
                    font.bold: true
                    opacity: enabled ? 1 : 0.5
                    Layout.preferredWidth: 96
                    onClicked: {
                        if (pane.running)
                            pane.live.stop()
                        else
                            pane.live.start(pane.port, pane.baud)
                    }
                }

                Item {
                    Layout.preferredWidth: Theme.gap
                }
                Label {
                    text: qsTr("Intervall")
                }
                ComboBox {
                    id: intervalBox
                    objectName: "liveIntervalBox"
                    Layout.preferredWidth: 90
                    enabled: !pane.running
                    model: pane.live.intervals.map(s => ({ text: pane.intervalText(s), value: s }))
                    textRole: "text"
                    valueRole: "value"
                    Accessible.name: qsTr("Abfrageintervall")
                    Component.onCompleted: currentIndex = indexOfValue(pane.live.interval)
                    onActivated: pane.live.interval = currentValue
                }

                Item {
                    Layout.preferredWidth: Theme.gap
                }
                Switch {
                    objectName: "liveRecordSwitch"
                    text: qsTr("Aufzeichnen (CSV)")
                    enabled: !pane.running
                    checked: pane.live.recording
                    onToggled: pane.live.recording = checked
                    ToolTip.visible: hovered
                    ToolTip.delay: 600
                    ToolTip.text: qsTr("Jede Runde sofort in eine neue CSV-Datei schreiben")
                }

                Item {
                    Layout.fillWidth: true
                }

                BusyIndicator {
                    Layout.preferredWidth: 24
                    Layout.preferredHeight: 24
                    running: pane.running && !pane.hasTiles
                    visible: running
                }
                Chip {
                    objectName: "liveVoltage"
                    visible: pane.running || pane.live.sampleCount > 0
                    text: qsTr("Bordspannung %1").arg(pane.live.voltageText)
                    textColor: pane.live.throttled ? Theme.warnText : Theme.muted
                    fill: pane.live.throttled ? Theme.warnBg : Theme.background
                }
                Chip {
                    objectName: "liveElapsed"
                    visible: pane.running || pane.live.sampleCount > 0
                    text: pane.live.elapsedText + " · " + (pane.live.sampleCount === 1 ? qsTr("1 Runde") : qsTr("%1 Runden").arg(pane.live.sampleCount))
                }
            }
            Rectangle {
                anchors.bottom: parent.bottom
                width: parent.width
                height: 1
                color: Theme.border
            }
        }

        // --- Hinweise ---
        ColumnLayout {
            Layout.fillWidth: true
            Layout.margins: Theme.gap
            spacing: 6

            Banner {
                objectName: "liveSafetyBanner"
                Layout.fillWidth: true
                kind: "warn"
                text: "⚠  " + qsTr("Während der Fahrt nur durch Beifahrer bedienen. Wer fährt, achtet auf den Verkehr, nicht auf den Bildschirm.")
            }
            Banner {
                objectName: "liveThrottleBanner"
                Layout.fillWidth: true
                kind: "warn"
                text: pane.running && pane.live.throttled
                      ? qsTr("Bordspannung niedrig (%1): die Abfrage ist gedrosselt, um die Batterie zu schonen.").arg(pane.live.voltageText)
                      : ""
            }
            Banner {
                objectName: "liveErrorBanner"
                Layout.fillWidth: true
                kind: "error"
                text: pane.live.errorMessage
                closable: true
                onClosed: pane.live.dismissError()
            }
            Banner {
                objectName: "liveNoticeBanner"
                Layout.fillWidth: true
                kind: "ok"
                text: pane.live.notice
                closable: true
                onClosed: pane.live.dismissNotice()
            }

            // Aufzeichnung läuft bzw. liegt vor
            RowLayout {
                Layout.fillWidth: true
                visible: pane.running && pane.live.recordingPath !== ""
                spacing: Theme.gap
                Chip {
                    text: "● " + qsTr("Aufzeichnung")
                    textColor: Theme.errorText
                    fill: Theme.errorBg
                }
                Label {
                    objectName: "liveRecordingPath"
                    Layout.fillWidth: true
                    text: pane.live.recordingPath
                    elide: Text.ElideMiddle
                    color: Theme.muted
                    font.family: "monospace"
                }
                Button {
                    text: qsTr("Ordner öffnen")
                    flat: true
                    onClicked: pane.live.openRecordingFolder()
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 1
            color: Theme.border
        }

        // --- Inhalt: Auswahl (vor dem Start) und Kacheln ---
        ScrollView {
            id: scroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentWidth: availableWidth
            clip: true

            ColumnLayout {
                id: content
                x: Theme.pad
                width: scroll.availableWidth - 2 * Theme.pad
                spacing: Theme.pad

                Item {
                    implicitHeight: 1
                }

                Card {
                    objectName: "liveSelection"
                    Layout.fillWidth: true
                    visible: !pane.running
                    spacing: 6

                    RowLayout {
                        Layout.fillWidth: true
                        SectionTitle {
                            Layout.fillWidth: true
                            text: qsTr("Werte auswählen")
                            topPadding: 0
                        }
                        Button {
                            text: qsTr("Übliche Werte")
                            flat: true
                            onClicked: pane.live.resetSelection()
                        }
                    }
                    Label {
                        Layout.fillWidth: true
                        visible: !pane.live.supportedKnown
                        text: pane.live.available.length === 0
                              ? qsTr("Welche Werte das Fahrzeug liefert, steht nach dem ersten Start fest. Ohne Auswahl werden die üblichen Werte abgefragt (Drehzahl, Geschwindigkeit, Kühlmittel, Last, Ansaugluft, Steuergerätespannung).")
                              : qsTr("Noch nicht mit dem Fahrzeug abgeglichen: nach dem ersten Start stehen hier nur die unterstützten Werte.")
                        color: Theme.muted
                        font.pixelSize: 12
                        wrapMode: Text.Wrap
                    }
                    Flow {
                        Layout.fillWidth: true
                        spacing: 4
                        Repeater {
                            objectName: "liveAvailable"
                            model: pane.live.available
                            delegate: CheckBox {
                                id: check
                                required property var modelData
                                readonly property bool wanted:
                                    pane.live.selectedKeys.indexOf(modelData.key) >= 0
                                objectName: "liveCheck_" + modelData.key
                                width: 250
                                text: modelData.name + (modelData.unit ? " (" + modelData.unit + ")" : "")
                                // Lange Namen werden gekürzt; der volle Name steht im Tooltip
                                ToolTip.visible: hovered
                                ToolTip.delay: 600
                                ToolTip.text: text
                                checked: wanted
                                onToggled: {
                                    pane.live.setSelected(modelData.key, checked)
                                    checked = Qt.binding(() => check.wanted)
                                }
                            }
                        }
                    }
                }

                EmptyState {
                    objectName: "liveEmpty"
                    Layout.fillWidth: true
                    Layout.topMargin: Theme.pad
                    visible: !pane.hasTiles
                    title: pane.running ? qsTr("Verbinde und ermittle unterstützte Werte …")
                                        : qsTr("Noch keine Live-Daten")
                    text: pane.running ? ""
                          : qsTr("Port oben wählen, Werte auswählen und „Start“ drücken. Gelesen wird nur (Mode 01); am Fahrzeug wird nichts verändert.")
                }

                GridLayout {
                    id: grid
                    Layout.fillWidth: true
                    visible: pane.hasTiles
                    columns: Math.max(1, Math.floor((content.width + columnSpacing) / 260))
                    columnSpacing: Theme.pad
                    rowSpacing: Theme.pad

                    Repeater {
                        objectName: "liveTiles"
                        model: pane.live.values
                        delegate: LiveTile {
                            Layout.fillWidth: true
                            Layout.preferredWidth: 240
                            historyLength: pane.live.historyLength
                        }
                    }
                }

                Item {
                    implicitHeight: Theme.gap
                }
            }
        }
    }

    // Eine Kachel: Name, großer Wert mit Einheit, Verlaufskurve auf der Skala der PID.
    component LiveTile: Rectangle {
        id: tile

        required property string key
        required property string name
        required property string unit
        required property real minimum
        required property real maximum
        required property bool hasValue
        required property string valueText
        required property string minimumText
        required property string maximumText
        required property string lowText
        required property string highText
        required property var history
        property int historyLength: 120

        objectName: "liveTile_" + key
        implicitHeight: tileColumn.implicitHeight + 2 * 14
        color: Theme.surface
        border.color: Theme.border
        radius: 6

        ColumnLayout {
            id: tileColumn
            anchors.fill: parent
            anchors.margins: 14
            spacing: 4

            Label {
                Layout.fillWidth: true
                text: tile.name
                color: Theme.muted
                font.bold: true
                font.pixelSize: 12
                elide: Text.ElideRight
            }
            RowLayout {
                spacing: 6
                Label {
                    objectName: "liveValue_" + tile.key
                    text: tile.valueText
                    font.pixelSize: 30
                    font.bold: true
                    font.features: { "tnum": 1 }
                    color: tile.hasValue ? palette.text : Theme.muted
                }
                Label {
                    Layout.alignment: Qt.AlignBaseline
                    text: tile.unit
                    color: Theme.muted
                    font.pixelSize: 14
                }
            }

            Canvas {
                id: spark
                Layout.fillWidth: true
                Layout.preferredHeight: 52
                Layout.topMargin: 2

                property var points: tile.history
                property color lineColor: Theme.accent
                property color gridColor: Theme.divider
                // Skala nach den gezeigten Werten statt nach dem vollen Bereich der Norm
                // (Drehzahl 0 bis 16384 1/min ließe jede Kurve flach erscheinen).
                readonly property var scale: autoScale(points)

                function autoScale(values) {
                    let lo = Infinity
                    let hi = -Infinity
                    for (let i = 0; values && i < values.length; ++i) {
                        const v = values[i]
                        if (v === null || isNaN(v))
                            continue
                        lo = Math.min(lo, v)
                        hi = Math.max(hi, v)
                    }
                    if (lo > hi)
                        return null
                    // Mindestspanne, damit Rauschen eines fast konstanten Werts nicht
                    // bildfüllend wird: 2 % des Normbereichs bzw. 5 % des Betrags.
                    const minSpan = Math.max((tile.maximum - tile.minimum) * 0.02,
                                             Math.abs(hi) * 0.05, 1e-6)
                    if (hi - lo < minSpan) {
                        const mid = (hi + lo) / 2
                        lo = mid - minSpan / 2
                        hi = mid + minSpan / 2
                    }
                    const pad = (hi - lo) * 0.1
                    return [Math.max(tile.minimum, lo - pad), Math.min(tile.maximum, hi + pad)]
                }

                function scaleText(v) {
                    const digits = Math.abs(v) >= 100 ? 0 : 1
                    return Number(v).toLocaleString(Qt.locale(), "f", digits)  // Sprache der Oberfläche
                }

                onPointsChanged: requestPaint()
                onLineColorChanged: requestPaint()
                onWidthChanged: requestPaint()

                onPaint: {
                    const ctx = getContext("2d")
                    ctx.reset()
                    // Grundlinie und obere Linie der Skala
                    ctx.strokeStyle = gridColor
                    ctx.lineWidth = 1
                    ctx.beginPath()
                    ctx.moveTo(0, 0.5)
                    ctx.lineTo(width, 0.5)
                    ctx.moveTo(0, height - 0.5)
                    ctx.lineTo(width, height - 0.5)
                    ctx.stroke()

                    const n = points ? points.length : 0
                    if (n === 0 || scale === null)
                        return
                    const low = scale[0]
                    const span = scale[1] - scale[0]
                    if (!(span > 0))
                        return
                    const step = width / Math.max(1, tile.historyLength - 1)
                    const y = v => {
                        const t = Math.min(1, Math.max(0, (v - low) / span))
                        return 2 + (height - 4) * (1 - t)
                    }
                    ctx.strokeStyle = lineColor
                    ctx.lineWidth = 2
                    ctx.lineJoin = "round"
                    ctx.beginPath()
                    let open = false
                    let last = null
                    for (let i = 0; i < n; ++i) {
                        const v = points[i]
                        const x = width - (n - 1 - i) * step
                        if (v === null || isNaN(v)) {
                            open = false  // nicht lesbar: Lücke in der Kurve
                            continue
                        }
                        if (open)
                            ctx.lineTo(x, y(v))
                        else
                            ctx.moveTo(x, y(v))
                        open = true
                        last = [x, y(v)]
                    }
                    ctx.stroke()
                    if (last !== null) {
                        ctx.fillStyle = lineColor
                        ctx.beginPath()
                        ctx.arc(last[0] - 2, last[1], 3, 0, 2 * Math.PI)
                        ctx.fill()
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 4
                Label {
                    // untere/obere Kante der Kurve
                    text: spark.scale === null ? tile.minimumText : spark.scaleText(spark.scale[0])
                    color: Theme.muted
                    font.pixelSize: 11
                }
                Label {
                    objectName: "liveRange_" + tile.key
                    Layout.fillWidth: true
                    horizontalAlignment: Text.AlignHCenter
                    text: tile.lowText === "–" ? "" : qsTr("gesehen %1 bis %2").arg(tile.lowText).arg(tile.highText)
                    color: Theme.muted
                    font.pixelSize: 11
                    elide: Text.ElideRight
                }
                Label {
                    text: spark.scale === null ? tile.maximumText : spark.scaleText(spark.scale[1])
                    color: Theme.muted
                    font.pixelSize: 11
                }
            }
        }
    }
}
