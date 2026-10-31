// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Text;

namespace ComputerUse.Windows
{
    // A selected keyboard paste recipe. Receiver policy stays in Python;
    // the unit cannot become typing, activation or a second paste attempt.
    internal static class NativePaste
    {
        internal static void Validate(string text)
        {
            if (Encoding.UTF8.GetByteCount(text) > 1024 * 1024 || text.IndexOf('\0') >= 0)
                throw new Failure("invalid_request", "paste requires at most 1 MiB of text without NUL");
            for (int n = 0; n < text.Length; n++)
            {
                if (Char.IsHighSurrogate(text[n]))
                {
                    if (++n >= text.Length || !Char.IsLowSurrogate(text[n]))
                        throw new Failure("invalid_request", "text contains an unpaired surrogate");
                }
                else if (Char.IsLowSurrogate(text[n]))
                    throw new Failure("invalid_request", "text contains an unpaired surrogate");
            }
        }

        internal static void Send(NativeKeyboard keyboard, INativeKeyboardTextProvider receiverProvider,
                                  INativeClipboardProvider clipboard, NativeWriteOperation operation,
                                  NativeInputContract contract, Request r, Dictionary<string, object> result,
                                  List<string> preparation)
        {
            var state = keyboard.Guard(contract, r);
            if (!state.Ready)
                throw new Failure("input_unavailable", "the selected paste recipient is not ready");
            var sequence = receiverProvider.BeginSequence(state, r);
            sequence.Validate(r);
            var consumerProvider = receiverProvider as INativePasteConsumerProvider;
            var consumer = consumerProvider == null ? null : consumerProvider.PasteConsumer(state, r);
            sequence.Validate(r); // A freshly captured consumer cannot replace the pinned receiver.
            var publication = sequence as INativePasteClipboardSequence;
            string text = (string)Wire.Object(operation.Intent["parameters"])["text"];
            bool finalParagraph = text.Length != 0 &&
                                  (text[text.Length - 1] == '\r' || text[text.Length - 1] == '\n') &&
                                  publication != null && publication.FinalParagraph(r);
            Action receiver = () =>
            {
                if (!keyboard.Guard(contract, r).Ready)
                    throw new Failure("input_unavailable", "the original paste recipient is no longer ready");
                sequence.Validate(r);
            };
            NativeClipboardTransaction lease;
            try
            {
                lease = clipboard.Save(r);
            }
            catch (NativeClipboardCleanupFailure error)
            {
                operation.Cleanup = "failed";
                throw new Failure("execution_unknown", error.Message);
            }
            try
            {
                receiver();
                lease.Install(text, r, finalParagraph, consumer);
                keyboard.SendBatch(operation, NativeKeys.Chord("ctrl+v"), r, result,
                                   () =>
                                   {
                                       // UIA validation can run application callbacks, including
                                       // clipboard writes. Check our payload after those calls.
                                       receiver();
                                       lease.Validate(r);
                                   });
                var handoff = lease.Handoff(r);
                result["detail"] =
                    handoff == NativePasteHandoff.TargetRead
                        ? "The original native consumer retrieved the clipboard payload; observe the control to verify the application effect."
                    : handoff == NativePasteHandoff.OwnershipLost
                        ? "Paste chord was sent, but clipboard ownership changed before target delivery was confirmed. Observe the target; no input was replayed."
                    : handoff == NativePasteHandoff.TargetLost
                        ? "Paste chord was sent, but the original native consumer ended before delivery was confirmed. Its application effect remains unconfirmed; no input was replayed."
                        : "Paste chord was sent; target clipboard delivery remains unconfirmed after the bounded handoff wait. A pending paste may still run. Observe the target before further input; nothing was replayed.";
            }
            finally
            {
                if (lease.Prepared)
                    preparation.Add("clipboard");
                var cleanup = lease.Finish(operation.Dispatched);
                if (cleanup == NativeClipboardRestore.Failed)
                    operation.Cleanup = "failed";
                else if (cleanup == NativeClipboardRestore.OwnershipLost)
                    result["detail"] = (string)result["detail"] + " Newer clipboard content was preserved.";
            }
        }
    }
}
