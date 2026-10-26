import Foundation

@main struct AXAttributeProfileTests {
    static func main() {
        for field in ["AXRole", "AXEdited", "AXAuditMetadata", "AXChildren", "settable.AXValue"] {
            assert(AXAttributeProfile.field(field), "explicit raw fields must not depend on the default profile")
        }
        for field in [
            "", "AX", "AXTitle\n", "performAction", "settable.AXTitle", "AX" + String(repeating: "x", count: 255)
        ] { assert(!AXAttributeProfile.field(field)) }
        assert(AXAttributeProfile.attribute("AXContents") && AXAttributeProfile.attribute("AXAuditRelations"))
        assert(!AXAttributeProfile.attribute("settable.AXValue"))
        print("PASS AX profiles: bounded native names permit explicit provider metadata and relations")
    }
}
