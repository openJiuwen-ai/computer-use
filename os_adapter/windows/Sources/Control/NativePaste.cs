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
            keyboard.RequireGlobalInput(); // Before acquiring or installing the shared clipboard.
            var state = keyboard.Guard(contract, r);
            if (!state.Ready)
                throw new Failure("input_unavailable", "the selected paste recipient is not ready");
            var sequence = receiverProvider.BeginSequence(state, r);
            sequence.Validate(r);
            var publication = sequence as INativePasteClipboardSequence;
            string text = Text(operation);
            bool finalParagraph = text.Length != 0 &&
                                  (text[text.Length - 1] == '\r' || text[text.Length - 1] == '\n') &&
                                  publication != null && publication.FinalParagraph(r);
            // The native rich-text mode consumes the final paragraph as its
            // fragment terminator. Supply a separate suffix for this recipe.
            Send(keyboard, clipboard, operation, r, result, preparation, finalParagraph ? text + "\r\n" : text,
                 () =>
                 {
                     if (!keyboard.Guard(contract, r).Ready)
                         throw new Failure("input_unavailable", "the original paste recipient is no longer ready");
                     sequence.Validate(r);
                 });
        }

        internal static void SendGlobal(NativeKeyboard keyboard, INativeClipboardProvider clipboard,
                                        NativeWriteOperation operation, Request r, Dictionary<string, object> result,
                                        List<string> preparation, Action guard)
        {
            keyboard.RequireGlobalInput();
            Send(keyboard, clipboard, operation, r, result, preparation, Text(operation), guard);
        }

        static string Text(NativeWriteOperation operation)
        {
            return (string)Wire.Object(operation.Intent["parameters"])["text"];
        }

        static void Send(NativeKeyboard keyboard, INativeClipboardProvider clipboard, NativeWriteOperation operation,
                         Request r, Dictionary<string, object> result, List<string> preparation, string text,
                         Action receiver)
        {
            Validate(text);
            if (text.Length == 0)
            {
                result["confirmation"] = "confirmed";
                result["detail"] = "Empty paste; no input or clipboard change was needed.";
                return;
            }
            receiver();
            INativeClipboardPublication lease;
            try
            {
                lease = clipboard.Create();
            }
            catch (NativeClipboardCleanupFailure error)
            {
                operation.Cleanup = "failed";
                throw new Failure("execution_unknown", error.Message);
            }
            try
            {
                lease.Publish(text, r);
                keyboard.SendBatch(operation, NativeKeys.Chord("ctrl+v"), r, result,
                                   () =>
                                   {
                                       // UIA validation can run application callbacks, including
                                       // clipboard writes. Check ownership after those calls.
                                       receiver();
                                       lease.Validate(r);
                                   });
                result["detail"] = "Paste chord sent. Published text remains on the clipboard until overwritten; " +
                                   "check the target to confirm paste completion.";
            }
            finally
            {
                try
                {
                    lease.Dispose();
                }
                catch (Exception error)
                {
                    operation.RetireWorker("clipboard cleanup could not be confirmed: " + error.Message);
                }
                if (lease.Changed)
                    preparation.Add("clipboard");
            }
        }
    }
}
