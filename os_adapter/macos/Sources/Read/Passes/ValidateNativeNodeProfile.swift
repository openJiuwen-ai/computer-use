import AppKit
import ApplicationServices

/// QUERY/PURE: preserve availability and requested native profile validation before collecting facts.
@MainActor enum ValidateNativeNodeProfilePass {
    static func run(
        access: NativeQueryAccess, fields: [String], followRelations: [String],
        relationPage: NativeInspectQuery.RelationPage?
    ) throws {
        try access.sharing.checkAvailable()
        guard fields.allSatisfy(AXAttributeProfile.field) else {
            throw DriverError("invalid native AX attribute name", code: .invalidRequest)
        }
        guard followRelations.allSatisfy(AXAttributeProfile.attribute) else {
            throw DriverError("invalid native AX relation name", code: .invalidRequest)
        }
        if let relationPage, !AXAttributeProfile.attribute(relationPage.property) {
            throw DriverError("invalid native AX page relation name", code: .invalidRequest)
        }

    }
}
