// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Carbon
import CoreGraphics

/// Encode real CGEvents without posting them or asking for input permissions.
@main enum NativeShortcutGlyphTests {
    @MainActor static func main() throws {
        let equal = CGKeyCode(kVK_ANSI_Equal), slash = CGKeyCode(kVK_ANSI_Slash)
        let keypadPlus = CGKeyCode(kVK_ANSI_KeypadPlus), rightBracket = CGKeyCode(kVK_ANSI_RightBracket)
        let keyboardType: UInt32 = 198
        // The ABC, German and Dvorak samples were read from TIS without selecting another layout.
        func abc(_ glyph: String, _ requiresShift: Bool) -> (CGKeyCode, Bool, UInt32)? {
            Input.glyphKey(glyph, requiresShift: requiresShift) { code, shifted in
                switch (code, shifted) {
                case (keypadPlus, _), (equal, true): return "+"
                case (slash, true): return "?"
                default: return nil
                }
            }.map { ($0.code, $0.shifted, keyboardType) }
        }
        let plain = try Input.chord("literal-plus", resolveGlyph: abc)
        let explicit = try Input.chord("shift+literal-plus", resolveGlyph: abc)
        let command = try Input.chord("super+literal-plus", resolveGlyph: abc)
        precondition(plain == explicit && plain.code == equal && plain.modifiers == ["shift"])
        precondition(command.code == equal && command.modifiers == ["super", "shift"])
        let question = try Input.chord("alt+?", resolveGlyph: abc)
        precondition(question.code == slash && question.modifiers == ["alt", "shift"])
        precondition(NativeKeyboardIntent.usesWindowCommand(.key, key: "super+literal-plus"))
        precondition(!NativeKeyboardIntent.usesWindowCommand(.key, key: "literal-plus"))
        func german(_ glyph: String, _ requiresShift: Bool) -> (CGKeyCode, Bool, UInt32)? {
            Input.glyphKey(glyph, requiresShift: requiresShift) { code, shifted in
                switch (code, shifted) {
                case (keypadPlus, _), (rightBracket, false): return "+"
                case (CGKeyCode(kVK_ANSI_Minus), true): return "?"
                default: return nil
                }
            }.map { ($0.code, $0.shifted, keyboardType) }
        }
        let unshifted = try Input.chord("literal-plus", resolveGlyph: german)
        precondition(unshifted.code == rightBracket && unshifted.modifiers.isEmpty && unshifted.keyboardType == keyboardType)
        let germanQuestion = try Input.chord("?", resolveGlyph: german)
        precondition(germanQuestion.code == CGKeyCode(kVK_ANSI_Minus) && germanQuestion.modifiers == ["shift"])
        rejects(.inputUnavailable) { _ = try Input.chord("shift+literal-plus", resolveGlyph: german) }
        let dvorak = Input.glyphKey("+", requiresShift: false) { code, shifted in
            code == keypadPlus || code == rightBracket && shifted ? "+" : nil
        }
        precondition(dvorak?.code == rightBracket && dvorak?.shifted == true)
        rejects(.inputUnavailable) { _ = try Input.chord("?", resolveGlyph: { _, _ in nil }) }
        let onlyKeypad = Input.glyphKey("+", requiresShift: false) { code, _ in code == keypadPlus ? "+" : nil }
        precondition(onlyKeypad == nil)
        for malformed in ["+", "super++", "ctrl+", "shift+shift+literal-plus", "super+?+?", "question"] {
            rejects(.invalidRequest) { _ = try Input.chord(malformed, resolveGlyph: abc) }
        }

        let old = try Input.chord("shift+=") { _, _ in preconditionFailure("physical chord queried the layout") }
        precondition(old.code == equal && old.modifiers == ["shift"] && old.keyboardType == nil)
        let source = CGEventSource(stateID: .privateState)!
        let events = try Input.keyEvents(plain, source: source)
        let ordered = events.downs + events.ups.reversed()
        precondition(ordered.allSatisfy { $0.getIntegerValueField(.keyboardEventKeyboardType) == Int64(keyboardType) })
        precondition(ordered.map(\.type) == [.flagsChanged, .keyDown, .keyUp, .flagsChanged])
        precondition(ordered.map { $0.getIntegerValueField(.keyboardEventKeycode) } == [56, Int64(equal), Int64(equal), 56])
        precondition(ordered.map(\.flags) == [.maskShift, .maskShift, .maskShift, []])
        let commandEvents = try Input.keyEvents(command, source: source)
        precondition(commandEvents.downs.map(\.flags) == [.maskCommand, [.maskCommand, .maskShift], [.maskCommand, .maskShift]])
        precondition(commandEvents.ups.reversed().map(\.flags) == [[.maskCommand, .maskShift], .maskCommand, []])
        print("PASS physical and literal chord encoding, keyboard layout, keypad exclusion, and paired Shift events")
    }

    static func rejects(_ code: FailureCode, _ action: () throws -> Void) {
        do { try action(); preconditionFailure("invalid chord accepted") }
        catch let error as DriverError { precondition(error.code == code, "unexpected error: \(error)") }
        catch { preconditionFailure("unexpected error: \(error)") }
    }
}
