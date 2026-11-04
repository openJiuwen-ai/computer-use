// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;

namespace ComputerUse.Windows
{
    internal sealed class NativeSelectionInsertSelection
    {
        internal int Start, End;
    }
    internal sealed class NativeSelectionInsertState
    {
        internal string Role;
        internal bool? Enabled, Writable;
        internal NativeSelectionInsertSelection Selection;
        internal bool Eligible, Ready;
    }
    internal sealed class NativeSelectionInsertSendResult
    {
        internal bool Completed;
        internal string Detail;
        internal NativeSelectionInsertSendResult()
        {
        }
        internal NativeSelectionInsertSendResult(bool completed, string detail = null)
        {
            Completed = completed;
            Detail = detail;
        }
    }
    // Opaque pinned adapter. Read/ValidateFinal never prepare or send input;
    // CreateBuffer owns only local resources; Send enters exactly one call.
    internal interface INativeSelectionInsertSequence
    {
        NativeSelectionInsertState Read(Request request);
        void ValidateFinal(NativeSelectionInsertState state, Request request);
        IDisposable CreateBuffer(string text);
        NativeSelectionInsertSendResult Send(IDisposable buffer, int timeoutMs);
    }
    internal sealed class NativeSelectionInsertOffer
    {
        internal readonly INativeSelectionInsertSequence Sequence;
        internal readonly NativeSelectionInsertState Initial;
        internal NativeSelectionInsertOffer(INativeSelectionInsertSequence sequence, NativeSelectionInsertState initial)
        {
            Sequence = sequence;
            Initial = initial;
        }
    }
    internal interface INativeSelectionInsertProvider
    {
        NativeSelectionInsertOffer OfferSelectionInsert(Dictionary<string, object> target, string[] within,
                                                        Request request);
    }
}
