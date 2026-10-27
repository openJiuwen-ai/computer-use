import Foundation
import CoreGraphics

@main struct NativeProviderTextValueEvidenceTests {
    static func main() {
        typealias E = NativeProviderTextValueEvidence
        var assertions = 0
        func expect(_ condition: Bool, _ label: String) {
            assertions += 1
            precondition(condition, "assertion \(assertions): \(label)")
        }
        func original() -> E.Facts {
            .init(
                role: "AXTextField", subrole: .absent, valueWritable: true, enabled: true, editable: .absent,
                valueUTF16Length: 12, rangeLocation: 0, rangeLength: 1,
                bounds: .rectangle(CGRect(x: 200, y: 300, width: 9, height: 19)),
                scope: .init(
                    presenterPID: 7, elementPublicPID: 7, elementActualPID: 7, windowPublicPID: 7, webAreaPublicPID: 7,
                    webAreaActualPID: 7, originalWindowID: 80, elementWindowID: 80, webAreaWindowID: 80,
                    reachedOriginalWindow: true, exactlyOneWebArea: true, elementProviderNodeID: "52",
                    webAreaProviderNodeID: "38"))
        }
        let accepted = E.prove(original())
        expect(accepted != nil, "actual nonempty native-field shape")
        var nonBMP = original()
        nonBMP.rangeLength = 2
        expect(E.prove(nonBMP) == accepted, "first non-BMP scalar uses both UTF16 units, same original contract")
        var moved = original()
        moved.valueUTF16Length = 30
        moved.bounds = .rectangle(CGRect(x: 420, y: 500, width: 16, height: 23))
        expect(E.prove(moved) == accepted, "normal text/geometry changes do not enter contract equality")
        var explicitEditable = original()
        explicitEditable.editable = .value(true)
        expect(E.prove(explicitEditable) == accepted, "explicit editable agrees with positive evidence")
        var emptySubrole = original()
        emptySubrole.subrole = .value("")
        expect(E.prove(emptySubrole) == accepted, "known empty ordinary subrole")

        func reject(_ label: String, _ change: (inout E.Facts) -> Void) {
            var value = original()
            change(&value)
            expect(E.prove(value) == nil, label)
        }
        for role in ([nil, "AXComboBox", "AXTextArea", "AXStaticText", "AXGroup"] as [String?]) {
            reject("not ordinary original single-line field: \(role ?? "unknown")") { $0.role = role }
        }
        for subrole in [
            E.OptionalAttribute<String>.unknown, .value("AXSearchField"), .value("AXSecureTextField"),
            .value("AXUninterpreted")
        ] { reject("unknown/search/secure/uninterpreted subrole") { $0.subrole = subrole } }
        reject("readonly guard despite positive range") { $0.valueWritable = false }
        reject("unknown writable capability") { $0.valueWritable = nil }
        reject("disabled control despite positive range") { $0.enabled = false }
        reject("unknown enabled evidence") { $0.enabled = nil }
        reject("explicitly noneditable field") { $0.editable = .value(false) }
        reject("failed editable query is not absent") { $0.editable = .unknown }
        for length in ([nil, 0, -1] as [Int?]) {
            reject("empty/unknown value has no new independent positive") { $0.valueUTF16Length = length }
        }
        reject("range outside original value") {
            $0.valueUTF16Length = 1
            $0.rangeLength = 2
        }
        for location in [-1, 1] { reject("range starts outside tested prefix") { $0.rangeLocation = location } }
        for length in [0, 3] { reject("range is not a valid first scalar query") { $0.rangeLength = length } }
        reject("attribute name alone/no actual range result") { $0.bounds = .unavailable(-25205) }
        reject("cannotComplete query") { $0.bounds = .unavailable(-25204) }
        reject("noValue query") { $0.bounds = .unavailable(-25212) }
        reject("success with wrong CF/AXValue shape") { $0.bounds = .malformed }
        for rect in [
            CGRect.zero, CGRect(x: 0, y: 1117, width: 0, height: 0), CGRect(x: 200, y: 300, width: 0, height: 19),
            CGRect(x: 200, y: 300, width: 9, height: 0), CGRect(x: 200, y: 300, width: -9, height: 19),
            CGRect(x: 200, y: 300, width: 9, height: -19), CGRect.null, CGRect.infinite,
            CGRect(x: CGFloat.nan, y: 300, width: 9, height: 19),
            CGRect(x: 200, y: CGFloat.infinity, width: 9, height: 19)
        ] {
            reject("empty/caret/negative/nonfinite/null bounds are not atomic positive") {
                $0.bounds = .rectangle(rect)
            }
        }
        reject("readonly/disabled state cannot be masked by a good rectangle") {
            $0.valueWritable = false
            $0.enabled = false
        }
        reject("same-role ARIA rich/plain/role-only success-empty runtime shape") {
            $0.bounds = .rectangle(CGRect(x: 0, y: 1117, width: 0, height: 0))
        }
        reject("same-role native-empty cannot be distinguished and is left to old routes") {
            $0.valueUTF16Length = 0
            $0.bounds = .rectangle(CGRect(x: 0, y: 120, width: 0, height: 0))
        }
        reject("native BoundPropertyField lacks provider marker") { $0.scope.elementProviderNodeID = nil }
        reject("inert native SelectedText object has no provider/WebArea proof") {
            $0.scope.elementProviderNodeID = nil
            $0.scope.webAreaProviderNodeID = nil
        }
        reject("missing WebArea provider marker") { $0.scope.webAreaProviderNodeID = nil }
        for id in ([nil, "", "0", "-1", "+52", " 52", "52x", "2147483648", "9999999999999999999999"] as [String?]) {
            reject("invalid provider-specific node marker \(id ?? "unknown")") { $0.scope.elementProviderNodeID = id }
        }
        reject("editor marker cannot equal its WebArea marker") { $0.scope.elementProviderNodeID = "38" }
        reject("nonpositive presenter") { $0.scope.presenterPID = 0 }
        reject("public editor is foreign") { $0.scope.elementPublicPID = 8 }
        reject("public editor owner unknown") { $0.scope.elementPublicPID = nil }
        reject("actual editor is a remote service") { $0.scope.elementActualPID = 8 }
        reject("actual editor unknown") { $0.scope.elementActualPID = nil }
        reject("original window owner changed") { $0.scope.windowPublicPID = 8 }
        reject("original window owner unknown") { $0.scope.windowPublicPID = nil }
        reject("WebArea public owner changed") { $0.scope.webAreaPublicPID = 8 }
        reject("WebArea public owner unknown") { $0.scope.webAreaPublicPID = nil }
        reject("WebArea actual owner remote") { $0.scope.webAreaActualPID = 8 }
        reject("WebArea actual owner unknown") { $0.scope.webAreaActualPID = nil }
        for id in ([nil, 0, 81] as [UInt32?]) {
            reject("original window missing/replaced") { $0.scope.originalWindowID = id }
        }
        for id in ([nil, 0, 81] as [UInt32?]) {
            reject("element native window missing/different") { $0.scope.elementWindowID = id }
        }
        for id in ([nil, 0, 81] as [UInt32?]) {
            reject("WebArea native window missing/different") { $0.scope.webAreaWindowID = id }
        }
        reject("parent chain failed/changed before original window") { $0.scope.reachedOriginalWindow = false }
        reject("missing or multiple WebArea ancestry") { $0.scope.exactlyOneWebArea = false }

        var changedNode = original()
        changedNode.scope.elementProviderNodeID = "53"
        expect(
            E.prove(changedNode) != nil && E.prove(changedNode) != accepted,
            "provider object identity remains in contract")
        var changedArea = original()
        changedArea.scope.webAreaProviderNodeID = "39"
        expect(E.prove(changedArea) != nil && E.prove(changedArea) != accepted, "WebArea identity remains in contract")
        print(
            """
            PASS provider text-value evidence: \(assertions) assertions; nonempty positive, actual same-role \
            negatives, unknowns, scope/provider identity and stable equality
            """)
    }
}
