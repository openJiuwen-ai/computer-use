import CoreGraphics
import Foundation

@main struct NativeKeyboardCleanupTests {
    static func main() {
        let source = CGEventSource(stateID: .privateState)!
        for code in [CGKeyCode(0), CGKeyCode(55), CGKeyCode(37)] {
            let up = CGEvent(keyboardEventSource: source, virtualKey: code, keyDown: false)!
            up.flags = [.maskCommand, .maskShift]
            var text: [UniChar] = [0xD83D, 0xDE42]
            up.keyboardSetUnicodeString(stringLength: text.count, unicodeString: &text)
            let oldSource = up.getIntegerValueField(.eventSourceStateID)
            let oldType = up.type
            NativeKeyboardCleanup.scopedUp(up)
            var count = 0, buffer = [UniChar](repeating: 0, count: 8)
            up.keyboardGetUnicodeString(
                maxStringLength: buffer.count, actualStringLength: &count, unicodeString: &buffer)
            let evidence: [String: Any] = [
                "code": code, "count": count, "units": Array(buffer.prefix(max(0, min(count, 8)))),
                "flags": up.flags.rawValue, "type": up.type.rawValue,
                "keycode": up.getIntegerValueField(.keyboardEventKeycode),
                "source": up.getIntegerValueField(.eventSourceStateID), "oldSource": oldSource
            ]
            FileHandle.standardError.write(
                try! JSONSerialization.data(withJSONObject: evidence, options: [.sortedKeys]))
            FileHandle.standardError.write(Data([10]))
            precondition(count == 0 && up.flags.isEmpty && up.type == oldType)
            precondition(up.getIntegerValueField(.keyboardEventKeycode) == Int64(code))
            precondition(up.getIntegerValueField(.eventSourceStateID) == oldSource)
        }
        print("PASS scoped HID cleanup: empty Unicode/flags with captured keycode/source/type, no event posted")
    }
}
