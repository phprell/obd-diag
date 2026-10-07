pragma Singleton
import QtQuick

// Farben und Abstände an einer Stelle, je ein Satz für hell und dunkel. Grundfarben
// (Text, Schaltflächen) kommen aus der Palette des Stils; welche gilt, entscheidet
// DesignController (ui/theme.py), Main.qml setzt danach ``dark``.
QtObject {
    id: theme

    property bool dark: false

    readonly property int gap: 8
    readonly property int pad: 16

    // Palette für Fusion (Main.qml setzt sie dem Fenster); hell = Fusion-Standard
    readonly property QtObject fusion: QtObject {
        readonly property color window: theme.dark ? "#2a2a2e" : "#efefef"
        readonly property color windowText: theme.dark ? "#ececee" : "#000000"
        readonly property color base: theme.dark ? "#303036" : "#ffffff"
        readonly property color alternateBase: theme.dark ? "#2a2a2f" : "#f7f7f7"
        readonly property color text: theme.dark ? "#ececee" : "#000000"
        readonly property color button: theme.dark ? "#34343a" : "#efefef"
        readonly property color buttonText: theme.dark ? "#ececee" : "#000000"
        readonly property color highlight: theme.dark ? "#3b82f6" : "#308cc6"
        readonly property color toolTipBase: theme.dark ? "#34343a" : "#ffffdc"
        readonly property color toolTipText: theme.dark ? "#ececee" : "#000000"
        readonly property color light: theme.dark ? "#4a4a52" : "#ffffff"
        readonly property color midlight: theme.dark ? "#3c3c43" : "#cacaca"
        readonly property color mid: theme.dark ? "#26262a" : "#b8b8b8"
        readonly property color shade: theme.dark ? "#141416" : "#9f9f9f"
        readonly property color shadow: theme.dark ? "#000000" : "#767676"
        readonly property color link: theme.dark ? "#93c5fd" : "#0000ff"
        readonly property color linkVisited: theme.dark ? "#c4b5fd" : "#ff00ff"
        readonly property color placeholderText: theme.dark ? "#8b8b94" : "#80000000"
        readonly property color disabledText: theme.dark ? "#6b6b74" : "#bebebe"
        readonly property color disabledBase: theme.dark ? "#26262a" : "#efefef"
        readonly property color disabledHighlight: theme.dark ? "#4b4b52" : "#919191"
    }

    readonly property color accent: dark ? "#60a5fa" : "#1d4ed8"
    // Hintergrund der Hauptschaltfläche (weiße Schrift)
    readonly property color accentButton: dark ? "#1e40af" : "#1e3a8a"
    readonly property color border: dark ? "#3f3f46" : "#d4d4d8"
    readonly property color muted: dark ? "#a1a1aa" : "#52525b"
    readonly property color surface: dark ? "#232327" : "#ffffff"
    readonly property color background: dark ? "#1c1c1f" : "#f4f4f5"
    readonly property color selection: dark ? "#1e3354" : "#dbeafe"

    readonly property color errorText: dark ? "#fca5a5" : "#991b1b"
    readonly property color errorBg: dark ? "#3a1619" : "#fef2f2"
    readonly property color errorBorder: dark ? "#7f1d1d" : "#fecaca"
    readonly property color warnText: dark ? "#fcd34d" : "#92400e"
    readonly property color warnBg: dark ? "#33260d" : "#fffbeb"
    readonly property color warnBorder: dark ? "#78450f" : "#fde68a"
    readonly property color okText: dark ? "#86efac" : "#166534"
    readonly property color okBg: dark ? "#10291a" : "#f0fdf4"
    readonly property color okBorder: dark ? "#166534" : "#bbf7d0"
    readonly property color infoText: dark ? "#93c5fd" : "#1e3a8a"
    readonly property color infoBg: dark ? "#14213d" : "#eff6ff"
    readonly property color infoBorder: dark ? "#1e40af" : "#bfdbfe"
    readonly property color divider: dark ? "#2e2e33" : "#ececef"
    readonly property color okDot: dark ? "#22c55e" : "#16a34a"
    readonly property color errorDot: dark ? "#ef4444" : "#dc2626"
    readonly property color warnDot: dark ? "#f59e0b" : "#d97706"
    // Rand der Etiketten: hebt sie im Dunkeln vom Hintergrund ab
    function chipBorder(fill) {
        return dark ? Qt.lighter(fill, 1.35) : Qt.darker(fill, 1.08)
    }

    // Readiness-Monitor: abgeschlossen, nicht abgeschlossen, nicht unterstützt
    function monitorText(state) {
        if (state === "complete")
            return okText
        if (state === "incomplete")
            return errorText
        return muted
    }

    function monitorBg(state) {
        if (state === "complete")
            return okBg
        if (state === "incomplete")
            return errorBg
        return background
    }

    // Art des Codes: gespeichert (bestätigt), ausstehend, permanent. Kräftig genug für
    // weiße Schrift auf dem Etikett, in beiden Designs gleich.
    function kindColor(kind) {
        if (kind === "stored")
            return "#dc2626"
        if (kind === "pending")
            return "#b45309"
        return "#7c3aed"
    }

    function likelihoodColor(likelihood) {
        if (likelihood === "high")
            return errorText
        if (likelihood === "medium")
            return warnText
        return muted
    }

    function likelihoodBg(likelihood) {
        if (likelihood === "high")
            return errorBg
        if (likelihood === "medium")
            return warnBg
        return background
    }
}
