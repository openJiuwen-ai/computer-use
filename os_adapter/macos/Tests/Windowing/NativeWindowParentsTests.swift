import Foundation

@main struct NativeWindowParentsTests {
    @MainActor static func main() {
        typealias W = NativeWindowParents.Window
        let main = W(ref: "main", pid: 10, id: 1, parent: nil)
        let other = W(ref: "other", pid: 10, id: 2, parent: nil)
        let child = W(ref: "child", pid: 10, id: 3, parent: nil)
        let sheet = W(ref: "sheet", pid: 10, id: 4, parent: "main")
        let axOnly = W(ref: "ax", pid: 10, id: nil, parent: "sheet")
        let windows = [main, other, child, sheet, axOnly]
        for raw in [nil, [:], [1: 0, 2: 0, 3: 0, 4: 0]] as [[UInt32: UInt32]?] {
            let result = NativeWindowParents.supplement(windows, native: raw)
            precondition(result.parents == ["sheet": "main", "ax": "sheet"] && result.unresolved.isEmpty)
        }
        let attached = NativeWindowParents.supplement(windows, native: [1: 0, 2: 0, 3: 1, 4: 0])
        precondition(
            attached.parents == ["child": "main", "sheet": "main", "ax": "sheet"] && attached.unresolved.isEmpty)
        // Detaching/reparenting is a fresh fact; no cached owner or name rule.
        let detached = NativeWindowParents.supplement(windows, native: [3: 0])
        precondition(detached.parents["child"] == nil)
        precondition(NativeWindowParents.supplement(windows, native: [3: 2]).parents["child"] == "other")
        for raw in [[3: 3], [3: 99], [1: 3, 3: 1], [3: 4, 4: 3]] as [[UInt32: UInt32]] {
            let result = NativeWindowParents.supplement(windows, native: raw)
            precondition(!result.unresolved.isEmpty && result.parents["main"] == nil)
            precondition(result.parents["sheet"] == "main", "Native facts must not replace conflicting AX evidence")
        }
        let conflicting = NativeWindowParents.supplement(windows, native: [4: 2])
        precondition(conflicting.parents["sheet"] == "main" && conflicting.unresolved == ["sheet"])
        let foreign = W(ref: "foreign", pid: 20, id: 5, parent: nil)
        let crossProcess = NativeWindowParents.supplement(windows + [foreign], native: [3: 5])
        precondition(crossProcess.parents["child"] == nil && crossProcess.unresolved == ["child"])
        let duplicate = W(ref: "duplicate", pid: 10, id: 1, parent: nil)
        precondition(NativeWindowParents.supplement(windows + [duplicate], native: [3: 1]).unresolved == ["child"])
        let mixedCycle = [main, W(ref: "child", pid: 10, id: 3, parent: "main")]
        let mixed = NativeWindowParents.supplement(mixedCycle, native: [1: 3])
        precondition(mixed.parents == ["child": "main"] && mixed.unresolved == ["main"])
        let cycle = [main, child, W(ref: "leaf", pid: 10, id: 6, parent: nil)]
        let rejected = NativeWindowParents.supplement(cycle, native: [1: 3, 3: 1, 6: 3])
        precondition(rejected.parents.isEmpty && rejected.unresolved == ["main", "child", "leaf"])
        print(
            """
            PASS native parent relationships: attached/detached, independent windows, AX preservation, \
            identity, conflicts and cycles
            """)
    }
}
