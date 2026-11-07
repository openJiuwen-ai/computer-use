// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices
import Foundation

/// A web document can change its reported location while its native text
/// space survives. Retain independent positions from that space and verify
/// their original live owners on a location conflict. This does not grant
/// focus, select another receiver or replace any target.
final class NativeDocumentLocation: Equatable {
    struct Query {
        let attribute: (AXUIElement, String) -> CFTypeRef?
        let markerOwner: (AXUIElement, AXTextMarker) -> AXUIElement?
        let clock: () -> TimeInterval

        static var live: Query {
            Query(
                attribute: { element, name in
                    bounded(element) {
                        var value: CFTypeRef?
                        guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success else {
                            return nil
                        }
                        return value
                    }
                },
                markerOwner: { document, marker in
                    bounded(document) {
                        var raw: CFTypeRef?
                        guard
                            AXUIElementCopyParameterizedAttributeValue(
                                document, "AXUIElementForTextMarker" as CFString, marker, &raw) == .success, let raw,
                            CFGetTypeID(raw) == AXUIElementGetTypeID()
                        else { return nil }
                        return unsafeDowncast(raw, to: AXUIElement.self)
                    }
                }, clock: { ProcessInfo.processInfo.systemUptime })
        }

        private static func bounded<T>(_ element: AXUIElement, read: () -> T?) -> T? {
            guard AXUIElementSetMessagingTimeout(element, 0.035) == .success else { return nil }
            defer { AXMessaging.configure(element) }
            return read()
        }
    }

    private struct Position {
        let marker: AXTextMarker
        let owner: AXUIElement
        let role: String
    }

    private let document: AXUIElement
    private let window: AXUIElement
    private let positions: [Position]

    private init(document: AXUIElement, window: AXUIElement, positions: [Position]) {
        self.document = document
        self.window = window
        self.positions = positions
    }

    static func == (lhs: NativeDocumentLocation, rhs: NativeDocumentLocation) -> Bool { lhs === rhs }

    static func capture(document: AXUIElement, window: AXUIElement, deadline: TimeInterval, query: Query = .live) throws
        -> NativeDocumentLocation?
    {
        var markers = [AXTextMarker]()
        for attribute in ["AXStartTextMarker", "AXEndTextMarker"] {
            guard try hasTime(deadline, query), let raw = query.attribute(document, attribute),
                CFGetTypeID(raw) == AXTextMarkerGetTypeID()
            else { return nil }
            markers.append(unsafeDowncast(raw, to: AXTextMarker.self))
        }
        guard !CFEqual(markers[0], markers[1]),
            try reaches(window, from: document, withoutInterveningDocument: true, deadline: deadline, query: query)
        else { return nil }
        var positions = [Position]()
        for marker in markers {
            guard try hasTime(deadline, query), let owner = query.markerOwner(document, marker),
                !CFEqual(owner, document), !positions.contains(where: { CFEqual($0.owner, owner) }),
                try hasTime(deadline, query), let role = query.attribute(owner, kAXRoleAttribute) as? String,
                try reaches(document, from: owner, withoutInterveningDocument: false, deadline: deadline, query: query)
            else { return nil }
            positions.append(Position(marker: marker, owner: owner, role: role))
        }
        guard try hasTime(deadline, query) else { return nil }
        return NativeDocumentLocation(document: document, window: window, positions: positions)
    }

    func confirms(target: AXUIElement, deadline: TimeInterval, query: Query = .live) throws -> Bool {
        for position in positions {
            guard try Self.hasTime(deadline, query), let owner = query.markerOwner(document, position.marker),
                CFEqual(owner, position.owner), try Self.hasTime(deadline, query),
                query.attribute(owner, kAXRoleAttribute) as? String == position.role,
                try Self.reaches(
                    document, from: owner, withoutInterveningDocument: false, deadline: deadline, query: query)
            else { return false }
        }
        // The addressed element can move while the provider resolves a marker.
        // Its current ownership is the last evidence this proof samples.
        return try Self.reaches(
            document, from: target, withoutInterveningDocument: false, deadline: deadline, query: query)
            && Self.reaches(window, from: document, withoutInterveningDocument: true, deadline: deadline, query: query)
            && Self.hasTime(deadline, query)
    }

    private static func hasTime(_ deadline: TimeInterval, _ query: Query) throws -> Bool {
        try RequestDiagnostics.checkCancellation()
        return query.clock() < deadline
    }

    private static func reaches(
        _ boundary: AXUIElement, from first: AXUIElement, withoutInterveningDocument: Bool, deadline: TimeInterval,
        query: Query
    ) throws -> Bool {
        var current = first
        var visited = Set<AXUIElement>()
        for _ in 0..<40 {
            guard try hasTime(deadline, query) else { return false }
            if CFEqual(current, boundary) {
                let role = withoutInterveningDocument ? kAXWindowRole : "AXWebArea"
                return query.attribute(current, kAXRoleAttribute) as? String == role
            }
            guard visited.insert(current).inserted, let role = query.attribute(current, kAXRoleAttribute) as? String,
                role != kAXWindowRole, (role == "AXWebArea") == (withoutInterveningDocument && CFEqual(current, first)),
                try hasTime(deadline, query), let parent = query.attribute(current, kAXParentAttribute),
                CFGetTypeID(parent) == AXUIElementGetTypeID()
            else { return false }
            current = unsafeDowncast(parent, to: AXUIElement.self)
        }
        return false
    }
}
