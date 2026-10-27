@main struct LocalMenuAttachmentTests {
    static func main() throws {
        var roles = ["window": "AXWindow", "wrapper": "AXGroup", "menu": "AXMenu", "item": "AXMenuItem"]
        var edges = ["window": ["wrapper"], "wrapper": ["menu"], "menu": ["item"]]
        func check(_ ancestors: [String] = ["window", "wrapper", "menu"]) -> Bool {
            LocalMenuAttachment.confirms(
                leaf: "item", ancestors: ancestors, window: "window", same: { $0 == $1 }, role: { roles[$0] },
                children: { edges[$0] ?? [] })
        }
        precondition(check())
        edges["wrapper"] = []
        precondition(!check())
        edges["wrapper"] = ["menu"]
        roles["wrapper"] = "AXWindow"
        precondition(!check())
        roles["wrapper"] = "AXMenuBar"
        precondition(!check())
        roles["wrapper"] = "AXGroup"
        precondition(!check(["other", "menu"]))
        roles["item"] = "AXButton"
        precondition(!check())
        roles["wrapper"] = "AXMenuButton"
        roles["menu"] = "AXList"
        roles["item"] = "AXStaticText"
        precondition(check(), "combobox choices retain the original logical owner")
        edges["wrapper"] = []
        precondition(!check(), "a detached popup must not follow a cached path")
        edges["wrapper"] = ["menu"]
        roles["wrapper"] = "AXGroup"
        precondition(!check(), "an arbitrary cross-window list does not establish a control attachment")
        print("PASS: local menu attachment, detached/reparented menus and unrelated window boundaries")
    }
}
