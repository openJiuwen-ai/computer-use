// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

/// Closed set of native contracts. Only declaration formatting lives here;
/// readiness sampling and execution stay with the selected route.
enum NativeInputUnit: Equatable {
    case action(String, String?)
    case menuAction(String, String?)
    case selection(NativeRowSelection.Contract)
    case textValue(NativeElementControl.TextValueContract)
    case selectedText(NativeSelectedTextUnit.Contract)
    case receiverContext(NativeReceiverPreparation.Contract)
    case pointerFocus(NativePointerFocusUnit.TargetKind)
    case keyboard(
        NativeKeyboardIntent.Kind, NativeReplacementUnit.Selection? = nil,
        NativeKeyboardInterpreterEvidence.Policy = .existing)
    case pointer(NativePointerIntent.Kind)
    case desktop(String)
    case scroll
    case launch(NativeInstallation)

    func declaration(id: String, supported: Bool, readiness: NativeReadiness) -> [String: Any] {
        var wire: [String: Any] = [
            "id": id, "channel": "object",
            "supported": supported && (readiness.ready || readiness.preparationAllowed) ? "yes" : "no",
            "readiness": readiness.state,
            "pending": readiness.pending, "backgroundPreparation": "no", "globalInput": "no",
            "preparationImpact": preparationImpact
        ]
        switch self {
        case .launch: wire["unit"] = "launch_application"
        case .selection: wire["unit"] = "native_selection"
        case .action(let name, let description), .menuAction(let name, let description):
            wire["unit"] = "native_action"
            var action = ["name": name]
            if let description { action["description"] = description }
            wire["action"] = action
            // Object AXPress may need the original window's responder context,
            // not a text editor or a keyboard receiver.
            wire["backgroundPreparation"] = readiness.pending.contains("receiver") ? "yes" : "no"
        case .textValue(let contract): wire["unit"] = contract.unit
        case .selectedText, .receiverContext:
            if case .selectedText = self {
                wire["unit"] = "native_text_edit"
            } else {
                wire["unit"] = "prepare_receiver"
            }
            wire["channel"] = "editor"
            wire["backgroundPreparation"] = "yes"
        case .scroll: wire["unit"] = "native_scroll"
        case .pointerFocus(let target):
            wire["globalInput"] = target == .scalarText || !NativeInputAuthorization.globalInputRequested ? "no" : "unknown"
            wire["unit"] = "prepare_receiver"
            wire["channel"] = "pointer"
            wire["backgroundPreparation"] = "yes"
        case .keyboard(let kind, _, let policy):
            // A retained local-Web qualification requires scoped HID. Other
            // text receivers can change during preparation; native dispatch
            // requalifies their transport rather than claiming no global use.
            wire["globalInput"] = NativeInputAuthorization.globalInputRequested || policy == .localWebNoProcess ? "yes"
                : [.insert, .replace].contains(kind) ? "unknown" : "no"
            wire["unit"] = kind.unit
            wire["channel"] = "keyboard"
            wire["backgroundPreparation"] = readiness.pending.contains("foreground") ? "no" : "yes"
        case .pointer:
            wire["globalInput"] = NativeInputAuthorization.globalInputRequested ? "yes" : "no"
            wire["unit"] = "pointer_sequence"
            wire["channel"] = "pointer"
            wire["backgroundPreparation"] = "yes"
        case .desktop(let intent):
            wire["globalInput"] = "yes"
            let pointer = NativePointerIntent.Kind(rawValue: intent) != nil
            wire["unit"] = pointer ? "pointer_sequence" : "key_sequence"
            wire["channel"] = pointer ? "pointer" : "keyboard"
        }
        if !readiness.preparationAllowed || readiness.pending.contains("foreground") {
            wire["backgroundPreparation"] = "no"
        }
        return wire
    }

    /// Potential preparation resources of this complete recipe, not evidence
    /// of effects and not permission to use a transport. Unknown stays local
    /// to this candidate and cannot veto another independent mechanism.
    private var preparationImpact: [String: String] {
        var value = ["mouse": "no", "keyboard": "no", "activation": "no", "clipboard": "no"]
        switch self {
        case .textValue, .selection, .scroll, .launch, .desktop: break
        case .selectedText, .receiverContext:
            value["activation"] = "yes"
        case .action, .menuAction:
            value["activation"] = "unknown"
        case .pointerFocus, .pointer:
            value["mouse"] = "yes"
            value["activation"] = "yes"
        case .keyboard(let kind, _, _):
            value["activation"] = "yes"
            // Whole-cell replacement may enter an editor with a pointer.
            if kind == .replace { value["mouse"] = "unknown" }
            if kind == .paste { value["clipboard"] = "yes" }
        }
        return value
    }
}
