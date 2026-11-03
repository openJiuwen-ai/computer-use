// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation
import ApplicationServices

@main enum NativeQtInlineListHitTests {
    typealias Evidence = NativeQtInlineListHitEvidence
    typealias Node = Evidence.Node<String>
    typealias Sibling = Evidence.Sibling<String>
    static let point = CGPoint(x: 150, y: 150)
    static let listID = "QApplication.TestDialog.popup.QListWidget"
    static let targetID = listID + ".qt_scrollarea_viewport.QWidget.QLabel"
    static let hitID = "QApplication.TestDialog.content.QWidget.QComboBox"

    static func node(
        _ object: String, role: String, identifier: String?, pid: Int32 = 42, windowID: UInt32 = 71,
        frame: CGRect? = CGRect(x: 100, y: 100, width: 100, height: 100)
    ) -> Node {
        .init(object: object, role: role, identifier: identifier, pid: pid, windowID: windowID, frame: frame)
    }
    static func facts(
        target: Node? = nil, list: Node? = nil, hit: Node? = nil,
        leaf: Bool = true, targetParent: Bool = true, listParent: Bool = true, hitParent: Bool = true,
        windowChildren: [Sibling]? = nil, listChildren: [Sibling]? = nil
    ) -> Evidence.Facts<String> {
        let outside = CGRect(x: 0, y: 0, width: 10, height: 10)
        return .init(
            point: point, pid: 42, windowID: 71,
            target: target ?? node("target", role: kAXStaticTextRole, identifier: targetID),
            list: list ?? node("list", role: kAXListRole, identifier: listID),
            hit: hit ?? node("hit", role: kAXMenuButtonRole, identifier: hitID),
            targetIsLeaf: leaf, targetParentIsList: targetParent,
            listParentIsWindow: listParent, hitParentIsWindow: hitParent,
            windowChildren: windowChildren ?? [
                .init(object: "hit", frame: CGRect(x: 100, y: 100, width: 100, height: 100)),
                .init(object: "list", frame: CGRect(x: 100, y: 100, width: 100, height: 100)),
                .init(object: "chrome", frame: outside)
            ],
            listChildren: listChildren ?? [
                .init(object: "target", frame: CGRect(x: 100, y: 100, width: 100, height: 100)),
                .init(object: "otherRow", frame: outside)
            ])
    }
    static func proves(_ facts: Evidence.Facts<String>) -> Bool { Evidence.proves(facts, same: ==) }

    static func main() {
        // The identifier signature remains local to the inline-list hit proof;
        // it must not be broadened to classify arbitrary custom Qt classes.
        for identifier in [targetID, hitID, "QApplication.Dialog.qt_scrollarea_viewport.custom_toggle"] {
            precondition(NativeQtAccessibilityIdentifier.isWidgetPath(identifier))
        }
        for identifier: String? in [
            nil, "", "QApplication", "QApplication.CustomView", "QApplication.CustomView.control",
            "Other.QApplication.QWidget.check", "OtherApplication.QWidget.check", "QApplication..QWidget.check",
            "QApplication.QWidget.check.", "QApplication.CustomQWidget.check"
        ] {
            precondition(!NativeQtAccessibilityIdentifier.isWidgetPath(identifier))
        }
        precondition(proves(facts()), "a current raised Qt inline-list leaf must survive its provider's lower hit")
        let rect = CGRect(x: 100, y: 100, width: 100, height: 100)
        let absent = CGRect(x: 0, y: 0, width: 10, height: 10)
        let higher = [Sibling(object: "hit", frame: rect), .init(object: "list", frame: rect)]
        let negatives = [
            facts(target: node("target", role: kAXStaticTextRole, identifier: nil)),
            facts(target: node("target", role: kAXStaticTextRole, identifier: "App.List.Row")),
            facts(hit: node("hit", role: kAXMenuButtonRole, identifier: "App.Control")),
            facts(hit: node("hit", role: kAXMenuButtonRole, identifier: "QApplication.OtherDialog.QWidget.QComboBox")),
            facts(target: node("target", role: kAXStaticTextRole, identifier: targetID, windowID: 72)),
            facts(list: node("list", role: kAXListRole, identifier: listID, pid: 43)),
            facts(hit: node("hit", role: kAXMenuButtonRole, identifier: hitID, windowID: 72)),
            facts(target: node("target", role: kAXStaticTextRole, identifier: targetID, frame: absent)),
            facts(list: node("list", role: kAXListRole, identifier: listID, frame: .zero)),
            facts(list: node("list", role: kAXListRole, identifier: listID, frame: nil)),
            facts(list: node("list", role: kAXGroupRole, identifier: listID)),
            facts(target: node("target", role: kAXButtonRole, identifier: targetID)),
            facts(leaf: false), facts(targetParent: false), facts(listParent: false), facts(hitParent: false),
            facts(windowChildren: [higher[0]]),
            facts(windowChildren: [higher[1], higher[0]]),
            facts(windowChildren: higher + [.init(object: "overlay", frame: rect)]),
            facts(windowChildren: higher + [.init(object: "unknown", frame: nil)]),
            facts(windowChildren: higher + [.init(object: "list", frame: rect)]),
            facts(listChildren: [.init(object: "otherRow", frame: rect)]),
            facts(listChildren: [.init(object: "target", frame: rect), .init(object: "otherRow", frame: rect)]),
            facts(listChildren: [.init(object: "target", frame: rect), .init(object: "otherRow", frame: nil)])
        ]
        for (index, evidence) in negatives.enumerated() {
            precondition(!proves(evidence), "insufficient or conflicting inline-list evidence \(index) cannot override a sibling hit")
        }

        let candidate = NativeElementHitSelection.Candidate(point: point, source: .center)
        let stacked = NativeElementHitSelection.select([candidate], canContinue: { _ in true }) { _ in
            NativeElementHitSelection.Observation<String>(
                evidence: .result(.success, relation: .providerStackedTarget), hit: "underlay", foreground: false)
        }!
        precondition(stacked.basis == .providerStackingGeometry && stacked.observation.hit == "underlay",
                     "provider proof must not masquerade as an exact AX hit")
        let ordinary = NativeElementHitSelection.select([candidate], canContinue: { _ in true }) { _ in
            NativeElementHitSelection.Observation<String>(
                evidence: .result(.success, relation: .otherControl), hit: "sibling", foreground: true)
        }!
        precondition(ordinary.basis == .rejected,
                     "ordinary same-window sibling hits retain their existing rejection")
        print("Qt inline-list hit regressions passed (27 cases)")
    }
}
