// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices
import Carbon

@MainActor enum Input {
    private static let keys: [String: CGKeyCode] = [
        "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12, "w": 13,
        "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "9": 25, "7": 26,
        "8": 28, "0": 29, "o": 31, "u": 32, "i": 34, "p": 35, "l": 37, "j": 38, "k": 40, "n": 45, "m": 46,
        "-": CGKeyCode(kVK_ANSI_Minus), "=": CGKeyCode(kVK_ANSI_Equal), "[": CGKeyCode(kVK_ANSI_LeftBracket),
        "]": CGKeyCode(kVK_ANSI_RightBracket), "\\": CGKeyCode(kVK_ANSI_Backslash), ";": CGKeyCode(kVK_ANSI_Semicolon),
        "'": CGKeyCode(kVK_ANSI_Quote), ",": CGKeyCode(kVK_ANSI_Comma), ".": CGKeyCode(kVK_ANSI_Period),
        "/": CGKeyCode(kVK_ANSI_Slash), "`": CGKeyCode(kVK_ANSI_Grave), "Return": 36, "Tab": 48, "space": 49,
        "BackSpace": 51, "Escape": 53, "Delete": 117, "Home": 115, "End": 119, "Page_Up": 116, "Page_Down": 121,
        "Left": 123, "Right": 124, "Down": 125, "Up": 126, "F1": CGKeyCode(kVK_F1), "F2": CGKeyCode(kVK_F2),
        "F3": CGKeyCode(kVK_F3), "F4": CGKeyCode(kVK_F4), "F5": CGKeyCode(kVK_F5), "F6": CGKeyCode(kVK_F6),
        "F7": CGKeyCode(kVK_F7), "F8": CGKeyCode(kVK_F8), "F9": CGKeyCode(kVK_F9), "F10": CGKeyCode(kVK_F10),
        "F11": CGKeyCode(kVK_F11), "F12": CGKeyCode(kVK_F12)
    ]
    private static let modifiers: [String: (CGKeyCode, CGEventFlags)] = [
        "super": (55, .maskCommand), "ctrl": (59, .maskControl), "alt": (58, .maskAlternate), "shift": (56, .maskShift)
    ]

    // The main typing area includes the extra ISO/JIS keys but no keypad keys.
    private static let typingKeyCodes = (
        keys.filter { $0.key.utf8.count == 1 }.values
            + [CGKeyCode(kVK_ISO_Section), CGKeyCode(kVK_JIS_Yen), CGKeyCode(kVK_JIS_Underscore)]
    ).sorted()

    private static func withKeyboardLayout<Result>(
        asciiFallback: Bool, _ read: (UnsafePointer<UCKeyboardLayout>, UInt32) -> Result
    ) -> Result? {
        var source = TISCopyCurrentKeyboardLayoutInputSource()?.takeRetainedValue()
        var property = source.flatMap { TISGetInputSourceProperty($0, kTISPropertyUnicodeKeyLayoutData) }
        if property == nil && asciiFallback {
            source = TISCopyCurrentASCIICapableKeyboardLayoutInputSource()?.takeRetainedValue()
            property = source.flatMap { TISGetInputSourceProperty($0, kTISPropertyUnicodeKeyLayoutData) }
        }
        guard let property else { return nil }
        return withExtendedLifetime(source) {
            let data = unsafeBitCast(property, to: CFData.self)
            guard let bytes = CFDataGetBytePtr(data) else { return nil }
            let layout = UnsafeRawPointer(bytes).assumingMemoryBound(to: UCKeyboardLayout.self)
            return read(layout, UInt32(LMGetKbdType()))
        }
    }

    private static func asciiCharacter(
        layout: UnsafePointer<UCKeyboardLayout>, keyboardType: UInt32, code: CGKeyCode, shifted: Bool, textInput: Bool
    ) -> String? {
        var dead: UInt32 = 0, length = 0
        var buffer = [UniChar](repeating: 0, count: 8)
        let options = textInput ? OptionBits(1 << kUCKeyTranslateNoDeadKeysBit) : 0
        let status = UCKeyTranslate(
            layout, code, UInt16(kUCKeyActionDown), shifted ? UInt32(shiftKey >> 8) : 0, keyboardType,
            options, &dead, buffer.count, &length, &buffer)
        guard status == noErr, textInput || dead == 0, length == 1, (32...126).contains(buffer[0]) else { return nil }
        return String(utf16CodeUnits: buffer, count: length)
    }

    private static func asciiKeys() -> [String: (CGKeyCode, CGEventFlags)] {
        // Some apps retranslate key codes and ignore CGEvent's Unicode field.
        // Match printable ASCII to the current layout so both paths receive the same text.
        withKeyboardLayout(asciiFallback: true) { layout, keyboardType in
            var result = [String: (CGKeyCode, CGEventFlags)]()
            for shifted in [false, true] {
                for code in UInt16(0)..<128 {
                    if let text = asciiCharacter(
                        layout: layout, keyboardType: keyboardType, code: code, shifted: shifted, textInput: true
                    ), result[text] == nil {
                        result[text] = (code, shifted ? .maskShift : [])
                    }
                }
            }
            return result
        } ?? [:]
    }

    static func glyphKey(
        _ glyph: String, requiresShift: Bool, translate: (CGKeyCode, Bool) -> String?
    ) -> (code: CGKeyCode, shifted: Bool)? {
        for shifted in requiresShift ? [true] : [false, true] {
            if let code = typingKeyCodes.first(where: { translate($0, shifted) == glyph }) {
                return (code, shifted)
            }
        }
        return nil
    }

    private static func shortcutGlyph(
        _ glyph: String, requiresShift: Bool
    ) -> (code: CGKeyCode, shifted: Bool, keyboardType: UInt32)? {
        withKeyboardLayout(asciiFallback: false) { layout, keyboardType in
            glyphKey(glyph, requiresShift: requiresShift) { code, shifted in
                asciiCharacter(layout: layout, keyboardType: keyboardType, code: code, shifted: shifted, textInput: false)
            }.map { ($0.code, $0.shifted, keyboardType) }
        } ?? nil
    }

    static func source() throws -> CGEventSource {
        guard CGPreflightPostEventAccess() else {
            throw DriverError(
                """
                accessibility permission denied; enable the driver or its launching terminal in System \
                Settings > Privacy & Security
                """, code: .desktopUnavailable)
        }
        guard let source = CGEventSource(stateID: .privateState) else {
            throw DriverError("cannot create input source")
        }
        return source
    }

    /// Global input deliberately has no AX window-preparation dependency.
    static func requireDesktopInput() throws {
        try NativeInputAuthorization.requireGlobalInput("desktop")
        try RequestDiagnostics.checkCancellation()
    }

    struct KeyChord: Equatable {
        let code: CGKeyCode
        let modifiers: [String]
        let keyboardType: UInt32?
    }

    static func chord(
        _ key: String,
        resolveGlyph: @MainActor (String, Bool) -> (code: CGKeyCode, shifted: Bool, keyboardType: UInt32)? = shortcutGlyph
    ) throws -> KeyChord {
        let parts = key.split(separator: "+", omittingEmptySubsequences: false).map(String.init)
        let names = Array(parts.dropLast())
        guard Set(names).count == names.count, names.allSatisfy({ modifiers[$0] != nil }) else {
            throw DriverError("invalid modifiers: \(key)", code: .invalidRequest)
        }
        if let name = parts.last, let code = keys[name] {
            return KeyChord(code: code, modifiers: names, keyboardType: nil)
        }
        guard let name = parts.last, let glyph = ["literal-plus": "+", "?": "?"][name] else {
            throw DriverError("unsupported key: \(key)", code: .invalidRequest)
        }
        let explicitShift = names.contains("shift")
        guard let key = resolveGlyph(glyph, explicitShift) else {
            throw DriverError("the current keyboard layout has no matching typing key for \(glyph)", code: .inputUnavailable)
        }
        return KeyChord(
            code: key.code, modifiers: key.shifted && !explicitShift ? names + ["shift"] : names,
            keyboardType: key.keyboardType)
    }

    /// Validation must happen before an element command performs focus preparation.
    static func validateKey(_ key: String) throws { _ = try chord(key) }

    static func applicationCommandCode(_ key: String) throws -> CGKeyCode? {
        let encoded = try chord(key)
        guard let character = key.split(separator: "+").last.map(String.init),
            let translated = asciiKeys()[character], translated.0 == encoded.code, translated.1.isEmpty
        else { return nil }
        return encoded.code
    }

    /// Consume before awaiting: failed handoff never replays a key prefix or
    /// repeats the sequence's before-input preparation.
    @MainActor final class FirstKeyDown {
        private var pending = true

        func run(_ body: (@MainActor () async throws -> Void)?) async throws {
            guard pending else { return }
            pending = false
            try await body?()
        }
    }

    /// Allocate the complete chord before any preparation or key-down. Both
    /// App/Element and desktop units share Return's ordinary text encoding.
    static func keyEvents(_ chord: KeyChord, source: CGEventSource) throws -> (downs: [CGEvent], ups: [CGEvent]) {
        if let keyboardType = chord.keyboardType { source.keyboardType = keyboardType }
        let names = chord.modifiers
        let codes = names.map { modifiers[$0]!.0 } + [chord.code]
        var downs = [CGEvent](), ups = [CGEvent]()
        for (index, item) in codes.enumerated() {
            guard let down = CGEvent(keyboardEventSource: source, virtualKey: item, keyDown: true),
                let up = CGEvent(keyboardEventSource: source, virtualKey: item, keyDown: false)
            else { throw DriverError("cannot create keyboard events") }
            if item == CGKeyCode(kVK_Return) {
                var character = UniChar(kReturnCharCode)
                down.keyboardSetUnicodeString(stringLength: 1, unicodeString: &character)
                up.keyboardSetUnicodeString(stringLength: 1, unicodeString: &character)
            }
            down.flags = names.prefix(min(index + 1, names.count)).reduce(CGEventFlags()) { $0.union(modifiers[$1]!.1) }
            up.flags = names.prefix(min(index, names.count)).reduce(CGEventFlags()) { $0.union(modifiers[$1]!.1) }
            downs.append(down)
            ups.append(up)
        }
        return (downs, ups)
    }

    /// Selected primitive: no target search, focus preparation, activation,
    /// fallback, or post-input focus requirement. The caller checks its exact
    /// receiver before every down; releases always use the captured route.
    /// The typed delivery distinguishes original App scope from explicitly
    /// authorized desktop input; a missing recipient cannot select global input.
    static func preparedKey(
        _ key: String, delivery: NativeKeyboardDelivery, validate: @escaping @MainActor () async throws -> Void,
        onDispatch: @escaping @MainActor () throws -> Void, onPosted: @escaping @MainActor () -> Void,
        onCleanupUnknown: @escaping () -> Void,
        beforeFirstKeyDown: (@MainActor () async throws -> Void)? = nil
    ) async throws {
        let encoded = try chord(key)
        let source = try source()
        let events = try keyEvents(encoded, source: source)
        var index = 0
        try await withPreparedRoute(
            source: source, delivery: delivery, validate: {
                try await validate()
                // Layout may change during asynchronous receiver validation. Recheck before the
                // first down and before the main key, without replacing an in-progress chord.
                if encoded.keyboardType != nil, index == 0 || index == events.downs.count - 1,
                    try chord(key) != encoded
                {
                    throw DriverError("the keyboard layout changed before the shortcut key", code: .inputUnavailable)
                }
            }, onDispatch: onDispatch, onPosted: onPosted,
            onCleanupUnknown: onCleanupUnknown, beforeFirstKeyDown: beforeFirstKeyDown
        ) { press, release in
            for event in events.downs.indices {
                index = event
                try await press(events.downs[event], events.ups[event], nil)
            }
            try await Task.sleep(for: .milliseconds(15))
            try release()
        }
    }

    /// Unicode insertion is a selected keyboard primitive, not replacement or
    /// implicit clipboard paste. The execution unit supplies the verified route.
    static func preparedText(
        _ text: String, delivery: NativeKeyboardDelivery, applicationPID: pid_t?,
        validate: @escaping @MainActor () async throws -> Void, onDispatch: @escaping @MainActor () throws -> Void,
        onPosted: @escaping @MainActor () -> Void, onCleanupUnknown: @escaping () -> Void,
        beforeFirstKeyDown: (@MainActor () async throws -> Void)? = nil,
        savePanelPath: (@MainActor (String) throws -> NativeSavePanelPathContinuation.Proof?)? = nil,
        attachedEditor: (@MainActor () throws -> NativeAppEditorHandoff.Proof?)? = nil
    ) async throws {
        try validateText(text)
        let source = try source(), ascii = asciiKeys()
        var postedPrefix = ""
        var releasedContent = false
        let nextPath: (@MainActor () async throws -> NativeSavePanelPathContinuation.Proof?)?
        if let savePanelPath {
            nextPath = { try await savePanelPathReceiver(after: postedPrefix, resolve: savePanelPath) }
        } else { nextPath = nil }
        let nextEditor: (@MainActor () throws -> NativeAppEditorHandoff.Proof?)?
        if let attachedEditor {
            nextEditor = { try releasedContent ? attachedEditor() : nil }
        } else { nextEditor = nil }
        try await withPreparedRoute(
            source: source, delivery: delivery, validate: validate, onDispatch: onDispatch, onPosted: onPosted,
            onCleanupUnknown: onCleanupUnknown, beforeFirstKeyDown: beforeFirstKeyDown,
            savePanelPath: nextPath, attachedEditor: nextEditor
        ) { press, release in
            var notBefore: TimeInterval?
            for scalar in text.unicodeScalars {
                let (down, up) = try unicodeEvents(
                    scalar, source: source, key: ascii[String(scalar)], foreground: delivery.isGlobal)
                try await press(down, up, notBefore)
                // Keep release behind a bounded provider round trip. This is
                // delivery settling, not a post-input focus or value assertion.
                let settlePID = savePanelPath == nil ? applicationPID : delivery.processRecipient?.pid
                if let settlePID {
                    let root = AXUIElementCreateApplication(settlePID)
                    AXUIElementSetMessagingTimeout(root, 0.2)
                    var ignored: CFTypeRef?
                    _ = AXUIElementCopyAttributeValue(root, kAXFocusedUIElementAttribute as CFString, &ignored)
                } else {
                    try await Task.sleep(for: .milliseconds(10))
                }
                try release()
                releasedContent = true
                if savePanelPath != nil { postedPrefix.unicodeScalars.append(scalar) }
                // Subsequent preflight work counts toward the same minimum
                // release interval. Every wait is followed by fresh checks.
                notBefore = ProcessInfo.processInfo.systemUptime + 0.010
            }
            if notBefore != nil { try await Task.sleep(for: .milliseconds(10)) }
        }
    }

    static func savePanelPathReceiver(
        after releasedPrefix: String,
        now: () -> TimeInterval = { ProcessInfo.processInfo.systemUptime },
        pause: (TimeInterval) async throws -> Void = { try await Task.sleep(for: .seconds($0)) },
        resolve: (String) throws -> NativeSavePanelPathContinuation.Proof?
    ) async throws -> NativeSavePanelPathContinuation.Proof? {
        guard releasedPrefix == "/" else { return try resolve(releasedPrefix) }
        // The recognized SavePanel can open Go To after a slash. Its previous
        // name field can still appear focused before the new receiver is published.
        // Do not let that transient state authorize the next scalar.
        let deadline = min(RequestDiagnostics.deadline ?? .infinity, now() + 0.25)
        while now() < deadline {
            try RequestDiagnostics.checkCancellation()
            if let proof = try resolve(releasedPrefix) {
                try RequestDiagnostics.checkCancellation()
                return proof
            }
            let remaining = deadline - now()
            guard remaining > 0 else { break }
            try await pause(min(0.025, remaining))
        }
        try RequestDiagnostics.checkCancellation()
        throw DriverError(
            "Go To did not confirm the path receiver after the slash; remaining text was not sent", code: .inputUnavailable)
    }

    struct AtomicTextEvents {
        let source: CGEventSource
        let down: CGEvent
        let up: CGEvent
    }

    /// Allocate and verify the selected whole-string encoding before a
    /// replacement changes selection. A lossy event removes only this recipe.
    static func atomicTextEvents(
        _ text: String, source: CGEventSource, key: (CGKeyCode, CGEventFlags)? = nil, foreground: Bool = false
    ) throws -> AtomicTextEvents? {
        guard !text.isEmpty else { return nil }
        try validateText(text)
        let (down, up) = try unicodeEvents(text, source: source, key: key, foreground: foreground)
        let expected = Array(text.utf16)
        for event in [down, up] {
            var count = 0
            event.keyboardGetUnicodeString(maxStringLength: 0, actualStringLength: &count, unicodeString: nil)
            guard count == expected.count else { return nil }
            var actual = [UniChar](repeating: 0, count: count)
            event.keyboardGetUnicodeString(
                maxStringLength: actual.count, actualStringLength: &count, unicodeString: &actual)
            guard actual == expected else { return nil }
        }
        return AtomicTextEvents(source: source, down: down, up: up)
    }

    static func prepareAtomicText(_ text: String, foreground: Bool) throws -> AtomicTextEvents? {
        guard let first = text.unicodeScalars.first else { return nil }
        return try atomicTextEvents(text, source: source(), key: asciiKeys()[String(first)], foreground: foreground)
    }

    /// Prepare bounded Unicode payloads before changing a rich editor's
    /// selection. Keep surrogate pairs together and verify every event's exact
    /// payload; delivery still revalidates the receiver before each fragment.
    static func prepareTextFragments(_ text: String, foreground: Bool) throws -> [AtomicTextEvents]? {
        var fragments = [AtomicTextEvents](), pending = "", units = 0
        for scalar in text.unicodeScalars {
            let width = scalar.value > 0xffff ? 2 : 1
            if units + width > 20 {
                guard let events = try prepareAtomicText(pending, foreground: foreground) else { return nil }
                fragments.append(events)
                pending = ""
                units = 0
            }
            pending.unicodeScalars.append(scalar)
            units += width
        }
        if !pending.isEmpty {
            guard let events = try prepareAtomicText(pending, foreground: foreground) else { return nil }
            fragments.append(events)
        }
        return fragments
    }

    /// One selected Unicode string, with the same receiver, mutation barrier,
    /// paired release and delivery settling as ordinary keyboard text.
    static func preparedAtomicText(
        _ events: AtomicTextEvents, delivery: NativeKeyboardDelivery, applicationPID: pid_t?,
        validate: @escaping @MainActor () async throws -> Void, onDispatch: @escaping @MainActor () throws -> Void,
        onPosted: @escaping @MainActor () -> Void, onCleanupUnknown: @escaping () -> Void
    ) async throws {
        try await withPreparedRoute(
            source: events.source, delivery: delivery, validate: validate, onDispatch: onDispatch, onPosted: onPosted,
            onCleanupUnknown: onCleanupUnknown
        ) { press, release in
            try await press(events.down, events.up, nil)
            if let applicationPID {
                let root = AXUIElementCreateApplication(applicationPID)
                AXUIElementSetMessagingTimeout(root, 0.2)
                var ignored: CFTypeRef?
                _ = AXUIElementCopyAttributeValue(root, kAXFocusedUIElementAttribute as CFString, &ignored)
            } else {
                try await Task.sleep(for: .milliseconds(10))
            }
            try release()
            try await Task.sleep(for: .milliseconds(10))
        }
    }

    static func unicodeEvents(
        _ scalar: Unicode.Scalar, source: CGEventSource, key: (CGKeyCode, CGEventFlags)? = nil, foreground: Bool = false
    ) throws -> (CGEvent, CGEvent) {
        try unicodeEvents(String(scalar), source: source, key: key, foreground: foreground)
    }

    private static func unicodeEvents(
        _ text: String, source: CGEventSource, key: (CGKeyCode, CGEventFlags)?, foreground: Bool
    ) throws -> (CGEvent, CGEvent) {
        let (code, flags) = key ?? (0, CGEventFlags())
        guard let down = CGEvent(keyboardEventSource: source, virtualKey: code, keyDown: true),
            let up = CGEvent(keyboardEventSource: source, virtualKey: code, keyDown: false)
        else { throw DriverError("cannot create Unicode input events") }
        down.flags = flags
        // Shift describes the character; global release must clear session
        // flags. Process-addressed events retain the character's full metadata.
        up.flags = foreground ? [] : flags
        let units = Array(text.utf16)
        units.withUnsafeBufferPointer { buffer in
            down.keyboardSetUnicodeString(stringLength: buffer.count, unicodeString: buffer.baseAddress!)
            up.keyboardSetUnicodeString(stringLength: buffer.count, unicodeString: buffer.baseAddress!)
        }
        return (down, up)
    }

    private static func withPreparedRoute(
        source: CGEventSource, delivery: NativeKeyboardDelivery, validate: @escaping @MainActor () async throws -> Void,
        onDispatch: @escaping @MainActor () throws -> Void, onPosted: @escaping @MainActor () -> Void,
        onCleanupUnknown: @escaping () -> Void,
        beforeFirstKeyDown: (@MainActor () async throws -> Void)? = nil,
        savePanelPath: (@MainActor () async throws -> NativeSavePanelPathContinuation.Proof?)? = nil,
        attachedEditor: (@MainActor () throws -> NativeAppEditorHandoff.Proof?)? = nil,
        send:
            @MainActor (
                _ press: @MainActor (CGEvent, CGEvent, TimeInterval?) async throws -> Void,
                _ release: @MainActor () throws -> Void
            ) async throws -> Void
    ) async throws {
        try delivery.requireAdmission()
        let recipient = delivery.processRecipient
        let process = recipient.flatMap { NativeProcessIdentity.read($0.pid) }
        guard recipient == nil || process != nil else {
            throw DriverError("keyboard recipient is no longer identifiable", code: .staleTarget)
        }
        var activeDelivery = delivery
        let pair = PairedInput<CGEvent>(
            source: source,
            post: { activeDelivery.post($0) },
            canRelease: { activeDelivery.canRelease(expectedProcess: process) },
            postRelease: { activeDelivery.post($0) },
            skipped: onCleanupUnknown)
        func validateOriginal() async throws {
            try await validate()
            guard recipient.map({ NativeProcessIdentity.read($0.pid) == process }) ?? true else {
                throw DriverError("keyboard receiving process changed", code: .staleTarget)
            }
            try recipient?.validateFocus()
        }
        func usePath(_ proof: NativeSavePanelPathContinuation.Proof) throws {
            guard let recipient, proof.authorizes(recipient, lifetime: process),
                NativeProcessIdentity.read(recipient.pid) == process
            else { throw DriverError("the Go To keyboard service changed", code: .staleTarget) }
            try proof.validateFocus()
            // This callback is supplied only to literal text. Its prefix
            // includes only released pairs, so no held event can be retargeted.
            activeDelivery = .process(proof.recipient)
        }
        let firstDown = FirstKeyDown()
        try await pair.run {
            try await send(
                { down, up, notBefore in
                    try await firstDown.run(beforeFirstKeyDown)
                    try await InputPacing.validate(notBefore: notBefore) {
                        if let editor = try attachedEditor?() {
                            guard let recipient, editor.authorizes(recipient, lifetime: process) else {
                                throw DriverError("the attached editor changed keyboard process", code: .staleTarget)
                            }
                            try editor.validateFocus()
                            activeDelivery = .process(editor.recipient)
                            return
                        }
                        if let path = try await savePanelPath?() { try usePath(path); return }
                        do { try await validateOriginal() } catch {
                            // The modal/focus change may arrive during ordinary
                            // validation. Only the same exact path proof can
                            // handle it; all other errors keep their meaning.
                            guard let failure = error as? any ClassifiedFailure,
                                [.blockedByModal, .staleTarget, .inputUnavailable].contains(failure.code),
                                let path = try await savePanelPath?()
                            else { throw error }
                            try usePath(path)
                        }
                    }
                    try onDispatch()
                    try pair.press(down, release: up)
                    onPosted()
                }, { try pair.release() })
        }
    }
}
