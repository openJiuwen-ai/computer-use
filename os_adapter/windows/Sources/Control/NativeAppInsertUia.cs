// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Windows.Automation;

namespace ComputerUse.Windows
{
    internal sealed partial class NativeUiaKeyboard
    {
        sealed partial class Sequence : INativeAppInsertRecipient
        {
            INativeKeyboardSequence INativeAppInsertRecipient.Sequence
            {
                get {
                    return this;
                }
            }
            NativeKeyboardState ReadPinnedInsertProperties(Request request)
            {
                if (element == null)
                    return null; // Retain native-only evidence; never upgrade by rediscovery.
                var properties = NativeOriginalTextProperties.Read(element, request);
                var sample =
                    new NativeKeyboardState { Role = properties.Role == null ? null : properties.Role.ProgrammaticName,
                                              Enabled = properties.Enabled };
                if (role == ControlType.Edit)
                    ReadTextCapability(element, sample, request, false);
                return sample;
            }
            NativeKeyboardState INativeAppInsertRecipient.Read(Request request)
            {
                return NativeAppInsertRecipientFailure.Query(
                    true, request,
                    () =>
                    {
                        Validate(request);
                        var actual = ReadPinnedInsertProperties(request);
                        if (actual != null &&
                            (actual.Role != (role == null ? null : role.ProgrammaticName) || actual.Enabled == false ||
                             role == ControlType.Edit && (actual.TextWritable == false ||
                                                          (selectionMechanism != null || directedCharacters) &&
                                                              (actual.Enabled != true || actual.TextWritable != true))))
                            throw new Failure(
                                "input_unavailable",
                                "the retained App recipient changed role, enabled or writable permission");
                        // Dynamic provider reads precede fresh focus and the final
                        // native queue. The returned role still describes the App.
                        ValidateDispatchReceiver(request);
                        ValidateDispatchFocus(request);
                        NativeRoute(request);
                        return new NativeKeyboardState { Native = target,    Label = target.Window.Native.Name,
                                                         Enabled = true,     Foreground = true,
                                                         WindowRoute = true, Focused = true };
                    });
            }
            NativeSelectionInsertOffer INativeAppInsertRecipient.OfferSelectionInsert(Request request)
            {
                return NativeAppInsertRecipientFailure.Query(
                    true, request,
                    () =>
                    {
                        var facts = ((INativeSelectionInsertSequence)this).Read(request);
                        return facts.Eligible && facts.Ready && facts.Enabled == true && facts.Writable == true
                                   ? new NativeSelectionInsertOffer(this, facts)
                                   : null;
                    });
            }
        }
    }
}
