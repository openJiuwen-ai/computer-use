@main enum NativeKeyboardWindowEvidenceTests {
    static func main() {
        backgroundSelection()
        print("NativeKeyboardWindowEvidenceTests passed")
    }

    static func backgroundSelection() {
        typealias Facts = NativeKeyboardWindowEvidence.BackgroundSelection
        let valid = Facts(
            foregroundAllowed: true, windowScope: true, windowCommand: true, foreground: false, standardWindow: true,
            focused: false, windowFocusWritable: false, appFocusWritable: false, main: true, mainWindowMatches: true,
            focusedWindowMatches: true)
        precondition(NativeKeyboardWindowEvidence.prefersForegroundBeforeSynthetic(valid))
        // An explicit delegate command can consume background keys even when
        // AXFocused is false. Strict shared must retain its existing attempt.
        for key in [\Facts.foregroundAllowed, \Facts.windowScope, \Facts.windowCommand, \Facts.standardWindow] {
            var facts = valid
            facts[keyPath: key] = false
            precondition(!NativeKeyboardWindowEvidence.prefersForegroundBeforeSynthetic(facts))
        }
        var foreground = valid
        foreground.foreground = true
        precondition(!NativeKeyboardWindowEvidence.prefersForegroundBeforeSynthetic(foreground))
        // A writable focus mechanism must retain its background attempt;
        // absent/error capability evidence cannot prove one unavailable.
        for key in [\Facts.focused, \Facts.windowFocusWritable, \Facts.appFocusWritable] {
            for value: Bool? in [true, nil] {
                var facts = valid
                facts[keyPath: key] = value
                precondition(!NativeKeyboardWindowEvidence.prefersForegroundBeforeSynthetic(facts))
            }
        }
        // Another or unknown Main/focused window still has selection work to
        // perform, so this narrow no-selector proof does not apply.
        for key in [\Facts.main, \Facts.mainWindowMatches, \Facts.focusedWindowMatches] {
            for value: Bool? in [false, nil] {
                var facts = valid
                facts[keyPath: key] = value
                precondition(!NativeKeyboardWindowEvidence.prefersForegroundBeforeSynthetic(facts))
            }
        }
    }
}
