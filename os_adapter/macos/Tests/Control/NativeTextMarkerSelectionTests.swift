import Foundation
import ApplicationServices

@main struct NativeTextMarkerSelectionTests {
    static func main() {
        func range(_ start: UInt8, _ end: UInt8) -> AXTextMarkerRange {
            AXTextMarkerRangeCreate(nil, AXTextMarkerCreate(nil, [start], 1), AXTextMarkerCreate(nil, [end], 1))
        }
        let complete = range(1, 8)
        var whole: AXRead<CFTypeRef> = .value(complete)
        var selected: AXRead<CFTypeRef> = .value(range(1, 8))
        var valid = true
        func sample() -> NativeTextMarkerSelection? {
            .read(whole: { whole }, selected: { selected }, valid: { _ in valid })
        }
        precondition(sample()?.full == true && sample()?.empty == false)
        // Same span length in another document, partial and reversed native
        // ranges are not evidence that this exact editable root is selected.
        for other in [range(21, 28), range(1, 5), range(8, 1)] {
            selected = .value(other)
            precondition(sample()?.full == false)
        }
        for state: AXRead<CFTypeRef> in [.unsupported, .unavailable(.noValue)] {
            selected = state
            precondition(sample()?.full == false)
        }
        for state: AXRead<CFTypeRef> in [.invalid, .unavailable(.cannotComplete), .value("not a range" as CFString)] {
            selected = state
            precondition(sample() == nil)
        }
        selected = .value(complete)
        for state: AXRead<CFTypeRef> in [
            .unsupported, .unavailable(.noValue), .invalid, .unavailable(.cannotComplete),
            .value("not a range" as CFString)
        ] {
            whole = state
            precondition(sample() == nil)
        }
        whole = .value(complete)
        valid = false
        precondition(sample() == nil)
        valid = true
        var reads = 0
        let changed = NativeTextMarkerSelection.read(
            whole: {
                reads += 1
                return .value(reads == 1 ? complete : range(1, 9))
            }, selected: { selected }, valid: { _ in true })
        precondition(changed == nil && reads == 2)
        whole = .value(range(1, 1))
        selected = .value(range(1, 1))
        precondition(sample()?.empty == true && sample()?.full == true)
        print(
            """
            PASS marker selection preserves native identity; partial, foreign, invalid and changing ranges \
            cannot authorize replacement
            """)
    }
}
