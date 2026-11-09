// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Runtime.ExceptionServices;

namespace ComputerUse.Windows
{
    // One addressed native edit. Insertion uses the current selection;
    // replacement can prepare the whole range without changing focus.
    internal static class NativeSelectionInsertPipeline
    {
        internal static void Run(NativeSelectionInsertOffer offer, string text, NativeWriteOperation operation,
                                 Request request, Dictionary<string, object> result)
        {
            ValidateSelectionInsertPayloadPass.Run(text);
            if (offer == null || offer.Sequence == null || offer.Initial == null ||
                offer.Initial.Role != "ControlType.Edit")
                throw new Failure("input_unavailable", "the original native selection-edit contract is unavailable");
            if (operation.InputTerminated)
                throw new Failure(
                    "execution_unknown",
                    "the operation already has input or unresolved cleanup; no selection edit is replayed");
            var sequence = offer.Sequence;
            var replacement = sequence as INativeSelectionReplacementSequence;
            string role = offer.Initial.Role;
            IDisposable buffer = null;
            Exception primary = null;
            try
            {
                var state = ReadSelectionInsertStatePass.Run(sequence, request);
                CheckSelectionInsertStatePass.Run(state, role);
                if (text.Length == 0 && replacement == null)
                {
                    result["confirmation"] = "confirmed";
                    result["detail"] = "Empty insertion requested; no selection or text input was sent.";
                    return;
                }
                buffer = CreateSelectionInsertBufferPass.Run(sequence, text);
                if (replacement == null)
                {
                    state = ReadSelectionInsertStatePass.Run(sequence, request);
                    CheckSelectionInsertStatePass.Run(state, role);
                }
                if (replacement != null && !CoversText(state))
                {
                    SelectReplacementRange(replacement, operation, request, result);
                    state = ReadSelectionInsertStatePass.Run(sequence, request);
                    CheckSelectionInsertStatePass.Run(state, role);
                    if (!CoversText(state))
                        throw new Failure("input_unavailable", "the original edit did not retain the complete selection");
                }
                // Earlier caret/selection changes are legal. ValidateFinal
                // checks this fresh state and pinned native identity, then no
                // further provider query separates that guard from dispatch.
                ValidateSelectionInsertFinalPass.Run(sequence, state, request);
                SendSelectionInsertPass.Run(sequence, buffer, operation, request, result);
                if (replacement != null)
                    result["detail"] = "Native full-selection text edit dispatched to the original Edit.";
                request.Check();
            }
            catch (Exception error)
            {
                primary = error;
            }
            finally
            {
                try
                {
                    ReleaseSelectionInsertBufferPass.Run(buffer, operation, result);
                }
                catch (Exception cleanup)
                {
                    var desktopCleanup =
                        primary as NativeDesktopCleanupFailure ?? cleanup as NativeDesktopCleanupFailure;
                    if (desktopCleanup != null)
                    {
                        // Keep typed resource health across this local resource
                        // finally. The existing object/operation owner retires
                        // the worker; this pipeline owns no health state.
                        primary = new NativeDesktopCleanupFailure(
                            desktopCleanup.Reason + "; selection-edit buffer cleanup also failed: " + cleanup.Message,
                            primary ?? cleanup);
                    }
                    else
                    {
                        var failure = NativeAppInsertRecipientFailure.FailureForClassification(primary);
                        string code = failure != null        ? failure.Code
                                      : operation.Dispatched ? "execution_unknown"
                                                             : "input_unavailable";
                        string detail = primary == null
                                            ? "Native selection-edit buffer cleanup failed: " + cleanup.Message
                                            : primary.Message + "; buffer cleanup also failed: " + cleanup.Message;
                        var combined = new Failure(
                            code, detail,
                            failure == null ? "read the target before continuing; do not replay uncertain input"
                                            : failure.Next);
                        primary = primary is NativeAppInsertRecipientFailure
                                      ? new NativeAppInsertRecipientFailure(combined)
                                      : combined;
                    }
                }
            }
            if (primary != null)
                ExceptionDispatchInfo.Capture(primary).Throw();
        }

        internal static bool CoversText(NativeSelectionInsertState state)
        {
            return state.TextLength.HasValue && state.Selection != null && state.Selection.Start == 0 &&
                   state.Selection.End == state.TextLength.Value;
        }

        static void SelectReplacementRange(INativeSelectionReplacementSequence replacement, NativeWriteOperation operation,
                                           Request request, Dictionary<string, object> result)
        {
            replacement.ValidateSelectionTarget(request);
            request.Check();
            int timeout = (int)Math.Max(1, Math.Min(100, request.Deadline - Wire.Now));
            result["preparation"] = new[] { "nativeSelection" };
            operation.SelectionPreparationStarted = true;
            request.Effect = true;
            NativeSelectionInsertSendResult selected;
            try
            {
                selected = replacement.SelectAll(timeout);
            }
            catch (Exception error)
            {
                throw new Failure("execution_unknown", "the native selection preparation failed after entry: " + error.Message);
            }
            if (selected == null || !selected.Completed)
                throw new Failure("execution_unknown", selected == null ? "the native selection preparation is uncertain"
                                                                         : selected.Detail ?? "the native selection preparation is uncertain");
        }
    }
}
