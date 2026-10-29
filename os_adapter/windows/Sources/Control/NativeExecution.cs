// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.InteropServices;

namespace ComputerUse.Windows
{
    // The native adapter supplies facts and exactly one addressed mutation.
    // Python chooses which declared action represents the user's intention.
    internal sealed class NativeExecution
    {
        static readonly string[] ObjectCapabilities =
        {
            "nativeBoundContracts",
            "nativeUnitReadinessV2",
            "nativeActionIntents",
            "nativeWindowScope",
            "nativeTextReplacement",
            "desktopCoordination",
            "nativeClickPipelineV1"
        };
        internal string[] Capabilities
        {
            get
            {
                return ObjectCapabilities.Concat(keyboard == null ? new string[0] : new[] { "nativeKeyboardIntents" }).Concat(keyboard != null && keyboard.SupportsText ? new[] { "nativeTextInput" } : new string[0]).Concat(pointer == null ? new string[0] : new[] { "nativePointer" }).Concat(pointer != null && pointer.SupportsElements ? new[] { "nativeElementPointer" } : new string[0]).Concat(desktopKeyboard != null && pointer != null && pointer.SupportsDesktop ? new[] { "nativeDesktopInput" } : new string[0]).Concat(currentReceiver == null ? new string[0] : new[] { "nativeCurrentReceiver" }).Concat(scroll == null ? new string[0] : new[] { "nativeScrollPattern" }).ToArray();
            }
        }

        readonly INativeObjectProvider provider;
        readonly NativeObjectPipeline objects;
        readonly NativeClickPipeline clicks;
        readonly NativeKeyboard keyboard;
        readonly NativeKeyboard desktopKeyboard;
        readonly NativePointer pointer;
        readonly NativeScroll scroll;
        readonly INativeCurrentReceiverProvider currentReceiver;
        internal NativeExecution(INativeObjectProvider provider, NativeKeyboard keyboard = null, NativePointer pointer = null, NativeScroll scroll = null, NativeKeyboard desktopKeyboard = null, INativeCurrentReceiverProvider currentReceiver = null)
        {
            this.provider = provider;
            this.objects = new NativeObjectPipeline(provider);
            this.keyboard = keyboard;
            this.pointer = pointer;
            this.scroll = scroll;
            this.desktopKeyboard = desktopKeyboard;
            this.currentReceiver = currentReceiver;
            this.clicks = new NativeClickPipeline(objects, pointer);
        }

        internal IDisposable Acquire(Request r)
        {
            return provider.Acquire(r);
        }

        internal void Validate(Dictionary<string, object> intent, Dictionary<string, object> target)
        {
            if (pointer != null && (NativePointer.Intent(intent, target) || pointer.ElementIntent(intent, target)))
                pointer.Validate(intent, target);
            else if (target["surface"] == null)
            {
                if (target["capture"] == null)
                    throw new Failure("invalid_request", "desktop input requires an observed desktop capture");
                if (desktopKeyboard == null)
                    throw new Failure("mode_unsupported", "desktop input requires exclusive mode and a desktop keyboard provider");
                if (!new[]
                {
                    "key",
                    "insert"
                }.Contains((string)intent["kind"]))
                    throw new Failure("invalid_request", "desktop keyboard supports key and insert intents");
                desktopKeyboard.ValidateIntent(intent);
            }
            else if (target["capture"] != null)
                throw new Failure("mode_unsupported", "this native unit does not implement the captured-image intent");
            else if ((string)intent["kind"] == "scroll" && scroll != null)
                NativeScroll.Validate(intent);
            else if (KeyboardIntent(intent) && keyboard != null)
                keyboard.ValidateIntent(intent);
            else
                ValidateIntent(intent);
        }

        static bool KeyboardIntent(Dictionary<string, object> intent)
        {
            return new[]
            {
                "focus",
                "key",
                "insert",
                "paste"
            }.Contains((string)intent["kind"]);
        }

        internal static void ValidateIntent(Dictionary<string, object> intent)
        {
            string kind = Wire.Str(intent, "kind");
            var p = Wire.Object(intent["parameters"]);
            if (kind == "replace")
            {
                Wire.Keys(p, "text");
                string text = Wire.Str(p, "text", true);
                NativeText.Validate(text);
            }
            else if (kind == "action")
            {
                Wire.Keys(p, "action");
                if (Wire.Str(p, "action").Length > 16384)
                    throw new Failure("invalid_request", "native action name is too long");
            }
            else if (kind == "openMenu")
                Wire.Keys(p, "");
            else if (kind == "click")
            {
                Wire.Keys(p, "", "button,count");
                if (p.ContainsKey("button") && !new[]
                {
                    "left",
                    "right",
                    "middle"
                }.Contains(Wire.Str(p, "button")))
                    throw new Failure("invalid_request", "invalid click button");
                if (p.ContainsKey("count"))
                    Wire.Num(p, "count", 1, 2, true);
                if (p.ContainsKey("button") && (string)p["button"] != "left" || p.ContainsKey("count") && Convert.ToInt32(p["count"]) != 1)
                    throw new Failure("mode_unsupported", "this object unit cannot synthesize right or repeated pointer clicks");
            }
            else
                throw new Failure("mode_unsupported", "this Windows native slice provides addressed actions and whole-value replacement only");
        }

        internal object InspectCurrentReceiver(NativeWriteOperation operation, Dictionary<string, object> target, Request r)
        {
            if (currentReceiver == null)
                throw new Failure("mode_unsupported", "native current receiver discovery is unavailable");
            if (operation.Dispatched)
                throw new Failure("execution_unknown", "current receiver discovery cannot follow dispatched input");
            if (operation.Selected != null)
                throw new Failure("invalid_request", "current receiver discovery must precede input contract selection");
            var evidence = currentReceiver.Read(target, r);
            r.Check();
            return Wire.Obj("kind", "inputContext", "binding", new Dictionary<string, object>(target), "observed", "observation:" + Guid.NewGuid().ToString("N"), "targetLabel", "Current receiver container", "identity", "yes", "withinScope", "yes", "enabled", "unknown", "blockedBy", new string[0], "primitives", new object[0], "within", new string[0], "input", Wire.Obj("foregroundSurface", null, "keyboardSurface", null, "focusedNode", null, "focusAncestors", new string[0], "editor", null, "editorOwner", null, "selectionKnown", "unknown", "geometryCurrent", "unknown", "windowRoute", "unknown", "currentReceiver", evidence.WireValue()));
        }

        internal object Inspect(NativeWriteOperation operation, Dictionary<string, object> target, string[] within, Request r)
        {
            operation.Bind(target, within);
            operation.Contracts.Clear();
            if (NativePointer.Intent(operation.Intent, target) && pointer != null)
                return pointer.Inspect(operation, target, within, r);
            if (target["surface"] == null && desktopKeyboard != null)
                return desktopKeyboard.Inspect(operation, target, within, r);
            if ((string)operation.Intent["kind"] == "scroll" && scroll != null)
                return scroll.Inspect(operation, target, within, r);
            if (KeyboardIntent(operation.Intent) && keyboard != null)
                return keyboard.Inspect(operation, target, within, r);
            if (target["node"] == null)
                throw new Failure("invalid_request", "object input needs an observed native element");
            var clickProvider = operation.ClickV1 ? provider as INativeClickObjectProvider : null;
            var state = clickProvider == null ? provider.Read(target, within, r) : clickProvider.ReadClick(target, within, r);
            r.Check();
            Dictionary<string, object> pointerFacts = null;
            string pointerUnavailable = null;
            if (pointer != null && pointer.ElementIntent(operation.Intent, target))
            {
                try
                {
                    pointerFacts = Wire.Object(pointer.Inspect(operation, target, within, r));
                }
                catch (Failure error)
                {
                    var options = Wire.Object(operation.Intent["parameters"]);
                    bool ordinary = (!options.ContainsKey("button") || (string)options["button"] == "left") && (!options.ContainsKey("count") || Convert.ToInt32(options["count"]) == 1);
                    if (!ordinary || error.Code != "input_unavailable")
                        throw;
                    pointerUnavailable = error.Message;
                // Geometry absence may leave an independent object action.
                // No pointer input or focus preparation has been sent.
                }
            }

            var primitives = new List<object>();
            string kind = (string)operation.Intent["kind"];
            Action<string, string, bool?> declare = (unit, action, supported) =>
            {
                var contract = new NativeInputContract
                {
                    Id = "primitive:" + Guid.NewGuid().ToString("N"),
                    Target = new Dictionary<string, object>(target),
                    Within = (string[])within.Clone(),
                    Unit = unit,
                    Action = action,
                    Role = state.Role
                };
                operation.Contracts.Add(contract.Id, contract);
                var item = Wire.Obj("id", contract.Id, "unit", unit, "channel", "object", "supported", supported == true ? "yes" : supported == false ? "no" : "unknown", "readiness", supported == true && state.Enabled == true ? "yes" : "no", "pending", state.Enabled == true ? new string[0] : new[] { "enabled" }, "backgroundPreparation", "no");
                if (action != null)
                    item["action"] = Wire.Obj("name", action, "description", action);
                if (operation.ClickV1)
                {
                    contract.Click = new NativeClickOffer("windows.uia." + action, "semantic", "object_action", supported == true && state.Enabled == true ? "ready" : state.Enabled == null ? "insufficientEvidence" : "unavailable", supported == true && state.Enabled == true ? null : "The selected UIA action is not currently enabled.", new string[0]);
                    item["click"] = contract.Click.WireValue();
                }

                primitives.Add(item);
            };
            if (kind == "replace")
                declare("native_text_edit", null, state.Replaceable);
            else if (state.ActionsComplete)
                foreach (string action in state.Actions)
                    declare("native_action", action, true);
            var result = Wire.Obj("kind", "inputContext", "binding", new Dictionary<string, object>(target), "observed", "observation:" + Guid.NewGuid().ToString("N"), "targetLabel", String.IsNullOrWhiteSpace(state.Label) ? "Native control" : state.Label, "identity", "yes", "withinScope", "yes", "enabled", state.Enabled == true ? "yes" : state.Enabled == false ? "no" : "unknown", "blockedBy", new string[0], "primitives", primitives, "nativeRole", state.Role, "actionsComplete", state.ActionsComplete, "within", within, "input", Wire.Obj("foregroundSurface", null, "keyboardSurface", null, "focusedNode", null, "focusAncestors", new string[0], "editor", null, "editorOwner", null, "selectionKnown", "unknown", "geometryCurrent", "unknown", "windowRoute", "unknown"));
            if (kind == "replace" && keyboard != null && keyboard.SupportsText)
            {
                try
                {
                    var keyboardFacts = Wire.Object(keyboard.Inspect(operation, target, within, r));
                    keyboardFacts["primitives"] = primitives.Concat(Wire.Array(keyboardFacts["primitives"])).ToArray();
                    result = keyboardFacts;
                }
                catch (Failure error)
                {
                    r.Check();
                    // A QUERY failure in the optional keyboard route cannot
                    // erase an independently declared ValuePattern edit.
                    // Identity, modality and cancellation still propagate;
                    // object execution performs its usual final native guards.
                    if (state.Replaceable != true || r.Effect || r.Dispatched || !new[]
                    {
                        "input_unavailable",
                        "mode_unsupported",
                        "observation_incomplete",
                        "ambiguous_target"
                    }.Contains(error.Code))
                        throw;
                    result["unavailableReason"] = "The optional keyboard route is unavailable: " + error.Message;
                }
                catch (COMException)
                {
                    r.Check();
                    if (state.Replaceable != true || r.Effect || r.Dispatched)
                        throw;
                    result["unavailableReason"] = "The optional keyboard provider query failed; the independent object replacement remains available.";
                }
            }

            if (pointerFacts != null)
            {
                result["primitives"] = primitives.Concat(Wire.Array(pointerFacts["primitives"])).ToArray();
                result["input"] = pointerFacts["input"];
            }

            var unavailable = new[]
            {
                state.ClickMetadataUnavailable,
                pointerUnavailable
            }.Where(x => x != null).ToArray();
            if (unavailable.Length != 0)
                result["unavailableReason"] = String.Join(" ", unavailable);
            return result;
        }

        internal object Execute(NativeWriteOperation operation, Dictionary<string, object> command, Request r, string operationId = null, long? step = null)
        {
            if (operation.ClickV1)
                return clicks.Execute(operation, command, r, operationId, step);
            NativeInputContract selected;
            if (pointer != null && operation.Contracts.TryGetValue((string)command["primitiveId"], out selected) && selected.Unit == "pointer_sequence")
                return pointer.Execute(operation, command, r);
            if (scroll != null && (string)command["kind"] == "native_scroll")
                return scroll.Execute(operation, command, r);
            var keyboardUnit = Wire.Object(command["target"])["surface"] == null ? desktopKeyboard : keyboard;
            if (keyboardUnit != null && new[]
            {
                "key_sequence",
                "prepare_receiver",
                "clipboard_paste"
            }.Contains((string)command["kind"]))
                return keyboardUnit.Execute(operation, command, r);
            return objects.Execute(operation, command, r);
        }
    }
// NativeDispatchPipeline for addressed UIA objects. NativeRuntime owns the
// cross-request resource scope and step journal. These local passes never
// choose another route or replay after a provider callback.
}
