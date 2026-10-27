@main struct NativeActionTests {
    static func main() throws {
        precondition(
            NativeAction.boundedNames(["AXPress", "AXPress", "AXIncrement", "AXPress"]) == ["AXPress", "AXIncrement"])
        precondition(NativeAction.boundedNames(Array(repeating: "AXPress", count: 300)) == ["AXPress"])
        precondition(NativeAction.boundedNames(["", "AXPress", ""]) == ["AXPress"])
        precondition(NativeAction.boundedNames([]) == [])
        precondition(NativeAction.boundedNames((0..<128).map { "Action\($0)" })?.count == 128)
        precondition(NativeAction.boundedNames((0...128).map { "Action\($0)" }) == nil)
        precondition(
            NativeAction.matches([.init(name: "AXPress", description: nil)], name: "AXPress", description: "点击"))
        precondition(
            NativeAction.matches([.init(name: "AXPress", description: "press")], name: "AXPress", description: "点击"))
        precondition(
            !NativeAction.matches([.init(name: "AXPick", description: "点击")], name: "AXPress", description: "点击"))
        precondition(
            !NativeAction.matches(
                [.init(name: "custom", description: "changed")], name: "custom", description: "selected"))
        precondition(!NativeAction.matches(nil, name: "AXPress", description: nil))
        print("PASS native action name deduplication and catalogue bounds")
    }
}
