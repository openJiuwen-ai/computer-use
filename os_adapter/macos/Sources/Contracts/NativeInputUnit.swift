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
    case cellSelection(NativeCellSelectionPreparation.Contract)
    case pointerFocus(NativePointerFocusUnit.TargetKind)
    case keyboard(
        NativeKeyboardIntent.Kind, NativeReplacementUnit.Selection? = nil,
        NativeKeyboardInterpreterEvidence.Policy = .existing)
    case applicationCommand(NativeApplicationKeyboardUnit.Command)
    case pointer(NativePointerIntent.Kind)
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
        case .selectedText, .receiverContext, .cellSelection:
            if case .selectedText = self {
                wire["unit"] = "native_text_edit"
            } else {
                wire["unit"] = "prepare_receiver"
            }
            wire["channel"] = "editor"
            wire["backgroundPreparation"] = "yes"
        case .scroll: wire["unit"] = "native_scroll"
        case .pointerFocus:
            wire["unit"] = "prepare_receiver"
            wire["channel"] = "pointer"
            wire["backgroundPreparation"] = "yes"
        case .keyboard(let kind, let replacement, _):
            wire["unit"] = kind.unit
            wire["channel"] = "keyboard"
            wire["backgroundPreparation"] = readiness.pending.contains("foreground") ? "no" : "yes"
            if kind == .replace, replacement == .structuredScalarShortcut, supported, readiness.ready {
                wire["keyboardEditing"] = kind.rawValue
            }
        case .applicationCommand:
            wire["unit"] = "key_sequence"
            wire["channel"] = "keyboard"
        case .pointer:
            wire["unit"] = "pointer_sequence"
            wire["channel"] = "pointer"
            wire["backgroundPreparation"] = "yes"
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
        case .textValue, .selection, .cellSelection, .scroll, .launch, .applicationCommand: break
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
