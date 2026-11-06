// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/// Observation-only association from already sampled AX child edges. A popup
/// container and its opener can expose the same list through different paths.
/// This establishes a logical host, not a physical parent or input permission.
enum NativeContentRelations {
    static func host<Node>(
        content: Node, excluding container: Node, roots: [Node], same: (Node, Node) -> Bool,
        role: (Node) -> String?, children: (Node) -> [Node]?
    ) -> Node? {
        var hosts = [Node]()
        for root in roots where !same(root, container) {
            var pending: [(node: Node, opened: Bool)] = [(root, false)]
            var seen = [(node: Node, opened: Bool)]()
            while let step = pending.popLast(), seen.count < 512 {
                guard !seen.contains(where: { same($0.node, step.node) && $0.opened == step.opened }),
                    let kind = role(step.node), !kind.isEmpty, kind != "AXUnknown"
                else { continue }
                seen.append(step)
                if same(step.node, content), step.opened {
                    if !hosts.contains(where: { same($0, root) }) { hosts.append(root) }
                    break
                }
                if !same(step.node, root), ["AXWindow", "AXSheet", "AXPopover", "AXApplication"].contains(kind) {
                    continue
                }
                let opened = step.opened || ["AXMenuButton", "AXPopUpButton", "AXComboBox", "AXMenuBarItem"].contains(kind)
                for child in children(step.node) ?? [] { pending.append((child, opened)) }
            }
        }
        return hosts.count == 1 ? hosts[0] : nil
    }
}
