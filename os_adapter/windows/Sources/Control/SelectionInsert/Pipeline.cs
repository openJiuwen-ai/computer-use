// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Runtime.ExceptionServices;

namespace ComputerUse.Windows
{
    // One selected native edit, using the current valid selection. The native
    // adapter pins identity; this pipeline never selects, focuses or retries.
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
            string role = offer.Initial.Role;
            IDisposable buffer = null;
            Exception primary = null;
            try
            {
                var state = ReadSelectionInsertStatePass.Run(sequence, request);
                CheckSelectionInsertStatePass.Run(state, role);
                if (text.Length == 0)
                {
                    result["confirmation"] = "confirmed";
                    result["detail"] = "Empty insertion requested; no selection or text input was sent.";
                    return;
                }
                buffer = CreateSelectionInsertBufferPass.Run(sequence, text);
                state = ReadSelectionInsertStatePass.Run(sequence, request);
                CheckSelectionInsertStatePass.Run(state, role);
                // Earlier caret/selection changes are legal. ValidateFinal
                // checks this fresh state and pinned native identity, then no
                // further provider query separates that guard from dispatch.
                ValidateSelectionInsertFinalPass.Run(sequence, state, request);
                SendSelectionInsertPass.Run(sequence, buffer, operation, request, result);
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
    }
}
