pragma Singleton
import QtQuick

// Farben und Abstände an einer Stelle; Grundfarben kommen aus der Palette des Stils.
QtObject {
    readonly property int gap: 8
    readonly property int pad: 16

    readonly property color accent: "#1d4ed8"
    readonly property color border: "#d4d4d8"
    readonly property color muted: "#52525b"
    readonly property color surface: "#ffffff"
    readonly property color background: "#f4f4f5"
    readonly property color selection: "#dbeafe"

    readonly property color errorText: "#991b1b"
    readonly property color errorBg: "#fef2f2"
    readonly property color errorBorder: "#fecaca"
    readonly property color warnText: "#92400e"
    readonly property color warnBg: "#fffbeb"
    readonly property color warnBorder: "#fde68a"
    readonly property color okText: "#166534"
    readonly property color okBg: "#f0fdf4"
    readonly property color okBorder: "#bbf7d0"
    readonly property color infoText: "#1e3a8a"
    readonly property color infoBg: "#eff6ff"
    readonly property color infoBorder: "#bfdbfe"

    // Art des Codes: gespeichert (bestätigt), ausstehend, permanent
    function kindColor(kind) {
        if (kind === "stored")
            return "#dc2626"
        if (kind === "pending")
            return "#d97706"
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
