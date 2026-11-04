// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;

namespace ComputerUse.Windows
{
    // Private call-scoped identity, not a public target or permission cache.
    // The write operation alone owns capture. Contracts borrow this narrow port.
    internal interface INativeAppInsertPinOwner
    {
        INativeAppInsertRecipient AppInsertRecipient { get; }
        void CaptureAppInsertRecipient(INativeAppInsertRecipient recipient);
    }
    internal interface INativeAppInsertRecipient
    {
        INativeKeyboardSequence Sequence { get; }
        NativeKeyboardState Read(Request request);
        NativeSelectionInsertOffer OfferSelectionInsert(Request request);
    }
    internal interface INativeAppInsertProvider
    {
        INativeAppInsertRecipient CaptureAppInsertRecipient(NativeKeyboardState ready, Request request);
    }
    // A boundary/permission loss differs from optional mechanism absence.
    // Existing Failure protocol mapping is retained, with no retry permission.
    internal sealed class NativeAppInsertRecipientFailure : Failure
    {
        internal readonly Exception Original;
        internal NativeAppInsertRecipientFailure(Exception original)
            : base(original is Failure ? ((Failure)original).Code : "input_unavailable",
                   "The original App text recipient is unavailable: " + original.Message,
                   "observe the selected working window before starting a new input operation")
        {
            Original = original;
        }
        // The marker stops route replacement; it does not turn an ordinary
        // exception into a known provider refusal. Receipt owners still apply
        // their original dispatch-sensitive mapping to unknown exceptions.
        internal static Failure FailureForClassification(Exception error)
        {
            while (error is NativeAppInsertRecipientFailure)
                error = ((NativeAppInsertRecipientFailure)error).Original;
            return error as Failure;
        }
        internal static Exception Preserve(Exception error)
        {
            if (error is NativeDesktopCleanupFailure || error is NativeAppInsertRecipientFailure)
                return error;
            var failure = error as Failure;
            if (failure != null && failure.Code != "input_unavailable" && failure.Code != "stale_target" &&
                failure.Code != "blocked_by_modal")
                return error;
            return new NativeAppInsertRecipientFailure(error);
        }
        internal static T Query<T>(bool pinned, Request request, Func<T> query)
        {
            try
            {
                return query();
            }
            catch (Exception error)
            {
                // Resource health must reach the sole runtime owner even if
                // cancellation arrived while a provider was unwinding.
                if (error is NativeDesktopCleanupFailure)
                    throw;
                request.Check();
                if (!pinned)
                    throw;
                throw Preserve(error);
            }
        }
    }
}
